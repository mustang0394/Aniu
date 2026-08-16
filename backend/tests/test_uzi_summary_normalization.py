"""UZI 摘要归一化回归测试。"""

from __future__ import annotations

import json
from pathlib import Path
import sys

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.services.uzi_report_service import uzi_report_service


def test_summary_normalizes_gap_objects_and_missing_valuation(tmp_path) -> None:
    report_dir = tmp_path / "1"
    work_dir = report_dir / "work"
    artifacts_dir = report_dir / "artifacts"
    work_dir.mkdir(parents=True)
    artifacts_dir.mkdir()
    (work_dir / "stage1-manifest.json").write_text(
        json.dumps({"data_as_of": "2026-08-16T00:00:00"}),
        encoding="utf-8",
    )

    summary = uzi_report_service._build_summary(
        {
            "ticker": "600519.SH",
            "company_name": "贵州茅台",
            "overall_score": 70,
            "verdict_label": "中性",
            "institutional_modeling": {
                "target_price": "—",
                "upside_pct": None,
            },
            "data_gaps": {
                "coverage_pct": 88,
                "unresolved": 1,
                "tasks": [
                    {"dim": "4_peers", "field": "peers", "severity": "warning"}
                ],
            },
        },
        {"generated_at": "2026-08-16T00:00:00"},
        report_dir=report_dir,
    )

    assert summary["data_gaps"]["items"] == ["4_peers.peers；严重性：warning"]
    assert summary["valuation"]["target_price"] == 0.0
    assert summary["valuation"]["upside_pct"] == 0.0
    assert summary["data_as_of"] == "2026-08-16T00:00:00"


def test_stage2_artifact_check_requires_manifest_and_html(tmp_path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / "synthesis.json").write_text("{}", encoding="utf-8")
    assert not uzi_report_service._stage2_artifacts_complete_at(tmp_path)

    (artifacts / "artifact-manifest.json").write_text("{}", encoding="utf-8")
    (artifacts / "full-report-standalone.html").write_text("html", encoding="utf-8")
    assert uzi_report_service._stage2_artifacts_complete_at(tmp_path)
