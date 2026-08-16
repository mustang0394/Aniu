"""UZI 测试共享辅助（不以 test_ 开头，避免被 pytest 收集）。"""

from __future__ import annotations

from pathlib import Path
import sys

from fastapi.testclient import TestClient

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings
from app.core import rate_limit as rate_limit_module
from app.db import database as database_module
from app.db.database import session_scope
from app.db.models import AppSettings, UziReportJob
from app.main import create_app
from app.services.scheduler_service import scheduler_service
from app.services.trading_calendar_service import trading_calendar_service
from app.services.uzi_report_service import uzi_report_service
from app.services.uzi_worker_client import UziWorkerClient


class FakeWorkerClient(UziWorkerClient):
    """假 Worker：内存状态机，用于主服务完整状态流转测试。"""

    def __init__(self) -> None:
        self.available = True
        self.version = "7bc779d"
        self.jobs: dict[int, dict] = {}
        self.auto_complete_stage1 = True
        self.stage1_fail_code: str | None = None
        self.stage2_fail_code: str | None = None
        self.cancelled: list[int] = []

    def health(self) -> dict | None:
        if not self.available:
            return None
        return {
            "status": "ok",
            "worker_version": self.version,
            "uzi_commit": self.version,
            "chromium_available": True,
            "active_jobs": 0,
            "queued_jobs": 0,
        }

    def submit_stage1(self, *, report_id, ticker, report_rel_dir, mx_api_key=None):
        if not self.available:
            return None
        self.jobs[report_id] = {
            "report_id": str(report_id),
            "status": "succeeded",
            "phase": "stage1_done",
            "progress": 45,
            "progress_message": "Stage 1 完成。",
            "error_code": None,
            "error_message": None,
            "ticker": ticker,
            "ticker_normalized": "600519.SH",
            "company_name": "贵州茅台",
        }
        if self.stage1_fail_code:
            self.jobs[report_id]["status"] = "failed"
            self.jobs[report_id]["error_code"] = self.stage1_fail_code
            self.jobs[report_id]["error_message"] = "Stage 1 失败。"
        return {"job": self.jobs[report_id]}

    def get_job(self, report_id):
        if not self.available:
            return None
        return self.jobs.get(report_id)

    def submit_stage2(self, *, report_id, ticker=None):
        if not self.available:
            return None
        if report_id not in self.jobs:
            return None
        self.jobs[report_id].update(
            {
                "status": "succeeded",
                "phase": "completed",
                "progress": 100,
                "progress_message": "报告已生成。",
            }
        )
        if self.stage2_fail_code:
            self.jobs[report_id]["status"] = "failed"
            self.jobs[report_id]["error_code"] = self.stage2_fail_code
            self.jobs[report_id]["error_message"] = "Stage 2 失败。"
        else:
            # 模拟真实 Worker：把产物写入共享目录（主服务从磁盘读取）。
            self._write_fake_artifacts(report_id)
        return {"job": self.jobs[report_id]}

    def _write_fake_artifacts(self, report_id: int) -> None:
        import json as _json

        from app.core.config import get_settings

        root = get_settings().uzi_report_root
        artifacts = root / str(report_id) / "artifacts"
        artifacts.mkdir(parents=True, exist_ok=True)
        (artifacts / "full-report-standalone.html").write_text("A" * 12000, encoding="utf-8")
        synthesis = {
            "ticker": "600519.SH",
            "name": "贵州茅台",
            "company_name": "贵州茅台",
            "overall_score": 78.5,
            "verdict_label": "谨慎看多",
            "one_liner": "核心结论",
            "data_as_of": "2026-08-16T00:00:00",
        }
        (artifacts / "synthesis.json").write_text(
            _json.dumps(synthesis, ensure_ascii=False), encoding="utf-8"
        )
        manifest = {
            "schema_version": 1,
            "ticker": "600519.SH",
            "generated_at": "2026-08-16T00:00:00",
            "artifacts": [
                {"file": "full-report-standalone.html", "size": 12000, "sha256": "x", "mime": "text/html; charset=utf-8"},
                {"file": "synthesis.json", "size": 200, "sha256": "y", "mime": "application/json; charset=utf-8"},
            ],
        }
        (artifacts / "artifact-manifest.json").write_text(
            _json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
        )

    def cancel(self, report_id):
        self.cancelled.append(report_id)
        if report_id in self.jobs:
            self.jobs[report_id]["status"] = "cancelled"
        return {"job": self.jobs.get(report_id, {})}


def create_uzi_test_client(
    monkeypatch,
    tmp_path,
    *,
    llm_configured: bool = True,
    worker: FakeWorkerClient | None = None,
    uzi_enabled: bool = True,
) -> tuple[TestClient, FakeWorkerClient]:
    monkeypatch.setenv("APP_LOGIN_PASSWORD", "release-pass")
    monkeypatch.setenv("SQLITE_DB_PATH", str(tmp_path / "uzi.db"))
    monkeypatch.setenv("UZI_ENABLED", "1" if uzi_enabled else "0")
    monkeypatch.setenv("UZI_WORKER_URL", "http://fake-worker:9001")
    monkeypatch.setenv("UZI_WORKER_SHARED_SECRET", "test-secret")
    monkeypatch.setenv("UZI_REPORT_ROOT", str(tmp_path / "uzi_reports"))
    monkeypatch.setenv("UZI_LLM_REVIEW_MOCK", "1")
    monkeypatch.setattr(trading_calendar_service, "ensure_years", lambda years: None)
    monkeypatch.setattr(scheduler_service, "start", lambda: None)
    monkeypatch.setattr(scheduler_service, "stop", lambda: None)
    get_settings.cache_clear()
    database_module._engine = None
    database_module._session_local = None
    rate_limit_module._limiter.reset()

    fake_worker = worker or FakeWorkerClient()
    uzi_report_service._worker = fake_worker
    uzi_report_service.reset_rate_limit()
    uzi_report_service._cancel_events.clear()
    uzi_report_service.stop()  # 确保上个测试的执行器线程已结束，队列已清空

    app = create_app()
    client = TestClient(app)

    # 显式建表（TestClient 的 lifespan 在进入 with 时才执行 init_db）。
    from app.db.database import init_db

    init_db()

    # 预置 AppSettings（LLM 配置）。
    from sqlalchemy import select

    with session_scope() as db:
        settings_row = db.scalar(select(AppSettings).order_by(AppSettings.id).limit(1))
        if settings_row is None:
            settings_row = AppSettings()
            db.add(settings_row)
            db.flush()
        if llm_configured:
            settings_row.llm_base_url = "https://llm.example.com/v1"
            settings_row.llm_api_key = "sk-test"
        else:
            settings_row.llm_base_url = None
            settings_row.llm_api_key = None

    return client, fake_worker


def auth_headers(client: TestClient) -> dict[str, str]:
    response = client.post("/api/aniu/login", json={"password": "release-pass"})
    payload = response.json()
    return {"Authorization": f"Bearer {payload['token']}"}


def make_completed_report(db, *, ticker: str = "600519.SH") -> UziReportJob:
    """直接构造一条 completed 报告（跳过执行器），供列表/详情/删除/产物测试。"""
    job = UziReportJob(
        ticker_input=ticker,
        ticker_normalized=ticker,
        company_name="贵州茅台",
        status="completed",
        phase="completed",
        progress=100,
        uzi_commit="7bc779d",
        llm_model="gpt-4o-mini",
        report_rel_dir="",
        summary_json={
            "schema_version": 1,
            "ticker": ticker,
            "company_name": "贵州茅台",
            "overall_score": 78.5,
            "verdict": "谨慎看多",
            "one_liner": "核心结论",
            "valuation": {"rating": "合理偏低", "target_price": 0, "upside_pct": 0, "methods": []},
            "risks": [],
            "catalysts": [],
            "panel": {"bullish": 0, "neutral": 0, "bearish": 0, "key_disagreements": []},
            "qualitative": {},
            "data_gaps": {"coverage_pct": 0, "unresolved": 0, "items": []},
            "sources": [],
            "data_as_of": "",
            "generated_at": "",
            "disclaimer": "历史研究资料，不构成投资建议",
        },
        artifact_manifest_json={
            "schema_version": 1,
            "artifacts": [
                {"file": "full-report-standalone.html", "size": 12345, "sha256": "x", "mime": "text/html; charset=utf-8"},
                {"file": "synthesis.json", "size": 123, "sha256": "y", "mime": "application/json; charset=utf-8"},
            ],
        },
    )
    db.add(job)
    db.flush()
    job.report_rel_dir = str(job.id)
    db.commit()
    db.refresh(job)
    return job


def write_fake_stage1(root, report_id: int, *, ticker: str = "600519.SH",
                      company_name: str = "贵州茅台") -> None:
    """为 LLM 编排测试写一份最小合法 Stage 1 产物到共享目录。"""
    import json as _json

    work = root / str(report_id) / "work"
    work.mkdir(parents=True, exist_ok=True)
    manifest = {
        "ticker_normalized": ticker,
        "company_name": company_name,
        "status": "succeeded",
        "data_as_of": "2026-08-16T00:00:00",
    }
    (work / "stage1-manifest.json").write_text(
        _json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )
    (work / "raw_data.json").write_text(
        _json.dumps({"basic": {"name": company_name, "code": ticker}},
                    ensure_ascii=False),
        encoding="utf-8",
    )
    (work / "dimensions.json").write_text(
        _json.dumps({"dimensions": [{"id": "d1", "name": "估值", "score": 7}]},
                    ensure_ascii=False),
        encoding="utf-8",
    )
    (work / "panel.json").write_text(
        _json.dumps({"categories": ["价值", "成长"], "bullish": 10,
                     "neutral": 5, "bearish": 3},
                    ensure_ascii=False),
        encoding="utf-8",
    )
    (work / "_data_gaps.json").write_text(
        _json.dumps({"coverage_pct": 92, "unresolved": 2,
                     "items": ["缺少海外营收分拆"]},
                    ensure_ascii=False),
        encoding="utf-8",
    )


def valid_uzi_synthesis() -> dict:
    """符合上游 agent_analysis schema 的合法 synthesis 输出（§13.4/阻断项4）。

    上游 validator（lib/agent_analysis_validator.py）要求：
    - panel_insights 为字符串；data_gap_acknowledged 为 dict；
    - narrative_override / great_divide_override 为 dict；
    - qualitative_deep_dive 为 {dim: {evidence: [...], associations: [...], conclusion}}。
    """
    return {
        "dim_commentary": {
            "0_basic": "公司主营稳健，营收与利润保持增长，毛利率稳定在较高水平。",
            "1_financials": "ROE 连续三年保持 15% 以上，现金流充裕，财务结构健康。",
        },
        "panel_insights": "评委投票分布中多头占优，价值派与成长派均给出偏多判断，分歧集中在估值中枢。",
        "great_divide_override": {
            "punchline": "价值派看多但成长派担心增速回落，多空分歧显著。",
            "bull_say_rounds": ["估值处于历史低位", "现金流稳定充裕", "股息率有吸引力"],
            "bear_say_rounds": ["增速可能放缓", "行业竞争加剧", "宏观有不确定性"],
        },
        "narrative_override": {
            "core_conclusion": "综合基本面与市场情绪，该标的中长期配置价值较高，建议逢低分批关注。",
            "risks": ["行业政策变化", "原材料价格波动", "市场风格切换"],
            "buy_zones": {
                "value": {"price": 1650, "rationale": "低于内在价值"},
                "growth": {"price": 1700, "rationale": "成长合理区间"},
                "technical": {"price": 1680, "rationale": "技术支撑位"},
                "youzi": {"price": 1720, "rationale": "情绪驱动点"},
            },
        },
        "qualitative_deep_dive": {
            "3_macro": {
                "evidence": [
                    {"source": "宏观数据", "url": "https://example.com/macro", "finding": "流动性宽松"},
                    {"source": "政策文件", "url": "https://example.com/policy", "finding": "产业政策友好"},
                ],
                "associations": [],
                "conclusion": "宏观环境友好，政策支持明确。",
            },
            "7_industry": {
                "evidence": [
                    {"source": "行业报告", "url": "https://example.com/ind", "finding": "行业景气上行"},
                    {"source": "同业数据", "url": "https://example.com/peer", "finding": "份额稳定"},
                ],
                "associations": [],
                "conclusion": "行业空间广阔，竞争格局良好。",
            },
            "13_policy": {
                "evidence": [
                    {"source": "监管公告", "url": "https://example.com/reg", "finding": "监管态度中性"},
                    {"source": "税制文件", "url": "https://example.com/tax", "finding": "税负稳定"},
                ],
                "associations": [],
                "conclusion": "政策面无重大扰动。",
            },
        },
        "data_gap_acknowledged": {
            "8_materials": "原材料成本明细未能获取，已尝试公开数据源。"
        },
    }
