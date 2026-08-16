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
import json
import logging
import os
import shutil
import sys
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
        import run_real_test  # noqa: PLC0415 - 受控加载上游入口

        return run_real_test
    except Exception as exc:  # noqa: BLE001 - 依赖或语法问题统一按不可用处理
        logger.warning("导入 run_real_test 失败: %s", exc)
        return None


def _is_dict(data: Any) -> bool:
    return isinstance(data, dict)


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
    try:
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
    try:
        os.chdir(scripts_dir)
        output = stage2_func(normalized_ticker)
    except UziStageError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise UziStageError(
            "UZI_STAGE2_FAILED",
            f"Stage 2 执行失败：{_safe_exc_text(exc)}",
        ) from exc
    finally:
        os.chdir(cwd_before)

    return _collect_stage2_output(
        tmp_dir=tmp_dir,
        scripts_dir=scripts_dir,
        raw_output=output,
        ticker=normalized_ticker,
    )


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