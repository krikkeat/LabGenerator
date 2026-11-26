"""ทดสอบตัวแชร์ handout — ใน zip มี private key จึงต้องแน่ใจว่าเข้าถึงผิดคนไม่ได้"""
import os
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
os.environ.setdefault("LABCTL_DB_PATH", str(Path(tempfile.gettempdir()) / "labctl-test.db"))

import labctl  # noqa: E402
from backend import handoutserver as hs  # noqa: E402

LAB = "sharetest"
USERS = ["user01", "user02"]


class TestHandoutShare(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = labctl.HANDOUTS_DIR / LAB
        cls.dir.mkdir(parents=True, exist_ok=True)
        for u in USERS:
            (cls.dir / f"{LAB}-{u}.zip").write_bytes(b"PK\x03\x04" + u.encode())
        cls.share = hs.HandoutShare(LAB, USERS, port=8971, ttl_hours=1).start()
        time.sleep(0.4)

    @classmethod
    def tearDownClass(cls):
        cls.share.expires_at = 0
        for u in USERS:
            (cls.dir / f"{LAB}-{u}.zip").unlink(missing_ok=True)
        try:
            cls.dir.rmdir()
        except OSError:
            pass

    def _get(self, path):
        return urllib.request.urlopen(f"http://127.0.0.1:8971{path}", timeout=5)

    def test_token_ของตัวเองโหลดได้(self):
        tok = self.share.tokens["user01"]
        with self._get(f"/h/{tok}") as r:
            body = r.read()
        self.assertEqual(r.status, 200)
        self.assertIn(b"user01", body)

    def test_token_คนละคนได้ไฟล์คนละไฟล์(self):
        with self._get(f"/h/{self.share.tokens['user02']}") as r:
            self.assertIn(b"user02", r.read())

    def test_token_มั่วเข้าไม่ได้(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self._get("/h/" + "x" * 32)
        self.assertEqual(cm.exception.code, 403)

    def test_ขอ_path_อื่นไม่ได้(self):
        for path in ("/", "/h/", "/../../etc/passwd", "/h/../../labs.db"):
            with self.assertRaises(urllib.error.HTTPError):
                self._get(path)

    def test_token_ยาวพอและไม่ซ้ำกัน(self):
        toks = list(self.share.tokens.values())
        self.assertEqual(len(set(toks)), len(toks))
        for t in toks:
            self.assertGreaterEqual(len(t), 24)

    def test_บันทึกว่าใครโหลดแล้ว(self):
        self.share.downloaded.clear()
        self._get(f"/h/{self.share.tokens['user01']}").read()
        self.assertIn("user01", self.share.downloaded)
        self.assertNotIn("user02", self.share.downloaded)

    def test_หมดอายุแล้วปิดตัวเองจริง(self):
        """หมดเวลาแล้วต้องเข้าไม่ได้อีก — ใน zip มี private key ปล่อยค้างไว้ไม่ได้"""
        share = hs.HandoutShare(LAB, USERS, port=8972, ttl_hours=1).start()
        time.sleep(0.4)
        tok = share.tokens["user01"]
        with urllib.request.urlopen(f"http://127.0.0.1:8972/h/{tok}", timeout=5) as r:
            self.assertEqual(r.status, 200)      # ก่อนหมดอายุยังโหลดได้
        share.expires_at = time.time() - 1
        deadline = time.time() + 5
        while time.time() < deadline:
            try:
                urllib.request.urlopen(f"http://127.0.0.1:8972/h/{tok}", timeout=2).read()
            except (urllib.error.URLError, urllib.error.HTTPError, OSError):
                return                            # ปิดพอร์ตหรือปฏิเสธแล้ว = ผ่าน
            time.sleep(0.3)
        self.fail("ยังโหลดได้อยู่ทั้งที่หมดอายุแล้ว")


class TestLocalIp(unittest.TestCase):
    def test_คืน_ip_ที่ใช้ได้(self):
        ip = hs.local_ip()
        self.assertRegex(ip, r"^\d{1,3}(\.\d{1,3}){3}$")


if __name__ == "__main__":
    unittest.main()
