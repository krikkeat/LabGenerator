"""ทดสอบ doctor และคำเตือนเรื่องอายุคีย์ — ส่วนที่ตัดสินใจล้วน ๆ ไม่แตะ AWS"""
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
os.environ.setdefault("LABCTL_DB_PATH", str(Path(tempfile.gettempdir()) / "labctl-test.db"))

import labctl  # noqa: E402
from backend import aws  # noqa: E402


class TestSyncedFolder(unittest.TestCase):
    """ตรวจผิด = ปล่อยให้ติดตั้งในที่ที่ state หายได้และ agent ทำงานไม่ได้"""

    def test_โฟลเดอร์ที่ซิงก์ถูกจับได้(self):
        for p in ("/Users/x/Library/CloudStorage/OneDrive-Corp/งาน/lab",
                  "/Users/x/Library/Mobile Documents/com~apple~CloudDocs/lab",
                  "/Users/x/Dropbox/lab-generator",
                  "/Users/x/Google Drive/My Drive/lab"):
            self.assertTrue(labctl.is_synced_folder(p), p)

    def test_โฟลเดอร์ปกติไม่ถูกจับผิด(self):
        for p in ("/Users/x/lab-generator", "/opt/lab", "/Users/x/work/onedrive-notes"):
            self.assertFalse(labctl.is_synced_folder(p), p)


class TestCredentialWarning(unittest.TestCase):
    """เตือนเมื่อ lab จะอยู่นานกว่าคีย์ — เคสที่ทำให้ VM ค้างข้ามคืนมาแล้ว"""

    def setUp(self):
        self.saved = aws.credential_info

    def tearDown(self):
        aws.credential_info = self.saved

    def _info(self, temporary=True, age_hours=1.0, session=8.0):
        left = (session - age_hours) if temporary else None
        aws.credential_info = lambda: {
            "stored_at": time.time() - age_hours * 3600, "age_hours": age_hours,
            "temporary": temporary, "method": "profile:labctl",
            "assumed_session_hours": session, "estimated_hours_left": left}

    def test_ttl_ยาวกว่าคีย์ต้องเตือน(self):
        self._info(age_hours=6)          # เหลือ ~2 ชม.
        warn, left = aws.ttl_outlives_credentials(8)
        self.assertTrue(warn)
        self.assertAlmostEqual(left, 2.0, places=5)

    def test_ttl_สั้นกว่าคีย์ไม่ต้องเตือน(self):
        self._info(age_hours=1)          # เหลือ ~7 ชม.
        self.assertFalse(aws.ttl_outlives_credentials(4)[0])

    def test_คีย์ถาวรไม่ต้องเตือน(self):
        self._info(temporary=False)
        self.assertFalse(aws.ttl_outlives_credentials(300)[0])

    def test_ไม่ตั้ง_ttl_ไม่ต้องเตือน(self):
        self._info(age_hours=7.9)
        self.assertFalse(aws.ttl_outlives_credentials(0)[0])
        self.assertFalse(aws.ttl_outlives_credentials(None)[0])

    def test_คีย์เลยอายุที่ประมาณไว้แล้วก็ยังเตือน(self):
        self._info(age_hours=10)         # เหลือ -2
        warn, left = aws.ttl_outlives_credentials(1)
        self.assertTrue(warn)
        self.assertLess(left, 0)


class TestDoctorChecks(unittest.TestCase):
    def test_แต่ละข้อคืนรูปแบบที่ถูก(self):
        for check in (labctl._check_terraform(), labctl._check_openssl(),
                      labctl._check_location(), labctl._check_state_dir(),
                      labctl._check_server(), labctl._check_labs()):
            self.assertIn(check["level"], (labctl.OK, labctl.WARN, labctl.FAIL))
            self.assertTrue(check["title"])

    def test_ที่ตั้งโปรเจกต์ปัจจุบันไม่อยู่ในโฟลเดอร์ซิงก์(self):
        # โปรเจกต์ถูกย้ายออกจาก OneDrive แล้วเมื่อ 2026-08-17 — ห้ามย้ายกลับ
        self.assertEqual(labctl._check_location()["level"], labctl.OK)


if __name__ == "__main__":
    unittest.main()
