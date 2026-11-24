import base64
import json
import unittest

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from app.yeguo import PLAY_IV, PLAY_KEY, YeguoScraper, decrypt_play_payload


class YeguoTests(unittest.TestCase):
    def test_decrypt_play_payload(self):
        payload = {"data": {"video_title": "测试", "total_serial": 2}}
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        padder = padding.PKCS7(128).padder()
        padded = padder.update(raw) + padder.finalize()
        encryptor = Cipher(algorithms.AES(PLAY_KEY), modes.CBC(PLAY_IV)).encryptor()
        ciphertext = encryptor.update(padded) + encryptor.finalize()
        encoded = base64.b64encode(ciphertext).decode()
        self.assertEqual(decrypt_play_payload(encoded), payload)

    def test_raw_site_id(self):
        self.assertEqual(YeguoScraper.raw_id("yeguo_3395"), "3395")
        self.assertEqual(YeguoScraper.raw_id("3395"), "3395")

    def test_parse_listing_card(self):
        html = """
        <html><body>
          <article>
            <a href="/drama/detail/123/">
              <img data-src="https://pic.example/cover.png" alt="测试剧">
              <span>完结</span><span>热门</span><span>18W播放</span>
              <h3>测试剧</h3>
            </a>
            <div><a href="/tag/都市">都市</a><a href="/tag/逆袭">逆袭</a></div>
          </article>
          <div>共 21 条</div>
        </body></html>
        """
        scraper = YeguoScraper.__new__(YeguoScraper)
        items, pages = scraper.parse_listing(html, "yeguo")
        self.assertEqual(pages, 2)
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item["site_id"], "yeguo_123")
        self.assertEqual(item["source"], "yeguo")
        self.assertEqual(item["title"], "测试剧")
        self.assertEqual(item["status_text"], "完结")
        self.assertEqual(item["views"], "18W")
        self.assertEqual(item["tags"], ["都市", "逆袭"])


if __name__ == "__main__":
    unittest.main()
