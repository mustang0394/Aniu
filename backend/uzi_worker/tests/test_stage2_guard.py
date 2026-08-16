"""Stage 2 守卫与产物校验测试（§20.2 / §12.4）。"""
from __future__ import annotations

import json
import time
from pathlib import Path

from app.runner import validate_and_finalize_artifacts


def _poll_until(predicate, timeout: float = 10.0, interval: float = 0.2) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def _run_stage1(client, report_id: str, headers) -> None:
    response = client.post(
        f"/internal/jobs/{report_id}/stage1",
        json={"ticker": "600519.SH", "report_rel_dir": report_id},
        headers=headers,
    )
    assert response.status_code == 202
    assert _poll_until(
        lambda: client.get(f"/internal/jobs/{report_id}", headers=headers).json()["job"][
            "status"
        ]
        == "succeeded"
    )


def test_stage2_rejects_missing_agent_analysis(worker_client, auth_headers):
    _run_stage1(worker_client, "701", auth_headers)
    response = worker_client.post(
        "/internal/jobs/701/stage2",
        json={"ticker": "600519.SH"},
        headers=auth_headers,
    )
    assert response.status_code == 409
    assert "agent_analysis" in response.json()["detail"]


def test_stage2_rejects_agent_reviewed_false(worker_client, worker_env, auth_headers):
    _run_stage1(worker_client, "702", auth_headers)
    work_dir = worker_env["report_root"] / "702" / "work"
    agent_path = work_dir / "agent_analysis.json"
    agent_path.write_text(
        json.dumps({"agent_reviewed": False, "commentary": "空壳"}), encoding="utf-8"
    )
    response = worker_client.post(
        "/internal/jobs/702/stage2",
        json={"ticker": "600519.SH"},
        headers=auth_headers,
    )
    assert response.status_code == 409
    assert "agent_reviewed" in response.json()["detail"]


def test_stage2_rejects_ticker_mismatch(worker_client, worker_env, auth_headers):
    _run_stage1(worker_client, "703", auth_headers)
    work_dir = worker_env["report_root"] / "703" / "work"
    agent_path = work_dir / "agent_analysis.json"
    agent_path.write_text(
        json.dumps({"agent_reviewed": True, "commentary": "合法评审"}), encoding="utf-8"
    )
    response = worker_client.post(
        "/internal/jobs/703/stage2",
        json={"ticker": "000001.SZ"},
        headers=auth_headers,
    )
    assert response.status_code == 409
    assert "不一致" in response.json()["detail"]


def test_stage2_full_flow_produces_artifacts(worker_client, worker_env, auth_headers):
    """Stage 1 → agent 评审 → Stage 2 → artifacts 完整流转（mock）。"""
    report_root = worker_env["report_root"]
    _run_stage1(worker_client, "704", auth_headers)

    agent_path = report_root / "704" / "work" / "agent_analysis.json"
    agent_path.write_text(
        json.dumps(
            {
                "agent_reviewed": True,
                "dim_commentary": {"value": "估值合理"},
                "panel_insights": [],
                "great_divide_override": None,
                "narrative_override": None,
                "qualitative_deep_dive": {"macro": "政策面平稳"},
                "data_gap_acknowledged": True,
            }
        ),
        encoding="utf-8",
    )

    response = worker_client.post(
        "/internal/jobs/704/stage2",
        json={"ticker": "600519.SH"},
        headers=auth_headers,
    )
    assert response.status_code == 202

    assert _poll_until(
        lambda: worker_client.get("/internal/jobs/704", headers=auth_headers).json()["job"][
            "status"
        ]
        == "succeeded"
    )
    job = worker_client.get("/internal/jobs/704", headers=auth_headers).json()["job"]
    assert job["phase"] == "completed"
    assert job["progress"] == 100

    artifacts_dir = report_root / "704" / "artifacts"
    assert artifacts_dir.is_dir()
    assert (artifacts_dir / "full-report-standalone.html").stat().st_size > 10 * 1024
    assert (artifacts_dir / "synthesis.json").is_file()
    assert (artifacts_dir / "share-card.png").stat().st_size > 0
    assert (artifacts_dir / "war-report.png").stat().st_size > 0
    assert (artifacts_dir / "artifact-manifest.json").is_file()

    manifest = json.loads(
        (artifacts_dir / "artifact-manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["overall_score"] == 78.5
    assert manifest["verdict_label"]
    assert all(item["sha256"] for item in manifest["artifacts"])
    html_files = [item for item in manifest["artifacts"] if item["file"].endswith(".html")]
    assert html_files and html_files[0]["file"] == "full-report-standalone.html"

    # 成功后应清理源码副本与缓存（§12.2），保留核心 JSON。
    assert not (report_root / "704" / "work" / "uzi").exists()


def test_artifacts_tmp_atomic_move(tmp_path):
    """产物校验 + 原子移动（§12.4）：目录越界与非法文件被拒绝。"""
    report_dir = tmp_path / "r"
    tmp_dir = report_dir / "artifacts.tmp"
    tmp_dir.mkdir(parents=True)
    work_dir = report_dir / "work"
    work_dir.mkdir(parents=True)

    # HTML 太小 → 失败。
    (tmp_dir / "full-report-standalone.html").write_text("<html>tiny</html>", encoding="utf-8")
    ok, code, message = validate_and_finalize_artifacts(report_dir)
    assert not ok
    assert code == "UZI_ARTIFACT_INVALID"
    assert "10KB" in message

    # 补全合法产物。
    (tmp_dir / "full-report-standalone.html").write_text(
        "<html>" + "x" * (12 * 1024) + "</html>", encoding="utf-8"
    )
    (tmp_dir / "synthesis.json").write_text(
        json.dumps({"overall_score": 70.0, "verdict_label": "中性", "ticker": "600519.SH"}),
        encoding="utf-8",
    )
    (work_dir / "agent_analysis.json").write_text(
        json.dumps({"agent_reviewed": True}), encoding="utf-8"
    )
    ok, code, message = validate_and_finalize_artifacts(report_dir)
    assert ok
    assert (report_dir / "artifacts" / "artifact-manifest.json").is_file()
    assert not (report_dir / "artifacts.tmp").exists()

    # agent_reviewed=false 时拒绝（§20.2）。
    work_dir.mkdir(parents=True, exist_ok=True)
    (work_dir / "agent_analysis.json").write_text(
        json.dumps({"agent_reviewed": False}), encoding="utf-8"
    )
    (report_dir / "artifacts").rename(tmp_dir)
    ok, code, _message = validate_and_finalize_artifacts(report_dir)
    assert not ok
    assert code == "UZI_ARTIFACT_INVALID"


def test_artifacts_manifest_path_escape_rejected(tmp_path):
    report_dir = tmp_path / "r"
    tmp_dir = report_dir / "artifacts.tmp"
    tmp_dir.mkdir(parents=True)
    work_dir = report_dir / "work"
    work_dir.mkdir(parents=True)

    # 用符号链接制造越界产物文件。
    outside = tmp_path / "outside.html"
    outside.write_text("o" * (12 * 1024), encoding="utf-8")
    (tmp_dir / "full-report-standalone.html").symlink_to(outside)

    ok, code, message = validate_and_finalize_artifacts(report_dir)
    assert not ok
    assert code == "UZI_ARTIFACT_INVALID"