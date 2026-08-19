#!/usr/bin/env python3
"""TGForwarder 统一 CLI 入口。"""

import argparse
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from utils.config_loader import load_config
from utils.logging_setup import setup_logging
from utils.database import Database


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
    logger.info("开始链接检测…")
    manager.run(
        delete=args.mode,
        limit=args.limit,
        concurrency=args.concurrency,
        recheck=args.recheck,
    )
    logger.info("链接检测完成")


def cmd_run_all(args):
    from TGForwarder import TGForwarder
    from TGNetDiskLinkChecker import TelegramLinkManager
    cfg = load_config(args.config)
    logger = setup_logging(cfg.get("logging", {}).get("level", "INFO"))
    logger.info("=== 阶段 1/2: 转发 ===")
    TGForwarder.from_config(cfg).run()
    logger.info("=== 阶段 2/2: 链接检测 ===")
    manager = TelegramLinkManager.from_config(cfg)
    mode = args.mode if args.mode is not None else manager.config["DELETE_MODE"]
    manager.run(delete=mode, limit=args.limit, concurrency=args.concurrency, recheck=args.recheck)
    logger.info("完整流水线执行完毕")


def cmd_bot(args):
    from TGAdminBot import TGAdminBot
    cfg = load_config(args.config)
    setup_logging(cfg.get("logging", {}).get("level", "INFO"))
    TGAdminBot(cfg, config_path=args.config).run()


def cmd_search(args):
    cfg = load_config(args.config)
    setup_logging(cfg.get("logging", {}).get("level", "INFO"))
    db_path = cfg.get("database", {}).get("path", "data/tgforwarder.db")
    db = Database(db_path)
    results = db.search_resources(args.query, limit=args.limit)
    if not results:
        print(f"未找到与「{args.query}」相关的资源。")
        return
    for i, r in enumerate(results, 1):
        title = r.get("title") or "未知"
        year = r.get("year") or ""
        rating = r.get("rating")
        extra = f" ({year})" if year else ""
        star = f" ⭐{rating:.1f}" if rating else ""
        print(f"{i}. {title}{extra}{star}")


def cmd_join(args):
    from TGForwarder import TGForwarder
    cfg = load_config(args.config)
    setup_logging(cfg.get("logging", {}).get("level", "INFO"))
    TGForwarder.from_config(cfg).run_join()


def cmd_clear(args):
    from TGForwarder import TGForwarder
    cfg = load_config(args.config)
    setup_logging(cfg.get("logging", {}).get("level", "INFO"))
    TGForwarder.from_config(cfg).clear(args.start, args.end)


def main():
    parser = argparse.ArgumentParser(
        description="TGForwarder — Telegram 资源聚合转发与网盘链接检测",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python main.py forward                  # 执行转发
  python main.py check-links              # 检测失效链接（仅标记）
  python main.py check-links --mode 4     # 检测并编辑标记失效链接
  python main.py run-all                  # 转发 + 检测完整流水线
  python main.py bot                      # 启动管理 Bot
  python main.py search 沙丘              # 搜索已索引资源
        """,
    )
    parser.add_argument("--config", "-c", default="config.yaml", help="配置文件路径")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("forward", help="监控源频道并转发资源").set_defaults(func=cmd_forward)

    p_check = sub.add_parser("check-links", help="检测目标频道网盘链接有效性")
    p_check.add_argument("--mode", "-m", type=int, choices=[1, 2, 3, 4],
                         help="1:删除  2:仅检测  3:删已标记  4:编辑标记")
    p_check.add_argument("--limit", "-l", type=int)
    p_check.add_argument("--concurrency", type=int)
    p_check.add_argument("--recheck", action="store_true", default=None)
    p_check.add_argument("--no-recheck", dest="recheck", action="store_false")
    p_check.set_defaults(func=cmd_check_links)

    p_all = sub.add_parser("run-all", help="转发 + 链接检测完整流水线")
    p_all.add_argument("--mode", "-m", type=int, choices=[1, 2, 3, 4])
    p_all.add_argument("--limit", "-l", type=int)
    p_all.add_argument("--concurrency", type=int)
    p_all.add_argument("--recheck", action="store_true", default=None)
    p_all.add_argument("--no-recheck", dest="recheck", action="store_false")
    p_all.set_defaults(func=cmd_run_all)

    sub.add_parser("bot", help="启动 Telegram 管理 Bot（命令控制 + 可选定时任务）").set_defaults(func=cmd_bot)

    p_search = sub.add_parser("search", help="搜索已索引资源")
    p_search.add_argument("query", help="搜索关键词")
    p_search.add_argument("--limit", type=int, default=20)
    p_search.set_defaults(func=cmd_search)

    sub.add_parser("join", help="尝试加入配置的监控频道").set_defaults(func=cmd_join)

    p_clear = sub.add_parser("clear", help="清理目标频道指定时间段的消息")
    p_clear.add_argument("--start", required=True)
    p_clear.add_argument("--end", required=True)
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
