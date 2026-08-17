"""เทสชั้น API จริงผ่าน TestClient — auth, endpoint สำคัญ และ error path

ใช้ db ชั่วคราวแยกจากของจริง และไม่แตะ AWS/terraform เลย (stub ตรงขอบ)
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
_DB = Path(tempfile.mkdtemp()) / "api-test.db"
os.environ["LABCTL_DB_PATH"] = str(_DB)

try:
    from fastapi.testclient import TestClient
except (ImportError, RuntimeError) as e:      # starlette ต้องมี httpx ถึงจะใช้ TestClient ได้
    raise unittest.SkipTest(f"ข้ามเทส API — {e}\nติดตั้งด้วย: pip3 install --user httpx")

from backend import app as A  # noqa: E402
from backend import aws, jobs  # noqa: E402

USER, PW = "instructor", "test-password-1"


class ApiTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(A.app)
        # เปิดครั้งแรกต้องตั้งบัญชีผู้ดูแลก่อน
        r = cls.client.post("/api/auth/setup", json={"username": USER, "password": PW})
        assert r.status_code in (200, 400), r.text     # 400 = ตั้งไว้แล้ว

    def login(self):
        c = TestClient(A.app)
        r = c.post("/api/auth/login", json={"username": USER, "password": PW})
        self.assertEqual(r.status_code, 200, r.text)
        return c


class TestAuthGate(ApiTestCase):
    """ทุก endpoint ต้องผ่าน login ก่อน — ในไฟล์ handout มี private key อยู่"""

    def test_ยังไม่ล็อกอินต้องได้_401(self):
        c = TestClient(A.app)
        for path in ("/api/labs", "/api/products", "/api/lab-guides",
                     "/api/aws/status", "/api/aws/orphans"):
            self.assertEqual(c.get(path).status_code, 401, path)

    def test_endpoint_ที่แก้ข้อมูลก็ต้อง_401(self):
        c = TestClient(A.app)
        self.assertEqual(c.post("/api/labs", json={}).status_code, 401)
        self.assertEqual(
            c.request("DELETE", "/api/labs/x", json={"confirm": "x"}).status_code, 401)

    def test_รหัสผ่านผิดเข้าไม่ได้(self):
        c = TestClient(A.app)
        r = c.post("/api/auth/login", json={"username": USER, "password": "ผิดแน่นอน"})
        self.assertNotEqual(r.status_code, 200)
        self.assertEqual(c.get("/api/labs").status_code, 401)

    def test_ล็อกอินแล้วเข้าได้(self):
        self.assertEqual(self.login().get("/api/labs").status_code, 200)


class TestEndpoints(ApiTestCase):
    def setUp(self):
        self.c = self.login()

    def test_products_มี_supported_os(self):
        d = self.c.get("/api/products").json()
        self.assertIn("supported_os", d)
        self.assertIn("ubuntu-24.04", d["supported_os"])

    def test_lab_guides_คืนรายการพร้อมธง_deletable(self):
        guides = self.c.get("/api/lab-guides").json()["guides"]
        self.assertTrue(guides)
        self.assertTrue(all({"path", "source", "deletable"} <= set(g) for g in guides))

    def test_lab_ที่ไม่มีอยู่ได้_404(self):
        self.assertEqual(self.c.get("/api/labs/ไม่มีจริงแน่นอน").status_code, 404)

    def test_สร้าง_lab_ด้วยชื่อผิดกติกาได้_400(self):
        r = self.c.post("/api/labs", json={"lab_name": "Bad_Name", "users": ["u1"]})
        self.assertEqual(r.status_code, 400)

    def test_destroy_ต้องพิมพ์ชื่อยืนยันให้ตรง(self):
        r = self.c.request("DELETE", "/api/labs/whatever", json={"confirm": "ไม่ตรง"})
        self.assertEqual(r.status_code, 400)

    def test_แนบ_guide_ให้_lab_ที่ไม่มีได้_404(self):
        r = self.c.put("/api/labs/ไม่มีจริง/guide",
                       json={"guide": "lab-guides/docker-101.md"})
        self.assertEqual(r.status_code, 404)

    def test_ลบ_guide_ของ_product_ไม่ได้(self):
        r = self.c.post("/api/lab-guides/delete",
                        json={"path": "products/cortex-xdr/guide.md"})
        self.assertEqual(r.status_code, 400)


class TestCreateFlowStubbed(ApiTestCase):
    """สร้าง lab จริงผ่าน HTTP แต่ stub AWS/terraform ทิ้ง — ตรวจว่าประกอบ cfg ถูก"""

    def setUp(self):
        self.c = self.login()
        self.captured = {}
        self._enq, self._ver = jobs.enqueue, aws.resolve_verified
        A.jobs.enqueue = lambda kind, cfg: (self.captured.update(kind=kind, cfg=cfg), "j1")[1]
        A.aws.resolve_verified = lambda *a, **k: {
            "account": "1", "arn": "a", "region": "ap-southeast-1", "access_key": "k",
            "secret_key": "s", "session_token": None, "method": "test"}

    def tearDown(self):
        A.jobs.enqueue, A.aws.resolve_verified = self._enq, self._ver
        import sqlite3
        from backend import db
        with sqlite3.connect(db.DB_PATH) as conn:
            conn.execute("DELETE FROM labs WHERE lab_name LIKE 'api-test%'")

    def test_สร้างสำเร็จได้_202_และเข้าคิว_create(self):
        r = self.c.post("/api/labs", json={
            "lab_name": "api-test1", "users": ["user01"], "os": "ubuntu-24.04",
            "ttl_hours": 2})
        self.assertEqual(r.status_code, 202, r.text)
        self.assertEqual(self.captured["kind"], "create")
        self.assertEqual(self.captured["cfg"]["lab_name"], "api-test1")

    def test_สร้างซ้ำชื่อเดิมที่ยัง_active_ได้_409(self):
        body = {"lab_name": "api-test2", "users": ["user01"], "os": "ubuntu-24.04"}
        self.assertEqual(self.c.post("/api/labs", json=body).status_code, 202)
        self.assertEqual(self.c.post("/api/labs", json=body).status_code, 409)

    def test_ttl_เกินเพดานได้_400(self):
        r = self.c.post("/api/labs", json={
            "lab_name": "api-test3", "users": ["u1"], "os": "ubuntu-24.04",
            "ttl_hours": 999})
        self.assertEqual(r.status_code, 400)

    def test_guide_ที่ไม่รู้จักได้_400(self):
        r = self.c.post("/api/labs", json={
            "lab_name": "api-test4", "users": ["u1"], "os": "ubuntu-24.04",
            "lab_guide": "../../etc/passwd"})
        self.assertEqual(r.status_code, 400)


if __name__ == "__main__":
    unittest.main()
