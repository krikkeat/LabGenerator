"""SQLite sidecar — เก็บเฉพาะสิ่งที่ Terraform state ไม่มีที่เก็บ:
สถานะ lab (queued/provisioning/active/error/destroyed), ประวัติ job และ config ที่สร้างจาก UI

IP / instance id / OS อ่านสดจาก terraform output เสมอ — ไม่ cache ลงตาราง
"""

import json
import sqlite3
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "labs.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS labs (
    lab_name    TEXT PRIMARY KEY,
    product     TEXT,
    status      TEXT NOT NULL,
    created_at  REAL NOT NULL,
    expire_date TEXT,
    config_json TEXT
);
CREATE TABLE IF NOT EXISTS jobs (
    id          TEXT PRIMARY KEY,
    lab_name    TEXT NOT NULL,
    kind        TEXT NOT NULL,
    state       TEXT NOT NULL,
    phase       TEXT,
    error       TEXT,
    started_at  REAL,
    finished_at REAL,
    created_at  REAL NOT NULL,
    log_path    TEXT
);
"""


def get_conn():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


def upsert_lab(lab_name, product, status, expire_date, config):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO labs (lab_name, product, status, created_at, expire_date, config_json)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(lab_name) DO UPDATE SET
                 product=excluded.product, status=excluded.status,
                 expire_date=excluded.expire_date, config_json=excluded.config_json""",
            (lab_name, product, status, time.time(), expire_date,
             json.dumps(config, ensure_ascii=False)),
        )


def set_lab_status(lab_name, status):
    with get_conn() as conn:
        conn.execute("UPDATE labs SET status=? WHERE lab_name=?", (status, lab_name))


def get_lab(lab_name):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM labs WHERE lab_name=?", (lab_name,)).fetchone()
        return dict(row) if row else None


def list_labs():
    with get_conn() as conn:
        return [dict(r) for r in
                conn.execute("SELECT * FROM labs ORDER BY created_at DESC").fetchall()]


def create_job(job_id, lab_name, kind, log_path):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO jobs (id, lab_name, kind, state, created_at, log_path) "
            "VALUES (?, ?, ?, 'queued', ?, ?)",
            (job_id, lab_name, kind, time.time(), str(log_path)),
        )


def update_job(job_id, **fields):
    cols = ", ".join(f"{k}=?" for k in fields)
    with get_conn() as conn:
        conn.execute(f"UPDATE jobs SET {cols} WHERE id=?", (*fields.values(), job_id))


def get_job(job_id):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return dict(row) if row else None


def latest_job_for_lab(lab_name):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM jobs WHERE lab_name=? ORDER BY created_at DESC LIMIT 1",
            (lab_name,)).fetchone()
        return dict(row) if row else None
