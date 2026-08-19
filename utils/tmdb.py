"""TMDB 元数据补全。"""

import re
import httpx

TITLE_PATTERNS = [
    re.compile(r"[《「【](.+?)[》」】]"),
    re.compile(r"(?:片名|名称|剧名|资源)[：:]\s*(.+?)(?:\n|$)"),
    re.compile(r"^(.{2,40}?)(?:\s*\(\d{4}\)|\s*\d{4})?", re.MULTILINE),
]


def parse_title(text: str) -> str | None:
    """从消息文本中解析影视标题。"""
    if not text:
        return None
    for pattern in TITLE_PATTERNS:
        m = pattern.search(text.strip())
        if m:
            title = m.group(1).strip()
            title = re.sub(r"[：:|\s]+$", "", title)
            if len(title) >= 2:
                return title
    first_line = text.strip().split("\n")[0].strip()
    if 2 <= len(first_line) <= 40:
        return first_line
    return None


def parse_year(text: str) -> str | None:
    m = re.search(r"(?:19|20)\d{2}", text or "")
    return m.group(0) if m else None


class TMDBClient:
    BASE = "https://api.themoviedb.org/3"

    def __init__(self, api_key: str, language: str = "zh-CN", timeout: float = 8.0):
        self.api_key = api_key
        self.language = language
        self.timeout = timeout

    async def search(self, title: str, year: str | None = None) -> dict | None:
        if not self.api_key or not title:
            return None
        params = {"api_key": self.api_key, "query": title, "language": self.language}
        if year:
            params["year"] = year
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.get(f"{self.BASE}/search/multi", params=params)
                resp.raise_for_status()
                results = resp.json().get("results", [])
                if not results:
                    return None
                item = results[0]
                media_type = item.get("media_type", "movie")
                rating = item.get("vote_average")
                genres = ""
                title_out = item.get("title") or item.get("name") or title
                year_out = (item.get("release_date") or item.get("first_air_date") or "")[:4]
                return {
                    "tmdb_id": item.get("id"),
                    "title": title_out,
                    "year": year_out or year,
                    "rating": rating,
                    "genres": genres,
                    "media_type": media_type,
                }
        except Exception:
            return None

    def format_header(self, meta: dict) -> str:
        """生成消息头部元数据块。"""
        title = meta.get("title", "")
        year = meta.get("year", "")
        rating = meta.get("rating")
        parts = [f"🎬 {title}"]
        if year:
            parts[0] += f" ({year})"
        if rating:
            parts.append(f"⭐{rating:.1f}")
        return " ".join(parts) + "\n\n"


async def enrich_message(text: str, client: TMDBClient | None) -> tuple[str, dict | None]:
    """尝试补全消息元数据，返回 (新文本, meta)。"""
    if not client:
        return text, None
    title = parse_title(text)
    if not title:
        return text, None
    year = parse_year(text)
    meta = await client.search(title, year)
    if not meta:
        return text, None
    header = client.format_header(meta)
    if header.strip() in text:
        return text, meta
    return header + text, meta
