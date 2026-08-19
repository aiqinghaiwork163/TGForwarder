import os
import shutil
import requests
import random
import time
import json
import re
import asyncio
import urllib.parse
import logging
from datetime import datetime, timezone, timedelta
from telethon import TelegramClient, functions
from telethon.tl.types import MessageMediaPhoto, MessageEntityTextUrl, Channel, ChatInviteAlready, ChatInvite
from telethon.sessions import StringSession
from telethon.tl.functions.messages import GetHistoryRequest, CheckChatInviteRequest, ImportChatInviteRequest
from telethon.tl.functions.channels import JoinChannelRequest
from collections import deque

from utils.link_utils import LINK_PATTERN, URLS_KW, extract_links as _extract_links, normalize_link_key
from utils.config_loader import build_proxy
from utils.logging_setup import setup_logging
from utils.database import Database
from utils.tmdb import TMDBClient, enrich_message, parse_title

'''
代理参数说明:
# SOCKS5
proxy = (socks.SOCKS5,proxy_address,proxy_port,proxy_username,proxy_password)
# HTTP
proxy = (socks.HTTP,proxy_address,proxy_port,proxy_username,proxy_password))
# HTTP_PROXY
proxy=(socks.HTTP,http_proxy_list[1][2:],int(http_proxy_list[2]),proxy_username,proxy_password)
'''

class TGForwarder:
    def __init__(self, api_id, api_hash, string_session, channels_groups_monitor, forward_to_channel,
                 limit, replies_limit, include, exclude, check_replies, proxy, checknum, replacements,
                 message_md, channel_match, hyperlink_text, past_years, only_today, try_join,
                 incremental=True, history_file='history.json', log_level='INFO',
                 db_path='data/tgforwarder.db', tmdb_cfg=None, client=None):
        self.logger = setup_logging(log_level)
        self.db = Database(db_path)
        self.db.migrate_from_json(history_file)
        self.tmdb_cfg = tmdb_cfg or {}
        self.tmdb_client = None
        if self.tmdb_cfg.get("enabled") and self.tmdb_cfg.get("api_key"):
            self.tmdb_client = TMDBClient(
                self.tmdb_cfg["api_key"],
                self.tmdb_cfg.get("language", "zh-CN"),
            )
        self.urls_kw = URLS_KW
        self.checkbox = {
            "links": [], "link_keys": [], "sizes": [], "bot_links": {},
            "chat_forward_count_msg_id": {}, "channel_state": {},
            "today": "", "today_count": 0,
        }
        self.checknum = checknum
        self.today_count = 0
        self.history = history_file
        self.pattern = LINK_PATTERN
        self.incremental = incremental
        self.forward_count = 0
        self.api_id = api_id
        self.api_hash = api_hash
        self.string_session = string_session
        self.channels_groups_monitor = channels_groups_monitor
        self.forward_to_channel = forward_to_channel
        self.limit = limit
        self.replies_limit = replies_limit
        self.include = include
        # 获取当前中国时区时间
        self.china_timezone_offset = timedelta(hours=8)  # 中国时区是 UTC+8
        self.today = (datetime.utcnow() + self.china_timezone_offset).date()
        # 获取当前年份
        current_year = datetime.now().year - 2
        # 过滤今年之前的影视资源
        if not past_years:
            years_list = [str(year) for year in range(1895, current_year)]
            self.exclude = exclude+years_list
        else:
            self.exclude = exclude
        self.only_today = only_today
        self.hyperlink_text = hyperlink_text
        self.replacements = replacements
        self.message_md = message_md
        self.channel_match = channel_match
        self.check_replies = check_replies
        self.download_folder = 'downloads'
        self.try_join = try_join
        if client:
            self.client = client
        else:
            self.client = TelegramClient(StringSession(string_session), api_id, api_hash, proxy=proxy)
        self._owns_client = client is None

    @classmethod
    def from_config(cls, cfg: dict) -> "TGForwarder":
        """从 config.yaml 构建实例。"""
        tg = cfg["telegram"]
        mon = cfg.get("monitor", {})
        flt = cfg.get("filters", {})
        dedup = cfg.get("dedup", {})
        log_cfg = cfg.get("logging", {})
        db_cfg = cfg.get("database", {})
        return cls(
            api_id=tg["api_id"],
            api_hash=tg["api_hash"],
            string_session=tg["string_session"],
            channels_groups_monitor=mon.get("channels", []),
            forward_to_channel=tg["forward_to_channel"],
            limit=mon.get("limit", 20),
            replies_limit=mon.get("replies_limit", 1),
            include=flt.get("include", []),
            exclude=flt.get("exclude", []),
            check_replies=mon.get("check_replies", False),
            proxy=build_proxy(cfg.get("proxy")),
            checknum=dedup.get("checknum", 50),
            replacements=cfg.get("replacements", {}),
            message_md=cfg.get("message_md", ""),
            channel_match=cfg.get("channel_match", []),
            hyperlink_text=cfg.get("hyperlink_text", {}),
            past_years=mon.get("past_years", False),
            only_today=mon.get("only_today", True),
            try_join=mon.get("try_join", False),
            incremental=mon.get("incremental", True),
            history_file=cfg.get("history_file", "history.json"),
            log_level=log_cfg.get("level", "INFO"),
            db_path=db_cfg.get("path", "data/tgforwarder.db"),
            tmdb_cfg=cfg.get("tmdb"),
        )

    def _link_exists(self, url: str, link_keys: list) -> bool:
        return normalize_link_key(url) in link_keys

    def _record_link(self, url: str, links: list, link_keys: list):
        key = normalize_link_key(url)
        if key not in link_keys:
            link_keys.append(key)
            links.append(url)

    def random_wait(self, min_ms, max_ms):
        min_sec = min_ms / 1000
        max_sec = max_ms / 1000
        wait_time = random.uniform(min_sec, max_sec)
        time.sleep(wait_time)
    def contains(self, s, include):
        return any(k in s for k in include)
    def nocontains(self, s, exclude):
        return not any(k in s for k in exclude)
    def replace_targets(self, message: str):
        """
        根据用户自定义的替换规则替换文本内容
        参数:
        message (str): 需要替换的原始文本
        replacements (dict): 替换规则字典，键为目标替换词，值为要被替换的词语列表
        """
        # 遍历替换规则
        if self.replacements:
            for target_word, source_words in self.replacements.items():
                # 确保source_words是列表
                if isinstance(source_words, str):
                    source_words = [source_words]
                # 遍历每个需要替换的词
                for word in source_words:
                    # 使用替换方法，而不是正则
                    message = message.replace(word, target_word)
        message = message.strip()
        return message

    async def _prepare_text(self, text: str, source_channel: str = "") -> tuple[str, dict | None]:
        text = self.replace_targets(text)
        meta = None
        if self.tmdb_client:
            text, meta = await enrich_message(text, self.tmdb_client)
        return text, meta

    async def _index_sent(self, text: str, target: str, source: str, meta: dict | None, link_keys: list):
        title = (meta or {}).get("title") or parse_title(text) or ""
        self.db.index_resource(
            message_id=None,
            target_channel=target,
            title=title,
            raw_text=text[:2000],
            link_keys=link_keys[-5:] if link_keys else [],
            source_channel=source,
            year=(meta or {}).get("year", ""),
            rating=(meta or {}).get("rating"),
            genres=(meta or {}).get("genres", ""),
            tmdb_id=(meta or {}).get("tmdb_id"),
        )
    async def dispatch_channel(self, message, jumpLinks=[], F=False, source_channel=""):
        hit = False
        if self.channel_match:
            for rule in self.channel_match:
                if rule.get('include'):
                    if not self.contains(message.message or '', rule['include']):
                        continue
                if rule.get('exclude'):
                    if not self.nocontains(message.message or '', rule['exclude']):
                        continue
                await self.send(message, rule['target'], jumpLinks, F, source_channel)
                hit = True
            if not hit:
                await self.send(message, self.forward_to_channel, jumpLinks, F, source_channel)
        else:
            await self.send(message, self.forward_to_channel, jumpLinks, F, source_channel)
    async def send(self, message, target_chat_name, jumpLinks=[], F=False, source_channel=""):
        text = message.message or ""
        if jumpLinks and self.hyperlink_text:
            categorized_urls = self.categorize_urls(jumpLinks)
            for category, keywords in self.hyperlink_text.items():
                if categorized_urls.get(category):
                    slinks = categorized_urls[category]
                    url = "\n".join(slinks)
                    url += '\n@@'
                    for keyword in keywords:
                        if keyword in text:
                            text = text.replace(keyword, url)
                            break
        text = text.replace('@@', '')
        if self.nocontains(text, self.urls_kw):
            return
        text, meta = await self._prepare_text(text, source_channel)
        try:
            if message.media and isinstance(message.media, MessageMediaPhoto):
                if F:
                    media = await message.download_media(self.download_folder)
                    await self.client.send_file(target_chat_name, media, caption=text)
                else:
                    await self.client.send_message(target_chat_name, text, file=message.media)
            else:
                await self.client.send_message(target_chat_name, text)
            links_in_msg = _extract_links(text)
            keys = [normalize_link_key(u) for u in links_in_msg]
            await self._index_sent(text, target_chat_name, source_channel, meta, keys)
        except Exception as e:
            self.logger.error(f'发送消息失败: {e}')
    async def get_peer(self,client, channel_name):
        peer = None
        try:
            peer = await client.get_input_entity(channel_name)
        except Exception as e:
            self.logger.warning(f"Unexpected error: {e}")
        finally:
            return peer
    async def get_all_replies(self,chat_name, message):
        '''
        获取频道消息下的评论，有些视频/资源链接被放在评论中
        '''
        offset_id = 0
        all_replies = []
        peer = await self.get_peer(self.client, chat_name)
        if peer is None:
            return []
        while True:
            try:
                replies = await self.client(functions.messages.GetRepliesRequest(
                    peer=peer,
                    msg_id=message.id,
                    offset_id=offset_id,
                    offset_date=None,
                    add_offset=0,
                    limit=100,
                    max_id=0,
                    min_id=0,
                    hash=0
                ))
                all_replies.extend(replies.messages)
                if len(replies.messages) < 100:
                    break
                offset_id = replies.messages[-1].id
            except Exception as e:
                self.logger.warning(f"Unexpected error while fetching replies: {e.__class__.__name__} {e}")
                break
        return all_replies
    async def daily_forwarded_count(self,target_channel):
        # 统计今日更新
        # 设置中国时区偏移（UTC+8）
        china_offset = timedelta(hours=8)
        china_tz = timezone(china_offset)
        # 获取中国时区的今天凌晨
        now = datetime.now(china_tz)
        start_of_day_china = datetime.combine(now.date(), datetime.min.time())
        start_of_day_china = start_of_day_china.replace(tzinfo=china_tz)
        # 转换为 UTC 时间
        start_of_day_utc = start_of_day_china.astimezone(timezone.utc)
        # 获取今天第一条消息
        result = await self.client(GetHistoryRequest(
            peer=target_channel,
            limit=1,  # 只需要获取一条消息
            offset_date=start_of_day_utc,
            offset_id=0,
            add_offset=0,
            max_id=0,
            min_id=0,
            hash=0
        ))
        # 获取第一条消息的位置
        first_message_pos = result.offset_id_offset
        # 今日消息总数就是从第一条消息到最新消息的距离
        today_count = first_message_pos if first_message_pos else 0
        msg = f'**今日共更新【{today_count}】条资源 **\n\n'
        msg = msg + self.message_md
        return msg,today_count
    async def del_channel_forward_count_msg(self):
        # 删除消息
        chat_forward_count_msg_id = self.checkbox.get("chat_forward_count_msg_id")
        if not chat_forward_count_msg_id:
            return

        forward_to_channel_message_id = chat_forward_count_msg_id.get(self.forward_to_channel)
        if forward_to_channel_message_id:
            await self.client.delete_messages(self.forward_to_channel, [forward_to_channel_message_id])

        if self.channel_match:
            for rule in self.channel_match:
                target_channel_msg_id = chat_forward_count_msg_id.get(rule['target'])
                await self.client.delete_messages(rule['target'], [target_channel_msg_id])
    async def send_daily_forwarded_count(self):
        await self.del_channel_forward_count_msg()

        chat_forward_count_msg_id = {}
        msg,tc = await self.daily_forwarded_count(self.forward_to_channel)
        sent_message = await self.client.send_message(self.forward_to_channel, msg , parse_mode='md', link_preview=False)
        self.checkbox["today_count"] = tc
        # 置顶消息
        await self.client.pin_message(self.forward_to_channel, sent_message.id)
        await self.client.delete_messages(self.forward_to_channel, [sent_message.id + 1])

        chat_forward_count_msg_id[self.forward_to_channel] = sent_message.id
        if self.channel_match:
            for rule in self.channel_match:
                m,t = await self.daily_forwarded_count(rule['target'])
                sm = await self.client.send_message(rule['target'], m)
                self.checkbox["today_count"] = self.checkbox["today_count"] + t
                chat_forward_count_msg_id[rule['target']] = sm.id
                await self.client.pin_message(rule['target'], sm.id)
                await self.client.delete_messages(rule['target'], [sm.id+1])
        self.checkbox["chat_forward_count_msg_id"] = chat_forward_count_msg_id
    async def extract_links(self, text):
        """从文本中提取各种共享链接"""
        return _extract_links(text)

    async def redirect_url(self, message):
        links = []
        if not message.entities:
            return links
        for entity in message.entities:
            if isinstance(entity, MessageEntityTextUrl):
                if 'start' in entity.url:
                    url = await self.tgbot(entity.url)
                    if url:
                        links.append(url)
                elif 'https://telegra.ph/' in entity.url:
                    res = requests.get(entity.url, timeout=15)
                    html = res.content.decode('utf-8')
                    matches = await self.extract_links(html)
                    if matches:
                        links += matches
                elif self.nocontains(entity.url, self.urls_kw):
                    continue
                else:
                    url = urllib.parse.unquote(entity.url)
                    matches = re.findall(self.pattern, url, re.VERBOSE)
                    if matches:
                        links += matches
        return links
    async def tgbot(self,url):
        link = ''
        try:
            # 发送 /start 命令，带上自定义参数
            # 提取机器人用户名
            bot_username = url.split('/')[-1].split('?')[0]
            # 提取命令和参数
            query_string = url.split('?')[1]
            command, parameter = query_string.split('=')
            bot_links = self.checkbox["bot_links"]

            if bot_links.get(parameter):
                link = bot_links.get(parameter)
                return link
            else:
                await self.client.send_message(bot_username, f'/{command} {parameter}')
                # 等待一段时间以便消息到达
                await asyncio.sleep(2)
                # 获取最近的消息
                messages = await self.client.get_messages(bot_username, limit=1)  # 获取最近1条消息
                # print(f'消息内容: {messages[0].message}')
                message = messages[0].message
                links = re.findall(r'(https?://[^\s]+)', message)
                if links:
                    link = links[0]
                    bot_links[parameter] = link
                    self.checkbox["bot_links"] = bot_links
        except Exception as e:
            self.logger.error(f'TG_Bot error: {e}')
        return link
    async def reverse_async_iter(self, async_iter, limit):
        # 使用 deque 存储消息，方便从尾部添加
        buffer = deque(maxlen=limit)

        # 将消息填充到 buffer 中
        async for message in async_iter:
            buffer.append(message)

        # 从 buffer 的尾部开始逆序迭代
        for message in reversed(buffer):
            yield message
    async def delete_messages_in_time_range(self, chat_name, start_time_str, end_time_str):
        """
        删除指定聊天中在指定时间范围内的消息
        :param chat_name: 聊天名称或ID
        :param start_time_str: 开始时间字符串，格式为 "YYYY-MM-DD HH:MM"
        :param end_time_str: 结束时间字符串，格式为 "YYYY-MM-DD HH:MM"
        """
        # 中国时区偏移量（UTC+8）
        china_timezone_offset = timedelta(hours=8)
        china_timezone = timezone(china_timezone_offset)
        # 将字符串时间解析为带有时区信息的 datetime 对象
        start_time = datetime.strptime(start_time_str, "%Y-%m-%d %H:%M").replace(tzinfo=china_timezone)
        end_time = datetime.strptime(end_time_str, "%Y-%m-%d %H:%M").replace(tzinfo=china_timezone)
        # 获取聊天实体
        chat = await self.client.get_entity(chat_name)
        # 遍历消息
        async for message in self.client.iter_messages(chat):
            # 将消息时间转换为中国时区
            message_china_time = message.date.astimezone(china_timezone)
            # 判断消息是否在目标时间范围内
            if start_time <= message_china_time <= end_time:
                # print(f"删除消息：{message.text} (时间：{message_china_time})")
                await message.delete()  # 删除消息
    async def clear_main(self, start_time, end_time):
        await self.delete_messages_in_time_range(self.forward_to_channel, start_time, end_time)
    def clear(self, start_time=None, end_time=None):
        start_time = start_time or "2025-01-08 23:55"
        end_time = end_time or "2025-01-09 08:00"
        with self.client.start():
            self.client.loop.run_until_complete(self.clear_main(start_time, end_time))
    def categorize_urls(self,urls):
        """
        将 URL 按云盘厂商和磁力链接分类并存储到字典中
        """
        # 定义分类规则
        categories = {
            "magnet": ["magnet"],  # 磁力链接
            "ed2k": ["ed2k"], # ed2k
            "uc": ["drive.uc.cn"],  # UC
            "mobile": ["caiyun.139.com"],  # 移动
            "tianyi": ["cloud.189.cn"],  # 天翼
            "quark": ["pan.quark.cn"],  # 夸克
            "115": ["115cdn.com","115.com", "anxia.com"],  # 115
            "aliyun": ["alipan.com", "aliyundrive.com"],  # 阿里云
            "pikpak": ["mypikpak.com"],
            "baidu": ["pan.baidu.com"],
            "123": ['123684.com','123685.com','123912.com','123pan.com','123pan.cn','123592.com'],
            "others": []  # 其他
        }
        # 初始化结果字典
        result = {category: [] for category in categories}
        # 遍历 URL 列表
        for url in urls:
            # 处理磁力链接
            if url.startswith("magnet:"):
                result["magnet"].append(url)
                continue
            # ed2k
            elif url.startswith("ed2k:"):
                result["ed2k"].append(url)
                continue
            # 解析 URL
            parsed_url = urllib.parse.urlparse(url)
            domain = parsed_url.netloc.lower()  # 获取域名并转换为小写
            # 判断 URL 类型
            categorized = False
            for category, domains in categories.items():
                if any(pattern in domain for pattern in domains):
                    result[category].append(url)
                    categorized = True
                    break
            # 如果未分类，放入 "others"
            if not categorized:
                result["others"].append(url)
        return result
    async def deduplicate_links(self,links=[]):
        """
        删除聊天中重复链接的旧消息，只保留最新的消息
        """
        # 将 links 列表转换为集合，方便快速查找
        if links:
            target_keys = {normalize_link_key(u) for u in links}
        else:
            target_keys = {normalize_link_key(u) for u in self.checkbox.get('links', [])}
        if not target_keys:
            return
        chats = [self.forward_to_channel]
        if self.channel_match:
            for rule in self.channel_match:
                chats.append(rule['target'])
        for chat_name in chats:
            # 已经存在的link
            links_exist = set()
            # 用于批量删除的消息ID列表
            messages_to_delete = []
            # 获取聊天实体
            chat = await self.client.get_entity(chat_name)
            # 遍历消息
            messages = self.client.iter_messages(chat)
            async for message in messages:
                if message.message:
                    # 提取消息中的链接
                    links_in_message = re.findall(self.pattern, message.message, re.VERBOSE)
                    if not links_in_message:
                        continue  # 如果消息中没有链接，跳过
                    link = links_in_message[0]
                    link_key = normalize_link_key(link)
                    if link_key in target_keys:
                        if link_key in links_exist:
                            messages_to_delete.append(message.id)
                        else:
                            links_exist.add(link_key)
            # 批量删除旧消息
            if messages_to_delete:
                self.logger.info(f"【{chat_name}】删除 {len(messages_to_delete)} 条历史重复消息")
                await self.client.delete_messages(chat, messages_to_delete)
    async def checkhistory(self):
        '''检索历史消息用于过滤去重'''
        links = []
        link_keys = []
        sizes = []
        self.checkbox = self.db.load_forwarder_state()
        if self.checkbox.get('today') == datetime.now().strftime("%Y-%m-%d"):
            links = self.checkbox.get('links', [])
            link_keys = self.checkbox.get('link_keys', [])
            if not link_keys and links:
                link_keys = [normalize_link_key(u) for u in links]
            sizes = self.checkbox.get('sizes', [])
        else:
            self.checkbox['links'] = []
            self.checkbox['link_keys'] = []
            self.checkbox['sizes'] = []
            self.checkbox["bot_links"] = {}
            self.checkbox["today_count"] = 0
        if 'channel_state' not in self.checkbox:
            self.checkbox['channel_state'] = {}
        self.today_count = self.checkbox.get('today_count') if self.checkbox.get('today_count') else self.checknum
        self.checknum = self.checknum if self.today_count < self.checknum else self.today_count
        chat = await self.client.get_entity(self.forward_to_channel)
        messages = self.client.iter_messages(chat, limit=self.checknum)
        async for message in messages:
            if hasattr(message.document, 'mime_type'):
                sizes.append(message.document.size)
            if message.message:
                matches = re.findall(self.pattern, message.message, re.VERBOSE)
                if matches:
                    self._record_link(matches[0], links, link_keys)
        return links, link_keys, list(set(sizes))
    async def join_channels(self):
        for channel in self.channels_groups_monitor:
            if '|' in channel:
                channel = channel.split('|')[0]
            if 'https://t.me/' in channel:
                # 提取邀请链接中的 hash
                invite_hash = channel.split("/")[-1].lstrip("+")
                # 检查邀请链接信息
                try:
                    invite = await self.client(CheckChatInviteRequest(invite_hash))
                except Exception as e:
                    self.logger.error(f"检查邀请链接失败: {e}")
                    return None
                if isinstance(invite, ChatInviteAlready):
                    chat = invite.chat
                    if isinstance(chat, Channel):
                        channel_id = chat.id
                        full_channel_id = f"-100{channel_id}"
                        self.logger.info(f"{channel} 频道: {chat.title}, ID: {full_channel_id}")
                        return full_channel_id
                    else:
                        self.logger.warning("chat 对象不是 Channel 类型")
                        return None
                elif isinstance(invite, ChatInvite):
                    if getattr(invite, "channel", False) and getattr(invite, "broadcast", False):
                        self.logger.info(f"未加入的私有频道: {invite.title}")
                        try:
                            result = await self.client(ImportChatInviteRequest(invite_hash))
                            if hasattr(result, "chats") and result.chats:
                                chat = result.chats[0]
                                if isinstance(chat, Channel):
                                    channel_id = chat.id
                                    full_channel_id = f"-100{channel_id}"
                                    self.logger.info(f"已加入 {channel}: {chat.title}, ID: {full_channel_id}")
                                    return full_channel_id
                                else:
                                    self.logger.warning("加入后未找到 Channel 对象")
                                    return None
                            else:
                                self.logger.warning("加入后未返回频道信息")
                                return None
                        except Exception as e:
                            self.logger.error(f"加入频道失败: {e}")
                            return None
                    else:
                        self.logger.warning("不是私有频道邀请链接，或无权限")
                        return None
                else:
                    self.logger.warning("尚未加入频道")
                    return None
            else:
                try:
                    await self.client(JoinChannelRequest(channel))
                    self.logger.info(f"成功加入频道/群组: {channel}")
                except Exception as e:
                    self.logger.error(f"加入频道/群组失败: {channel}, 错误: {e}")
    def run_join(self):
        with self.client.start():
            self.client.loop.run_until_complete(self.join_channels())
    async def copy_and_send_message(self, source_chat, target_chat, message_id, text=''):
        """
        复制消息内容并发送新消息
        :param source_chat: 源聊天（可以是用户名、ID 或输入实体）
        :param target_chat: 目标聊天（可以是用户名、ID 或输入实体）
        :param message_id: 要复制的消息 ID
        """
        try:
            # 获取原始消息
            message = await self.client.get_messages(source_chat, ids=message_id)
            if not message:
                self.logger.warning("未找到消息")
                return
            await self.client.send_message(
                target_chat,
                text,
                file=message.media
            )
        except Exception as e:
            self.logger.error(f"操作失败: {e}")
    async def forward_messages(self, chat_name, limit, hlinks, hlink_keys, hsizes, reply=False, reply_limit=None):
        links = hlinks
        link_keys = hlink_keys
        sizes = hsizes
        F = False
        channel_key = chat_name.split('|')[0] if '|' in chat_name else chat_name
        last_id = self.checkbox.get('channel_state', {}).get(channel_key, 0) if self.incremental else 0
        mode_desc = f"增量(min_id={last_id})" if self.incremental and last_id else f"最近{limit}条"
        self.logger.info(f'监控频道【{channel_key}】，模式={mode_desc}，去重库={len(link_keys)}条')
        processed_max_id = last_id
        try:
            chat = None
            if 'https://t.me/' in chat_name:
                invite_hash = chat_name.split("/")[-1].lstrip("+")
                try:
                    invite = await self.client(CheckChatInviteRequest(invite_hash))
                    chat = invite.chat
                except Exception as e:
                    self.logger.error(f"检查邀请链接失败: {e}")
                    return links, link_keys, sizes
            else:
                chat = await self.client.get_entity(chat_name)
            F = chat.noforwards

            if self.incremental and last_id:
                message_iter = self.client.iter_messages(chat, min_id=last_id, reverse=True, limit=limit)
            else:
                messages = self.client.iter_messages(chat, limit=limit, reverse=False)
                message_iter = self.reverse_async_iter(messages, limit=limit)

            async for message in message_iter:
                processed_max_id = max(processed_max_id, message.id)
                if self.only_today:
                    message_china_time = message.date + self.china_timezone_offset
                    if message_china_time.date() != self.today:
                        continue
                self.random_wait(200, 1000)
                if message.media:
                    if hasattr(message.document, 'mime_type') and self.contains(message.document.mime_type, 'video') and self.nocontains(message.message, self.exclude):
                        size = message.document.size
                        text = message.message or ''
                        if message.message:
                            jumpLinks = await self.redirect_url(message)
                            if jumpLinks and self.hyperlink_text:
                                categorized_urls = self.categorize_urls(jumpLinks)
                                for category, keywords in self.hyperlink_text.items():
                                    if categorized_urls.get(category):
                                        url = categorized_urls[category][0]
                                    else:
                                        continue
                                    for keyword in keywords:
                                        if keyword in text:
                                            text = text.replace(keyword, url)
                        if size not in sizes:
                            await self.copy_and_send_message(chat_name, self.forward_to_channel, message.id, text)
                            sizes.append(size)
                            self.forward_count += 1
                        else:
                            self.logger.debug(f'视频已存在，size: {size}')
                    elif self.contains(message.message or '', self.include) and message.message and self.nocontains(message.message, self.exclude):
                        jumpLinks = await self.redirect_url(message)
                        matches = re.findall(self.pattern, message.message, re.VERBOSE) if self.contains(message.message, self.urls_kw) else []
                        if matches or jumpLinks:
                            link = jumpLinks[0] if jumpLinks else matches[0]
                            if not self._link_exists(link, link_keys):
                                await self.dispatch_channel(message, jumpLinks, F, channel_key)
                                self.forward_count += 1
                                self._record_link(link, links, link_keys)
                            else:
                                self.logger.debug(f'链接已存在: {normalize_link_key(link)}')
                    if (self.check_replies or reply) and message.message:
                        replies = await self.get_all_replies(chat_name, message)
                        replies = replies[-reply_limit:] if reply_limit else replies[-self.replies_limit:]
                        for r in replies:
                            jumpLinks = await self.redirect_url(r)
                            matches = re.findall(self.pattern, r.message, re.VERBOSE) if self.contains(r.message or '', self.urls_kw) else []
                            if matches or jumpLinks:
                                link = jumpLinks[0] if jumpLinks else matches[0]
                                if not self._link_exists(link, link_keys):
                                    await self.dispatch_channel(r, jumpLinks, F, channel_key)
                                    self.forward_count += 1
                                    self._record_link(link, links, link_keys)
                                else:
                                    self.logger.debug(f'链接已存在: {normalize_link_key(link)}')
                elif message.message:
                    if self.contains(message.message, self.include) and self.nocontains(message.message, self.exclude):
                        jumpLinks = await self.redirect_url(message)
                        matches = re.findall(self.pattern, message.message, re.VERBOSE) if self.contains(message.message, self.urls_kw) else []
                        if matches or jumpLinks:
                            link = jumpLinks[0] if jumpLinks else matches[0]
                            if not self._link_exists(link, link_keys):
                                await self.dispatch_channel(message, jumpLinks, source_channel=channel_key)
                                self.forward_count += 1
                                self._record_link(link, links, link_keys)
                            else:
                                self.logger.debug(f'链接已存在: {normalize_link_key(link)}')

            if self.incremental and processed_max_id > last_id:
                self.checkbox.setdefault('channel_state', {})[channel_key] = processed_max_id

            self.logger.info(f"从 {channel_key} 转发 {self.forward_count} 条资源")
            return list(set(links)), link_keys, list(set(sizes))
        except Exception as e:
            self.logger.error(f"从 {channel_key} 转发失败: {e}")
            return links, link_keys, sizes
    async def main(self):
        reply = False
        reply_limit = None
        start_time = time.time()
        links, link_keys, sizes = await self.checkhistory()
        if not os.path.exists(self.download_folder):
            os.makedirs(self.download_folder)
        total_forwarded = 0
        for chat_name in self.channels_groups_monitor:
            limit = self.limit
            reply = False
            reply_limit = None
            if '|' in chat_name:
                limit = chat_name.split('|')[1]
                chat_name = chat_name.split('|')[0]
                if 'reply_' in limit:
                    reply = True
                    reply_limit = int(limit.split('_')[1])
                    limit = int(limit.split('_')[2]) if len(limit.split('_')) == 3 else self.limit
                limit = int(limit)

            self.forward_count = 0
            try:
                links, link_keys, sizes = await self.forward_messages(
                    chat_name, limit, links, link_keys, sizes, reply, reply_limit
                )
                total_forwarded += self.forward_count
            except Exception as e:
                self.logger.error(f"处理频道 {chat_name} 异常: {e}")
                continue
        await self.send_daily_forwarded_count()
        self.checkbox['links'] = list(set(links))[-self.checkbox["today_count"]:]
        self.checkbox['link_keys'] = list(dict.fromkeys(link_keys))[-self.checkbox["today_count"]:]
        self.checkbox['sizes'] = list(set(sizes))[-self.checkbox["today_count"]:]
        self.checkbox['today'] = datetime.now().strftime("%Y-%m-%d")
        self.db.save_forwarder_state(self.checkbox)
        if os.path.exists(self.download_folder):
            shutil.rmtree(self.download_folder)
        await self.deduplicate_links()
        if self._owns_client:
            await self.client.disconnect()
        elapsed = time.time() - start_time
        self.logger.info(f'任务完成，共转发 {total_forwarded} 条，耗时 {elapsed:.1f} 秒')
    def run(self):
        if self._owns_client:
            with self.client.start():
                if self.try_join:
                    self.client.loop.run_until_complete(self.join_channels())
                self.client.loop.run_until_complete(self.main())
        else:
            if self.try_join:
                self.client.loop.run_until_complete(self.join_channels())
            self.client.loop.run_until_complete(self.main())

if __name__ == '__main__':
    import sys
    from pathlib import Path

    config_path = Path('config.yaml')
    if config_path.exists():
        from utils.config_loader import load_config
        TGForwarder.from_config(load_config()).run()
    else:
        print("未找到 config.yaml，请复制 config.yaml.example 并填写参数，或使用: python main.py forward")
        sys.exit(1)

