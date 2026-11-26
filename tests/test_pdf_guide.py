"""lab guide แบบ PDF — เก็บ ตรวจ และแนบเข้า handout

PDF ถูกคัดลอกเข้า zip ของผู้เรียนทุกคน จึงต้องมั่นใจว่ารับเฉพาะ PDF จริงและ
ไม่ใหญ่จนกินดิสก์
"""
import base64
import os
import sys
import tempfile
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
os.environ.setdefault("LABCTL_DB_PATH", str(Path(tempfile.gettempdir()) / "labctl-test.db"))

import labctl  # noqa: E402
from backend import app as A  # noqa: E402

# PDF จริงขั้นต่ำที่ browser เปิดได้
MINIMAL_PDF = (b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
               b"2 0 obj<</Type/Pages/Kids[]/Count 0>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n")


class TestSavePdfGuide(unittest.TestCase):
    def tearDown(self):
        for name in ("pdftest-custom.pdf", "pdftest-custom.md"):
            (labctl.GUIDES_DIR / name).unlink(missing_ok=True)

    def test_เก็บ_pdf_ได้และขึ้นในรายการ(self):
        path = labctl.save_custom_guide_pdf("pdftest", MINIMAL_PDF)
        self.assertEqual(path, "lab-guides/pdftest-custom.pdf")
        self.assertEqual((labctl.BASE_DIR / path).read_bytes(), MINIMAL_PDF)
        entry = next(g for g in labctl.list_lab_guides() if g["path"] == path)
        self.assertEqual(entry["kind"], "pdf")
        self.assertTrue(entry["deletable"])

    def test_ไฟล์ที่ไม่ใช่_pdf_ถูกปฏิเสธ(self):
        for bad in (b"PK\x03\x04zip", b"<html>", "# แค่ markdown".encode("utf-8"), b""):
            with self.assertRaises(labctl.ConfigError):
                labctl.save_custom_guide_pdf("pdftest", bad)

    def test_ชื่อ_lab_ที่พาออกนอกโฟลเดอร์ถูกปฏิเสธ(self):
        for bad in ("../evil", "a/b", ".."):
            with self.assertRaises(labctl.ConfigError):
                labctl.save_custom_guide_pdf(bad, MINIMAL_PDF)

    def test_ลบ_pdf_ที่อัปโหลดเองได้(self):
        path = labctl.save_custom_guide_pdf("pdftest", MINIMAL_PDF)
        self.assertTrue(labctl.is_custom_guide(path))
        labctl.delete_custom_guide(path)
        self.assertFalse((labctl.BASE_DIR / path).exists())


class TestDecodeUpload(unittest.TestCase):
    def test_รับทั้ง_base64_ล้วนและ_data_url(self):
        raw = base64.b64encode(MINIMAL_PDF).decode()
        self.assertEqual(A._decode_pdf(raw), MINIMAL_PDF)
        self.assertEqual(A._decode_pdf("data:application/pdf;base64," + raw), MINIMAL_PDF)

    def test_ใหญ่เกินเพดานถูกปฏิเสธ(self):
        big = base64.b64encode(b"%PDF-" + b"x" * (A.GUIDE_PDF_MAX_BYTES + 10)).decode()
        with self.assertRaises(Exception) as cm:
            A._decode_pdf(big)
        self.assertEqual(getattr(cm.exception, "status_code", None), 400)

    def test_base64_เสียถูกปฏิเสธ(self):
        for bad in ("ไม่ใช่ base64!!", "data:application/pdf;base64,@@@@"):
            with self.assertRaises(Exception):
                A._decode_pdf(bad)


class TestHandoutWithPdf(unittest.TestCase):
    def test_ส่วน_pdf_มีทั้งลิงก์และตัวแสดงผล(self):
        html = labctl.render_pdf_guide_section(labctl.GUIDE_PDF_NAME, 2 * 1024 * 1024)
        self.assertIn(labctl.GUIDE_PDF_NAME, html)
        self.assertIn("2.0 MB", html)
        self.assertIn("<object", html)          # แสดงในหน้าได้
        self.assertIn("<a href=", html)         # และมีลิงก์สำรอง

    def test_handout_ที่ฝังส่วน_pdf_ยังเป็น_html_ที่ถูกต้อง(self):
        from test_labctl import check_html, VM_LINUX
        section = labctl.render_pdf_guide_section(labctl.GUIDE_PDF_NAME, 1024)
        page = labctl.render_user_handout({"lab_name": "l", "title": "T"},
                                          "user01", VM_LINUX, section, None)
        self.assertEqual(check_html(page), [])


if __name__ == "__main__":
    unittest.main()
