"""链接工具单元测试。"""

import unittest
from utils.link_utils import extract_links, extract_share_id, normalize_link_key


class TestLinkUtils(unittest.TestCase):
    def test_extract_quark_link(self):
        text = "资源：https://pan.quark.cn/s/abc123def 请自取"
        links = extract_links(text)
        self.assertTrue(any("pan.quark.cn" in l for l in links))

    def test_extract_magnet(self):
        text = "magnet:?xt=urn:btih:ABCDEF1234567890&dn=test"
        links = extract_links(text)
        self.assertEqual(len(links), 1)
        self.assertTrue(links[0].startswith("magnet:"))

    def test_normalize_same_share_different_query(self):
        url1 = "https://pan.quark.cn/s/abc123?utm=foo"
        url2 = "https://pan.quark.cn/s/abc123?utm=bar"
        self.assertEqual(normalize_link_key(url1), normalize_link_key(url2))

    def test_extract_share_id_quark(self):
        url = "https://pan.quark.cn/s/abc123"
        share_id, service = extract_share_id(url)
        self.assertEqual(share_id, "abc123")
        self.assertEqual(service, "quark")

    def test_normalize_magnet_btih(self):
        url = "magnet:?xt=urn:btih:ABCDEF&tr=tracker"
        key = normalize_link_key(url)
        self.assertEqual(key, "magnet:abcdef")


if __name__ == "__main__":
    unittest.main()
