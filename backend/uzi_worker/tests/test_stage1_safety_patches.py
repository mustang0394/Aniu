"""Stage 1 数据缺失防御补丁测试。

背景（上游 commit b004d7a，已核对并复现）：
  上游在“数据缺失”时会让 Stage 1 整体崩溃：
    TypeError: '>' not supported between instances of 'NoneType' and 'int'
  根因：
  1. lib/fin_models.compute_dcf 数据不足时返回 {"intrinsic_per_share": None,
     "safety_margin_pct": None, ...}，而 research_workflow 直接
     dcf_result.get("intrinsic_per_share", 0) > 0 —— 键存在但值为 None 时
     .get 不返回默认值，None > 0 抛 TypeError。
  2. lib/stock_features.extract_features 在护城河数据缺失时把 moat_total
     置为 None，规则层 f.get("moat_total", 0) >= 24 同样崩溃。

本测试不依赖真实 UZI 源码，用伪造模块验证补丁行为（文档 §19.2 / §20.2）。
"""
from __future__ import annotations

import sys
import types

import pytest

from app.uzi_adapter import _install_stage1_safety_patches


def _make_fake_upstream():
    """构造伪造的上游 lib.fin_models / lib.stock_features / run_real_test。"""
    lib = types.ModuleType("lib")
    fin_models = types.ModuleType("lib.fin_models")
    stock_features = types.ModuleType("lib.stock_features")
    playwright_fallback = types.ModuleType("lib.playwright_fallback")
    run_real_test = types.ModuleType("run_real_test")

    def fake_compute_dcf(features, assumptions=None):
        # 复刻上游“数据不足”分支：键存在但值为 None。
        return {
            "method": "DCF (2-stage + Gordon Growth terminal)",
            "verdict": "⛔ 数据不足 · 无法 DCF",
            "intrinsic_per_share": None,
            "safety_margin_pct": None,
            "error": "FCF / 营收 / 净利率均缺失",
        }

    def fake_extract_features(raw, dims):
        # 复刻上游“护城河数据缺失”：moat_total 为 None。
        return {"name": "测试公司", "moat_total": None, "moat_known": False}

    # 复刻上游 fetch_url：记录收到的 timeout 供断言。
    calls: list[tuple] = []

    def fake_fetch_url(url, wait_for=None, timeout=15):
        calls.append((url, wait_for, timeout))
        return "<html>ok</html>"

    fin_models.compute_dcf = fake_compute_dcf
    stock_features.extract_features = fake_extract_features
    playwright_fallback.fetch_url = fake_fetch_url
    # run_real_test 在模块级 from lib.stock_features import extract_features，
    # 形成独立绑定 —— 补丁必须同时覆盖这类绑定。
    run_real_test.extract_features = fake_extract_features

    return {
        "lib": lib,
        "lib.fin_models": fin_models,
        "lib.stock_features": stock_features,
        "lib.playwright_fallback": playwright_fallback,
        "run_real_test": run_real_test,
        "_fetch_calls": calls,
    }


@pytest.fixture()
def fake_upstream():
    mods = _make_fake_upstream()
    for name, mod in mods.items():
        sys.modules[name] = mod
    yield mods
    for name in mods:
        sys.modules.pop(name, None)


def test_dcf_none_keys_removed_so_defaults_apply(fake_upstream):
    """数据不足的 DCF 结果不再携带 None 键，下游 .get(key, 0) 默认值生效。"""
    restores = _install_stage1_safety_patches()
    try:
        fin_models = fake_upstream["lib.fin_models"]
        dcf = fin_models.compute_dcf({})

        # None 键被移除：.get 无默认值 → None，有默认值 → 0（不抛 TypeError）。
        assert dcf.get("intrinsic_per_share") is None
        assert dcf.get("intrinsic_per_share", 0) == 0
        assert dcf.get("safety_margin_pct", 0) == 0
        # 语义保持：verdict 仍是“数据不足”。
        assert dcf.get("verdict") == "⛔ 数据不足 · 无法 DCF"

        # 复刻 research_workflow.py:57 的崩溃比较 —— 补丁后不抛。
        dcf_result = dcf
        assert not (dcf_result.get("intrinsic_per_share", 0) > 0)
    finally:
        for restore in reversed(restores):
            restore()


def test_moat_total_none_normalized_to_zero(fake_upstream):
    """moat_total 为 None 时置 0，规则层 >= 比较判负而非崩溃。"""
    restores = _install_stage1_safety_patches()
    try:
        stock_features = fake_upstream["lib.stock_features"]
        features = stock_features.extract_features({}, {})

        assert features["moat_total"] == 0
        assert features["moat_known"] is False  # “无数据”语义不变

        # 复刻 research_workflow.py:656 / investor_criteria 的比较 —— 不抛。
        assert not (features.get("moat_total", 0) >= 28)
        assert not (features.get("moat_total", 0) >= 24)
    finally:
        for restore in reversed(restores):
            restore()


def test_module_level_bindings_also_patched(fake_upstream):
    """run_real_test / score_fns 等模块级 import 绑定同样被覆盖。"""
    restores = _install_stage1_safety_patches()
    try:
        run_real_test = fake_upstream["run_real_test"]
        # run_real_test 模块属性已被替换为安全包装。
        features = run_real_test.extract_features({}, {})
        assert features["moat_total"] == 0
    finally:
        for restore in reversed(restores):
            restore()


def test_patches_restored_after_run(fake_upstream):
    """Stage 1 结束后恢复原函数，不污染后续任务。"""
    fin_models = fake_upstream["lib.fin_models"]
    stock_features = fake_upstream["lib.stock_features"]
    playwright_fallback = fake_upstream["lib.playwright_fallback"]
    original_dcf = fin_models.compute_dcf
    original_extract = stock_features.extract_features
    original_fetch = playwright_fallback.fetch_url

    restores = _install_stage1_safety_patches()
    for restore in reversed(restores):
        restore()

    assert fin_models.compute_dcf is original_dcf
    assert stock_features.extract_features is original_extract
    assert playwright_fallback.fetch_url is original_fetch
    # 恢复后回到上游原行为（None 键仍在 → 比较仍会抛，与上游一致）。
    dcf = fin_models.compute_dcf({})
    assert dcf.get("intrinsic_per_share") is None
    features = stock_features.extract_features({}, {})
    assert features["moat_total"] is None


def test_playwright_timeout_default_unchanged(fake_upstream, monkeypatch):
    """未设 UZI_PLAYWRIGHT_TIMEOUT 时，15s 行为与上游完全一致。"""
    monkeypatch.delenv("UZI_PLAYWRIGHT_TIMEOUT", raising=False)
    restores = _install_stage1_safety_patches()
    try:
        playwright_fallback = fake_upstream["lib.playwright_fallback"]
        playwright_fallback.fetch_url("https://example.com", timeout=15)
        assert fake_upstream["_fetch_calls"][-1][2] == 15
    finally:
        for restore in reversed(restores):
            restore()


def test_playwright_timeout_env_override(fake_upstream, monkeypatch):
    """设了 UZI_PLAYWRIGHT_TIMEOUT 后，硬编码 15s 提升为该值。"""
    monkeypatch.setenv("UZI_PLAYWRIGHT_TIMEOUT", "45")
    restores = _install_stage1_safety_patches()
    try:
        playwright_fallback = fake_upstream["lib.playwright_fallback"]
        playwright_fallback.fetch_url("https://example.com", timeout=15)
        assert fake_upstream["_fetch_calls"][-1][2] == 45
        # 调用方显式传非 15 的值 → 保持原值，不被覆盖。
        playwright_fallback.fetch_url("https://example.com", timeout=30)
        assert fake_upstream["_fetch_calls"][-1][2] == 30
    finally:
        for restore in reversed(restores):
            restore()


def test_playwright_timeout_invalid_env_ignored(fake_upstream, monkeypatch):
    """非数字的 UZI_PLAYWRIGHT_TIMEOUT 被忽略，保持 15s。"""
    monkeypatch.setenv("UZI_PLAYWRIGHT_TIMEOUT", "abc")
    restores = _install_stage1_safety_patches()
    try:
        playwright_fallback = fake_upstream["lib.playwright_fallback"]
        playwright_fallback.fetch_url("https://example.com", timeout=15)
        assert fake_upstream["_fetch_calls"][-1][2] == 15
    finally:
        for restore in reversed(restores):
            restore()
