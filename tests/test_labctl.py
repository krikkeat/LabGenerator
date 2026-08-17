"""ทดสอบส่วนที่ไม่ต้องแตะ AWS — config, handout, lab guide, terraform resolver

รัน: python3 -m unittest discover -s tests -v   (จากโฟลเดอร์ lab-generator)
"""
import os
import sys
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
# ชี้ db ไปที่ไฟล์ชั่วคราวก่อน import backend — กันไม่ให้ทดสอบไปแตะ labs.db ของจริง
os.environ.setdefault("LABCTL_DB_PATH", str(Path(tempfile.gettempdir()) / "labctl-test.db"))

import labctl  # noqa: E402

VOID = {"br", "hr", "img", "meta", "link", "input"}


class WellFormed(HTMLParser):
    """เช็คว่า tag เปิด-ปิดครบ — handout ถูกประกอบจาก f-string จึงพังได้ง่ายเวลาแก้"""

    def __init__(self):
        super().__init__()
        self.stack, self.errors = [], []

    def handle_starttag(self, tag, attrs):
        if tag not in VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        if not self.stack or self.stack[-1] != tag:
            self.errors.append(f"</{tag}> ไม่ตรงกับ {self.stack[-3:]}")
            while self.stack and self.stack.pop() != tag:
                pass
        else:
            self.stack.pop()


def check_html(html_text):
    p = WellFormed()
    p.feed(html_text)
    return p.errors + ([f"tag ค้าง: {p.stack}"] if p.stack else [])


VM_LINUX = {"public_ip": "1.2.3.4", "username": "ubuntu", "os": "ubuntu-24.04",
            "is_windows": False, "instance_id": "i-1"}
VM_WIN = {"public_ip": "5.6.7.8", "username": "Administrator", "os": "windows-2022",
          "is_windows": True, "instance_id": "i-2"}


class TestValidateConfig(unittest.TestCase):
    def test_ชื่อ_lab_ต้องเป็นตัวพิมพ์เล็ก(self):
        for bad in ("Lab_1", "ชื่อไทย", "a" * 33, "lab name"):
            with self.assertRaises(labctl.ConfigError):
                labctl.validate_config({"lab_name": bad, "users": ["user01"]})

    def test_ต้องมี_user_อย่างน้อยหนึ่งคน(self):
        with self.assertRaises(labctl.ConfigError):
            labctl.validate_config({"lab_name": "ok", "users": []})

    def test_os_ที่ไม่รองรับถูกปฏิเสธ(self):
        with self.assertRaises(labctl.ConfigError):
            labctl.validate_config({"lab_name": "ok", "users": ["u1"], "os": "plan9"})

    def test_custom_ami_ต้องมี_username(self):
        with self.assertRaises(labctl.ConfigError):
            labctl.validate_config({"lab_name": "ok", "users": ["u1"],
                                    "custom_ami": "ami-0123456789abcdef0"})

    def test_config_ที่ถูกต้องผ่าน(self):
        cfg = labctl.validate_config({"lab_name": "ok-lab", "users": ["user01"],
                                      "os": "ubuntu-24.04"})
        self.assertEqual(cfg["lab_name"], "ok-lab")


class TestHandoutSSH(unittest.TestCase):
    def setUp(self):
        self.cfg = {"lab_name": "lab1", "title": "T", "allowed_cidr": "203.0.113.5/32",
                    "extra_open_ports": [8080]}
        self.html = labctl.render_user_handout(self.cfg, "user01", VM_LINUX, "<p>guide</p>", None)

    def test_html_ถูกต้อง(self):
        self.assertEqual(check_html(self.html), [])

    def test_มีวิธีต่อของ_mac_และ_windows(self):
        self.assertIn("chmod 600 user01.pem", self.html)
        self.assertIn("icacls user01.pem", self.html)      # Windows ใช้ chmod ไม่ได้
        self.assertIn("PuTTYgen", self.html)

    def test_มีวิธีรับส่งไฟล์(self):
        self.assertIn("scp -i user01.pem", self.html)

    def test_แก้ปัญหาใส่_ip_จริงในคำสั่ง(self):
        self.assertIn("ssh-keygen -R 1.2.3.4", self.html)

    def test_บอกขอบเขตการเข้าถึง(self):
        self.assertIn("203.0.113.5/32", self.html)
        self.assertIn("8080", self.html)

    def test_ไม่มีส่วนของ_rdp_ปนมา(self):
        self.assertNotIn("mstsc", self.html)


class TestHandoutRDP(unittest.TestCase):
    def setUp(self):
        self.cfg = {"lab_name": "lab2", "title": "T"}

    def test_มีรหัสผ่านเมื่อถอดได้(self):
        h = labctl.render_user_handout(self.cfg, "user01", VM_WIN, "<p>g</p>", "P@ss w0rd")
        self.assertEqual(check_html(h), [])
        self.assertIn("P@ss w0rd", h)
        self.assertIn("mstsc", h)
        self.assertIn("Windows App", h)          # client ฝั่ง macOS
        self.assertNotIn("ยังไม่พร้อม", h)

    def test_บอกวิธีเมื่อรหัสผ่านยังไม่พร้อม(self):
        h = labctl.render_user_handout(self.cfg, "user01", VM_WIN, "<p>g</p>", None)
        self.assertEqual(check_html(h), [])
        self.assertIn("ยังไม่พร้อม", h)

    def test_รหัสผ่านถูก_escape(self):
        h = labctl.render_user_handout(self.cfg, "u", VM_WIN, "", "a<b>&c")
        self.assertIn("a&lt;b&gt;&amp;c", h)
        self.assertNotIn("a<b>&c", h)


class TestMarkdown(unittest.TestCase):
    def test_หัวข้อและรายการและโค้ด(self):
        out = labctl.md_to_html("# หัว\n- ข้อ 1\n- ข้อ 2\n\n```\ncode <here>\n```\n> quote")
        self.assertIn("<h1>หัว</h1>", out)
        self.assertIn("<li>ข้อ 1</li>", out)
        self.assertIn("<blockquote>quote</blockquote>", out)
        self.assertIn("code &lt;here&gt;", out)   # โค้ดต้องถูก escape ไม่ใช่ render

    def test_html_ที่ได้ถูกต้อง(self):
        self.assertEqual(check_html(labctl.md_to_html("# a\n- b\n- c\n\npara")), [])


class TestLabGuides(unittest.TestCase):
    def test_รายการมีทั้ง_lab_guides_และ_product(self):
        guides = labctl.list_lab_guides()
        self.assertTrue(any(g["source"] == "lab-guides" for g in guides))
        self.assertTrue(any(g["source"].startswith("product:") for g in guides))
        self.assertTrue(all("deletable" in g for g in guides))

    def test_guide_ของ_product_ลบไม่ได้(self):
        for g in labctl.list_lab_guides():
            if g["source"].startswith("product:"):
                self.assertFalse(g["deletable"])
                with self.assertRaises(labctl.ConfigError):
                    labctl.delete_custom_guide(g["path"])

    def test_ชื่อ_lab_ที่พาออกนอกโฟลเดอร์ถูกปฏิเสธ(self):
        for bad in ("../evil", "a/b", "..", "ชื่อไทย"):
            with self.assertRaises(labctl.ConfigError):
                labctl.save_custom_guide(bad, "x")

    def test_เขียนและลบ_custom_guide(self):
        path = labctl.save_custom_guide("unittest-lab", "# hi")
        try:
            self.assertTrue((labctl.BASE_DIR / path).exists())
            self.assertTrue(labctl.is_custom_guide(path))
            self.assertIn(path, {g["path"] for g in labctl.list_lab_guides()})
        finally:
            labctl.delete_custom_guide(path)
        self.assertFalse((labctl.BASE_DIR / path).exists())

    def test_ลบไฟล์นอกโฟลเดอร์guideไม่ได้(self):
        for bad in ("backend/auth.py", "lab-guides/../backend/auth.py", "/etc/passwd"):
            with self.assertRaises(labctl.ConfigError):
                labctl.delete_custom_guide(bad)


class TestTTL(unittest.TestCase):
    def test_ค่าปกติผ่าน(self):
        self.assertEqual(labctl.validate_ttl(8), 8)
        self.assertEqual(labctl.validate_ttl(0.5), 0.5)
        self.assertEqual(labctl.validate_ttl(labctl.TTL_MAX_HOURS), labctl.TTL_MAX_HOURS)

    def test_ไม่ใส่หรือศูนย์คือปิด(self):
        self.assertIsNone(labctl.validate_ttl(None))
        self.assertIsNone(labctl.validate_ttl(0))

    def test_ติดลบหรือเกินเพดานถูกปฏิเสธ(self):
        for bad in (-1, labctl.TTL_MAX_HOURS + 1, 10000):
            with self.assertRaises(labctl.ConfigError):
                labctl.validate_ttl(bad)

    def test_เพดานเท่ากับฝั่ง_web(self):
        self.assertEqual(labctl.TTL_MAX_HOURS, 24 * 14)


class TestDueLabs(unittest.TestCase):
    """เลือกผิดตรงนี้ = ลบ lab ที่ผู้เรียนยังใช้อยู่ หรือปล่อยเครื่องเดินเงินทิ้ง"""

    NOW = 1_000_000

    def test_เลือกเฉพาะที่ครบกำหนดและสถานะเก็บได้(self):
        labs = [
            {"lab_name": "ครบแล้ว", "status": "active", "expires_at": self.NOW - 1},
            {"lab_name": "พอดีเป๊ะ", "status": "active", "expires_at": self.NOW},
            {"lab_name": "ยังไม่ครบ", "status": "active", "expires_at": self.NOW + 1},
            {"lab_name": "ลบไปแล้ว", "status": "destroyed", "expires_at": self.NOW - 999},
            {"lab_name": "กำลังสร้าง", "status": "provisioning", "expires_at": self.NOW - 999},
            {"lab_name": "กำลังลบ", "status": "destroying", "expires_at": self.NOW - 999},
            {"lab_name": "ไม่ตั้ง ttl", "status": "active", "expires_at": None},
        ]
        got = [l["lab_name"] for l in labctl.due_labs(labs, self.NOW)]
        self.assertEqual(got, ["ครบแล้ว", "พอดีเป๊ะ"])

    def test_lab_ที่destroyล้มต้องยังถูกตามเก็บ(self):
        """destroy ล้ม (คีย์หมดอายุ) → สถานะ error — เดิมหลุดจากการเก็บอัตโนมัติ
        ทั้งที่เป็นตัวที่ resource ยังเดินเงินอยู่จริง"""
        labs = [{"lab_name": "ลบไม่สำเร็จ", "status": "error", "expires_at": self.NOW - 60}]
        self.assertEqual([l["lab_name"] for l in labctl.due_labs(labs, self.NOW)],
                         ["ลบไม่สำเร็จ"])

    def test_error_ที่ยังไม่ครบกำหนดยังไม่ถูกแตะ(self):
        labs = [{"lab_name": "error แต่ยังไม่หมดอายุ", "status": "error",
                 "expires_at": self.NOW + 60}]
        self.assertEqual(labctl.due_labs(labs, self.NOW), [])

    def test_ไม่มีอะไรครบกำหนดคืนลิสต์ว่าง(self):
        self.assertEqual(labctl.due_labs([], self.NOW), [])
        self.assertEqual(
            labctl.due_labs([{"lab_name": "x", "status": "active",
                              "expires_at": self.NOW + 60}], self.NOW), [])


class TestShouldAttemptDestroy(unittest.TestCase):
    """กันยิง destroy ซ้ำถี่ ๆ ตอนที่มันล้มเพราะคีย์หมดอายุ"""

    NOW = 1_000_000
    CD = labctl.DESTROY_RETRY_COOLDOWN

    def test_ยังไม่เคยมี_job_ก็ลบได้เลย(self):
        self.assertTrue(labctl.should_attempt_destroy(None, self.NOW))

    def test_job_ล่าสุดไม่ใช่destroyก็ลบได้(self):
        for kind in ("create", "refresh", "export"):
            self.assertTrue(labctl.should_attempt_destroy(
                {"kind": kind, "state": "success", "finished_at": self.NOW - 5}, self.NOW))

    def test_กำลังลบอยู่ต้องไม่สั่งซ้ำ(self):
        for state in ("queued", "running"):
            self.assertFalse(labctl.should_attempt_destroy(
                {"kind": "destroy", "state": state, "finished_at": None}, self.NOW))

    def test_เพิ่งล้มไปต้องรอ_cooldown(self):
        self.assertFalse(labctl.should_attempt_destroy(
            {"kind": "destroy", "state": "error", "finished_at": self.NOW - 60}, self.NOW))

    def test_ล้มไปนานแล้วลองใหม่ได้(self):
        self.assertTrue(labctl.should_attempt_destroy(
            {"kind": "destroy", "state": "error", "finished_at": self.NOW - self.CD - 1},
            self.NOW))

    def test_ล้มแล้วไม่มีเวลาจบบันทึกไว้ก็ยังลองใหม่ได้(self):
        self.assertTrue(labctl.should_attempt_destroy(
            {"kind": "destroy", "state": "error", "finished_at": None}, self.NOW))


class TestLaunchAgent(unittest.TestCase):
    def test_plist_ชี้ไปที่ไฟล์และคำสั่งที่ถูก(self):
        xml = labctl._launch_plist_xml(600)
        self.assertIn("<string>expire</string>", xml)
        self.assertIn(str(labctl.BASE_DIR / "labctl.py"), xml)
        self.assertIn("<integer>600</integer>", xml)
        self.assertIn(labctl.LAUNCH_LABEL, xml)

    def test_ไม่รันตอนโหลด(self):
        # RunAtLoad=true จะทำให้ทุก login ยิง terraform destroy ทันที — ต้องเป็น false
        self.assertIn("<key>RunAtLoad</key><false/>", labctl._launch_plist_xml(600))


class TestTerraformBin(unittest.TestCase):
    def setUp(self):
        self.saved = os.environ.get("TERRAFORM_BIN")

    def tearDown(self):
        if self.saved is None:
            os.environ.pop("TERRAFORM_BIN", None)
        else:
            os.environ["TERRAFORM_BIN"] = self.saved

    def test_env_ที่ชี้ไฟล์ที่รันไม่ได้คืน_None(self):
        with tempfile.NamedTemporaryFile() as f:
            os.chmod(f.name, 0o644)
            os.environ["TERRAFORM_BIN"] = f.name
            self.assertIsNone(labctl.terraform_bin())

    def test_env_ที่ชี้ไฟล์รันได้ถูกใช้(self):
        with tempfile.NamedTemporaryFile(delete=False) as f:
            name = f.name
        try:
            os.chmod(name, 0o755)
            os.environ["TERRAFORM_BIN"] = name
            self.assertEqual(labctl.terraform_bin(), name)
        finally:
            os.unlink(name)

    def test_ข้อความบอกวิธีติดตั้งครบ(self):
        self.assertIn("brew", labctl.TF_MISSING_MSG)
        self.assertIn("TERRAFORM_BIN", labctl.TF_MISSING_MSG)


if __name__ == "__main__":
    unittest.main()
