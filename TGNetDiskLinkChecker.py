import json
import os
import logging
import re
import asyncio
import httpx
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import RPCError
from bs4 import BeautifulSoup

from utils.link_utils import extract_share_id, extract_net_disk_urls
from utils.config_loader import build_proxy
from utils.logging_setup import setup_logging
from utils.database import Database


class TelegramLinkManager:
    INVALID_MARKER = "⚠️ 含失效链接"

    def __init__(self, config, client=None, db=None):
        log_level = config.get("LOG_LEVEL", "INFO")
        self.logger = setup_logging(log_level)
        self.config = config
        db_path = config.get("DB_PATH", "data/tgforwarder.db")
        self.db = db or Database(db_path)
        self.db.migrate_link_check_json(
            config.get("JSON_PATH_NORMAL", "messages.json"),
            config.get("JSON_PATH_123", "messages_123.json"),
        )
        if client:
            self.client = client
            self._owns_client = False
        else:
            self.client = TelegramClient(
                StringSession(config["STRING_SESSION"]),
                config["API_ID"],
                config["API_HASH"],
                proxy=config["PROXY"],
            )
            self._owns_client = True
        self.json_path_normal = config["JSON_PATH_NORMAL"]
        self.json_path_123 = config["JSON_PATH_123"]
        self.target_channel = config["TARGET_CHANNEL"]
        self.batch_size = config["BATCH_SIZE"]
        self.net_disk_domains = config["NET_DISK_DOMAINS"]
        self.edit_mode = config.get("EDIT_INVALID", True)

    @classmethod
    def from_config(cls, cfg: dict) -> "TelegramLinkManager":
        """从 config.yaml 构建实例。"""
        tg = cfg["telegram"]
        lc = cfg.get("link_checker", {})
        log_cfg = cfg.get("logging", {})
        db_cfg = cfg.get("database", {})
        config = {
            "API_ID": tg["api_id"],
            "API_HASH": tg["api_hash"],
            "STRING_SESSION": tg["string_session"],
            "JSON_PATH_NORMAL": lc.get("json_path_normal", "messages.json"),
            "JSON_PATH_123": lc.get("json_path_123", "messages_123.json"),
            "TARGET_CHANNEL": tg.get("target_channel") or tg["forward_to_channel"],
            "PROXY": build_proxy(cfg.get("proxy")),
            "BATCH_SIZE": lc.get("batch_size", 500),
            "DELETE_MODE": lc.get("delete_mode", 2),
            "LIMIT": lc.get("limit", 1000),
            "CONCURRENCY": lc.get("concurrency", 20),
            "RECHECK": lc.get("recheck", True),
            "NET_DISK_DOMAINS": lc.get("net_disk_domains"),
            "LOG_LEVEL": log_cfg.get("level", "INFO"),
            "DB_PATH": db_cfg.get("path", "data/tgforwarder.db"),
            "EDIT_INVALID": lc.get("edit_invalid", True),
        }
        return cls(config)

    def extract_links(self, message_text: str):
        """从消息文本中提取网盘链接"""
        return extract_net_disk_urls(message_text, self.net_disk_domains)

    # 异步读取JSON文件
    async def load_json_data(self, json_path: str):
        """读取JSON文件，若不存在则创建新文件"""
        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if "messages" not in data:
                    data["messages"] = []
                if "last_processed_id" not in data:
                    data["last_processed_id"] = 0
                self.logger.info(f"加载JSON数据: {json_path}, messages_count={len(data['messages'])}, last_processed_id={data['last_processed_id']}")
                return data
        except (FileNotFoundError, json.JSONDecodeError):
            self.logger.info(f"JSON文件未找到，创建新文件: {json_path}")
            data = {"messages": [], "last_processed_id": 0}
            await self.save_json_data(data, json_path)
            return data

    # 异步保存JSON文件
    async def save_json_data(self, data, json_path: str):
        """保存数据到JSON文件"""
        try:
            with open(json_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            self.logger.info(f"JSON文件保存成功: {json_path}, messages_count={len(data['messages'])}, last_processed_id={data['last_processed_id']}")
        except Exception as e:
            self.logger.error(f"保存JSON失败: {e}, 路径: {json_path}")

    async def fetch_and_save_all_messages(self, limit=None):
        """分批获取新消息并保存到 SQLite。"""
        offset_id = self.db.get_link_check_last_id()
        total_new_messages = 0

        while True:
            messages_fetched = 0
            try:
                async for message in self.client.iter_messages(
                    self.target_channel,
                    min_id=offset_id,
                    reverse=True,
                    limit=self.batch_size,
                ):
                    if message is None:
                        break
                    text = message.text or ""
                    links = self.extract_links(text)
                    if links:
                        is_123 = any("123" in url for url in links)
                        self.db.upsert_link_check_message(message.id, links, is_123)
                        offset_id = max(offset_id, message.id)
                        messages_fetched += 1
                        total_new_messages += 1
                        if limit and total_new_messages >= limit:
                            break

                if offset_id > self.db.get_link_check_last_id():
                    self.db.set_link_check_last_id(offset_id)

                if messages_fetched == 0 or (limit and total_new_messages >= limit):
                    break
            except Exception as e:
                self.logger.error(f"获取消息失败: {e}")
                break

        self.logger.info(f"新消息保存完成，总计 {total_new_messages} 条")

    def extract_share_id(self, url: str):
        """从链接中提取分享ID（委托 utils）。"""
        return extract_share_id(url)

    # 检查网盘链接有效性
    async def check_uc(self, share_id: str):
        url = f"https://drive.uc.cn/s/{share_id}"
        headers = {"User-Agent": "Mozilla/5.0 (Linux; Android 10; SM-G975F) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/87.0.4280.101 Mobile Safari/537.36"}
        timeout = httpx.Timeout(10.0, connect=5.0, read=5.0)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.get(url, headers=headers)
                response.raise_for_status()
                soup = BeautifulSoup(response.text, 'html.parser')
                page_text = soup.get_text(strip=True)
                error_keywords = ["失效", "不存在", "违规", "删除", "已过期", "被取消"]
                if any(keyword in page_text for keyword in error_keywords):
                    return False
                if "文件" in page_text or "分享" in page_text:
                    return True
                return False
        except httpx.TimeoutException as e:
            self.logger.error(f"UC网盘链接 {url} 检测超时: {str(e)}")
            return True
        except httpx.HTTPStatusError as e:
            self.logger.error(f"UC网盘链接 {url} HTTP错误: {e.response.status_code}")
            return False
        except Exception as e:
            if 'ConnectError' in str(e):
                return True
            self.logger.error(f"UC网盘链接 {url} 检测失败: {type(e).__name__}: {str(e)}")
            return False

    async def check_aliyun(self, share_id: str):
        api_url = "https://api.aliyundrive.com/adrive/v3/share_link/get_share_by_anonymous"
        headers = {"Content-Type": "application/json"}
        data = json.dumps({"share_id": share_id})
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(api_url, headers=headers, data=data)
                response_json = response.json()
                return bool(response_json.get('has_pwd') or response_json.get('file_infos'))
        except httpx.RequestError as e:
            self.logger.error(f"检测阿里云盘链接失败: {e}")
            return True

    async def check_115(self, share_id: str):
        api_url = "https://webapi.115.com/share/snap"
        params = {"share_code": share_id, "receive_code": ""}
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(api_url, params=params)
                response_json = response.json()
                return bool(response_json.get('state') or '请输入访问码' in response_json.get('error', ''))
        except httpx.RequestError as e:
            self.logger.error(f"检测115网盘链接失败: {e}")
            return True

    async def check_quark(self, share_id: str):
        api_url = "https://drive.quark.cn/1/clouddrive/share/sharepage/token"
        headers = {"Content-Type": "application/json"}
        data = json.dumps({"pwd_id": share_id, "passcode": ""})
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(api_url, headers=headers, data=data)
                response_json = response.json()
                return response_json.get('message') == "ok" or response_json.get('message') == "需要提取码"
        except httpx.RequestError as e:
            self.logger.error(f"检测夸克网盘链接失败: {e}")
            return True

    async def check_123(self, share_id: str):
        api_url = f"https://www.123pan.com/api/share/info?shareKey={share_id}"
        timeout = httpx.Timeout(10.0, connect=5.0, read=5.0)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.get(api_url, headers={"User-Agent": "Mozilla/5.0"})
                if response.status_code == 403:
                    return True
                response_json = response.json()
                return bool(response_json.get('data', {}).get('HasPwd', False) or response_json.get('code') == 0)
        except (httpx.RequestError, json.JSONDecodeError) as e:
            self.logger.error(f"检测123网盘链接失败: {e}")
            return True

    async def check_baidu(self, share_id: str):
        url = f"https://pan.baidu.com/s/{share_id}"
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"}
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(url, headers=headers, follow_redirects=True)
                text = response.text
                if "need verify" in text:
                    return True
                if any(x in text for x in ["分享的文件已经被取消", "分享已过期", "你访问的页面不存在"]):
                    return False
                return bool("请输入提取码" in text or "提取文件" in text or "过期时间" in text)
        except httpx.RequestError as e:
            self.logger.error(f"检测百度网盘链接失败: {e}")
            return False

    async def check_tianyi(self, share_id: str):
        api_url = "https://api.cloud.189.cn/open/share/getShareInfoByCodeV2.action"
        timeout = httpx.Timeout(10.0, connect=5.0, read=5.0)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(api_url, data={"shareCode": share_id})
                response.raise_for_status()
                text = response.text
                if any(x in text for x in ["ShareInfoNotFound", "ShareNotFound", "FileNotFound", "ShareExpiredError", "ShareAuditNotPass"]):
                    return False
                return True
        except httpx.TimeoutException as e:
            self.logger.error(f"天翼云盘链接 {share_id} 检测超时: {str(e)}")
            return True
        except httpx.HTTPStatusError as e:
            self.logger.error(f"天翼云盘链接 {share_id} HTTP错误: {e.response.status_code}")
            return False
        except Exception as e:
            if 'ConnectError' in str(e):
                return True
            self.logger.error(f"天翼云盘链接 {share_id} 检测失败: {type(e).__name__}: {str(e)}")
            return False

    # 检查单个链接有效性
    async def check_url(self, url: str, semaphore: asyncio.Semaphore):
        async with semaphore:
            # self.logger.info(f"开始检测链接: {url}")
            share_id, service = self.extract_share_id(url)
            if not share_id or not service:
                self.logger.warning(f"无法识别链接: {url}")
                return True
            check_functions = {
                "uc": self.check_uc, "aliyun": self.check_aliyun, "quark": self.check_quark,
                "115": self.check_115, "123": self.check_123, "baidu": self.check_baidu,
                "tianyi": self.check_tianyi
            }
            result = await check_functions.get(service, lambda x: True)(share_id)
            if not result:
                self.logger.info(f"链接 {url} 检测完成，结果: {result}")
            return result

    async def _check_all_urls(self, messages: list[dict], concurrency: int):
        all_urls_123, all_urls_normal, url_to_mid = [], [], {}
        for message in messages:
            for url in message["urls"]:
                url_to_mid[url] = message["message_id"]
                if "123" in url:
                    all_urls_123.append(url)
                else:
                    all_urls_normal.append(url)

        self.logger.info(
            f"待检测: {len(all_urls_123)} 条123链接, {len(all_urls_normal)} 条其他链接"
        )
        semaphore_123 = asyncio.Semaphore(min(10, concurrency))
        semaphore_normal = asyncio.Semaphore(concurrency)
        invalid_by_mid: dict[int, list] = {}

        async def run_checks(urls, sem, conc):
            results = {}
            if not urls:
                return results
            tasks = [self.check_url(u, sem) for u in urls]
            try:
                outs = await asyncio.wait_for(
                    asyncio.gather(*tasks, return_exceptions=True),
                    timeout=max(120.0, len(urls) / max(1, conc) * 10),
                )
                for url, result in zip(urls, outs):
                    if not result or isinstance(result, Exception):
                        results[url] = False
            except asyncio.TimeoutError:
                self.logger.error(f"链接检测超时，共 {len(urls)} 条")
                for url in urls:
                    results[url] = False
            return results

        bad_123 = await run_checks(all_urls_123, semaphore_123, min(10, concurrency))
        bad_normal = await run_checks(all_urls_normal, semaphore_normal, concurrency)
        for url, bad in {**bad_123, **bad_normal}.items():
            if bad:
                mid = url_to_mid[url]
                invalid_by_mid.setdefault(mid, [])
                if url not in invalid_by_mid[mid]:
                    invalid_by_mid[mid].append(url)

        for mid, invalids in invalid_by_mid.items():
            self.db.update_link_check_invalid(mid, invalids)
        return invalid_by_mid

    async def _delete_invalid_messages(self, messages: list[dict], include_123: bool = True):
        for message in messages:
            invalid = message.get("invalid_urls", [])
            if not invalid:
                continue
            if message.get("is_123") and include_123:
                if len(invalid) < len(message.get("urls", [])):
                    continue
                self.logger.warning("123网盘消息全部链接失效，谨慎删除")
            try:
                await self.client.delete_messages(self.target_channel, message["message_id"])
                self.logger.info(f"删除失效消息: {message['message_id']}")
                self.db.remove_link_check_message(message["message_id"])
            except RPCError as e:
                self.logger.error(f"删除消息失败: {e}")

    async def _edit_invalid_messages(self, messages: list[dict]):
        for message in messages:
            invalid = message.get("invalid_urls", [])
            if not invalid or message.get("edited"):
                continue
            if message.get("is_123") and len(invalid) < len(message.get("urls", [])):
                continue
            try:
                msg = await self.client.get_messages(self.target_channel, ids=message["message_id"])
                if not msg or not msg.text:
                    continue
                text = msg.text
                for url in invalid:
                    if url in text:
                        text = text.replace(url, f"[已失效] {url}")
                if self.INVALID_MARKER not in text:
                    text += f"\n\n{self.INVALID_MARKER}"
                await self.client.edit_message(self.target_channel, message["message_id"], text)
                self.db.mark_link_check_edited(message["message_id"])
                self.logger.info(f"已编辑标记失效消息: {message['message_id']}")
            except RPCError as e:
                self.logger.error(f"编辑消息失败: {e}")

    async def process_messages(self, delete, concurrency=500):
        messages = self.db.get_all_link_check_messages()
        if delete in (1, 2, 4):
            await self._check_all_urls(messages, concurrency)

        messages = self.db.get_all_link_check_messages()

        if delete == 1:
            normal = [m for m in messages if not m.get("is_123")]
            pan123 = [m for m in messages if m.get("is_123")]
            await self._delete_invalid_messages(normal, include_123=False)
            await self._delete_invalid_messages(pan123, include_123=True)
        elif delete == 3:
            await self._delete_invalid_messages(messages)
        elif delete == 4:
            await self._edit_invalid_messages(messages)

    async def recheck_invalid_urls(self, concurrency=500):
        """重新检测标记为失效的链接。"""
        messages = self.db.get_all_link_check_messages()
        invalid_urls = []
        url_to_mid = {}
        for message in messages:
            for url in message.get("invalid_urls", []):
                invalid_urls.append(url)
                url_to_mid[url] = message["message_id"]

        if not invalid_urls:
            return

        self.logger.info(f"重新检测 {len(invalid_urls)} 条失效链接")
        sem = asyncio.Semaphore(min(concurrency, 50))
        tasks = [self.check_url(u, sem) for u in invalid_urls]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        cleared_mids = set()
        for url, result in zip(invalid_urls, results):
            if result and not isinstance(result, Exception):
                mid = url_to_mid[url]
                msg = next(m for m in messages if m["message_id"] == mid)
                new_invalid = [u for u in msg["invalid_urls"] if u != url]
                self.db.update_link_check_invalid(mid, new_invalid)
                cleared_mids.add(mid)
                self.logger.info(f"链接 {url} 重新检测有效")

        for mid in cleared_mids:
            self.logger.debug(f"消息 {mid} 失效列表已更新")

    async def run_async(self, delete, limit=None, concurrency=500, recheck=False):
        if delete in [1, 2, 4]:
            await self.fetch_and_save_all_messages(limit)
            await self.process_messages(delete=2, concurrency=concurrency)
            if recheck:
                await self.recheck_invalid_urls(concurrency)
            if delete == 1:
                await self.process_messages(delete=1, concurrency=concurrency)
            elif delete == 4:
                await self.process_messages(delete=4, concurrency=concurrency)
        else:
            await self.process_messages(delete, concurrency)

    def run(self, delete=None, limit=None, concurrency=None, recheck=None):
        delete = delete if delete is not None else self.config["DELETE_MODE"]
        limit = limit if limit is not None else self.config["LIMIT"]
        concurrency = concurrency if concurrency is not None else self.config["CONCURRENCY"]
        recheck = recheck if recheck is not None else self.config["RECHECK"]

        async def _run():
            await self.run_async(delete, limit, concurrency, recheck)
            if self._owns_client:
                await self.client.disconnect()

        if self._owns_client:
            with self.client.start():
                self.client.loop.run_until_complete(_run())
        else:
            self.client.loop.run_until_complete(_run())


if __name__ == "__main__":
    import sys
    from pathlib import Path

    config_path = Path('config.yaml')
    if config_path.exists():
        from utils.config_loader import load_config
        manager = TelegramLinkManager.from_config(load_config())
        manager.run()
    else:
        print("未找到 config.yaml，请复制 config.yaml.example 并填写参数，或使用: python main.py check-links")
        sys.exit(1)
