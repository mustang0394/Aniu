"""UZI 报告上下文只读技能（文档 §14）。

提供工具 ``uzi_get_report_context``：按 report_id 或 ticker 读取已完成
UZI 深度报告的标准化摘要（``UziReportJob.summary_json``，结构见文档 §9.1），
为 AniU 分析、交易或聊天模式提供带时间戳的历史研究参考。

安全边界：
- 只读工具，不包含任何写、交易、exec 能力（§14, §22 第 9 项）。
- 只返回 ``completed`` 报告；queued / failed / cancelled 一律不返回（§14.2）。
- 报告内容视为历史研究资料，不是实时证据；交易模式仍须查询本轮实时数据。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import or_, select

from app.db.models import UziReportJob
from app.skills.base import BaseSkill
from app.skills.context import get_chat_context_ports

_STALE_AFTER_DAYS = 7
_DEFAULT_MAX_CHARS = 12000
_MIN_MAX_CHARS = 1000
_MAX_MAX_CHARS = 20000
_MAX_TICKER_LENGTH = 64

# 章节 → summary_json 顶层字段映射（§14.2 / §9.1）。
_SECTION_KEYS: dict[str, str] = {
    "overview": None,  # 特殊处理：由多个字段组装
    "valuation": "valuation",
    "risks": "risks",
    "catalysts": "catalysts",
    "panel": "panel",
    "qualitative": "qualitative",
    "data_gaps": "data_gaps",
    "sources": "sources",
}

_DEFAULT_SECTIONS = ["overview", "valuation", "risks", "catalysts", "panel", "data_gaps"]

# 章节裁剪优先级：优先保留 overview，最后丢弃 sources（§14.2）。
_SECTION_PRIORITY = [
    "overview",
    "valuation",
    "risks",
    "catalysts",
    "panel",
    "data_gaps",
    "qualitative",
    "sources",
]

_DISCLAIMER = "该报告是历史研究资料，不构成投资建议；当前交易决策必须重新查询实时数据。"


def _clamp_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        numeric = int(value)
    except (TypeError, ValueError):
        numeric = default
    return max(minimum, min(maximum, numeric))


def _iso(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC).isoformat(timespec="seconds")
    return str(value)


def _now_utc() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _build_overview(summary: dict[str, Any]) -> dict[str, Any]:
    """overview 章节：由 summary_json 多个顶层字段组装（§14.2）。"""
    return {
        "one_liner": summary.get("one_liner") or "",
        "verdict": summary.get("verdict") or "",
        "overall_score": summary.get("overall_score") or 0,
        "company_name": summary.get("company_name") or "",
        "data_as_of": summary.get("data_as_of") or "",
        "generated_at": summary.get("generated_at") or "",
    }


def _render_sections(
    summary: dict[str, Any],
    sections: list[str],
) -> dict[str, Any]:
    rendered: dict[str, Any] = {}
    for section in sections:
        if section == "overview":
            rendered[section] = _build_overview(summary)
        else:
            key = _SECTION_KEYS[section]
            value = summary.get(key)
            rendered[section] = value if value is not None else {}
    return rendered


def _data_gap_warning(summary: dict[str, Any]) -> str | None:
    gaps = summary.get("data_gaps")
    if not isinstance(gaps, dict):
        return None
    unresolved = gaps.get("unresolved") or 0
    items = gaps.get("items") or []
    if unresolved > 0 or items:
        return (
            f"该报告存在 {unresolved} 个未解决数据缺口"
            + (f"，共 {len(items)} 条记录" if items else "")
            + "；相关结论的确定性有限。"
        )
    return None


class Skill(BaseSkill):
    id = "uzi_report_context"
    name = "UZI报告上下文"
    description = "读取已完成的 UZI 个股深度报告摘要，提供带时间戳的历史研究参考（只读）。"
    run_types = ["analysis", "trade", "chat"]
    tools = [
        {
            "type": "function",
            "function": {
                "name": "uzi_get_report_context",
                "description": (
                    "读取已完成的 UZI 个股深度报告摘要，为分析、交易或聊天提供带时间戳的"
                    "历史研究参考。报告不是实时证据：交易模式引用后仍必须查询本轮实时行情、"
                    "持仓、资金和必要委托，不得根据报告自动调用交易工具。"
                    "优先按明确的 report_id 查询；否则按 ticker 获取该股票最新一份已完成报告。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "report_id": {
                            "type": "integer",
                            "minimum": 1,
                            "description": "UZI 报告任务 ID。与 ticker 至少提供一个；同时提供时两者必须对应同一股票。",
                        },
                        "ticker": {
                            "type": "string",
                            "maxLength": 64,
                            "description": "股票代码或名称，例如 600519.SH 或 贵州茅台。",
                        },
                        "sections": {
                            "type": "array",
                            "items": {
                                "type": "string",
                                "enum": [
                                    "overview",
                                    "valuation",
                                    "risks",
                                    "catalysts",
                                    "panel",
                                    "qualitative",
                                    "data_gaps",
                                    "sources",
                                ],
                            },
                            "description": (
                                "需要返回的章节；为空时默认返回 overview、valuation、risks、"
                                "catalysts、panel 和 data_gaps。"
                            ),
                        },
                        "max_chars": {
                            "type": "integer",
                            "minimum": 1000,
                            "maximum": 20000,
                            "default": 12000,
                            "description": "返回内容最大字符数，超出时按章节优先级裁剪。",
                        },
                    },
                    "additionalProperties": False,
                },
            },
        },
    ]

    def do_uzi_get_report_context(self, *, arguments, context):
        raw_report_id = arguments.get("report_id")
        raw_ticker = arguments.get("ticker")
        raw_sections = arguments.get("sections")
        max_chars = _clamp_int(
            arguments.get("max_chars", _DEFAULT_MAX_CHARS),
            default=_DEFAULT_MAX_CHARS,
            minimum=_MIN_MAX_CHARS,
            maximum=_MAX_MAX_CHARS,
        )

        report_id: int | None = None
        if raw_report_id is not None:
            try:
                report_id = int(raw_report_id)
            except (TypeError, ValueError):
                return {
                    "ok": False,
                    "tool_name": "uzi_get_report_context",
                    "error": "report_id 必须是正整数。",
                }
            if report_id < 1:
                return {
                    "ok": False,
                    "tool_name": "uzi_get_report_context",
                    "error": "report_id 必须是正整数。",
                }

        ticker = str(raw_ticker or "").strip()
        if len(ticker) > _MAX_TICKER_LENGTH:
            return {
                "ok": False,
                "tool_name": "uzi_get_report_context",
                "error": f"ticker 长度不能超过 {_MAX_TICKER_LENGTH} 个字符。",
            }

        if report_id is None and not ticker:
            return {
                "ok": False,
                "tool_name": "uzi_get_report_context",
                "error": "report_id 和 ticker 至少提供一个。",
            }

        sections: list[str] = []
        if raw_sections is None:
            sections = list(_DEFAULT_SECTIONS)
        elif isinstance(raw_sections, list):
            sections = [
                str(item).strip()
                for item in raw_sections
                if str(item).strip() in _SECTION_KEYS
            ]
            if not sections:
                sections = list(_DEFAULT_SECTIONS)
        else:
            sections = list(_DEFAULT_SECTIONS)

        session_scope = get_chat_context_ports(context).session_scope_factory

        with session_scope() as db:
            job = None
            if report_id is not None:
                job = db.get(UziReportJob, report_id)
                if job is not None and job.status != "completed":
                    job = None
                if job is not None and ticker:
                    # report_id 与 ticker 同时提供：必须对应同一股票。
                    match_tickers = {
                        str(job.ticker_normalized or "").strip(),
                        str(job.ticker_input or "").strip(),
                    }
                    if ticker not in match_tickers:
                        return {
                            "ok": False,
                            "tool_name": "uzi_get_report_context",
                            "error": (
                                f"report_id {report_id} 对应股票与 ticker 不一致："
                                f"报告股票为 {match_tickers - {''} or '未知'}。"
                            ),
                        }
            else:
                # 按 ticker 查询最新完成报告：同时匹配标准代码、原始输入、公司名
                # （review 问题10：此前只匹配前两者，按公司名查询查不到）。
                ticker_pattern = f"%{ticker}%"
                job = db.scalar(
                    select(UziReportJob)
                    .where(
                        UziReportJob.status == "completed",
                        or_(
                            UziReportJob.ticker_normalized == ticker,
                            UziReportJob.ticker_input == ticker,
                            UziReportJob.company_name.ilike(ticker_pattern),
                        ),
                    )
                    .order_by(UziReportJob.created_at.desc())
                    .limit(1)
                )

        if job is None:
            if report_id is not None:
                return {
                    "ok": False,
                    "tool_name": "uzi_get_report_context",
                    "error": f"报告 {report_id} 不存在或未完成。",
                }
            return {
                "ok": False,
                "tool_name": "uzi_get_report_context",
                "error": f"未找到 {ticker} 的已完成深度报告。",
            }

        summary = job.summary_json or {}

        rendered = _render_sections(summary, sections)
        full_text = json.dumps(
            rendered, ensure_ascii=False, default=str, sort_keys=True
        )
        truncated = False
        if len(full_text) > max_chars:
            # 按优先级从低到高丢弃章节，直到内容不超过 max_chars。
            kept = set(sections)
            for section in reversed(_SECTION_PRIORITY):
                if len(full_text) <= max_chars:
                    break
                if section not in kept:
                    continue
                kept.discard(section)
                rendered.pop(section, None)
                truncated = True
                full_text = json.dumps(
                    rendered, ensure_ascii=False, default=str, sort_keys=True
                )
            # 全部章节都被丢弃仍超长（极小概率）：硬截断文本。
            if len(full_text) > max_chars:
                marker = "\n...(内容过长，已截断)"
                clipped = full_text[: max(0, max_chars - len(marker))]
                rendered = {"_truncated": clipped + marker}
                truncated = True

        created_at = job.created_at or _now_utc()
        if created_at.tzinfo is not None:
            created_naive = created_at.astimezone(UTC).replace(tzinfo=None)
        else:
            created_naive = created_at
        age_days = max(0, (_now_utc() - created_naive).days)

        return {
            "ok": True,
            "tool_name": "uzi_get_report_context",
            "summary": (
                f"已读取 {job.company_name or job.ticker_normalized or job.ticker_input} "
                f"的 UZI 深度报告摘要"
                + ("（数据已过期）" if age_days > _STALE_AFTER_DAYS else "")
                + "。"
            ),
            "result": {
                "ok": True,
                "source": "uzi_report",
                "report_id": job.id,
                "ticker": str(
                    job.ticker_normalized or job.ticker_input or ""
                ).strip(),
                "company_name": job.company_name or "",
                "generated_at": _iso((summary.get("generated_at") or "")),
                "data_as_of": _iso(summary.get("data_as_of") or job.data_as_of),
                "age_days": age_days,
                "is_stale": age_days > _STALE_AFTER_DAYS,
                "uzi_commit": job.uzi_commit or "",
                "llm_model": job.llm_model or "",
                "sections": rendered,
                "data_gap_warning": _data_gap_warning(summary),
                "truncated": truncated,
                "disclaimer": _DISCLAIMER,
            },
        }
