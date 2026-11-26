"""regression: destroy จากหน้าเว็บเคยพังด้วย 422 แล้วขึ้น "[object Object]"

สาเหตุ: lab ที่ไม่มี guide ทำให้หน้าเว็บส่ง delete_guide เป็น null (null && x ได้ null)
แต่ backend ประกาศเป็น bool ล้วน — ต้องรับ null ได้ เพราะหน้าเว็บที่เปิดค้างอยู่
อาจยังเป็นโค้ดเก่า
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
os.environ.setdefault("LABCTL_DB_PATH", str(Path(tempfile.gettempdir()) / "labctl-test.db"))

from fastapi.testclient import TestClient  # noqa: E402
from backend import app as A  # noqa: E402


class TestDestroyBody(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # สร้าง session ตรง ๆ แทน setup/login — db ทดสอบใช้ร่วมกับไฟล์เทสอื่น
        # ถ้ามีบัญชีอยู่ก่อนแล้ว setup จะถูกปฏิเสธและ login ด้วยรหัสของเราจะไม่ผ่าน
        from backend import auth
        cls.client = TestClient(A.app)
        token, _ = auth.create_session("destroytest", True)
        cls.client.cookies.set(A.SESSION_COOKIE, token)

    def setUp(self):
        self.captured = []
        self._enqueue, self._prepared = A.jobs.enqueue, A._prepared_cfg
        A.jobs.enqueue = lambda kind, cfg: (self.captured.append((kind, dict(cfg))), "job")[1]
        A._prepared_cfg = lambda name: {"lab_name": name, "users": ["user01"]}

    def tearDown(self):
        A.jobs.enqueue, A._prepared_cfg = self._enqueue, self._prepared

    def _delete(self, body):
        return self.client.request("DELETE", "/api/labs/lab-x", json=body)

    def test_delete_guide_เป็น_null_ต้องไม่พัง(self):
        r = self._delete({"confirm": "lab-x", "delete_guide": None})
        self.assertEqual(r.status_code, 202, r.text)
        self.assertNotIn("_delete_guide", self.captured[-1][1])

    def test_ไม่ส่ง_delete_guide_มาเลยก็ได้(self):
        self.assertEqual(self._delete({"confirm": "lab-x"}).status_code, 202)

    def test_delete_guide_true_ถูกส่งต่อให้_job(self):
        r = self._delete({"confirm": "lab-x", "delete_guide": True})
        self.assertEqual(r.status_code, 202)
        self.assertTrue(self.captured[-1][1].get("_delete_guide"))

    def test_ชื่อยืนยันไม่ตรงยังถูกปฏิเสธ(self):
        r = self._delete({"confirm": "ชื่อผิด", "delete_guide": None})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.captured, [])


if __name__ == "__main__":
    unittest.main()
