"""真实模式适配器协议测试（阻断项1/2/3）。

构造一个模拟上游仓库（skills/deep-analysis/scripts/run_real_test.py），
验证：
- 入口从 run_real_test 加载（而非旧候选模块名）；
- stage1 产物从 .cache/{ticker}/ 收集并复制到 work/；
- stage2 前把 agent_analysis.json 复制到 .cache/{ticker}/；
- stage2 产物从 reports/{ticker}_{date}/ 收集（full-report-standalone.html）。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _make_fake_upstream(source_root: Path) -> None:
    """构造最小上游仓库副本。"""
    scripts = source_root / "skills" / "deep-analysis" / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    # 模拟上游 run_real_test.py：stage1 写 .cache/{ticker}/，stage2 读 agent_analysis 并写产物。
    (scripts / "run_real_test.py").write_text(
        """
import json, os, sys
from pathlib import Path
HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

def stage1(ticker: str) -> dict:
    cache = Path(".cache") / ticker
    cache.mkdir(parents=True, exist_ok=True)
    (cache / "raw_data.json").write_text(json.dumps({
        "ticker": ticker, "dimensions": {"0_basic": {"data": {"name": "茅台", "fetched_at": "2026-08-16T00:00:00"}}}
    }), encoding="utf-8")
    (cache / "dimensions.json").write_text(json.dumps({"ticker": ticker, "dimensions": [{"id": "value", "score": 70}]}), encoding="utf-8")
    (cache / "panel.json").write_text(json.dumps({"ticker": ticker, "investors": [{"name": "A"}]}), encoding="utf-8")
    return {"ticker": ticker, "raw": {}, "dims": {}, "panel": {}, "features": {}}

def stage2(ticker: str) -> str:
    cache = Path(".cache") / ticker
    agent = json.loads((cache / "agent_analysis.json").read_text(encoding="utf-8"))
    assert agent.get("agent_reviewed") is True, "agent_reviewed 未置位"
    reports = Path("reports") / f"{ticker}_20260816"
    reports.mkdir(parents=True, exist_ok=True)
    html = "<html>" + "x" * (12 * 1024) + "</html>"
    (reports / "full-report-standalone.html").write_text(html, encoding="utf-8")
    (reports / "one-liner.txt").write_text("一句话", encoding="utf-8")
    (cache / "synthesis.json").write_text(json.dumps({
        "ticker": ticker, "name": "茅台", "overall_score": 78.5, "verdict_label": "谨慎看多"
    }), encoding="utf-8")
    return str((reports / "full-report-standalone.html").resolve())

# 早退路径
def stage1_unresolved(ticker: str) -> dict:
    return {"status": "name_not_resolved", "candidates": []}

def stage1_non_stock(ticker: str) -> dict:
    return {"status": "non_stock_security", "label": "ETF", "security_type": "etf"}
""",
        encoding="utf-8",
    )


@pytest.fixture()
def repo(tmp_path) -> Path:
    source_root = tmp_path / "uzi-src"
    _make_fake_upstream(source_root)
    return source_root


def _report_dir(tmp_path) -> Path:
    report_dir = tmp_path / "reports" / "42"
    work = report_dir / "work"
    work.mkdir(parents=True)
    # 模拟 runner._copy_uzi_source：源码副本
    import shutil
    shutil.copytree(tmp_path / "uzi-src", work / "uzi")
    return report_dir


def test_stage1_collects_from_cache_and_copies_to_work(tmp_path, repo) -> None:
    from app.uzi_adapter import run_stage1

    report_dir = _report_dir(tmp_path)
    result = run_stage1(
        report_dir=report_dir,
        ticker="600519.SH",
        source_root=repo,
        mock=False,
    )
    assert result.success
    work = report_dir / "work"
    assert (work / "raw_data.json").is_file()
    assert (work / "dimensions.json").is_file()
    assert (work / "panel.json").is_file()
    assert result.manifest["ticker_normalized"] == "600519.SH"
    assert result.manifest["company_name"] == "茅台"
    assert set(result.manifest["files"]) >= {"raw_data.json", "dimensions.json", "panel.json"}
    # 源码副本里的 .cache 也要存在（目录隔离）。
    assert (work / "uzi" / "skills" / "deep-analysis" / "scripts" / ".cache" / "600519.SH" / "raw_data.json").is_file()


def test_stage1_unresolved_maps_to_stable_code(tmp_path, repo) -> None:
    from app.uzi_adapter import UziStageError, run_stage1

    report_dir = _report_dir(tmp_path)
    # 覆盖 stage1 为 unresolved 版本
    scripts = report_dir / "work" / "uzi" / "skills" / "deep-analysis" / "scripts"
    (scripts / "run_real_test.py").write_text(
        (scripts / "run_real_test.py").read_text(encoding="utf-8").replace(
            "def stage1(ticker: str) -> dict:",
            "def stage1(ticker: str) -> dict:\n    return {'status': 'name_not_resolved', 'candidates': []}",
        ),
        encoding="utf-8",
    )
    with pytest.raises(UziStageError) as exc:
        run_stage1(report_dir=report_dir, ticker="不存在的公司", source_root=repo, mock=False)
    assert exc.value.error_code == "UZI_UNRESOLVED_TICKER"


def test_stage2_copies_agent_analysis_and_collects_artifacts(tmp_path, repo) -> None:
    from app.uzi_adapter import run_stage1, run_stage2

    report_dir = _report_dir(tmp_path)
    run_stage1(report_dir=report_dir, ticker="600519.SH", source_root=repo, mock=False)

    # 主服务写完 agent_analysis.json 到 work/
    (report_dir / "work" / "agent_analysis.json").write_text(
        json.dumps({"agent_reviewed": True, "dim_commentary": {"0_basic": "不错"}}),
        encoding="utf-8",
    )
    result = run_stage2(
        report_dir=report_dir,
        normalized_ticker="600519.SH",
        source_root=repo,
        mock=False,
    )
    assert result.success
    tmp_dir = report_dir / "artifacts.tmp"
    assert (tmp_dir / "full-report-standalone.html").is_file()
    assert (tmp_dir / "synthesis.json").is_file()
    assert (tmp_dir / "one-liner.txt").is_file()
    assert (tmp_dir / "report.meta.json").is_file()
    syn = json.loads((tmp_dir / "synthesis.json").read_text(encoding="utf-8"))
    assert syn["overall_score"] == 78.5
    assert syn["verdict_label"] == "谨慎看多"
    # agent_analysis 已复制到上游读取位置
    cache_agent = (
        report_dir / "work" / "uzi" / "skills" / "deep-analysis" / "scripts"
        / ".cache" / "600519.SH" / "agent_analysis.json"
    )
    assert cache_agent.is_file()
    assert json.loads(cache_agent.read_text(encoding="utf-8"))["agent_reviewed"] is True


def test_mock_follows_same_directory_protocol(tmp_path) -> None:
    """mock 也按 .cache/{ticker}/ + reports/{ticker}_{date}/ 协议产出（阻断项2）。"""
    from app.uzi_adapter import run_stage1, run_stage2

    report_dir = tmp_path / "reports" / "43"
    (report_dir / "work").mkdir(parents=True)
    r1 = run_stage1(report_dir=report_dir, ticker="600519.SH", source_root=tmp_path / "src", mock=True)
    assert r1.success
    work = report_dir / "work"
    cache = work / ".cache" / "600519.SH"
    assert (cache / "raw_data.json").is_file()
    assert (cache / "panel.json").is_file()
    assert (work / "raw_data.json").is_file()

    (work / "agent_analysis.json").write_text(
        json.dumps({"agent_reviewed": True}), encoding="utf-8"
    )
    r2 = run_stage2(report_dir=report_dir, normalized_ticker="600519.SH",
                    source_root=tmp_path / "src", mock=True)
    assert r2.success
    tmp_dir = report_dir / "artifacts.tmp"
    assert (tmp_dir / "full-report-standalone.html").stat().st_size > 10 * 1024
    syn = json.loads((tmp_dir / "synthesis.json").read_text(encoding="utf-8"))
    assert syn["overall_score"] == 78.5
    assert syn["verdict_label"] == "谨慎看多"
    assert (tmp_dir / "share-card.png").is_file()
    assert (tmp_dir / "war-report.png").is_file()
