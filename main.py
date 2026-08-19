#!/usr/bin/env python3
"""TGForwarder 统一 CLI 入口。"""

import argparse
import sys
from pathlib import Path

# 加载 .env（若存在）
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from utils.config_loader import load_config
from utils.logging_setup import setup_logging


def cmd_forward(args):
    from TGForwarder import TGForwarder
    cfg = load_config(args.config)
    logger = setup_logging(cfg.get("logging", {}).get("level", "INFO"))
    forwarder = TGForwarder.from_config(cfg)
    logger.info("开始转发任务…")
    forwarder.run()
    logger.info("转发任务完成")


def cmd_check_links(args):
    from TGNetDiskLinkChecker import TelegramLinkManager
    cfg = load_config(args.config)
    logger = setup_logging(cfg.get("logging", {}).get("level", "INFO"))
    manager = TelegramLinkManager.from_config(cfg)
    delete = args.mode if args.mode is not None else None
    limit = args.limit
    concurrency = args.concurrency
    recheck = args.recheck if args.recheck is not None else None
    logger.info("开始链接检测…")
    manager.run(delete=delete, limit=limit, concurrency=concurrency, recheck=recheck)
    logger.info("链接检测完成")


def cmd_join(args):
    from TGForwarder import TGForwarder
    cfg = load_config(args.config)
    setup_logging(cfg.get("logging", {}).get("level", "INFO"))
    forwarder = TGForwarder.from_config(cfg)
    forwarder.run_join()


def cmd_clear(args):
    from TGForwarder import TGForwarder
    cfg = load_config(args.config)
    setup_logging(cfg.get("logging", {}).get("level", "INFO"))
    forwarder = TGForwarder.from_config(cfg)
    forwarder.clear(args.start, args.end)


def main():
    parser = argparse.ArgumentParser(
        description="TGForwarder — Telegram 资源聚合转发与网盘链接检测",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python main.py forward                  # 执行转发
  python main.py check-links              # 检测失效链接（仅标记）
  python main.py check-links --mode 1     # 检测并删除失效链接
  python main.py join                     # 尝试加入监控频道
  python main.py forward --config my.yaml # 指定配置文件
        """,
    )
    parser.add_argument(
        "--config", "-c",
        default="config.yaml",
        help="配置文件路径（默认 config.yaml）",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("forward", help="监控源频道并转发资源").set_defaults(func=cmd_forward)

    p_check = sub.add_parser("check-links", help="检测目标频道网盘链接有效性")
    p_check.add_argument("--mode", "-m", type=int, choices=[1, 2, 3],
                         help="1:检测并删除  2:仅检测  3:删除已标记")
    p_check.add_argument("--limit", "-l", type=int, help="本次检测最大消息数")
    p_check.add_argument("--concurrency", type=int, help="并发检测数")
    p_check.add_argument("--recheck", action="store_true", default=None, help="重新检测已标记失效的链接")
    p_check.add_argument("--no-recheck", dest="recheck", action="store_false", help="跳过重新检测")
    p_check.set_defaults(func=cmd_check_links)

    sub.add_parser("join", help="尝试加入配置的监控频道").set_defaults(func=cmd_join)

    p_clear = sub.add_parser("clear", help="清理目标频道指定时间段的消息")
    p_clear.add_argument("--start", required=True, help="开始时间，格式 YYYY-MM-DD HH:MM")
    p_clear.add_argument("--end", required=True, help="结束时间，格式 YYYY-MM-DD HH:MM")
    p_clear.set_defaults(func=cmd_clear)

    args = parser.parse_args()
    try:
        args.func(args)
    except FileNotFoundError as e:
        print(e, file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n已中断", file=sys.stderr)
        sys.exit(130)


if __name__ == "__main__":
    main()
