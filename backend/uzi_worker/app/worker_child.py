"""Stage 子进程入口（由 runner 以受控参数启动）。

职责：

- 加载任务状态，切换到任务工作目录（``.cache`` 落在任务目录内）。
- 调用 uzi_adapter 执行 Stage 1 / Stage 2。
- 把进度写入 worker-state.json，最终状态由主进程监控线程确认。
- 注册/注销 MX Key 脱敏。

命令行参数全部固定生成（runner 传入），用户输入只作为 ticker 参数，
绝不拼入 shell。
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger("uzi.worker_child")


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="UZI Stage 子进程")
    parser.add_argument("--report-id", required=True)
    parser.add_argument("--stage", required=True, choices=["1", "2"])
    parser.add_argument("--ticker", default="")
    parser.add_argument("--report-root", required=True)
    parser.add_argument("--source-root", required=True)
    parser.add_argument("--mock", action="store_true")
    return parser.parse_args(argv)


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    from app.sanitize import install_sanitizing_filter

    install_sanitizing_filter()


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(list(argv) if argv is not None else sys.argv[1:])
    _configure_logging()

    from app.sanitize import register_secret, unregister_secret
    from app.state_store import StateStore
    from app.uzi_adapter import (
        UziStageError,
        run_stage1,
        run_stage2,
    )
    from app.config import get_worker_config

    config = get_worker_config()
    report_root = Path(args.report_root)
    store = StateStore(report_root, recover=False)
    report_dir = store.report_dir(args.report_id)

    # 子进程也登记脱敏：Worker Token 与 MX Key 不得出现在日志中。
    if config.token:
        register_secret(config.token)
    mx_api_key = os.environ.get("UZI_MX_API_KEY") or None
    if mx_api_key:
        register_secret(mx_api_key)

    work_dir = report_dir / "work"
    work_dir.mkdir(parents=True, exist_ok=True)
    # 文档 §12.3：切换当前目录到任务工作目录，确保 .cache 落在任务目录。
    os.chdir(work_dir)

    def _update(phase: str, progress: int, message: str) -> None:
        store.update(
            args.report_id,
            apply=lambda state: state.mark_running(
                phase=phase, progress=progress, message=message
            ),
        )

    try:
        if args.stage == "1":
            _update("stage1_running", 10, "正在进行数据采集。")
            result = run_stage1(
                report_dir=report_dir,
                ticker=args.ticker,
                source_root=Path(args.source_root),
                mock=args.mock,
                mx_api_key=mx_api_key,
            )
            if not result.success:
                raise UziStageError(
                    result.error_code or "UZI_STAGE1_FAILED",
                    result.error_message or "Stage 1 未成功。",
                )
            manifest_path = report_dir / "work" / "stage1-manifest.json"
            import json

            manifest_path.write_text(
                json.dumps(result.manifest, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            _update(
                "stage1_done",
                45,
                "Stage 1 完成：数据采集与机械评分已就绪。",
            )
            logger.info(
                "stage1 完成: report_id=%s ticker=%s",
                args.report_id,
                args.ticker,
            )
        else:
            _update("stage2_running", 85, "正在综合并渲染报告。")
            result = run_stage2(
                report_dir=report_dir,
                normalized_ticker=args.ticker,
                source_root=Path(args.source_root),
                mock=args.mock,
            )
            if not result.success:
                raise UziStageError(
                    result.error_code or "UZI_STAGE2_FAILED",
                    result.error_message or "Stage 2 未成功。",
                )
            _update("stage2_done", 95, "报告渲染完成，等待主服务校验。")
            logger.info(
                "stage2 完成: report_id=%s ticker=%s",
                args.report_id,
                args.ticker,
            )
    except UziStageError as exc:
        store.update(
            args.report_id,
            apply=lambda state: state.mark_failed(
                error_code=exc.error_code,
                error_message=exc.message,
            ),
        )
        logger.error("stage%s 失败: error_code=%s message=%s",
                     args.stage, exc.error_code, exc.message)
        return 1
    except Exception as exc:  # noqa: BLE001 - 兜底：任何异常转为结构化失败
        store.update(
            args.report_id,
            apply=lambda state: state.mark_failed(
                error_code=(
                    "UZI_STAGE1_FAILED" if args.stage == "1" else "UZI_STAGE2_FAILED"
                ),
                error_message=str(exc)[:500],
            ),
        )
        logger.exception("stage%s 发生未预期异常", args.stage)
        return 1
    finally:
        if mx_api_key:
            unregister_secret(mx_api_key)
        if config.token:
            unregister_secret(config.token)
    return 0


if __name__ == "__main__":
    sys.exit(main())