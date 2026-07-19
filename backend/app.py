"""FastAPI layer บาง ๆ หน้า labctl.py — ทุก endpoint จับคู่กับฟังก์ชันที่มีอยู่แล้ว

รัน: python3 labctl.py serve   (127.0.0.1:8787 — ขอบเขตความเชื่อถือเดียวกับ CLI)
"""

import json
import shutil
import stat
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import labctl
from . import auth, aws, db, jobs

BASE_DIR = Path(__file__).resolve().parent.parent
FRONTEND_DIR = BASE_DIR / "frontend"
CONFIGS_DIR = BASE_DIR / "configs"

app = FastAPI(title="labctl", docs_url="/api/docs", openapi_url="/api/openapi.json")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

_find_config_for = jobs.find_config_for


@app.on_event("startup")
def _start_expiry_scheduler():
    jobs.start_scheduler()


# ---------------------------------------------------------------------------
# auth — ตั้งค่าครั้งแรก / เข้าสู่ระบบ (cookie session)
# ---------------------------------------------------------------------------

SESSION_COOKIE = "labctl_session"


@app.middleware("http")
async def _require_login(request: Request, call_next):
    path = request.url.path
    if path.startswith("/api/") and not path.startswith("/api/auth/"):
        user = auth.verify_session(request.cookies.get(SESSION_COOKIE))
        if not user:
            return JSONResponse({"detail": "ต้องเข้าสู่ระบบก่อน"}, status_code=401)
    return await call_next(request)


class SetupBody(BaseModel):
    username: str
    password: str


class LoginBody(BaseModel):
    username: str
    password: str
    remember: bool = True


@app.get("/api/auth/status")
def api_auth_status(request: Request):
    user = auth.verify_session(request.cookies.get(SESSION_COOKIE))
    return {"setup_required": auth.setup_required(),
            "authenticated": bool(user), "username": user}


@app.post("/api/auth/setup")
def api_auth_setup(body: SetupBody):
    try:
        auth.create_account(body.username, body.password)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@app.post("/api/auth/login")
def api_auth_login(body: LoginBody):
    if not auth.verify_login(body.username, body.password):
        raise HTTPException(401, "ชื่อผู้ใช้หรือรหัสผ่านไม่ถูกต้อง")
    token, ttl = auth.create_session(body.username, body.remember)
    resp = JSONResponse({"ok": True, "username": body.username.strip().lower()})
    resp.set_cookie(SESSION_COOKIE, token, max_age=ttl if body.remember else None,
                    httponly=True, samesite="lax")
    return resp


@app.post("/api/auth/logout")
def api_auth_logout(request: Request):
    auth.destroy_session(request.cookies.get(SESSION_COOKIE))
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(SESSION_COOKIE)
    return resp


def _prepared_cfg(lab_name):
    cfg = _find_config_for(lab_name)
    if cfg is None:
        raise HTTPException(404, f"ไม่พบ config ของ lab '{lab_name}'")
    try:
        return labctl.apply_product(labctl.validate_config(dict(cfg)))
    except labctl.ConfigError as e:
        raise HTTPException(400, str(e))


def _passwords_for(lab_name, lab_info, outputs):
    """ถอดรหัสผ่าน Windows ต่อ user — ใช้ key ใน keys/ ถ้ามี ไม่มีก็เขียนจาก outputs"""
    enc = outputs.get("windows_passwords_encrypted", {})
    keys = outputs.get("private_keys", {})
    key_dir = labctl.KEYS_DIR / lab_name
    result = {}
    for user, vm in lab_info.items():
        if not vm["is_windows"]:
            continue
        key_path = key_dir / f"{user}.pem"
        if not key_path.exists() and user in keys:
            key_dir.mkdir(parents=True, exist_ok=True)
            key_path.write_text(keys[user], encoding="utf-8")
            key_path.chmod(stat.S_IRUSR | stat.S_IWUSR)
        pw = labctl.decrypt_windows_password(enc.get(user), key_path) \
            if key_path.exists() else None
        result[user] = pw
    return result


# ---------------------------------------------------------------------------
# aws connection
# ---------------------------------------------------------------------------

@app.get("/api/aws/status")
def api_aws_status(force: bool = False):
    return aws.status(force=force)


class AwsLoginBody(BaseModel):
    access_key_id: str
    secret_access_key: str
    session_token: Optional[str] = None
    region: Optional[str] = None
    remember: bool = False


@app.post("/api/aws/login")
def api_aws_login(body: AwsLoginBody):
    try:
        return aws.login(body.access_key_id, body.secret_access_key,
                         body.session_token, body.region, body.remember)
    except aws.AwsError as e:
        raise HTTPException(400, str(e))


# ---------------------------------------------------------------------------
# products & labs
# ---------------------------------------------------------------------------

@app.get("/api/products")
def api_products():
    out = []
    for name, meta in labctl.list_products().items():
        out.append({
            "id": name,
            "product_name": meta.get("product_name", name),
            "description": meta.get("description", ""),
            "os": meta.get("os"),
            "instance_type": meta.get("instance_type"),
            "root_volume_gb": meta.get("root_volume_gb"),
            "params": meta.get("params", {}),
        })
    return {"products": out, "supported_os": labctl.SUPPORTED_OS}


@app.get("/api/labs")
def api_labs():
    rows = {r["lab_name"]: r for r in db.list_labs()}
    # lab ที่สร้างจาก CLI (มี tfvars แต่ไม่มีแถวใน DB) — โชว์ด้วย ไม่ปล่อยหาย
    for varfile in (BASE_DIR / "terraform").glob("*.tfvars.json"):
        name = varfile.stem.replace(".tfvars", "")
        if name in rows:
            continue
        try:
            tfvars = json.loads(varfile.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        cfg = _find_config_for(name) or {}
        rows[name] = {
            "lab_name": name, "product": cfg.get("product"),
            "status": "active", "created_at": varfile.stat().st_mtime,
            "expire_date": tfvars.get("expire_date") or cfg.get("expire_date"),
            "config_json": None, "_users": len(tfvars.get("users", [])),
        }
    labs = []
    for r in sorted(rows.values(), key=lambda x: x["created_at"], reverse=True):
        cfg = json.loads(r["config_json"]) if r.get("config_json") else (_find_config_for(r["lab_name"]) or {})
        products = labctl.list_products()
        pname = products.get(r["product"], {}).get("product_name") if r.get("product") else None
        prod_meta = products.get(r["product"], {}) if r.get("product") else {}
        job = db.latest_job_for_lab(r["lab_name"])
        labs.append({
            "lab_name": r["lab_name"],
            "product": r.get("product"),
            "product_name": pname,
            "title": cfg.get("title"),
            "users": len(cfg.get("users", [])) or r.get("_users", 0),
            "status": r["status"],
            "expire_date": r.get("expire_date"),
            "expires_at": r.get("expires_at"),
            "os": cfg.get("os") or prod_meta.get("os"),
            "instance_type": cfg.get("instance_type") or prod_meta.get("instance_type"),
            "job": {"id": job["id"], "state": job["state"], "kind": job["kind"],
                    "error": job["error"]} if job else None,
        })
    return {"labs": labs}


class CreateLabBody(BaseModel):
    lab_name: str
    users: List[str]
    product: Optional[str] = None
    product_params: Dict[str, str] = {}
    os: Optional[str] = None
    region: Optional[str] = None
    instance_type: Optional[str] = None
    root_volume_gb: Optional[int] = None
    extra_open_ports: Optional[List[int]] = None
    allowed_cidr: Optional[str] = None
    expire_date: Optional[str] = None
    title: Optional[str] = None
    custom_ami: Optional[str] = None
    custom_username: Optional[str] = None
    custom_is_windows: Optional[bool] = None
    # ชั่วโมงก่อน lab ถูกลบอัตโนมัติ — default 8, 0 = ปิด auto-expire
    ttl_hours: Optional[float] = 8


@app.post("/api/labs", status_code=202)
def api_create_lab(body: CreateLabBody):
    cfg = {k: v for k, v in body.model_dump().items() if v not in (None, "", [], {})}
    # custom_is_windows=False มีความหมาย — อย่าให้โดนตัดทิ้งตอนกรองค่าว่าง
    if body.custom_ami and body.custom_is_windows is not None:
        cfg["custom_is_windows"] = body.custom_is_windows
    ttl = cfg.pop("ttl_hours", 8) or 0
    if ttl < 0 or ttl > 24 * 14:
        raise HTTPException(400, "ttl_hours ต้องอยู่ระหว่าง 0 (ปิด) ถึง 336 (14 วัน)")
    if "users" not in cfg:
        cfg["users"] = []
    try:
        cfg = labctl.apply_product(labctl.validate_config(cfg))
    except labctl.ConfigError as e:
        raise HTTPException(400, str(e))
    if not aws.resolve_credentials():
        raise HTTPException(400, "ยังไม่ได้เชื่อมต่อ AWS — ไปที่หน้า 'เชื่อมต่อ AWS' ก่อน แล้วค่อยสร้าง lab")
    existing = db.get_lab(cfg["lab_name"])
    if existing and existing["status"] not in ("destroyed", "error"):
        raise HTTPException(409, f"lab '{cfg['lab_name']}' มีอยู่แล้ว (สถานะ {existing['status']})")
    # เก็บเฉพาะ field ที่มาจากผู้ใช้ (ไม่เอา _user_data / _product_params ที่ derive ได้)
    stored = {k: v for k, v in cfg.items() if not k.startswith("_")}
    import time as _time
    expires_at = (_time.time() + ttl * 3600) if ttl else None
    db.upsert_lab(cfg["lab_name"], cfg.get("product"), "queued",
                  cfg.get("expire_date"), stored,
                  ttl_hours=ttl or None, expires_at=expires_at)
    job_id = jobs.enqueue("create", cfg)
    return {"job_id": job_id, "lab_name": cfg["lab_name"]}


@app.get("/api/labs/{lab_name}")
def api_lab_detail(lab_name: str):
    row = db.get_lab(lab_name)
    cfg = _find_config_for(lab_name)
    if not row and not cfg:
        raise HTTPException(404, f"ไม่พบ lab '{lab_name}'")
    status = row["status"] if row else "active"
    info = {
        "lab_name": lab_name,
        "status": status,
        "product": (row or {}).get("product") or (cfg or {}).get("product"),
        "title": (cfg or {}).get("title"),
        "os": (cfg or {}).get("os"),
        "region": (cfg or {}).get("region", "ap-southeast-1"),
        "expire_date": (row or {}).get("expire_date") or (cfg or {}).get("expire_date"),
        "expires_at": (row or {}).get("expires_at"),
        "instance_type": (cfg or {}).get("instance_type"),
        "config": {k: v for k, v in (cfg or {}).items() if not k.startswith("_")} or None,
        "job": db.latest_job_for_lab(lab_name),
        "machines": [],
    }
    if cfg and info["product"]:
        products = labctl.list_products()
        info["product_name"] = products.get(info["product"], {}).get("product_name")
    if status in ("active", "provisioning", "error"):
        try:
            outputs = jobs.tf_outputs_for(lab_name)
        except jobs.JobFailure:
            outputs = None
        if outputs:
            lab_info = outputs["lab_info"]
            passwords = _passwords_for(lab_name, lab_info, outputs)
            handout_dir = labctl.HANDOUTS_DIR / lab_name
            for user, vm in sorted(lab_info.items()):
                zip_path = handout_dir / f"{lab_name}-{user}.zip"
                info["machines"].append({
                    "user": user,
                    "public_ip": vm["public_ip"],
                    "username": vm["username"],
                    "os": vm["os"],
                    "is_windows": vm["is_windows"],
                    "instance_id": vm["instance_id"],
                    "password": passwords.get(user),
                    "handout_ready": zip_path.exists(),
                })
            if not info["os"]:
                info["os"] = next(iter(lab_info.values()))["os"]
    return info


# ---------------------------------------------------------------------------
# jobs
# ---------------------------------------------------------------------------

@app.get("/api/jobs/{job_id}")
def api_job(job_id: str):
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(404, "ไม่พบ job")
    return job


@app.get("/api/jobs/{job_id}/events")
def api_job_events(job_id: str):
    return StreamingResponse(
        jobs.sse_events(job_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------------
# refresh / destroy
# ---------------------------------------------------------------------------

@app.post("/api/labs/{lab_name}/refresh", status_code=202)
def api_refresh(lab_name: str):
    cfg = _prepared_cfg(lab_name)
    job_id = jobs.enqueue("refresh", cfg)
    return {"job_id": job_id, "lab_name": lab_name}


class DestroyBody(BaseModel):
    confirm: str


@app.delete("/api/labs/{lab_name}", status_code=202)
def api_destroy(lab_name: str, body: DestroyBody):
    if body.confirm != lab_name:
        raise HTTPException(400, "ต้องพิมพ์ชื่อ lab ให้ตรงเพื่อยืนยันการลบ")
    cfg = _prepared_cfg(lab_name)
    job_id = jobs.enqueue("destroy", cfg)
    return {"job_id": job_id, "lab_name": lab_name}


# ---------------------------------------------------------------------------
# handouts
# ---------------------------------------------------------------------------

@app.get("/api/labs/{lab_name}/handouts/export")
def api_export_all(lab_name: str):
    handout_dir = labctl.HANDOUTS_DIR / lab_name
    if not handout_dir.exists():
        raise HTTPException(404, "ยังไม่มี handout ของ lab นี้ — รัน refresh ก่อน")
    out_base = labctl.HANDOUTS_DIR / f"{lab_name}-all"
    zip_path = out_base.with_suffix(".zip")
    if zip_path.exists():
        zip_path.unlink()
    shutil.make_archive(str(out_base), "zip", root_dir=handout_dir)
    return FileResponse(zip_path, filename=f"{lab_name}-all.zip")


@app.get("/api/labs/{lab_name}/handouts/{user}.zip")
def api_handout_zip(lab_name: str, user: str):
    zip_path = labctl.HANDOUTS_DIR / lab_name / f"{lab_name}-{user}.zip"
    # กัน path traversal — ชื่อที่ validate แล้วเท่านั้นจะชี้เข้าโฟลเดอร์ handout ได้
    if not zip_path.resolve().is_relative_to(labctl.HANDOUTS_DIR.resolve()) \
            or not zip_path.exists():
        raise HTTPException(404, "ไม่พบ handout — รัน refresh เพื่อสร้างใหม่")
    return FileResponse(zip_path, filename=zip_path.name)


# static frontend — mount ท้ายสุดเพื่อไม่บัง /api/*
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
