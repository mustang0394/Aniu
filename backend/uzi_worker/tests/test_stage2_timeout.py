"""Stage 2 卡死保护与中间产物进度同步测试。"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from app.config import (
    DEFAULT_QUANT_MAX_FUNDS,
    DEFAULT_QUANT_TIMEOUT_SECONDS,
    DEFAULT_STAGE2_TIMEOUT_SECONDS,
    get_worker_config,
)
from app.models import WorkerJobState
from app.runner import JobRunner
from app.state_store import StateStore
from app.uzi_adapter import _run_bounded_renderer, run_stage2


class _RunningProcess:
    pid = 99999999

    @staticmethod
    def poll():
        return None


def _make_runner(
    tmp_path: Path, *, timeout_seconds: int = 600
) -> tuple[JobRunner, StateStore]:
    report_root = tmp_path / "reports"
    store = StateStore(report_root)
    runner = JobRunner(
        store=store,
        report_root=report_root,
        source_root=tmp_path / "source",
        mock=True,
        stage2_timeout_seconds=timeout_seconds,
    )
    return runner, store


def _put_running_stage2(store: StateStore, report_id: str) -> WorkerJobState:
    state = WorkerJobState(report_id=report_id)
    state.mark_running(
        phase="stage2_running",
        progress=85,
        message="正在综合并渲染报告。",
    )
    store.upsert(state)
    return state


def test_stage2_timeout_terminates_and_marks_failed(tmp_path, monkeypatch) -> None:
    runner, store = _make_runner(tmp_path, timeout_seconds=1)
    _put_running_stage2(store, "801")
    runner._procs["801"] = _RunningProcess()
    runner._proc_stages["801"] = "2"
    runner._proc_started_at["801"] = time.monotonic() - 2

    terminated: list[str] = []
    monkeypatch.setattr(
        runner,
        "_terminate_process_group",
        lambda report_id: terminated.append(report_id),
    )

    runner._poll("801")

    state = store.get("801")
    assert state is not None
    assert state.status == "failed"
    assert state.error_code == "UZI_JOB_TIMEOUT"
    assert "stage2.log" in str(state.error_message)
    assert terminated == ["801"]
    assert "801" not in runner._procs
    assert "801" not in runner._proc_stages
    assert "801" not in runner._proc_started_at


def test_stage2_progress_uses_real_intermediate_artifacts(tmp_path) -> None:
    runner, store = _make_runner(tmp_path)
    state = _put_running_stage2(store, "802")
    report_dir = store.report_dir("802")
    work_dir = report_dir / "work"
    work_dir.mkdir(parents=True, exist_ok=True)
    (work_dir / "stage1-manifest.json").write_text(
        json.dumps({"success": True, "ticker_normalized": "600519.SH"}),
        encoding="utf-8",
    )

    scripts_dir = (
        work_dir / "uzi" / "skills" / "deep-analysis" / "scripts"
    )
    cache_dir = scripts_dir / ".cache" / "600519.SH"
    output_dir = scripts_dir / "reports" / "600519.SH_20260817"
    cache_dir.mkdir(parents=True)
    output_dir.mkdir(parents=True)
    (cache_dir / "synthesis.json").write_text("{}", encoding="utf-8")
    (output_dir / "full-report.html").write_text("html", encoding="utf-8")
    (output_dir / "full-report-standalone.html").write_text(
        "standalone", encoding="utf-8"
    )
    (output_dir / "share-card.png").write_bytes(b"png")

    runner._sync_stage2_progress("802")

    updated = store.get("802")
    assert updated is not None
    assert updated.status == "running"
    assert updated.phase == "stage2_running"
    assert updated.progress == 93
    assert updated.progress_message == "分享卡已生成，正在渲染战报图片。"
    assert updated.updated_at != state.updated_at or updated.progress != state.progress


def test_stage2_progress_reports_bounded_quant_lookup(tmp_path) -> None:
    runner, store = _make_runner(tmp_path)
    _put_running_stage2(store, "806")
    report_dir = store.report_dir("806")
    work_dir = report_dir / "work"
    work_dir.mkdir(parents=True, exist_ok=True)
    (work_dir / "stage1-manifest.json").write_text(
        json.dumps({"success": True, "ticker_normalized": "600519.SH"}),
        encoding="utf-8",
    )
    marker = (
        work_dir
        / "uzi/skills/deep-analysis/scripts/.cache/600519.SH/.aniu-quant-running.json"
    )
    marker.parent.mkdir(parents=True)
    marker.write_text(json.dumps({"max_funds": 12}), encoding="utf-8")

    runner._sync_stage2_progress("806")

    updated = store.get("806")
    assert updated is not None
    assert updated.progress == 86
    assert updated.progress_message == "正在识别基金风格（最多 12 只，超时会自动跳过）。"


def test_stage2_timeout_config_is_positive(monkeypatch) -> None:
    monkeypatch.setenv("UZI_STAGE2_TIMEOUT_SECONDS", "17")
    monkeypatch.setenv("UZI_RENDER_TIMEOUT_SECONDS", "23")
    monkeypatch.setenv("UZI_QUANT_MAX_FUNDS", "7")
    monkeypatch.setenv("UZI_QUANT_TIMEOUT_SECONDS", "11")
    assert get_worker_config().stage2_timeout_seconds == 17
    assert get_worker_config().render_timeout_seconds == 23
    assert get_worker_config().quant_max_funds == 7
    assert get_worker_config().quant_timeout_seconds == 11

    monkeypatch.setenv("UZI_STAGE2_TIMEOUT_SECONDS", "0")
    assert get_worker_config().stage2_timeout_seconds == DEFAULT_STAGE2_TIMEOUT_SECONDS

    monkeypatch.setenv("UZI_STAGE2_TIMEOUT_SECONDS", "not-a-number")
    assert get_worker_config().stage2_timeout_seconds == DEFAULT_STAGE2_TIMEOUT_SECONDS
    monkeypatch.setenv("UZI_QUANT_MAX_FUNDS", "0")
    monkeypatch.setenv("UZI_QUANT_TIMEOUT_SECONDS", "invalid")
    assert get_worker_config().quant_max_funds == DEFAULT_QUANT_MAX_FUNDS
    assert get_worker_config().quant_timeout_seconds == DEFAULT_QUANT_TIMEOUT_SECONDS


def test_renderer_timeout_terminates_isolated_process(tmp_path) -> None:
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "render_share_card.py").write_text(
        "import time\n"
        "def render(*args, **kwargs):\n"
        "    time.sleep(30)\n",
        encoding="utf-8",
    )

    with pytest.raises(TimeoutError, match="已跳过"):
        _run_bounded_renderer(
            scripts_dir=scripts_dir,
            ticker="600519.SH",
            selector="#share-card",
            out_name="share-card.png",
            scale=2,
            timeout_seconds=1,
        )


def test_optional_render_timeout_keeps_html_report(tmp_path) -> None:
    report_dir = tmp_path / "reports" / "803"
    work_dir = report_dir / "work"
    scripts_dir = (
        work_dir / "uzi" / "skills" / "deep-analysis" / "scripts"
    )
    scripts_dir.mkdir(parents=True)
    (work_dir / "agent_analysis.json").write_text(
        json.dumps({"agent_reviewed": True}), encoding="utf-8"
    )
    (scripts_dir / "render_share_card.py").write_text(
        "import time\n"
        "def render(*args, **kwargs):\n"
        "    time.sleep(30)\n"
        "main = render\n",
        encoding="utf-8",
    )
    (scripts_dir / "render_war_report.py").write_text(
        "from render_share_card import render\n"
        "def main(ticker):\n"
        "    return render(ticker, selector='#war-report', "
        "out_name='war-report.png', scale=2)\n",
        encoding="utf-8",
    )
    (scripts_dir / "run_real_test.py").write_text(
        "import json\n"
        "from pathlib import Path\n"
        "def stage2(ticker):\n"
        "    cache = Path('.cache') / ticker\n"
        "    cache.mkdir(parents=True, exist_ok=True)\n"
        "    (cache / 'synthesis.json').write_text(json.dumps({"
        "'ticker': ticker, 'overall_score': 70, 'verdict_label': '中性'}), "
        "encoding='utf-8')\n"
        "    out = Path('reports') / f'{ticker}_20260817'\n"
        "    out.mkdir(parents=True, exist_ok=True)\n"
        "    html = out / 'full-report-standalone.html'\n"
        "    html.write_text('<html>' + 'x' * 12288 + '</html>', encoding='utf-8')\n"
        "    try:\n"
        "        from render_share_card import main as render_share\n"
        "        render_share(ticker)\n"
        "    except Exception:\n"
        "        pass\n"
        "    try:\n"
        "        from render_war_report import main as render_war\n"
        "        render_war(ticker)\n"
        "    except Exception:\n"
        "        pass\n"
        "    return str(html.resolve())\n",
        encoding="utf-8",
    )

    result = run_stage2(
        report_dir=report_dir,
        normalized_ticker="600519.SH",
        source_root=tmp_path / "unused",
        mock=False,
        render_timeout_seconds=1,
    )

    assert result.success
    assert (report_dir / "artifacts.tmp" / "full-report-standalone.html").is_file()
    assert (report_dir / "artifacts.tmp" / "synthesis.json").is_file()
    assert not (report_dir / "artifacts.tmp" / "share-card.png").exists()
    assert not (report_dir / "artifacts.tmp" / "war-report.png").exists()


def test_quant_timeout_falls_back_and_stage2_continues(tmp_path) -> None:
    report_dir = tmp_path / "reports" / "804"
    work_dir = report_dir / "work"
    scripts_dir = work_dir / "uzi" / "skills" / "deep-analysis" / "scripts"
    lib_dir = scripts_dir / "lib"
    lib_dir.mkdir(parents=True)
    (lib_dir / "__init__.py").write_text("", encoding="utf-8")
    (lib_dir / "quant_signal.py").write_text(
        "import time\n"
        "def detect_quant_signal(stock_code, fund_managers=None, max_funds=80):\n"
        "    time.sleep(30)\n"
        "    return {'is_quant_factor_style': True}\n",
        encoding="utf-8",
    )
    (work_dir / "agent_analysis.json").write_text(
        json.dumps({"agent_reviewed": True}), encoding="utf-8"
    )
    (scripts_dir / "run_real_test.py").write_text(
        "import json\n"
        "from pathlib import Path\n"
        "def stage2(ticker):\n"
        "    from lib.quant_signal import detect_quant_signal\n"
        "    quant = detect_quant_signal(ticker, [{'fund': str(i)} for i in range(30)])\n"
        "    cache = Path('.cache') / ticker\n"
        "    cache.mkdir(parents=True, exist_ok=True)\n"
        "    (cache / 'synthesis.json').write_text(json.dumps({'ticker': ticker, "
        "'overall_score': 70, 'quant': quant}), encoding='utf-8')\n"
        "    out = Path('reports') / f'{ticker}_20260817'\n"
        "    out.mkdir(parents=True, exist_ok=True)\n"
        "    html = out / 'full-report-standalone.html'\n"
        "    html.write_text('<html>' + 'x' * 12288 + '</html>', encoding='utf-8')\n"
        "    return str(html.resolve())\n",
        encoding="utf-8",
    )

    started = time.monotonic()
    result = run_stage2(
        report_dir=report_dir,
        normalized_ticker="600519.SH",
        source_root=tmp_path / "unused",
        mock=False,
        quant_max_funds=5,
        quant_timeout_seconds=1,
    )

    assert result.success
    assert time.monotonic() - started < 8
    synthesis = json.loads(
        (report_dir / "artifacts.tmp" / "synthesis.json").read_text(encoding="utf-8")
    )
    assert synthesis["quant"]["is_quant_factor_style"] is False
    assert "超过 1 秒" in synthesis["quant"]["fallback_reason"]


def test_quant_fund_managers_are_capped(tmp_path) -> None:
    report_dir = tmp_path / "reports" / "805"
    work_dir = report_dir / "work"
    scripts_dir = work_dir / "uzi" / "skills" / "deep-analysis" / "scripts"
    lib_dir = scripts_dir / "lib"
    lib_dir.mkdir(parents=True)
    (lib_dir / "__init__.py").write_text("", encoding="utf-8")
    (lib_dir / "quant_signal.py").write_text(
        "def detect_quant_signal(stock_code, fund_managers=None, max_funds=80):\n"
        "    return {'is_quant_factor_style': False, 'count': len(fund_managers or []), "
        "'received_max_funds': max_funds}\n",
        encoding="utf-8",
    )
    (work_dir / "agent_analysis.json").write_text(
        json.dumps({"agent_reviewed": True}), encoding="utf-8"
    )
    (scripts_dir / "run_real_test.py").write_text(
        "import json\n"
        "from pathlib import Path\n"
        "def stage2(ticker):\n"
        "    from lib.quant_signal import detect_quant_signal\n"
        "    quant = detect_quant_signal(ticker, list(range(30)), max_funds=80)\n"
        "    cache = Path('.cache') / ticker\n"
        "    cache.mkdir(parents=True, exist_ok=True)\n"
        "    (cache / 'synthesis.json').write_text(json.dumps({'ticker': ticker, "
        "'overall_score': 70, 'quant': quant}), encoding='utf-8')\n"
        "    out = Path('reports') / f'{ticker}_20260817'\n"
        "    out.mkdir(parents=True, exist_ok=True)\n"
        "    html = out / 'full-report-standalone.html'\n"
        "    html.write_text('<html>' + 'x' * 12288 + '</html>', encoding='utf-8')\n"
        "    return str(html.resolve())\n",
        encoding="utf-8",
    )

    result = run_stage2(
        report_dir=report_dir,
        normalized_ticker="600519.SH",
        source_root=tmp_path / "unused",
        mock=False,
        quant_max_funds=6,
        quant_timeout_seconds=5,
    )

    assert result.success
    synthesis = json.loads(
        (report_dir / "artifacts.tmp" / "synthesis.json").read_text(encoding="utf-8")
    )
    assert synthesis["quant"]["count"] == 6
    assert synthesis["quant"]["received_max_funds"] == 6
