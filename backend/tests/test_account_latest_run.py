"""账户列表嵌入「最近运行」摘要测试（多账户方案 §7 扩展）。

覆盖：
- 多账户多运行下，每账户返回最新一条
- 无运行账户 latest_run 为 None
- 归档账户按 include_archived 开关可选包含
- GET /accounts 响应体含 latest_run 字段
- 不触发远程妙想调用（纯 DB）
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from tests.helpers import (
    create_account,
    create_run,
    reset_test_database,
    session_scope,
    teardown_test_database,
)


def _make_client(monkeypatch, tmp_path, db_name="latest_run.db") -> TestClient:
    from app.core.config import get_settings
    from app.core.rate_limit import _limiter
    from app.main import create_app
    from app.services.scheduler_service import scheduler_service

    monkeypatch.setenv("APP_LOGIN_PASSWORD", "release-pass")
    reset_test_database(monkeypatch, tmp_path, db_name)
    monkeypatch.setattr(scheduler_service, "start", lambda: None)
    monkeypatch.setattr(scheduler_service, "stop", lambda: None)
    _limiter._buckets.clear()
    return TestClient(create_app())


def _auth_headers(client: TestClient) -> dict[str, str]:
    response = client.post("/api/aniu/login", json={"password": "release-pass"})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['token']}"}


def test_list_accounts_with_latest_run_picks_newest(monkeypatch, tmp_path) -> None:
    from app.services.account_service import account_service

    reset_test_database(monkeypatch, tmp_path)
    base = datetime(2024, 1, 1, tzinfo=timezone.utc)
    with session_scope() as db:
        account_a = create_account(db, slug="acct-a", name="A账户")
        account_b = create_account(db, slug="acct-b", name="B账户")
        # A 两次运行，最新一条为交易且失败
        create_run(
            db,
            account_a.id,
            run_type="analysis",
            status="completed",
            started_at=base,
        )
        create_run(
            db,
            account_a.id,
            run_type="trade",
            status="failed",
            error_message="boom",
            started_at=base + timedelta(hours=2),
        )
        # B 无运行
        db.commit()
        a_id, b_id = account_a.id, account_b.id

    with session_scope() as db:
        reads = account_service.list_accounts_with_latest_run(db)
    by_id = {r.id: r for r in reads}
    assert a_id in by_id and b_id in by_id

    latest_a = by_id[a_id].latest_run
    assert latest_a is not None
    assert latest_a.run_type == "trade"
    assert latest_a.status == "failed"
    assert latest_a.error_message == "boom"
    assert latest_a.started_at == (base + timedelta(hours=2)).replace(tzinfo=None)

    # 无运行账户不返回 latest_run
    assert by_id[b_id].latest_run is None
    teardown_test_database()


def test_list_latest_runs_empty_for_unknown_ids(monkeypatch, tmp_path) -> None:
    from app.services.account_service import account_service

    reset_test_database(monkeypatch, tmp_path)
    with session_scope() as db:
        assert account_service.list_latest_runs(db, []) == {}
        assert account_service.list_latest_runs(db, [999_999]) == {}
    teardown_test_database()


def test_list_accounts_with_latest_run_includes_archived(monkeypatch, tmp_path) -> None:
    from app.services.account_service import account_service

    reset_test_database(monkeypatch, tmp_path)
    base = datetime(2024, 2, 1, tzinfo=timezone.utc)
    with session_scope() as db:
        archived = create_account(
            db,
            slug="archived-acct",
            name="归档账户",
            archived=True,
        )
        create_run(
            db,
            archived.id,
            run_type="analysis",
            status="completed",
            started_at=base,
        )
        db.commit()
        archived_id = archived.id

    with session_scope() as db:
        # 默认不含归档
        active = account_service.list_accounts_with_latest_run(db, include_archived=False)
        assert all(r.id != archived_id for r in active)
        # 包含归档
        with_archived = account_service.list_accounts_with_latest_run(
            db, include_archived=True
        )
        by_id = {r.id: r for r in with_archived}
        assert archived_id in by_id
        assert by_id[archived_id].latest_run is not None
        assert by_id[archived_id].latest_run.run_type == "analysis"
    teardown_test_database()


def test_accounts_api_returns_latest_run_field(monkeypatch, tmp_path) -> None:
    client = _make_client(monkeypatch, tmp_path)
    headers = _auth_headers(client)
    base = datetime(2024, 3, 1, tzinfo=timezone.utc)
    with session_scope() as db:
        account_a = create_account(db, slug="acct-a", name="A账户")
        create_run(
            db,
            account_a.id,
            run_type="analysis",
            status="completed",
            started_at=base,
        )
        db.commit()
        a_id = account_a.id

    response = client.get("/api/aniu/accounts", headers=headers)
    assert response.status_code == 200
    body = response.json()
    by_id = {item["id"]: item for item in body}
    # 有运行的账户含 latest_run
    assert by_id[a_id]["latest_run"] is not None
    assert by_id[a_id]["latest_run"]["run_type"] == "analysis"
    # default 账户（init_db 创建）无运行时 latest_run 为 None
    default_item = next((i for i in body if i["slug"] == "default"), None)
    assert default_item is not None
    assert default_item["latest_run"] is None
    teardown_test_database()
