"""เสิร์ฟ handout ให้ผู้เรียนโหลดเองผ่าน Wi-Fi ห้องอบรม

ทำไมต้องแยกจาก server หลัก: server หลักผูก 127.0.0.1 เพราะมีสิทธิ์สั่งสร้าง/ลบ
resource บน AWS — เอาออกวง LAN ไม่ได้เด็ดขาด ตัวนี้เปิดแยกพอร์ต ทำได้อย่างเดียว
คือส่งไฟล์ zip ของ lab เดียวที่ระบุ และต้องมี token ที่ถูกต้องเท่านั้น

กติกาความปลอดภัย (ใน zip มี private key ของผู้เรียน จึงต้องแน่น):
  - token ต่อคน สุ่มด้วย secrets เทียบแบบ constant-time
  - เสิร์ฟเฉพาะ lab ที่สั่งเปิด ไปหยิบ lab อื่นหรือไฟล์อื่นในเครื่องไม่ได้
  - หมดอายุตามเวลาที่กำหนด (default 4 ชม.) แล้วปิดตัวเอง
  - บันทึกว่าใครโหลดไปแล้ว ใช้เช็คหน้างานว่าใครยังไม่ได้ไฟล์
"""

import hmac
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse

import labctl


class HandoutShare:
    def __init__(self, lab_name, users, port=8899, ttl_hours=4):
        self.lab = lab_name
        self.dir = labctl.HANDOUTS_DIR / lab_name
        self.port = port
        self.expires_at = time.time() + ttl_hours * 3600
        self.tokens = {u: secrets.token_urlsafe(24) for u in users}
        self.downloaded = {}
        self._httpd = None

    def user_for(self, token):
        """เทียบ token แบบ constant-time — กันเดา token จากเวลาที่ตอบกลับ"""
        for user, t in self.tokens.items():
            if hmac.compare_digest(t, token or ""):
                return user
        return None

    def zip_for(self, user):
        p = self.dir / f"{self.lab}-{user}.zip"
        return p if p.exists() else None

    def expired(self):
        return time.time() > self.expires_at

    def links(self, host):
        return {u: f"http://{host}:{self.port}/h/{t}" for u, t in self.tokens.items()}

    def _make_handler(self):
        share = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass                      # เงียบไว้ ใช้ log ของเราเอง

            def _deny(self, code=404):
                self.send_response(code)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write("ไม่พบไฟล์ หรือลิงก์หมดอายุแล้ว\n".encode())

            def do_GET(self):
                if share.expired():
                    return self._deny(410)
                path = urlparse(self.path).path
                if not path.startswith("/h/"):
                    return self._deny()
                user = share.user_for(path[3:])
                if not user:
                    return self._deny(403)
                zip_path = share.zip_for(user)
                if not zip_path:
                    return self._deny()
                data = zip_path.read_bytes()
                share.downloaded[user] = time.time()
                self.send_response(200)
                self.send_header("Content-Type", "application/zip")
                self.send_header("Content-Disposition",
                                 f'attachment; filename="{zip_path.name}"')
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        return Handler

    def serve_forever(self):
        self._httpd = HTTPServer(("0.0.0.0", self.port), self._make_handler())
        self._httpd.timeout = 1
        while not self.expired():
            self._httpd.handle_request()
        self._httpd.server_close()

    def start(self):
        threading.Thread(target=self.serve_forever, daemon=True).start()
        return self


def local_ip():
    """IP ในวง LAN ที่ผู้เรียนจะเข้าถึงได้ — ไม่ใช่ 127.0.0.1"""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))        # ไม่ได้ส่งอะไรจริง แค่ให้ OS เลือก interface
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()
