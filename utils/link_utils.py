"""网盘/磁力链接提取与规范化去重工具。"""

import re
import urllib.parse

LINK_PATTERN = r'''
    (?:链接：\s*)?
    (?!https?://t\.me)
    (?!https?://image\.tmdb\.org)
    (
      magnet:\?xt=urn:btih:[a-zA-Z0-9]+|
      ed2k://\|file\|[^|]+\|\d+\|[A-Fa-f0-9]+\|/?|
      https?://(?:[\w.-]+\.)+[\w]+
      (?:
        /(?:s|share|m/i|t|web/share)
        (?:/[\w.-]+)*
        (?:\?(?:[\w]+=[\w:]+&?)*)?
        [^\s'"<>()]+
      )
    )
    '''

NET_DISK_PATTERNS = {
    "uc": {"domains": ["drive.uc.cn"], "pattern": r"https?://drive\.uc\.cn/s/([a-zA-Z0-9]+)"},
    "aliyun": {
        "domains": ["aliyundrive.com", "alipan.com"],
        "pattern": r"https?://(?:www\.)?(?:aliyundrive|alipan)\.com/s/([a-zA-Z0-9]+)",
    },
    "quark": {"domains": ["pan.quark.cn"], "pattern": r"https?://(?:www\.)?pan\.quark\.cn/s/([a-zA-Z0-9]+)"},
    "115": {
        "domains": ["115.com", "115cdn.com", "anxia.com"],
        "pattern": r"https?://(?:www\.)?(?:115|115cdn|anxia)\.com/s/([a-zA-Z0-9]+)",
    },
    "baidu": {
        "domains": ["pan.baidu.com", "yun.baidu.com"],
        "pattern": r"https?://(?:[a-z]+\.)?(?:pan|yun)\.baidu\.com/(?:s/|share/init\?surl=)([a-zA-Z0-9_-]+)(?:\?|$)",
    },
    "pikpak": {"domains": ["mypikpak.com"], "pattern": r"https?://(?:www\.)?mypikpak\.com/s/([a-zA-Z0-9]+)"},
    "123": {
        "domains": ["123684.com", "123685.com", "123912.com", "123pan.com", "123pan.cn", "123592.com"],
        "pattern": r"https?://(?:www\.)?(?:123684|123685|123912|123pan|123pan\.cn|123592)\.com/s/([a-zA-Z0-9-]+)",
    },
    "tianyi": {"domains": ["cloud.189.cn"], "pattern": r"https?://cloud\.189\.cn/(?:t/|web/share\?code=)([a-zA-Z0-9]+)"},
}

URLS_KW = [
    "ed2k", "magnet", "drive.uc.cn", "caiyun.139.com", "cloud.189.cn", "pan.quark.cn",
    "115cdn.com", "115.com", "anxia.com", "alipan.com", "aliyundrive.com", "pan.baidu.com",
    "mypikpak.com", "123684.com", "123685.com", "123912.com", "123pan.com", "123pan.cn", "123592.com",
]


def extract_links(text: str) -> list[str]:
    """从文本中提取资源链接，去重保序。"""
    if not text:
        return []
    matches = re.findall(LINK_PATTERN, text, re.VERBOSE)
    seen = set()
    result = []
    for match in matches:
        if match not in seen:
            seen.add(match)
            result.append(match)
    return result


def extract_share_id(url: str) -> tuple[str | None, str | None]:
    """从链接中提取 (share_id, service)。"""
    for service, cfg in NET_DISK_PATTERNS.items():
        if any(domain in url for domain in cfg["domains"]):
            match = re.search(cfg["pattern"], url)
            if match:
                return match.group(1), service
    return None, None


def normalize_link_key(url: str) -> str:
    """
    将链接规范化为去重键。
    网盘链接按 share_id + 类型；磁力/ed2k 按协议前缀归一化。
    """
    url = urllib.parse.unquote(url.strip())
    if url.startswith("magnet:"):
        btih_match = re.search(r"btih:([a-fA-F0-9]+)", url, re.IGNORECASE)
        if btih_match:
            return f"magnet:{btih_match.group(1).lower()}"
        return url.split("&")[0].lower()
    if url.startswith("ed2k:"):
        return url.split("|")[4] if url.count("|") >= 4 else url
    share_id, service = extract_share_id(url)
    if share_id and service:
        return f"{service}:{share_id}"
    parsed = urllib.parse.urlparse(url)
    return f"url:{parsed.netloc}{parsed.path}".lower().rstrip("/")


def extract_net_disk_urls(message_text: str, domains: list[str] | None = None) -> list[str]:
    """从消息文本中提取网盘链接（供 LinkChecker 使用）。"""
    if not message_text:
        return []
    domains = domains or [d for cfg in NET_DISK_PATTERNS.values() for d in cfg["domains"]]
    url_pattern = r"https?://[^\s]+"
    urls = re.findall(url_pattern, message_text)
    return [url for url in urls if any(domain in url for domain in domains)]
