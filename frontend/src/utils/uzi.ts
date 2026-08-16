/**
 * UZI 报告展示辅助（状态文案 / 语义色 / 产物文案）。
 */
import type { UziArtifactKey, UziReportStatus } from '@/types'

export const UZI_PHASE_LABELS: Record<string, string> = {
  queued: '已入队',
  stage1_running: '数据采集与机械评分',
  llm_review: '大模型深度评审',
  stage2_running: '报告综合与渲染',
  completed: '已完成',
  failed: '失败',
  cancelled: '已取消',
}

export function uziStatusLabel(status: UziReportStatus | string | null | undefined): string {
  if (!status) return '未知'
  return UZI_PHASE_LABELS[status] ?? status
}

export type BadgeTone = 'neutral' | 'accent' | 'success' | 'danger' | 'warning'

const STATUS_TONES: Record<string, BadgeTone> = {
  queued: 'warning',
  stage1_running: 'accent',
  llm_review: 'accent',
  stage2_running: 'accent',
  completed: 'success',
  failed: 'danger',
  cancelled: 'neutral',
}

export function uziStatusTone(status: UziReportStatus | string | null | undefined): BadgeTone {
  if (!status) return 'neutral'
  return STATUS_TONES[status] ?? 'neutral'
}

export const UZI_ARTIFACT_LABELS: Record<UziArtifactKey, string> = {
  html: 'HTML 报告',
  share_card: '分享图',
  war_report: '战报图',
  meta: '元数据',
  one_liner: '一句话结论',
  synthesis: '综合数据',
}

export const UZI_ARTIFACT_FILENAMES: Record<UziArtifactKey, string> = {
  html: 'report.html',
  share_card: 'share-card.png',
  war_report: 'war-report.png',
  meta: 'report.meta.json',
  one_liner: 'one-liner.txt',
  synthesis: 'synthesis.json',
}

export function formatReportDuration(
  createdAt: string | null | undefined,
  finishedAt: string | null | undefined,
): string {
  if (!createdAt) return '--'
  const start = new Date(createdAt).getTime()
  if (Number.isNaN(start)) return '--'
  const end = finishedAt ? new Date(finishedAt).getTime() : Date.now()
  if (Number.isNaN(end)) return '--'
  const seconds = Math.max(0, Math.floor((end - start) / 1000))
  const minutes = Math.floor(seconds / 60)
  const remain = seconds % 60
  if (minutes >= 60) {
    const hours = Math.floor(minutes / 60)
    return `${hours}时 ${minutes % 60}分`
  }
  return minutes > 0 ? `${minutes}分 ${String(remain).padStart(2, '0')}秒` : `${remain}秒`
}