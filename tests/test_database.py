"""数据库与 TMDB 工具测试。"""

import json
import os
import tempfile
import unittest

from utils.database import Database
from utils.tmdb import parse_title, parse_year, TMDBClient


class TestDatabase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db = Database(self.tmp.name)

    def tearDown(self):
        os.unlink(self.tmp.name)

    def test_forwarder_state_roundtrip(self):
        state = {
            "links": ["http://a.com"],
            "link_keys": ["quark:abc"],
            "sizes": [100],
            "bot_links": {},
            "chat_forward_count_msg_id": {},
            "channel_state": {"TestChannel": 42},
            "today": "2026-08-19",
            "today_count": 5,
        }
        self.db.save_forwarder_state(state)
        loaded = self.db.load_forwarder_state()
        self.assertEqual(loaded["channel_state"]["TestChannel"], 42)
        self.assertEqual(loaded["today_count"], 5)

    def test_link_check_messages(self):
        self.db.upsert_link_check_message(100, ["https://pan.quark.cn/s/abc"], False)
        self.db.update_link_check_invalid(100, ["https://pan.quark.cn/s/abc"])
        msgs = self.db.get_all_link_check_messages()
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0]["invalid_urls"], ["https://pan.quark.cn/s/abc"])

    def test_search_resources(self):
        self.db.index_resource(
            message_id=1, target_channel="ch", title="沙丘2",
            raw_text="沙丘2 2024", link_keys=["quark:abc"],
        )
        results = self.db.search_resources("沙丘")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["title"], "沙丘2")

    def test_migrate_from_json(self):
        history = self.tmp.name + ".history.json"
        with open(history, "w") as f:
            json.dump({"links": [], "channel_state": {"X": 1}, "today": "2026-01-01", "today_count": 0}, f)
        self.db.migrate_from_json(history)
        self.assertFalse(os.path.exists(history))
        self.assertTrue(os.path.exists(history + ".bak"))

    def test_pause_setting(self):
        self.assertFalse(self.db.is_paused())
        self.db.set_paused(True)
        self.assertTrue(self.db.is_paused())


class TestTMDB(unittest.TestCase):
    def test_parse_title_book_marks(self):
        self.assertEqual(parse_title("《沙丘2》夸克网盘"), "沙丘2")

    def test_parse_title_field(self):
        self.assertEqual(parse_title("片名：星际穿越\n链接：xxx"), "星际穿越")

    def test_parse_year(self):
        self.assertEqual(parse_year("沙丘2 (2024)"), "2024")

    def test_format_header(self):
        client = TMDBClient("dummy")
        header = client.format_header({"title": "沙丘2", "year": "2024", "rating": 8.7})
        self.assertIn("沙丘2", header)
        self.assertIn("8.7", header)


if __name__ == "__main__":
    unittest.main()
