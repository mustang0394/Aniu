"""StateStore 缓存失效回归测试。

issue: 删除失败报告后，用同一 report_id 重新提交会立即报同样错。
根因：StateStore.get 在磁盘状态文件已被删除时，仍返回内存 _cache 里
的过期终态，导致主服务 get_job 拿到 failed 状态后直接抛错而不重新提交。
"""
from __future__ import annotations

from pathlib import Path

from app.models import WorkerJobState
from app.state_store import StateStore


def test_get_returns_none_and_clears_cache_when_state_file_deleted(tmp_path: Path) -> None:
    store = StateStore(tmp_path, recover=False)
    report_id = "2"

    # 写入一个 failed 终态
    state = WorkerJobState(report_id=report_id)
    state.mark_failed(
        error_code="UZI_STAGE1_FAILED",
        error_message="Stage 1 执行失败：'>=' not supported ...",
    )
    store.upsert(state)

    # 确认能读到
    fetched = store.get(report_id)
    assert fetched is not None
    assert fetched.status == "failed"

    # 模拟 delete_report 删除报告目录（含 worker-state.json）
    import shutil

    shutil.rmtree(tmp_path / report_id)

    # 修复后：磁盘文件没了 → get 必须返回 None 并清掉内存缓存
    assert store.get(report_id) is None
    assert "2" not in store._cache

    # 之后再次 upsert 同一 report_id 应能正常工作（无残留）
    fresh = WorkerJobState(report_id=report_id)
    fresh.mark_running(phase="stage1_running", progress=5, message="重新采集")
    store.upsert(fresh)
    assert store.get(report_id) is not None
    assert store.get(report_id).status == "running"
