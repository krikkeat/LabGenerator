"""บัญชีผู้ดูแลของ web UI — ตั้งค่าครั้งแรกจากหน้าเว็บ เก็บเป็น hash ในเครื่องเท่านั้น

- รหัสผ่าน: PBKDF2-HMAC-SHA256 (200k รอบ) + salt สุ่มต่อบัญชี — ไม่เก็บ plaintext
- session: token สุ่ม เก็บเฉพาะ hash ของ token ใน labs.db (ขโมยไฟล์ db ไปก็ใช้ต่อไม่ได้)
- server ผูก 127.0.0.1 อยู่แล้ว — ชั้นนี้กันเครื่องที่ใช้ร่วมกันหลายคน/หน้าจอค้างไว้
"""

import hashlib
import hmac
import re
import secrets
import time

from . import db

PBKDF2_ITERS = 200_000
SESSION_REMEMBER_SECS = 30 * 24 * 3600   # จำไว้ในเครื่องนี้ — 30 วัน
SESSION_SHORT_SECS = 12 * 3600           # ไม่จำ — 12 ชม. (คลุมวัน training เต็มวัน)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS auth_account (
    username   TEXT PRIMARY KEY,
    pw_salt    BLOB NOT NULL,
    pw_hash    BLOB NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS auth_sessions (
    token_hash TEXT PRIMARY KEY,
    username   TEXT NOT NULL,
    expires_at REAL NOT NULL
);
"""


def _conn():
    conn = db.get_conn()
    conn.executescript(_SCHEMA)
    return conn


def _hash_pw(password, salt):
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITERS)


def _hash_token(token):
    return hashlib.sha256(token.encode()).hexdigest()


def setup_required():
    with _conn() as conn:
        return conn.execute("SELECT COUNT(*) FROM auth_account").fetchone()[0] == 0


def create_account(username, password):
    username = (username or "").strip().lower()
    if not username or not re.fullmatch(r"[a-z0-9-]{1,30}", username):
        raise ValueError("username ใช้ได้เฉพาะ a-z, 0-9 และ - (ไม่เกิน 30 ตัว)")
    if len(password or "") < 8:
        raise ValueError("รหัสผ่านต้องยาวอย่างน้อย 8 ตัวอักษร")
    if not setup_required():
        raise ValueError("มีบัญชีผู้ดูแลอยู่แล้ว — เข้าสู่ระบบแทน")
    salt = secrets.token_bytes(16)
    with _conn() as conn:
        conn.execute("INSERT INTO auth_account VALUES (?, ?, ?, ?)",
                     (username, salt, _hash_pw(password, salt), time.time()))


def verify_login(username, password):
    username = (username or "").strip().lower()
    with _conn() as conn:
        row = conn.execute("SELECT pw_salt, pw_hash FROM auth_account WHERE username=?",
                           (username,)).fetchone()
    if not row:
        # เทียบกับ hash หลอกเพื่อให้เวลาตอบใกล้เคียงกัน (กัน user enumeration)
        _hash_pw(password or "", b"\x00" * 16)
        return False
    return hmac.compare_digest(_hash_pw(password or "", row["pw_salt"]), row["pw_hash"])


def create_session(username, remember):
    token = secrets.token_urlsafe(32)
    ttl = SESSION_REMEMBER_SECS if remember else SESSION_SHORT_SECS
    with _conn() as conn:
        conn.execute("DELETE FROM auth_sessions WHERE expires_at < ?", (time.time(),))
        conn.execute("INSERT INTO auth_sessions VALUES (?, ?, ?)",
                     (_hash_token(token), username.strip().lower(), time.time() + ttl))
    return token, ttl


def verify_session(token):
    if not token:
        return None
    with _conn() as conn:
        row = conn.execute(
            "SELECT username, expires_at FROM auth_sessions WHERE token_hash=?",
            (_hash_token(token),)).fetchone()
    if not row or row["expires_at"] < time.time():
        return None
    return row["username"]


def destroy_session(token):
    if not token:
        return
    with _conn() as conn:
        conn.execute("DELETE FROM auth_sessions WHERE token_hash=?", (_hash_token(token),))
