"""expire_date ต้อง derive จาก TTL — ทั้งฝั่ง CLI และ API

ค่านี้ไปเป็น tag `Expire` บน resource ทุกตัวและโชว์ในหน้าเว็บ ถ้าเพี้ยนจะทำให้คน
ที่มาไล่เก็บของทีหลังเข้าใจผิดว่า lab หมดอายุแล้ว/ยังไม่หมด
"""
import datetime
import os
import sys
import tempfile
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
os.environ.setdefault("LABCTL_DB_PATH", str(Path(tempfile.gettempdir()) / "labctl-test.db"))

import labctl  # noqa: E402

NOW = datetime.datetime(2026, 8, 17, 22, 30)


class TestExpireDateFromTTL(unittest.TestCase):
    def test_ไม่ตั้ง_ttl_คืน_None(self):
        self.assertIsNone(labctl.expire_date_from_ttl(None, NOW))
        self.assertIsNone(labctl.expire_date_from_ttl(0, NOW))

    def test_ttl_สั้นได้วันเดียวกัน(self):
        self.assertEqual(labctl.expire_date_from_ttl(1, NOW), "2026-08-17")

    def test_ttl_ข้ามเที่ยงคืนได้วันถัดไป(self):
        # 22:30 + 2 ชม. = 00:30 ของวันรุ่งขึ้น
        self.assertEqual(labctl.expire_date_from_ttl(2, NOW), "2026-08-18")

    def test_ttl_หลายวัน(self):
        self.assertEqual(labctl.expire_date_from_ttl(24 * 7, NOW), "2026-08-24")
        self.assertEqual(labctl.expire_date_from_ttl(labctl.TTL_MAX_HOURS, NOW), "2026-08-31")

    def test_ttl_เศษชั่วโมง(self):
        self.assertEqual(labctl.expire_date_from_ttl(0.05, NOW), "2026-08-17")

    def test_ผลลัพธ์เป็นวันที่อ่านได้จริง(self):
        d = labctl.expire_date_from_ttl(8)
        self.assertGreaterEqual(datetime.date.fromisoformat(d), datetime.date.today())


class TestCreateFlowUsesDerivedDate(unittest.TestCase):
    """ทดสอบถึงชั้น endpoint — จุดที่พลาดง่ายคือลืมตั้งก่อน build_tfvars/บันทึก db"""

    @classmethod
    def setUpClass(cls):
        from backend import app as A, aws, jobs
        cls.A, cls.aws, cls.jobs = A, aws, jobs

    def setUp(self):
        self.captured = {}
        self._enqueue, self._verify = self.jobs.enqueue, self.aws.resolve_verified
        self.A.jobs.enqueue = lambda kind, cfg: (self.captured.update(cfg=cfg), "job")[1]
        self.A.aws.resolve_verified = lambda *a, **k: {
            "account": "1", "arn": "a", "region": "ap-southeast-1",
            "access_key": "k", "secret_key": "s", "session_token": None, "method": "test"}

    def tearDown(self):
        self.A.jobs.enqueue, self.A.aws.resolve_verified = self._enqueue, self._verify
        import sqlite3
        from backend import db
        with sqlite3.connect(db.DB_PATH) as conn:
            conn.execute("DELETE FROM labs WHERE lab_name LIKE 'exp-test%'")

    def _create(self, **kw):
        body = self.A.CreateLabBody(lab_name=kw.pop("lab_name", "exp-test"),
                                    users=["user01"], os="ubuntu-24.04", **kw)
        self.A.api_create_lab(body)
        return self.captured["cfg"]

    def test_ตั้ง_ttl_แล้ว_expire_date_ตรงกับวันหมดอายุ(self):
        cfg = self._create(ttl_hours=8)
        self.assertEqual(cfg["expire_date"], labctl.expire_date_from_ttl(8))

    def test_ค่าที่ส่งมาเองถูกแทนด้วยค่าที่derive(self):
        cfg = self._create(ttl_hours=8, expire_date="2020-01-01")
        self.assertNotEqual(cfg["expire_date"], "2020-01-01")
        self.assertEqual(cfg["expire_date"], labctl.expire_date_from_ttl(8))

    def test_ไม่ตั้ง_ttl_ใช้ค่าที่ส่งมาตามเดิม(self):
        cfg = self._create(ttl_hours=0, expire_date="2026-12-31")
        self.assertEqual(cfg["expire_date"], "2026-12-31")

    def test_ค่าที่บันทึกลง_db_ตรงกับที่ส่งให้_terraform(self):
        import json
        from backend import db
        cfg = self._create(lab_name="exp-test2", ttl_hours=8)
        row = db.get_lab("exp-test2")
        self.assertEqual(row["expire_date"], cfg["expire_date"])
        self.assertEqual(json.loads(row["config_json"])["expire_date"], cfg["expire_date"])


class TestCliCreateDerivesDate(unittest.TestCase):
    """ฝั่ง CLI ต้อง derive ก่อน build_tfvars ไม่งั้น tag Expire จะเป็นค่าเก่าในไฟล์ config"""

    def setUp(self):
        self.seen = {}
        self.saved = {name: getattr(labctl, name) for name in
                      ("build_tfvars", "tf", "select_workspace", "export_lab", "register_lab")}
        labctl.build_tfvars = lambda cfg: self.seen.setdefault("cfg", dict(cfg)) and None \
            or type("F", (), {"name": "x.tfvars.json"})()
        labctl.tf = lambda *a, **k: None
        labctl.select_workspace = lambda *a, **k: None
        labctl.export_lab = lambda *a, **k: []
        # คืน epoch จริงเพื่อให้ข้อความสรุปใน cmd_create ไม่เพี้ยน
        labctl.register_lab = lambda cfg, ttl=None, **k: (
            self.seen.__setitem__("ttl", ttl), __import__("time").time() + (ttl or 0) * 3600)[1]

    def tearDown(self):
        for name, fn in self.saved.items():
            setattr(labctl, name, fn)

    def _run(self, cfg, ttl):
        import contextlib, io
        with contextlib.redirect_stdout(io.StringIO()):   # cmd_create พิมพ์สรุปออก stdout
            labctl.cmd_create(cfg, ttl_hours=ttl)

    def test_ttl_เขียนทับค่าเดิมในconfig(self):
        self._run({"lab_name": "cli-test", "users": ["u1"], "os": "ubuntu-24.04",
                   "expire_date": "2020-01-01"}, 8)
        self.assertEqual(self.seen["cfg"]["expire_date"], labctl.expire_date_from_ttl(8))

    def test_ไม่มีttlคงค่าเดิมไว้(self):
        self._run({"lab_name": "cli-test", "users": ["u1"], "os": "ubuntu-24.04",
                   "expire_date": "2026-12-31"}, None)
        self.assertEqual(self.seen["cfg"]["expire_date"], "2026-12-31")


if __name__ == "__main__":
    unittest.main()
