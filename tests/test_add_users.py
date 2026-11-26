"""เพิ่มเครื่องใน lab ที่ active — ต้องต่อชื่อ userNN ไม่ชนของเดิม และส่ง job add_users"""
import json
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


class TestAddUsers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from backend import auth
        cls.client = TestClient(A.app)
        token, _ = auth.create_session("adduserstest", True)
        cls.client.cookies.set(A.SESSION_COOKIE, token)

    def setUp(self):
        self.captured = []
        self.saved = {}
        self.status = "active"
        self.orig = (A.jobs.enqueue, A.db.get_lab, A.db.update_lab_config, A.aws.resolve_verified)
        A.jobs.enqueue = lambda kind, cfg: (self.captured.append((kind, dict(cfg))), "job")[1]
        A.db.get_lab = lambda name: {"status": self.status, "config_json": json.dumps(
            {"lab_name": name, "users": ["user01", "user02"]})}
        A.db.update_lab_config = lambda name, cfg: self.saved.update(cfg)
        A.aws.resolve_verified = lambda: None

    def tearDown(self):
        (A.jobs.enqueue, A.db.get_lab, A.db.update_lab_config, A.aws.resolve_verified) = self.orig

    def _post(self, body):
        return self.client.post("/api/labs/lab-x/users", json=body)

    def test_count_ต่อเลขจากชื่อเดิม(self):
        r = self._post({"count": 2})
        self.assertEqual(r.status_code, 202, r.text)
        self.assertEqual(r.json()["users"], ["user03", "user04"])
        kind, cfg = self.captured[-1]
        self.assertEqual(kind, "add_users")
        self.assertEqual(cfg["users"], ["user01", "user02", "user03", "user04"])
        self.assertEqual(self.saved["users"], cfg["users"])

    def test_ระบุชื่อเอง(self):
        r = self._post({"users": ["Alice"]})
        self.assertEqual(r.status_code, 202, r.text)
        self.assertEqual(self.captured[-1][1]["users"][-1], "alice")

    def test_ชื่อซ้ำถูกปฏิเสธ(self):
        self.assertEqual(self._post({"users": ["user01"]}).status_code, 400)
        self.assertEqual(self.captured, [])

    def test_ชื่อผิดรูปแบบถูกปฏิเสธ(self):
        self.assertEqual(self._post({"users": ["bad name"]}).status_code, 400)
        self.assertEqual(self.saved, {})

    def test_lab_ไม่_active_ถูกปฏิเสธ(self):
        self.status = "provisioning"
        self.assertEqual(self._post({"count": 1}).status_code, 409)


if __name__ == "__main__":
    unittest.main()
