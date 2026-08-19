"""Telegram 管理 Bot — 通过私聊命令控制转发与检测。"""

import asyncio
import logging
from telethon import TelegramClient, events
from telethon.sessions import StringSession

from utils.config_loader import build_proxy
from utils.database import Database
from utils.logging_setup import setup_logging


HELP_TEXT = """**TGForwarder 管理 Bot**

/status — 查看运行状态与统计
/forward — 手动触发转发
/check — 手动触发链接检测
/run — 转发 + 检测（完整流水线）
/pause — 暂停自动任务
/resume — 恢复自动任务
/search `<关键词>` — 搜索已索引资源
/stats — 详细统计
/help — 显示此帮助
"""


class TGAdminBot:
    def __init__(self, cfg: dict, config_path: str = "config.yaml"):
        self.cfg = cfg
        self.config_path = config_path
        self.logger = setup_logging(cfg.get("logging", {}).get("level", "INFO"))
        admin = cfg.get("admin", {})
        self.admin_user_ids = set(admin.get("user_ids", []))
        self.admin_chat = admin.get("chat_id")
        self.schedule_interval = admin.get("schedule_interval_minutes", 0)

        db_cfg = cfg.get("database", {})
        self.db = Database(db_cfg.get("path", "data/tgforwarder.db"))

        tg = cfg["telegram"]
        self.client = TelegramClient(
            StringSession(tg["string_session"]),
            tg["api_id"],
            tg["api_hash"],
            proxy=build_proxy(cfg.get("proxy")),
        )
        self._running_task = None
        self._schedule_task = None

    def _authorized(self, event) -> bool:
        if self.admin_user_ids and event.sender_id not in self.admin_user_ids:
            return False
        if self.admin_chat and event.chat_id != self.admin_chat:
            return False
        return True

    def register_handlers(self):
        @self.client.on(events.NewMessage(pattern=r"^/"))
        async def handler(event):
            if not self._authorized(event):
                return
            text = (event.raw_text or "").strip()
            cmd = text.split()[0].lower().split("@")[0]
            args = text[len(cmd):].strip()

            handlers = {
                "/start": self._cmd_help,
                "/help": self._cmd_help,
                "/status": self._cmd_status,
                "/forward": self._cmd_forward,
                "/check": self._cmd_check,
                "/run": self._cmd_run,
                "/pause": self._cmd_pause,
                "/resume": self._cmd_resume,
                "/search": self._cmd_search,
                "/stats": self._cmd_stats,
            }
            fn = handlers.get(cmd)
            if fn:
                await fn(event, args)
            else:
                await event.reply("未知命令，发送 /help 查看帮助。")

    async def _cmd_help(self, event, _args):
        await event.reply(HELP_TEXT, parse_mode="md")

    async def _cmd_status(self, event, _args):
        stats = self.db.get_stats()
        paused = self.db.is_paused()
        status = "⏸ 已暂停" if paused else "▶️ 运行中"
        msg = (
            f"**状态**: {status}\n"
            f"**今日转发**: {stats['today_resources']} 条\n"
            f"**资源总数**: {stats['total_resources']} 条\n"
            f"**链接消息**: {stats['total_link_messages']} 条\n"
            f"**失效消息**: {stats['invalid_link_messages']} 条\n"
            f"**监控频道**: {stats['monitored_channels']} 个"
        )
        await event.reply(msg, parse_mode="md")

    async def _cmd_stats(self, event, _args):
        stats = self.db.get_stats()
        await event.reply(
            f"```\n{stats}\n```",
            parse_mode="md",
        )

    async def _cmd_pause(self, event, _args):
        self.db.set_paused(True)
        await event.reply("⏸ 已暂停自动任务。")

    async def _cmd_resume(self, event, _args):
        self.db.set_paused(False)
        await event.reply("▶️ 已恢复自动任务。")

    async def _cmd_search(self, event, args):
        if not args:
            await event.reply("用法: /search 关键词")
            return
        results = self.db.search_resources(args, limit=10)
        if not results:
            await event.reply(f"未找到与「{args}」相关的资源。")
            return
        lines = []
        for i, r in enumerate(results, 1):
            title = r.get("title") or "未知"
            year = r.get("year") or ""
            rating = r.get("rating")
            extra = f" ({year})" if year else ""
            star = f" ⭐{rating:.1f}" if rating else ""
            lines.append(f"{i}. {title}{extra}{star}")
        await event.reply("\n".join(lines))

    async def _run_forward(self):
        from TGForwarder import TGForwarder
        forwarder = TGForwarder.from_config(self.cfg)
        forwarder.db = self.db
        forwarder.client = self.client
        forwarder._owns_client = False
        await forwarder.main()

    async def _run_check(self, mode=None):
        from TGNetDiskLinkChecker import TelegramLinkManager
        manager = TelegramLinkManager.from_config(self.cfg)
        manager.db = self.db
        manager.client = self.client
        manager._owns_client = False
        delete = mode if mode is not None else manager.config["DELETE_MODE"]
        await manager.run_async(
            delete=delete,
            limit=manager.config["LIMIT"],
            concurrency=manager.config["CONCURRENCY"],
            recheck=manager.config["RECHECK"],
        )

    async def _cmd_forward(self, event, _args):
        if self._running_task and not self._running_task.done():
            await event.reply("⚠️ 已有任务在运行，请稍候。")
            return
        await event.reply("🚀 开始转发…")
        try:
            await self._run_forward()
            await event.reply("✅ 转发完成。")
        except Exception as e:
            self.logger.error(f"转发失败: {e}")
            await event.reply(f"❌ 转发失败: {e}")

    async def _cmd_check(self, event, _args):
        if self._running_task and not self._running_task.done():
            await event.reply("⚠️ 已有任务在运行，请稍候。")
            return
        await event.reply("🔍 开始链接检测…")
        try:
            await self._run_check()
            await event.reply("✅ 链接检测完成。")
        except Exception as e:
            self.logger.error(f"检测失败: {e}")
            await event.reply(f"❌ 检测失败: {e}")

    async def _cmd_run(self, event, _args):
        if self._running_task and not self._running_task.done():
            await event.reply("⚠️ 已有任务在运行，请稍候。")
            return
        await event.reply("🚀 开始完整流水线（转发 + 检测）…")
        try:
            await self._run_forward()
            await self._run_check()
            await event.reply("✅ 流水线完成。")
        except Exception as e:
            self.logger.error(f"流水线失败: {e}")
            await event.reply(f"❌ 流水线失败: {e}")

    async def _schedule_loop(self):
        interval = self.schedule_interval
        if not interval or interval <= 0:
            return
        self.logger.info(f"定时任务已启用，间隔 {interval} 分钟")
        while True:
            await asyncio.sleep(interval * 60)
            if self.db.is_paused():
                self.logger.info("已暂停，跳过本次定时任务")
                continue
            try:
                self.logger.info("定时任务：开始转发")
                await self._run_forward()
                self.logger.info("定时任务：开始链接检测")
                await self._run_check()
            except Exception as e:
                self.logger.error(f"定时任务失败: {e}")

    def run(self):
        self.register_handlers()
        with self.client.start():
            me = self.client.loop.run_until_complete(self.client.get_me())
            self.logger.info(f"管理 Bot 已启动，账号: {me.username or me.id}")
            if self.schedule_interval > 0:
                self.client.loop.create_task(self._schedule_loop())
            self.client.run_until_disconnected()
