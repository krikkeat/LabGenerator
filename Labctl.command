#!/bin/bash
# Labctl.command — ดับเบิลคลิกเพื่อเปิด Hands-on Lab Generator
# (เปิด server + browser ให้เอง · หยุดด้วย Ctrl+C หรือปิดหน้าต่างนี้)

cd "$(dirname "$0")" || exit 1
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

URL="http://127.0.0.1:8787"

# server เปิดอยู่แล้ว → เปิด browser อย่างเดียว
if curl -s -o /dev/null --max-time 2 "$URL/api/auth/status" 2>/dev/null; then
  open "$URL"
  echo "labctl เปิดอยู่แล้ว — เปิด browser ให้แล้ว (ปิดหน้าต่างนี้ได้เลย)"
  exit 0
fi

# ใช้ venv ของโปรเจกต์ (.venv) — Homebrew Python บล็อก pip install --user (PEP 668)
PY=".venv/bin/python"
if ! "$PY" -c "import fastapi, uvicorn" 2>/dev/null; then
  echo "ติดตั้ง dependencies ครั้งแรก (fastapi + uvicorn) ลง .venv…"
  { [ -x "$PY" ] || python3 -m venv .venv; } && \
  "$PY" -m pip install --quiet fastapi 'uvicorn[standard]' || {
    echo "ติดตั้งไม่สำเร็จ — ลองรันเอง: python3 -m venv .venv && .venv/bin/pip install fastapi 'uvicorn[standard]'"
    read -r -p "กด Enter เพื่อปิด…"
    exit 1
  }
fi

if ! command -v terraform >/dev/null; then
  echo "คำเตือน: ยังไม่พบ terraform ในเครื่อง — เปิดหน้าเว็บได้ แต่สร้าง lab จริงไม่ได้"
  echo "         ติดตั้งได้จาก https://developer.hashicorp.com/terraform/install"
fi

echo "เปิด labctl ที่ $URL  (หยุดด้วย Ctrl+C หรือปิดหน้าต่างนี้)"
exec "$PY" labctl.py serve
