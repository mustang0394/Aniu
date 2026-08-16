"""UZI 可用 web_fetch 的 SSRF 边界测试。"""

from __future__ import annotations

from pathlib import Path
import sys

sys.path.append(str(Path(__file__).resolve().parents[1]))

import skills.builtin_utils.handler as handler
from skills.builtin_utils.handler import Skill


class _FakeResponse:
    def __init__(self, status_code: int, *, location: str = "", text: str = "ok") -> None:
        self.status_code = status_code
        self.headers = {"content-type": "text/plain"}
        if location:
            self.headers["location"] = location
        self.text = text

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _FakeClient:
    responses: list[_FakeResponse] = []
    urls: list[str] = []

    def __init__(self, **_kwargs) -> None:
        pass

    def __enter__(self):
        type(self).urls = []
        return self

    def __exit__(self, *_exc) -> None:
        return None

    def get(self, url: str, **_kwargs):
        type(self).urls.append(url)
        return type(self).responses.pop(0)


def test_web_fetch_revalidates_redirect_target(monkeypatch) -> None:
    _FakeClient.responses = [
        _FakeResponse(302, location="http://127.0.0.1:8000/internal"),
    ]
    monkeypatch.setattr(handler.httpx, "Client", _FakeClient)

    def validate(url: str) -> str | None:
        return "blocked private target" if "127.0.0.1" in url else None

    monkeypatch.setattr(handler, "_validate_remote_url", validate)
    result = Skill().do_web_fetch(
        arguments={"url": "https://public.example/report"},
        context={},
    )

    assert result["ok"] is False
    assert "blocked private target" in result["error"]
    assert _FakeClient.urls == ["https://public.example/report"]


def test_web_fetch_follows_bounded_public_redirects(monkeypatch) -> None:
    _FakeClient.responses = [
        _FakeResponse(302, location="/next"),
        _FakeResponse(200, text="public content"),
    ]
    monkeypatch.setattr(handler.httpx, "Client", _FakeClient)
    monkeypatch.setattr(handler, "_validate_remote_url", lambda _url: None)

    result = Skill().do_web_fetch(
        arguments={"url": "https://public.example/report"},
        context={},
    )

    assert result["ok"] is True
    assert result["result"]["url"] == "https://public.example/next"
    assert result["result"]["content"].endswith("public content")
