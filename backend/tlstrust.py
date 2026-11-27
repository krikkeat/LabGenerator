"""ให้ HTTPS ที่ labctl เรียก (Python, AWS CLI, terraform) เชื่อ root CA เดียวกับ macOS

ทำไมต้องมี: เครือข่ายองค์กรบางแห่ง (เช่น proxy ที่ตรวจ HTTPS) เซ็น certificate ใหม่ด้วย
root CA ของบริษัท ซึ่งติดตั้งไว้ใน keychain ของ macOS — เบราว์เซอร์กับ curl ผ่าน แต่
Python และ AWS CLI ใช้รายชื่อ CA ของตัวเอง จึงล้มด้วย CERTIFICATE_VERIFY_FAILED

ทางแก้: ดึง CA ทั้งหมดที่ macOS เชื่อ (root ของระบบ + ที่ admin ติดตั้ง) มาทำเป็น bundle
แล้วชี้ทุกตัวไปที่ bundle นี้ — ยังตรวจ certificate เต็มรูปแบบ ไม่ได้ปิดการตรวจ
"""
import os
import platform
import ssl
import subprocess
import time
from pathlib import Path

BUNDLE = Path.home() / ".lab-generator" / "ca-bundle.pem"
KEYCHAINS = ("/System/Library/Keychains/SystemRootCertificates.keychain",
             "/Library/Keychains/System.keychain")
MAX_AGE = 24 * 3600          # สร้างใหม่วันละครั้ง — CA ที่ IT เพิ่มทีหลังจะเข้ามาเอง
EXPORT_TIMEOUT = 20


def _export_keychains():
    pems = []
    for kc in KEYCHAINS:
        if not os.path.exists(kc):
            continue
        res = subprocess.run(["security", "find-certificate", "-a", "-p", kc],
                             capture_output=True, text=True, timeout=EXPORT_TIMEOUT)
        if res.returncode == 0:
            pems.append(res.stdout)
    return "".join(pems)


def bundle_path():
    """path ของ bundle (สร้าง/ต่ออายุให้ถ้าจำเป็น) · None ถ้าไม่ใช่ macOS หรือสร้างไม่ได้"""
    if platform.system() != "Darwin":
        return None
    try:
        fresh = BUNDLE.exists() and time.time() - BUNDLE.stat().st_mtime < MAX_AGE
        if not fresh:
            pem = _export_keychains()
            if "BEGIN CERTIFICATE" not in pem:
                return str(BUNDLE) if BUNDLE.exists() else None
            BUNDLE.parent.mkdir(parents=True, exist_ok=True)
            tmp = BUNDLE.with_suffix(".tmp")
            tmp.write_text(pem, encoding="utf-8")
            tmp.replace(BUNDLE)
        return str(BUNDLE)
    except (OSError, subprocess.SubprocessError):
        return str(BUNDLE) if BUNDLE.exists() else None


def ssl_context():
    """context สำหรับ urllib — CA ของ macOS + ผ่อนเฉพาะ VERIFY_X509_STRICT

    Python 3.13+ เปิด strict เป็นค่าเริ่มต้น ซึ่งปฏิเสธ CA ขององค์กรที่ไม่มี
    Authority Key Identifier ("Missing Authority Key Identifier") ทั้งที่ certificate
    ถูกต้องตามที่ OS เชื่อ — ส่วนการตรวจ chain, hostname และวันหมดอายุยังทำครบ"""
    path = bundle_path()
    ctx = ssl.create_default_context(cafile=path) if path else ssl.create_default_context()
    if hasattr(ssl, "VERIFY_X509_STRICT"):
        ctx.verify_flags &= ~ssl.VERIFY_X509_STRICT
    return ctx


def cli_env(base=None):
    """env สำหรับ subprocess ของ aws CLI / terraform — ชี้ AWS_CA_BUNDLE ไปที่ bundle
    (ไม่ทับถ้าผู้ใช้ตั้ง AWS_CA_BUNDLE เองไว้แล้ว)"""
    env = dict(os.environ if base is None else base)
    path = bundle_path()
    if path and not env.get("AWS_CA_BUNDLE"):
        env["AWS_CA_BUNDLE"] = path
    return env
