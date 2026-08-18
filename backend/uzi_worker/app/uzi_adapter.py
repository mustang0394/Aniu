"""受控 UZI 适配器：加载上游 run_real_test 并调用 stage1/stage2。

约束（文档 §12.3）：

- 通过受控 Python 导入调用，禁止 ``shell=True``。
- 禁止把用户输入拼入命令行字符串。
- 运行前把进程当前目录切换到上游 scripts 目录（``skills/deep-analysis/scripts``），
  确保 ``.cache`` / ``reports`` 都落在任务源码副本内（目录隔离）。
- 外部数据（网页等）视为不可信数据；这里只做本地调用，不执行外部指令。

上游协议（commit 7bc779dd，已核对源码）：

- 入口：``skills/deep-analysis/scripts/run_real_test.py``，模块导出 ``stage1(ticker)``
  与 ``stage2(ticker)``。
- Stage 1 数据写到 ``.cache/{normalized_ticker}/``（相对 scripts 目录）：``raw_data.json``、
  ``dimensions.json``、``panel.json``、``_data_gaps.json``（可选）。
- Stage 2 读取 ``.cache/{normalized_ticker}/agent_analysis.json``（AniU 评审写入），
  生成 ``reports/{ticker}_{YYYYMMDD}/full-report-standalone.html`` 等产物，并把
  synthesis 写到 ``.cache/{ticker}/synthesis.json``；``stage2`` 返回 standalone HTML
  路径字符串。

Mock 模式（``UZI_WORKER_MOCK=1``）：不加载真实 UZI 源码，而是写出符合
同一目录协议的假产物（``.cache/{ticker}/`` + ``reports/{ticker}_{date}/``），
用于容器烟雾测试与主服务联调（§20.7）。
"""
from __future__ import annotations

import base64
import importlib
import json
import logging
import os
import signal
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# 上游源码内 scripts 目录（相对 UZI 源码根）。
# 源码副本位于 {report_dir}/work/uzi/，因此 scripts 目录为
# {report_dir}/work/uzi/skills/deep-analysis/scripts。
SCRIPTS_REL = Path("skills") / "deep-analysis" / "scripts"

# 上游 stage1 写出的缓存文件（相对 scripts 目录的 .cache/{ticker}/）。
STAGE1_CACHE_FILES = ("raw_data.json", "dimensions.json", "panel.json", "_data_gaps.json")

# Stage 2 产物文件名（§12.2 契约，与主服务 _ARTIFACT_KEY_FILES 一致）。
STAGE2_ARTIFACT_FILES = (
    "full-report-standalone.html",
    "report.meta.json",
    "one-liner.txt",
    "synthesis.json",
    "share-card.png",
    "war-report.png",
)

_ACTIVE_RENDER_LOCK = threading.Lock()
_ACTIVE_RENDER_PROCESS: subprocess.Popen | None = None
_ACTIVE_QUANT_LOCK = threading.Lock()
_ACTIVE_QUANT_PROCESS: subprocess.Popen | None = None

# 上游 stage2 生成的 HTML 文件名（standalone 独立版，已内联头像资源）。
UPSTREAM_HTML_NAME = "full-report-standalone.html"


class UziStageError(RuntimeError):
    """UZI 阶段执行的结构化失败（含稳定错误码）。"""

    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _normalize_mock_ticker(ticker: str) -> str:
    """Mock 模式下的股票代码归一化（仅用于联调，非真实解析）。"""
    text = ticker.strip()
    if "." in text:
        return text.upper()
    if len(text) == 6 and text.isdigit():
        return text + (".SH" if text.startswith(("5", "6", "9")) else ".SZ")
    return text


def _scripts_dir(report_dir: Path) -> Path:
    """任务源码副本内的上游 scripts 目录。"""
    return report_dir / "work" / "uzi" / SCRIPTS_REL


def _load_run_real_test(scripts_dir: Path) -> Any:
    """从上游 scripts 目录加载 ``run_real_test`` 模块；失败返回 None（不抛异常）。

    上游模块内部用普通 ``import`` 引用同目录兄弟模块（lib.*、assemble_report、
    inline_assets、render_share_card 等），因此必须把 scripts 目录加入 ``sys.path``。
    """
    scripts_dir = Path(scripts_dir)
    if not scripts_dir.is_dir():
        logger.warning("UZI scripts 目录不存在: %s", scripts_dir)
        return None
    scripts_path = str(scripts_dir.resolve())
    if scripts_path not in sys.path:
        sys.path.insert(0, scripts_path)
    try:
        # 每个任务使用独立源码副本：强制重新导入，避免跨任务/跨测试模块缓存串数据。
        sys.modules.pop("run_real_test", None)
        for module_name in list(sys.modules):
            if module_name == "lib" or module_name.startswith("lib."):
                sys.modules.pop(module_name, None)
        import run_real_test  # noqa: PLC0415 - 受控加载上游入口

        return run_real_test
    except Exception as exc:  # noqa: BLE001 - 依赖或语法问题统一按不可用处理
        logger.warning("导入 run_real_test 失败: %s", exc)
        return None


def _is_dict(data: Any) -> bool:
    return isinstance(data, dict)


# ── Stage 1 数据缺失防御（上游 None 比较崩溃补丁）──────────────
# 上游 commit b004d7a（本项目核对的最新版）在“数据缺失”时会让 Stage 1
# 整体崩溃，错误形如：
#   TypeError: '>' not supported between instances of 'NoneType' and 'int'
# 两个根因（均已用上游源码复现）：
#   1. lib/fin_models.compute_dcf 在 FCF/营收/净利率均缺失时返回
#      {"intrinsic_per_share": None, "safety_margin_pct": None, ...}，
#      而 lib/research_workflow.build_initiating_coverage 直接执行
#      dcf_result.get("intrinsic_per_share", 0) > 0 —— 键存在但值为 None 时
#      .get 不返回默认值，None > 0 抛 TypeError（Task 1.5 崩溃点）。
#   2. lib/stock_features.extract_features 在护城河数据缺失时把 moat_total
#      置为 None，而 research_workflow.run_idea_screen 与 investor_criteria
#      规则层直接 f.get("moat_total", 0) >= 24 比较，同样崩溃。
# 修复策略：不改上游源码，只在运行时对纯函数做安全包装：
#   - compute_dcf：数据不足结果中移除 None 键 → .get(key, 0) 生效，
#     verdict（"⛔ 数据不足 · 无法 DCF"）语义不变，Stage 2 渲染器按键缺失
#     显示“数据缺失”；
#   - extract_features：moat_total 为 None 时置 0 —— 规则层 0 >= 24 判负
#     而非崩溃；moat_known 仍为 False，“无数据”语义不受影响；
#   - lib.playwright_fallback.fetch_url：可选超时提升 —— 设了
#     UZI_PLAYWRIGHT_TIMEOUT（秒，5-300）时把上游硬编码 15s 的调用提升为
#     该值（stats.gov.cn 等慢站点 15s 经常超时导致兜底失败）；未设置则
#     行为与上游完全一致。


def _install_stage1_safety_patches() -> list[Any]:
    """安装 Stage 1 数据缺失防御补丁，返回恢复函数列表。

    必须在 ``_load_run_real_test`` 之后、``stage1_func`` 调用之前安装；
    Stage 1 结束后调用恢复函数（逆序）还原上游模块原貌。
    使用 `sys.modules` 遍历而非只 patch 固定模块，覆盖模块级
    ``from lib.xxx import ...`` 的既有绑定（run_real_test、
    lib.pipeline.score_fns 等）与未来新增的 import 点。
    """
    import importlib
    from typing import Callable

    def _load_module(name: str) -> Any | None:
        """加载上游模块；伪上游/协议测试无 lib 包时返回 None。"""
        try:
            return importlib.import_module(name)
        except ModuleNotFoundError:
            return None

    fin_models = _load_module("lib.fin_models")
    stock_features = _load_module("lib.stock_features")
    if fin_models is None and stock_features is None:
        return []

    restored: list[Callable[[], None]] = []

    def _patch_module_attr(
        module: Any, name: str, original: Any, wrapper: Any
    ) -> None:
        setattr(module, name, wrapper)
        restored.append(
            lambda m=module, n=name, o=original: setattr(m, n, o)
        )

    if fin_models is not None:
        original_dcf = getattr(fin_models, "compute_dcf", None)
        if callable(original_dcf):
            def safe_compute_dcf(
                features: dict, assumptions: dict | None = None
            ) -> dict:
                result = original_dcf(features, assumptions)
                if isinstance(result, dict):
                    for key in ("intrinsic_per_share", "safety_margin_pct"):
                        if result.get(key) is None:
                            result = {k: v for k, v in result.items() if k != key}
                return result

            _patch_module_attr(fin_models, "compute_dcf", original_dcf, safe_compute_dcf)
            for _mod_name, _mod in list(sys.modules.items()):
                if getattr(_mod, "compute_dcf", None) is original_dcf:
                    _patch_module_attr(_mod, "compute_dcf", original_dcf, safe_compute_dcf)

    if stock_features is not None:
        original_extract = getattr(stock_features, "extract_features", None)
        if callable(original_extract):
            def safe_extract_features(raw: dict, dims: dict) -> dict:
                features = original_extract(raw, dims)
                if isinstance(features, dict) and features.get("moat_total") is None:
                    features = dict(features)
                    features["moat_total"] = 0
                return features

            _patch_module_attr(
                stock_features, "extract_features", original_extract, safe_extract_features
            )
            for _mod_name, _mod in list(sys.modules.items()):
                if getattr(_mod, "extract_features", None) is original_extract:
                    _patch_module_attr(
                        _mod, "extract_features", original_extract, safe_extract_features
                    )

    # ── Playwright 兜底超时：UZI_PLAYWRIGHT_TIMEOUT 覆盖上游硬编码 15s ──
    # 上游 lib/playwright_fallback.py 有 10 处调用硬编码 timeout=15
    # （3_macro/4_peers/8_materials/15_events/17_sentiment/19_contests 等），
    # stats.gov.cn 等慢站点经常 15s 超时导致兜底失败。这里包装 fetch_url：
    # 设了 UZI_PLAYWRIGHT_TIMEOUT（秒，5-300）时，把调用方硬编码的 15s 提升为
    # 该值；未设置则行为与上游完全一致。
    playwright_fallback = _load_module("lib.playwright_fallback")
    if playwright_fallback is not None:
        original_fetch_url = getattr(playwright_fallback, "fetch_url", None)
        if callable(original_fetch_url):
            def _playwright_timeout_override() -> int | None:
                raw = os.environ.get("UZI_PLAYWRIGHT_TIMEOUT", "").strip()
                if not raw:
                    return None
                try:
                    return max(5, min(int(raw), 300))
                except ValueError:
                    return None

            def safe_fetch_url(
                url: str, wait_for: str | None = None, timeout: int = 15
            ) -> str | None:
                if timeout == 15:
                    override = _playwright_timeout_override()
                    if override is not None:
                        timeout = override
                return original_fetch_url(url, wait_for=wait_for, timeout=timeout)

            _patch_module_attr(
                playwright_fallback, "fetch_url", original_fetch_url, safe_fetch_url
            )
            for _mod_name, _mod in list(sys.modules.items()):
                if getattr(_mod, "fetch_url", None) is original_fetch_url:
                    _patch_module_attr(
                        _mod, "fetch_url", original_fetch_url, safe_fetch_url
                    )

    return restored


@dataclass
class StageResult:
    success: bool
    error_code: str | None = None
    error_message: str | None = None
    manifest: dict[str, Any] | None = None


def run_stage1(
    *,
    report_dir: Path,
    ticker: str,
    source_root: Path,
    mock: bool,
    mx_api_key: str | None = None,
) -> StageResult:
    """执行 Stage 1：采集与机械评分。

    上游把数据写到 ``.cache/{normalized_ticker}/``；本函数随后把核心 JSON
    复制到 ``work/``（供主服务 LLM 编排读取），并返回 stage1-manifest 数据。
    """
    work_dir = report_dir / "work"
    work_dir.mkdir(parents=True, exist_ok=True)

    if mock:
        return _mock_stage1(report_dir=report_dir, work_dir=work_dir, ticker=ticker)

    scripts_dir = _scripts_dir(report_dir)
    module = _load_run_real_test(scripts_dir)
    if module is None:
        raise UziStageError(
            "UZI_SOURCE_MISSING",
            "UZI 源码未安装或 scripts 目录缺失（UZI_SOURCE_ROOT 指向完整上游仓库）。",
        )
    stage1_func = getattr(module, "stage1", None)
    if not callable(stage1_func):
        raise UziStageError(
            "UZI_STAGE1_FAILED",
            "run_real_test 中未找到 stage1 入口函数。",
        )

    # 切换 cwd 到 scripts 目录，确保 .cache / reports 落在任务源码副本内（§12.3）。
    cwd_before = os.getcwd()
    env_patch(mx_api_key=mx_api_key)
    safety_restores: list[Any] = []
    try:
        # 数据缺失防御：上游在 DCF/护城河数据缺失时的 None 比较会抛
        # TypeError 让整个 Stage 1 失败；在调用 stage1 前安装安全包装。
        safety_restores = _install_stage1_safety_patches()
        os.chdir(scripts_dir)
        output = stage1_func(ticker)
    except UziStageError:
        raise
    except Exception as exc:  # noqa: BLE001 - 转为结构化错误
        raise UziStageError(
            "UZI_STAGE1_FAILED",
            f"Stage 1 执行失败：{_safe_exc_text(exc)}",
        ) from exc
    finally:
        for _restore in reversed(safety_restores):
            try:
                _restore()
            except Exception:  # noqa: BLE001 - 恢复失败只记录，不影响结果
                logger.warning("恢复 UZI stage1 安全补丁失败", exc_info=True)
        os.chdir(cwd_before)
        unregister_mx_secret(mx_api_key)

    if _is_dict(output):
        # 上游 stage1 早退（中文名无法解析 / 非个股标的），禁止继续生成空报告（§5.2）。
        status = str(output.get("status") or "").strip()
        if status == "name_not_resolved":
            raise UziStageError(
                "UZI_UNRESOLVED_TICKER",
                f"无法解析股票代码/名称：{ticker}。",
            )
        if status == "non_stock_security":
            label = str(output.get("label") or output.get("security_type") or "非个股标的")
            raise UziStageError(
                "UZI_NON_STOCK_SECURITY",
                f"{ticker} 是{label}，已停止生成空报告。",
            )

    resolved_ticker = _extract_string(output, "ticker") or _normalize_mock_ticker(ticker)

    cache_dir = scripts_dir / ".cache" / resolved_ticker
    collected: list[str] = []
    for name in STAGE1_CACHE_FILES:
        src = cache_dir / name
        if src.is_file():
            shutil.copy2(src, work_dir / name)
            collected.append(name)

    manifest = _build_stage1_manifest(
        work_dir=work_dir,
        ticker_input=ticker,
        ticker_normalized=resolved_ticker,
        files=collected,
    )
    return StageResult(success=True, manifest=manifest)


def run_stage2(
    *,
    report_dir: Path,
    normalized_ticker: str,
    source_root: Path,
    mock: bool,
    render_timeout_seconds: int = 90,
    quant_max_funds: int = 12,
    quant_timeout_seconds: int = 45,
) -> StageResult:
    """执行 Stage 2：综合与报告渲染。

    上游从 ``.cache/{ticker}/agent_analysis.json`` 读取 AniU 评审结果，
    产物写到 ``reports/{ticker}_{date}/``；本函数随后把产物收集到
    ``artifacts.tmp/``（由 runner 原子移动到 artifacts/，§12.4）。
    """
    tmp_dir = report_dir / "artifacts.tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    work_dir = report_dir / "work"

    if mock:
        return _mock_stage2(report_dir=report_dir, tmp_dir=tmp_dir,
                            work_dir=work_dir, ticker=normalized_ticker)

    scripts_dir = _scripts_dir(report_dir)
    module = _load_run_real_test(scripts_dir)
    if module is None:
        raise UziStageError(
            "UZI_SOURCE_MISSING",
            "UZI 源码未安装或 scripts 目录缺失（UZI_SOURCE_ROOT 指向完整上游仓库）。",
        )
    stage2_func = getattr(module, "stage2", None)
    if not callable(stage2_func):
        raise UziStageError(
            "UZI_STAGE2_FAILED",
            "run_real_test 中未找到 stage2 入口函数。",
        )

    cache_dir = scripts_dir / ".cache" / normalized_ticker

    # AniU 评审结果：work/agent_analysis.json → 上游读取位置 .cache/{ticker}/
    agent_src = work_dir / "agent_analysis.json"
    if agent_src.is_file():
        cache_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(agent_src, cache_dir / "agent_analysis.json")

    cwd_before = os.getcwd()
    restore_renderers = None
    restore_quant_signal = None
    try:
        os.chdir(scripts_dir)
        restore_quant_signal = _install_bounded_quant_signal(
            scripts_dir=scripts_dir,
            max_funds=max(1, int(quant_max_funds)),
            timeout_seconds=max(1, int(quant_timeout_seconds)),
        )
        restore_renderers = _install_bounded_renderers(
            scripts_dir=scripts_dir,
            timeout_seconds=max(1, int(render_timeout_seconds)),
        )
        output = stage2_func(normalized_ticker)
    except UziStageError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise UziStageError(
            "UZI_STAGE2_FAILED",
            f"Stage 2 执行失败：{_safe_exc_text(exc)}",
        ) from exc
    finally:
        if restore_renderers is not None:
            restore_renderers()
        if restore_quant_signal is not None:
            restore_quant_signal()
        os.chdir(cwd_before)

    return _collect_stage2_output(
        tmp_dir=tmp_dir,
        scripts_dir=scripts_dir,
        raw_output=output,
        ticker=normalized_ticker,
    )


def _empty_quant_signal(reason: str = "") -> dict[str, Any]:
    """返回上游 ``detect_quant_signal`` 可接受的降级结果。"""
    result: dict[str, Any] = {
        "count": 0,
        "quant_funds": [],
        "active_funds_total": 0,
        "quant_funds_total": 0,
        "is_quant_factor_style": False,
    }
    if reason:
        result["fallback_reason"] = reason[:200]
    return result


def _install_bounded_quant_signal(
    *, scripts_dir: Path, max_funds: int, timeout_seconds: int
):
    """限制上游基金样本，并让可选的量化风格识别可以独立超时降级。

    上游会在 Stage 2 的 ``detect_style`` 内同步调用 AkShare 查询基金持仓。
    这些查询没有单请求超时，且传入基金列表时不会应用 ``max_funds``。
    这里同时截断输入列表，并在隔离进程中执行整个识别步骤。失败只返回
    “未识别为量化风格”，不阻断后续综合、HTML 和图片渲染。
    """
    quant_path = scripts_dir / "lib" / "quant_signal.py"
    if not quant_path.is_file():
        return lambda: None

    try:
        quant_module = importlib.import_module("lib.quant_signal")
    except Exception as exc:  # noqa: BLE001 - 上游可选模块不可用时直接降级
        logger.warning("UZI 量化风格模块不可用，已跳过: %s", exc)
        return lambda: None

    original = getattr(quant_module, "detect_quant_signal", None)
    if not callable(original):
        return lambda: None

    configured_limit = max(1, int(max_funds))

    def bounded_detect(
        stock_code: str,
        fund_managers: Any = None,
        max_funds: int = 80,
        *args: Any,
        **kwargs: Any,
    ) -> dict[str, Any]:
        del args, kwargs
        try:
            requested_limit = max(1, int(max_funds))
        except (TypeError, ValueError):
            requested_limit = configured_limit
        effective_limit = min(configured_limit, requested_limit)
        managers = fund_managers
        if isinstance(managers, (list, tuple)):
            managers = list(managers)[:effective_limit]
        print(
            f"\n🔎 UZI 基金风格识别：最多 {effective_limit} 只基金，"
            f"超时 {timeout_seconds} 秒后自动跳过。",
            flush=True,
        )
        progress_marker = (
            scripts_dir / ".cache" / str(stock_code or "") / ".aniu-quant-running.json"
        )
        try:
            progress_marker.parent.mkdir(parents=True, exist_ok=True)
            progress_marker.write_text(
                json.dumps(
                    {"max_funds": effective_limit, "timeout_seconds": timeout_seconds}
                ),
                encoding="utf-8",
            )
        except OSError:
            pass
        try:
            result = _run_bounded_quant_signal(
                scripts_dir=scripts_dir,
                stock_code=str(stock_code or ""),
                fund_managers=managers,
                max_funds=effective_limit,
                timeout_seconds=timeout_seconds,
            )
            print("✓ 基金风格识别完成，继续综合报告。", flush=True)
            return result
        except Exception as exc:  # noqa: BLE001 - 可选增强失败必须继续主报告
            reason = _safe_exc_text(exc)
            logger.warning("UZI 基金风格识别已降级: %s", reason)
            print(f"⚠️ 基金风格识别已跳过：{reason}；继续综合报告。", flush=True)
            return _empty_quant_signal(reason)
        finally:
            try:
                progress_marker.unlink(missing_ok=True)
            except OSError:
                pass

    quant_module.detect_quant_signal = bounded_detect

    def restore() -> None:
        quant_module.detect_quant_signal = original

    return restore


def _run_bounded_quant_signal(
    *,
    scripts_dir: Path,
    stock_code: str,
    fund_managers: Any,
    max_funds: int,
    timeout_seconds: int,
) -> dict[str, Any]:
    """在独立进程组执行上游量化基金识别，并返回 JSON 结果。"""
    marker = "__ANIU_UZI_QUANT_RESULT__"
    code = (
        "import json,sys; "
        "from lib.quant_signal import detect_quant_signal; "
        "p=json.loads(sys.stdin.read()); "
        "r=detect_quant_signal(p['stock_code'], p.get('fund_managers'), "
        "max_funds=int(p['max_funds'])); "
        f"print('{marker}'+json.dumps(r, ensure_ascii=False))"
    )
    payload = json.dumps(
        {
            "stock_code": stock_code,
            "fund_managers": fund_managers,
            "max_funds": max(1, int(max_funds)),
        },
        ensure_ascii=False,
        default=str,
    )
    env = dict(os.environ)
    # 上游默认 1 是为规避 AkShare/mini-racer 的线程安全问题；保持串行。
    env["UZI_QUANT_WORKERS"] = "1"
    proc = subprocess.Popen(  # noqa: S603 - 固定解释器与代码，不经过 shell
        [sys.executable, "-c", code],
        cwd=str(scripts_dir),
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=(os.name == "posix"),
    )
    global _ACTIVE_QUANT_PROCESS
    with _ACTIVE_QUANT_LOCK:
        _ACTIVE_QUANT_PROCESS = proc
    try:
        try:
            output, _ = proc.communicate(input=payload, timeout=timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            _terminate_helper_process(proc)
            raise TimeoutError(
                f"基金风格识别超过 {timeout_seconds} 秒"
            ) from exc
    finally:
        with _ACTIVE_QUANT_LOCK:
            if _ACTIVE_QUANT_PROCESS is proc:
                _ACTIVE_QUANT_PROCESS = None

    if proc.returncode != 0:
        tail = str(output or "").strip().splitlines()[-1:] or [""]
        raise RuntimeError(f"基金风格识别进程失败：{tail[0][:160]}")
    result_line = next(
        (line for line in reversed(str(output or "").splitlines()) if line.startswith(marker)),
        "",
    )
    if not result_line:
        raise RuntimeError("基金风格识别未返回结果")
    try:
        result = json.loads(result_line[len(marker):])
    except json.JSONDecodeError as exc:
        raise RuntimeError("基金风格识别返回了非法 JSON") from exc
    if not isinstance(result, dict):
        raise RuntimeError("基金风格识别结果不是对象")
    return result


def _install_bounded_renderers(*, scripts_dir: Path, timeout_seconds: int):
    """把上游两次 Playwright 截图改为有硬超时的隔离子进程。

    上游 ``stage2`` 会捕获图片渲染异常，因此单张图片超时不会丢掉已经
    生成的 HTML 和 synthesis。独立进程组也允许精确回收 Chromium 后代进程。
    """
    if not (scripts_dir / "render_share_card.py").is_file():
        return lambda: None

    share_module = importlib.import_module("render_share_card")
    original_main = getattr(share_module, "main", None)
    original_render = getattr(share_module, "render", None)
    previous_war_module = sys.modules.pop("render_war_report", None)

    def bounded_render(
        ticker: str,
        selector: str = "#share-card",
        out_name: str = "share-card.png",
        scale: int = 2,
    ) -> Path:
        return _run_bounded_renderer(
            scripts_dir=scripts_dir,
            ticker=ticker,
            selector=selector,
            out_name=out_name,
            scale=scale,
            timeout_seconds=timeout_seconds,
        )

    share_module.main = bounded_render
    share_module.render = bounded_render

    def restore() -> None:
        share_module.main = original_main
        share_module.render = original_render
        sys.modules.pop("render_war_report", None)
        if previous_war_module is not None:
            sys.modules["render_war_report"] = previous_war_module

    return restore


def _run_bounded_renderer(
    *,
    scripts_dir: Path,
    ticker: str,
    selector: str,
    out_name: str,
    scale: int,
    timeout_seconds: int,
) -> Path:
    code = (
        "import sys; "
        "from render_share_card import render; "
        "render(sys.argv[1], selector=sys.argv[2], out_name=sys.argv[3], "
        "scale=int(sys.argv[4]))"
    )
    proc = subprocess.Popen(  # noqa: S603 - 固定解释器与代码，参数不经 shell
        [sys.executable, "-c", code, ticker, selector, out_name, str(scale)],
        cwd=str(scripts_dir),
        env=dict(os.environ),
        start_new_session=(os.name == "posix"),
    )
    global _ACTIVE_RENDER_PROCESS
    with _ACTIVE_RENDER_LOCK:
        _ACTIVE_RENDER_PROCESS = proc
    try:
        exit_code = proc.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        _terminate_helper_process(proc)
        raise TimeoutError(
            f"{out_name} 渲染超过 {timeout_seconds} 秒，已跳过。"
        ) from exc
    finally:
        with _ACTIVE_RENDER_LOCK:
            if _ACTIVE_RENDER_PROCESS is proc:
                _ACTIVE_RENDER_PROCESS = None

    if exit_code != 0:
        raise RuntimeError(f"{out_name} 渲染进程退出码为 {exit_code}。")
    report_dirs = sorted(
        child
        for child in (scripts_dir / "reports").iterdir()
        if child.is_dir() and child.name.startswith(f"{ticker}_")
    )
    if not report_dirs:
        raise RuntimeError(f"{out_name} 渲染成功但报告目录不存在。")
    output_path = report_dirs[-1] / out_name
    if not output_path.is_file():
        raise RuntimeError(f"{out_name} 渲染进程成功但产物不存在。")
    return output_path


def _terminate_helper_process(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(proc.pid, signal.SIGTERM)
        else:  # pragma: no cover - Worker 正式环境为 Linux
            proc.terminate()
        proc.wait(timeout=3)
        return
    except (ProcessLookupError, PermissionError):
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        if os.name == "posix":
            os.killpg(proc.pid, signal.SIGKILL)
        else:  # pragma: no cover
            proc.kill()
        proc.wait(timeout=2)
    except (ProcessLookupError, PermissionError, subprocess.TimeoutExpired):
        pass


def terminate_active_helpers() -> None:
    """供取消信号处理器回收量化查询和 Playwright 隔离进程。"""
    with _ACTIVE_QUANT_LOCK:
        quant_proc = _ACTIVE_QUANT_PROCESS
    if quant_proc is not None:
        _terminate_helper_process(quant_proc)
    with _ACTIVE_RENDER_LOCK:
        render_proc = _ACTIVE_RENDER_PROCESS
    if render_proc is not None:
        _terminate_helper_process(render_proc)


def terminate_active_renderer() -> None:
    """向后兼容旧调用方；现在会回收全部 Stage 2 辅助进程。"""
    terminate_active_helpers()


def _build_stage1_manifest(
    *,
    work_dir: Path,
    ticker_input: str,
    ticker_normalized: str,
    files: list[str],
) -> dict[str, Any]:
    """从 work/ 下已复制的 core JSON 提取清单（company_name、data_as_of 等）。"""
    raw_data = _load_json_file(work_dir / "raw_data.json")
    company_name: str | None = None
    data_as_of: str | None = None
    if _is_dict(raw_data):
        basic = raw_data.get("dimensions", {}).get("0_basic", {})
        if not _is_dict(basic):
            basic = raw_data
        basic_data = basic.get("data", {}) if _is_dict(basic.get("data")) else {}
        company_name = (
            _extract_string(basic_data, "name")
            or _extract_string(raw_data, "name")
            or _extract_string(raw_data, "company_name")
        )
        data_as_of = (
            _extract_string(basic_data, "fetched_at")
            or _extract_string(raw_data, "fetched_at")
            or _extract_string(basic_data, "as_of")
            or _extract_string(raw_data, "data_as_of")
        )
    return {
        "schema_version": 1,
        "success": True,
        "ticker_input": ticker_input,
        "ticker_normalized": ticker_normalized,
        "company_name": company_name,
        "data_as_of": data_as_of,
        "files": files,
        "generated_at": _utc_now_iso(),
    }


def _collect_stage2_output(
    *,
    tmp_dir: Path,
    scripts_dir: Path,
    raw_output: Any,
    ticker: str,
) -> StageResult:
    """从上游 reports/ 与 .cache/ 收集 stage2 产物到 artifacts.tmp。"""
    reports_root = scripts_dir / "reports"
    cache_dir = scripts_dir / ".cache" / ticker

    # stage2 返回 standalone HTML 绝对路径；取其父目录为本次报告目录。
    report_out_dir: Path | None = None
    html_text = str(raw_output or "").strip() if not _is_dict(raw_output) else ""
    if html_text:
        html_path = Path(html_text)
        if not html_path.is_absolute():
            html_path = scripts_dir / html_path
        resolved = html_path.resolve()
        if resolved.is_file():
            report_out_dir = resolved.parent
            shutil.copy2(resolved, tmp_dir / UPSTREAM_HTML_NAME)
    if report_out_dir is None:
        # 兜底：按 reports/{ticker}_* 找最近目录。
        if reports_root.is_dir():
            candidates = sorted(reports_root.glob(f"{ticker}_*"))
            if candidates:
                report_out_dir = candidates[-1]
                src_html = report_out_dir / UPSTREAM_HTML_NAME
                if src_html.is_file():
                    shutil.copy2(src_html, tmp_dir / UPSTREAM_HTML_NAME)

    if report_out_dir is not None and report_out_dir.is_dir():
        _copy_if_exists(report_out_dir / "one-liner.txt", tmp_dir / "one-liner.txt")
        _copy_if_exists(report_out_dir / "share-card.png", tmp_dir / "share-card.png")
        _copy_if_exists(report_out_dir / "war-report.png", tmp_dir / "war-report.png")

    # synthesis：上游写到 .cache/{ticker}/synthesis.json
    syn_src = cache_dir / "synthesis.json"
    if syn_src.is_file():
        shutil.copy2(syn_src, tmp_dir / "synthesis.json")

    # report.meta.json：由 Worker 生成（上游不产出该文件）。
    meta = _build_meta(report_dir=tmp_dir.parent, ticker=ticker)
    (tmp_dir / "report.meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    collected = [
        name for name in STAGE2_ARTIFACT_FILES if (tmp_dir / name).is_file()
    ]
    return StageResult(
        success=True,
        manifest={
            "schema_version": 1,
            "success": True,
            "ticker": ticker,
            "artifacts": collected,
            "generated_at": _utc_now_iso(),
        },
    )


def _build_meta(*, report_dir: Path, ticker: str) -> dict[str, Any]:
    synthesis = _load_json_file(report_dir / "artifacts.tmp" / "synthesis.json") or {}
    return {
        "schema_version": 1,
        "ticker": str(synthesis.get("ticker") or ticker or "").strip(),
        "company_name": str(synthesis.get("name") or "").strip(),
        "generated_at": _utc_now_iso(),
        "uzi_commit": os.environ.get(
            "UZI_COMMIT", "7bc779dd15ca4a741fcda20319a431f283232366"
        ),
        "data_as_of": str(synthesis.get("fetched_at") or "").strip(),
    }


def _copy_if_exists(src: Path, dst: Path) -> None:
    if src.is_file():
        shutil.copy2(src, dst)


def _load_json_file(path: Path) -> dict[str, Any] | None:
    try:
        if not path.is_file():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if _is_dict(payload) else None


def _safe_exc_text(exc: Exception) -> str:
    text = str(exc or "").strip()
    if not text:
        return exc.__class__.__name__
    return text[:500]


def _extract_string(payload: Any, key: str) -> str | None:
    if _is_dict(payload):
        value = payload.get(key)
        if value is not None:
            return str(value).strip() or None
    return None


# ── MX Key 处理（文档 §8：临时传递 + 日志脱敏）──────────────
def env_patch(*, mx_api_key: str | None) -> None:
    """把 MX Key 写入子进程环境变量并登记脱敏；仅在内存中使用。

    - ``MX_APIKEY``：上游 UZI 代码实际读取的环境变量（review P2）。
    - ``UZI_MX_API_KEY``：兼容别名（历史名称）。
    """
    from app.sanitize import register_secret

    if mx_api_key:
        os.environ["MX_APIKEY"] = mx_api_key
        os.environ["UZI_MX_API_KEY"] = mx_api_key
        register_secret(mx_api_key)


def unregister_mx_secret(mx_api_key: str | None) -> None:
    from app.sanitize import unregister_secret

    if mx_api_key:
        unregister_secret(mx_api_key)


# ── Mock 模式（§20.7 烟雾测试，目录协议与真实模式一致）────────
def _mock_stage1(*, report_dir: Path, work_dir: Path, ticker: str) -> StageResult:
    if os.environ.get("UZI_MOCK_SLEEP_SECONDS"):
        time.sleep(int(os.environ["UZI_MOCK_SLEEP_SECONDS"]))

    # 测试钩子：中文名解析失败 / 非个股标的（文档 §5.2 第 5 条）。
    if os.environ.get("UZI_MOCK_FAIL_RESOLVE") in {"1", "true"}:
        raise UziStageError(
            "UZI_UNRESOLVED_TICKER",
            f"无法解析股票代码/名称：{ticker}",
        )
    if os.environ.get("UZI_MOCK_NON_STOCK") in {"1", "true"}:
        raise UziStageError(
            "UZI_NON_STOCK_SECURITY",
            "该标的为 ETF/指数/基金/可转债等非个股，已停止生成报告。",
        )

    normalized = _normalize_mock_ticker(ticker)
    now = _utc_now_iso()
    cache_dir = work_dir / ".cache" / normalized
    cache_dir.mkdir(parents=True, exist_ok=True)

    raw_data = {
        "schema_version": 1,
        "ticker": normalized,
        "company_name": f"模拟公司-{normalized}",
        "quote": {"last": 1735.0, "change_pct": 1.2, "as_of": now},
        "financials": {
            "revenue_3y": [120.0, 130.0, 141.0],
            "net_profit_3y": [30.0, 33.0, 36.5],
        },
        "data_as_of": now,
    }
    (cache_dir / "raw_data.json").write_text(
        json.dumps(raw_data, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    dimensions = {
        "schema_version": 1,
        "ticker": normalized,
        "dimensions": [
            {"id": "value", "name": "价值", "score": 72.0},
            {"id": "quality", "name": "质量", "score": 81.0},
            {"id": "growth", "name": "成长", "score": 66.0},
            {"id": "risk", "name": "风险", "score": 58.0},
        ],
        "data_as_of": now,
    }
    (cache_dir / "dimensions.json").write_text(
        json.dumps(dimensions, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    panel = {
        "schema_version": 1,
        "ticker": normalized,
        "panel": {
            "bullish": 18,
            "neutral": 21,
            "bearish": 12,
            "investors": [
                {"name": "模拟投资者A", "stance": "bullish", "category": "value"},
                {"name": "模拟投资者B", "stance": "bearish", "category": "risk"},
                {"name": "模拟投资者C", "stance": "neutral", "category": "growth"},
            ],
        },
        "data_as_of": now,
    }
    (cache_dir / "panel.json").write_text(
        json.dumps(panel, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    gaps = {
        "schema_version": 1,
        "coverage_pct": 92.0,
        "unresolved": 1,
        "items": [
            {"dimension": "8_materials", "note": "模拟数据缺口（上游数据源缺失）"}
        ],
    }
    (cache_dir / "_data_gaps.json").write_text(
        json.dumps(gaps, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # 复制到 work/（主服务 LLM 编排读取路径）
    collected: list[str] = []
    for name in STAGE1_CACHE_FILES:
        src = cache_dir / name
        if src.is_file():
            shutil.copy2(src, work_dir / name)
            collected.append(name)

    logger.info("mock stage1 完成: ticker=%s normalized=%s files=%s",
                ticker, normalized, ",".join(collected))
    return StageResult(
        success=True,
        manifest={
            "schema_version": 1,
            "success": True,
            "ticker_input": ticker,
            "ticker_normalized": normalized,
            "company_name": f"模拟公司-{normalized}",
            "data_as_of": now,
            "files": collected,
            "generated_at": now,
        },
    )


def _mock_stage2(*, report_dir: Path, tmp_dir: Path, work_dir: Path,
                 ticker: str) -> StageResult:
    if os.environ.get("UZI_MOCK_SLEEP_SECONDS"):
        time.sleep(int(os.environ["UZI_MOCK_SLEEP_SECONDS"]))

    now = _utc_now_iso()
    date = datetime.now().strftime("%Y%m%d")
    cache_dir = work_dir / ".cache" / ticker
    cache_dir.mkdir(parents=True, exist_ok=True)
    reports_dir = work_dir / "reports" / f"{ticker}_{date}"
    reports_dir.mkdir(parents=True, exist_ok=True)

    # full-report-standalone.html：必须 > 10KB（§12.4）。
    html_lines = [
        "<!DOCTYPE html>",
        "<html lang=\"zh-CN\">",
        "<head><meta charset=\"utf-8\">",
        "<title>模拟深度报告</title></head>",
        "<body>",
        f"<h1>模拟深度报告 {ticker}</h1>",
        "<p>本 HTML 由 mock 模式生成，仅用于容器烟雾测试与联调。</p>",
        "<!-- " + "mock-fill-" * 1200 + " -->",
        "</body></html>",
    ]
    html = "".join(html_lines)
    assert len(html.encode("utf-8")) > 10 * 1024, "mock HTML 必须大于 10KB"
    (reports_dir / UPSTREAM_HTML_NAME).write_text(html, encoding="utf-8")

    synthesis = {
        "schema_version": 1,
        "ticker": ticker,
        "name": f"模拟公司-{ticker}",
        "overall_score": 78.5,
        "verdict_label": "谨慎看多",
        "one_liner": "模拟合成摘要：估值合理、质量较高，关注风险维度。",
        "data_as_of": now,
        "disclaimer": "历史研究资料，不构成投资建议",
    }
    (cache_dir / "synthesis.json").write_text(
        json.dumps(synthesis, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    (reports_dir / "one-liner.txt").write_text(
        f"模拟深度报告核心结论：{ticker} 谨慎看多（mock）", encoding="utf-8"
    )

    # 1x1 透明 PNG（合法文件，用于烟雾测试校验非空）。
    png_bytes = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8"
        "z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
    )
    (reports_dir / "share-card.png").write_bytes(png_bytes)
    (reports_dir / "war-report.png").write_bytes(png_bytes)

    # 收集到 artifacts.tmp（走与真实模式一致的收集逻辑）。
    return _collect_stage2_output(
        tmp_dir=tmp_dir,
        scripts_dir=work_dir,
        raw_output=str((reports_dir / UPSTREAM_HTML_NAME).resolve()),
        ticker=ticker,
    )
