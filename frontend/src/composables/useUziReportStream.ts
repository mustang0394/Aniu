/**
 * UZI 报告进度流（文档 §10.5 / §15.2）。
 *
 * 订阅 ``GET /uzi/reports/{id}/events``（fetch + reader，携带 JWT）；
 * 断线或事件流结束后自动回退到轮询 ``GET /uzi/reports/{id}``，
 * 直到任务进入终态（completed / failed / cancelled）。
 */
import { onBeforeUnmount, ref } from 'vue'

import { api, getStoredToken } from '@/services/api'
import type { UziReportDetail, UziReportEvent, UziReportStatus } from '@/types'
import { parseSseChunk } from '@/utils/sse'

const POLL_INTERVAL_MS = 5000
const TERMINAL_STATUSES: readonly UziReportStatus[] = [
  'completed',
  'failed',
  'cancelled',
]

export type UziStreamStatus =
  | 'idle'
  | 'connecting'
  | 'live'
  | 'polling'
  | 'completed'
  | 'failed'
  | 'cancelled'
  | 'error'

function isTerminalStatus(status: UziReportStatus | null | undefined): boolean {
  return status != null && TERMINAL_STATUSES.includes(status)
}

export function useUziReportStream() {
  const reportId = ref<number | null>(null)
  const job = ref<UziReportDetail | null>(null)
  const status = ref<UziReportStatus | null>(null)
  const phase = ref<string | null>(null)
  const progress = ref(0)
  const message = ref<string | null>(null)
  const errorCode = ref<string | null>(null)
  const errorMessage = ref<string | null>(null)
  const streamStatus = ref<UziStreamStatus>('idle')
  const connected = ref(false)
  const lastHeartbeatAt = ref(0)

  let controller: AbortController | null = null
  let pollTimer: number | null = null
  let currentReportId: number | null = null

  function applyJob(detail: UziReportDetail | null | undefined): void {
    if (!detail) return
    job.value = detail
    status.value = detail.status
    phase.value = detail.phase
    progress.value = detail.progress ?? 0
    message.value = detail.progress_message
    errorCode.value = detail.error_code
    errorMessage.value = detail.error_message
    if (isTerminalStatus(detail.status)) {
      streamStatus.value = detail.status as UziStreamStatus
    }
  }

  function applyEvent(event: UziReportEvent): void {
    switch (event.type) {
      case 'snapshot':
        if (event.job) applyJob(event.job)
        break
      case 'progress':
        if (typeof event.progress === 'number') {
          progress.value = event.progress
        }
        if (typeof event.phase === 'string') {
          phase.value = event.phase
        }
        if (typeof event.message === 'string') {
          message.value = event.message
        }
        if (typeof event.status === 'string') {
          status.value = event.status as UziReportStatus
        }
        break
      case 'status_changed':
        if (typeof event.to === 'string') {
          status.value = event.to as UziReportStatus
        }
        if (typeof event.progress === 'number') {
          progress.value = event.progress
        }
        break
      case 'completed':
      case 'failed':
      case 'cancelled':
        status.value = event.type
        streamStatus.value = event.type
        if (typeof event.progress === 'number') {
          progress.value = event.progress
        }
        if (typeof event.message === 'string') {
          message.value = event.message
        }
        if (event.job) applyJob(event.job)
        break
      case 'heartbeat':
        lastHeartbeatAt.value = Date.now()
        break
    }
  }

  function startPolling(reportIdValue: number): void {
    stopPolling()
    streamStatus.value = 'polling'

    const pollOnce = async (): Promise<void> => {
      if (currentReportId !== reportIdValue) return
      try {
        const detail = await api.getUziReport(reportIdValue)
        if (currentReportId !== reportIdValue) return
        applyJob(detail)
        if (isTerminalStatus(detail.status)) {
          stopPolling()
          return
        }
      } catch (err) {
        errorMessage.value = (err as Error).message || '轮询报告状态失败。'
      }
      pollTimer = window.setTimeout(pollOnce, POLL_INTERVAL_MS)
    }
    pollTimer = window.setTimeout(pollOnce, POLL_INTERVAL_MS)
  }

  function stopPolling(): void {
    if (pollTimer !== null) {
      window.clearTimeout(pollTimer)
      pollTimer = null
    }
  }

  async function start(reportIdValue: number): Promise<void> {
    stop()
    currentReportId = reportIdValue
    reportId.value = reportIdValue
    streamStatus.value = 'connecting'
    connected.value = false
    errorMessage.value = null

    controller = new AbortController()
    const token = getStoredToken()
    const headers: Record<string, string> = { Accept: 'text/event-stream' }
    if (token) {
      headers.Authorization = `Bearer ${token}`
    }

    try {
      const response = await fetch(api.uziReportEventsUrl(reportIdValue), {
        method: 'GET',
        headers,
        signal: controller.signal,
        cache: 'no-store',
      })
      if (!response.ok || !response.body) {
        throw new Error(`SSE 连接失败 (${response.status})`)
      }
      if (currentReportId !== reportIdValue) return
      connected.value = true
      streamStatus.value = 'live'

      const reader = response.body.getReader()
      const decoder = new TextDecoder('utf-8')
      let buffer = ''

      for (;;) {
        const { value, done } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })

        let idx = buffer.indexOf('\n\n')
        while (idx >= 0) {
          const chunk = buffer.slice(0, idx)
          buffer = buffer.slice(idx + 2)
          const event = parseSseChunk<UziReportEvent>(chunk, (err, payload) => {
            console.warn('[useUziReportStream] parse failed', err, payload)
          })
          if (event && isTerminalEvent(event)) {
            applyEvent(event)
            connected.value = false
            stopPolling()
            return
          }
          if (event) applyEvent(event)
          idx = buffer.indexOf('\n\n')
        }
      }

      // 服务端正常关闭：未到终态则回退轮询。
      connected.value = false
      if (currentReportId === reportIdValue && !isTerminalStatus(status.value)) {
        startPolling(reportIdValue)
      }
    } catch (err) {
      if ((err as DOMException)?.name === 'AbortError') return
      connected.value = false
      errorMessage.value = (err as Error).message || '事件流中断，已切换为轮询更新。'
      if (currentReportId === reportIdValue && !isTerminalStatus(status.value)) {
        startPolling(reportIdValue)
      }
    }
  }

  function isTerminalEvent(event: UziReportEvent): boolean {
    return event.type === 'completed' || event.type === 'failed' || event.type === 'cancelled'
  }

  function stop(): void {
    if (controller) {
      controller.abort()
      controller = null
    }
    stopPolling()
    connected.value = false
    currentReportId = null
  }

  function reset(): void {
    stop()
    reportId.value = null
    job.value = null
    status.value = null
    phase.value = null
    progress.value = 0
    message.value = null
    errorCode.value = null
    errorMessage.value = null
    streamStatus.value = 'idle'
    lastHeartbeatAt.value = 0
  }

  onBeforeUnmount(() => {
    stop()
  })

  return {
    reportId,
    job,
    status,
    phase,
    progress,
    message,
    errorCode,
    errorMessage,
    streamStatus,
    connected,
    lastHeartbeatAt,
    start,
    stop,
    reset,
  }
}