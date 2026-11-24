import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from app.hlsproxy import HlsProxy


class FakeResponse:
    status_code = 200
    headers = {"Content-Type": "video/mp2t"}

    def iter_content(self, chunk_size=65536):
        yield b"data"

    def close(self):
        pass


class HlsProxyTests(unittest.TestCase):
    def setUp(self):
        self.proxy = HlsProxy(None, None, None)
        self.url_a = "https://a.qnlycc.cn/seg-a.ts"
        self.url_b = "https://a.qnlycc.cn/seg-b.ts"
        self.token_a = parse_qs(urlparse(self.proxy.proxy_url(
            self.url_a, {"X-Test": "one", "Referer": "https://huangguoai.com/"}
        )).query)["t"][0]
        self.token_b = parse_qs(urlparse(self.proxy.proxy_url(
            self.url_b, {"X-Test": "two", "Referer": "https://huangguoai.com/"}
        )).query)["t"][0]

    def test_tokens_are_valid_and_tamper_proof(self):
        self.assertTrue(self.proxy.verify(self.url_a, self.token_a))
        self.assertFalse(self.proxy.verify(self.url_a, self.token_b))
        self.assertFalse(self.proxy.verify(self.url_a, "bad-token"))

    def test_only_allowed_cdn_hosts(self):
        self.assertTrue(self.proxy._allowed("https://x.qnlycc.cn/a.ts"))
        self.assertTrue(self.proxy._allowed("https://huangguoai.com/a.ts"))
        self.assertFalse(self.proxy._allowed("https://evil.example/a.ts"))

    def test_each_token_keeps_its_own_cdn_headers(self):
        calls = []

        def fake_get(url, headers=None, stream=True, timeout=60):
            calls.append(headers)
            return FakeResponse()

        with patch("app.hlsproxy.requests.get", side_effect=fake_get):
            self.proxy.stream(self.url_a, self.token_a)
            self.proxy.stream(self.url_b, self.token_b)

        self.assertEqual(calls[0]["X-Test"], "one")
        self.assertEqual(calls[1]["X-Test"], "two")


if __name__ == "__main__":
    unittest.main()
