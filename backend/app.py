"""FastAPI layer บาง ๆ หน้า labctl.py — ทุก endpoint จับคู่กับฟังก์ชันที่มีอยู่แล้ว

รัน: python3 labctl.py serve   (127.0.0.1:8787 — ขอบเขตความเชื่อถือเดียวกับ CLI)
"""

import json
import re
import shutil
import stat
import time
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
async def _no_stale_frontend(request: Request, call_next):
    """บังคับให้ browser ถามเซิร์ฟเวอร์ก่อนใช้ไฟล์หน้าเว็บที่แคชไว้

    ES module ถูกแคชแรงเป็นพิเศษ — แก้ app.js แล้วผู้ใช้ยังเห็นของเก่าจนกว่าจะ
    hard reload ซึ่งไม่มีใครเดาถูก (เสียเวลาไล่บั๊กที่แก้ไปแล้ว)
    ETag ยังทำงานปกติ การ revalidate จึงตอบ 304 ไม่ได้โหลดใหม่ทั้งไฟล์
    """
    resp = await call_next(request)
    if not request.url.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-cache, must-revalidate"
    return resp


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
    # default = จำลงดิสก์ ให้ตรงกับที่หน้าเว็บติ๊กไว้ — ถ้าไม่จำ คีย์จะอยู่แค่ใน
    # process นี้ แล้วหายทุกครั้งที่ปิด server ทำให้ CLI มองไม่เห็นด้วย
    remember: bool = True


@app.post("/api/aws/login")
def api_aws_login(body: AwsLoginBody):
    try:
        return aws.login(body.access_key_id, body.secret_access_key,
                         body.session_token, body.region, body.remember)
    except aws.AwsError as e:
        raise HTTPException(400, str(e))


def _known_regions():
    """ทุก region ที่ lab ในเครื่องนี้เคยถูกสร้าง + region ปัจจุบันของ credential
    — ตรวจแค่ region เดียวจะพลาด lab เก่าที่สร้างไว้คนละ region"""
    regions = []
    for lab in db.list_labs():
        cfg = _find_config_for(lab["lab_name"]) or {}
        r = cfg.get("region")
        if r and r not in regions:
            regions.append(r)
    cur = aws.default_region() or aws.DEFAULT_REGION
    if cur not in regions:
        regions.append(cur)
    return regions


@app.get("/api/aws/orphans")
def api_aws_orphans(lab: Optional[str] = None, region: Optional[str] = None):
    """ถาม AWS ตรง ๆ ว่ายังมี resource ติด tag Project=hands-on-lab เหลืออยู่ไหม

    ไม่พึ่ง terraform state เลย — ใช้ยืนยันหลัง destroy ล้ม หรือหลัง state หาย"""
    try:
        return aws.scan_tagged_resources(regions=[region] if region else _known_regions(),
                                         lab_name=lab)
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
            # หน้า wizard เอาไปบอกว่า product นี้มี guide มาตรฐานให้อยู่แล้ว
            "lab_guide": meta.get("lab_guide"),
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


GUIDE_MAX_BYTES = 512 * 1024
# PDF ใหญ่กว่า markdown มาก และถูกคัดลอกเข้า zip ของผู้เรียน "ทุกคน" —
# 20 MB × 20 คน = 400 MB บนดิสก์ จึงต้องมีเพดานที่สมเหตุสมผล
GUIDE_PDF_MAX_BYTES = 20 * 1024 * 1024


def _decode_pdf(b64):
    """แปลง base64 ที่หน้าเว็บส่งมาเป็นไบต์ + กันไฟล์ใหญ่เกิน

    รับ data URL ด้วย (FileReader.readAsDataURL คืนรูปแบบนั้น) จะได้ไม่ต้องให้
    ฝั่งหน้าเว็บมาตัด prefix เอง
    """
    import base64
    if "," in b64[:100] and b64.lstrip().startswith("data:"):
        b64 = b64.split(",", 1)[1]
    try:
        data = base64.b64decode(b64, validate=True)
    except (ValueError, TypeError):
        raise HTTPException(400, "ไฟล์ PDF ที่ส่งมาถอดรหัสไม่ได้")
    if len(data) > GUIDE_PDF_MAX_BYTES:
        raise HTTPException(400, f"ไฟล์ PDF ใหญ่เกิน {GUIDE_PDF_MAX_BYTES // (1024*1024)} MB")
    if not data:
        raise HTTPException(400, "ไฟล์ PDF ว่างเปล่า")
    return data


def _resolve_guide(lab_name, guide, markdown, pdf_base64=None):
    """คืน path ของ lab guide ที่จะใช้ — จากไฟล์ที่อัปโหลดมา หรือไฟล์ที่มีอยู่แล้ว

    ใช้ร่วมกันระหว่างตอนสร้าง lab กับตอนเปลี่ยน guide ทีหลัง กติกาจะได้ไม่หลุดกัน
    """
    if pdf_base64:
        try:
            return labctl.save_custom_guide_pdf(lab_name, _decode_pdf(pdf_base64))
        except labctl.ConfigError as e:
            raise HTTPException(400, str(e))
    if markdown is not None and markdown.strip():
        if len(markdown.encode("utf-8")) > GUIDE_MAX_BYTES:
            raise HTTPException(400, f"ไฟล์ lab guide ใหญ่เกิน {GUIDE_MAX_BYTES // 1024} KB")
        try:
            return labctl.save_custom_guide(lab_name, markdown)
        except labctl.ConfigError as e:
            raise HTTPException(400, str(e))
    if guide:
        # ต้องเป็นไฟล์ที่อยู่ในรายการจริง — กัน path ที่พาไปอ่านไฟล์อื่นในเครื่อง
        if guide not in {g["path"] for g in labctl.list_lab_guides()}:
            raise HTTPException(400, f"ไม่รู้จัก lab guide '{guide}'")
        return guide
    return None


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
    # hands-on lab guide ที่แนบมาตั้งแต่ตอนสร้าง — เลือกไฟล์ที่มีอยู่ (lab_guide)
    # หรือส่งเนื้อไฟล์ที่ผู้สอนอัปโหลดมา (lab_guide_markdown)
    lab_guide: Optional[str] = None
    lab_guide_markdown: Optional[str] = None
    # lab guide ที่อัปโหลดมาเป็น PDF (base64 หรือ data URL จาก FileReader)
    lab_guide_pdf: Optional[str] = None
    # spot instance — ถูกกว่ามากแต่ถูกยึดคืนได้ เหมาะกับ lab สั้น ๆ ที่สร้างใหม่เร็ว
    use_spot: Optional[bool] = None


@app.post("/api/labs", status_code=202)
def api_create_lab(body: CreateLabBody):
    cfg = {k: v for k, v in body.model_dump().items() if v not in (None, "", [], {})}
    # custom_is_windows=False มีความหมาย — อย่าให้โดนตัดทิ้งตอนกรองค่าว่าง
    if body.custom_ami and body.custom_is_windows is not None:
        cfg["custom_is_windows"] = body.custom_is_windows
    ttl = cfg.pop("ttl_hours", 8) or 0
    if ttl < 0 or ttl > 24 * 14:
        raise HTTPException(400, "ttl_hours ต้องอยู่ระหว่าง 0 (ปิด) ถึง 336 (14 วัน)")
    guide_sel = cfg.pop("lab_guide", None)
    guide_md = cfg.pop("lab_guide_markdown", None)
    guide_pdf = cfg.pop("lab_guide_pdf", None)
    if "users" not in cfg:
        cfg["users"] = []
    try:
        cfg = labctl.apply_product(labctl.validate_config(cfg))
    except labctl.ConfigError as e:
        raise HTTPException(400, str(e))
    # แนบหลัง apply_product เพื่อให้ guide ที่ผู้สอนเลือกเองชนะ guide ประจำ product
    guide_path = _resolve_guide(cfg["lab_name"], guide_sel, guide_md, guide_pdf)
    if guide_path:
        cfg["lab_guide"] = guide_path
    # tag Expire บน resource ต้องตรงกับเวลาที่ lab จะถูกลบจริง ไม่ใช่ค่าที่พิมพ์มาเอง
    derived_expire = labctl.expire_date_from_ttl(ttl)
    if derived_expire:
        cfg["expire_date"] = derived_expire
    # ยืนยันกับ STS เลย ไม่ใช่แค่ดูว่ามีคีย์อยู่ — คีย์ที่หมดอายุจะได้ถูกปัดตกตรงนี้
    # แทนที่จะปล่อยให้ job ไปตายตอน apply แล้วทิ้ง lab สถานะ error ไว้
    try:
        aws.resolve_verified()
    except aws.AwsError as e:
        raise HTTPException(400, str(e))
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
        # ปุ่ม "สร้างซ้ำ" ใช้ค่านี้เติมฟอร์ม — ไม่ส่งกลับไปจะกลายเป็น default 8 ทุกครั้ง
        "ttl_hours": (row or {}).get("ttl_hours"),
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
        except (jobs.JobFailure, ValueError, OSError) as e:
            # อ่าน output ไม่ได้ (terraform ยังไม่ได้ติดตั้ง / state ยังไม่มี /
            # JSON เพี้ยน) ไม่ควรทำให้หน้า lab พังทั้งหน้า — แสดงสถานะเท่าที่มี
            outputs = None
            info["tf_error"] = str(e)
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


@app.get("/api/labs/{lab_name}/reachability")
def api_reachability(lab_name: str, timeout: float = 3.0):
    """เช็คว่าพอร์ต SSH/RDP ของแต่ละเครื่องเปิดรับแล้วหรือยัง

    ใช้ก่อนเริ่มคลาส — รู้ก่อนว่าเครื่องไหนยังไม่พร้อม ดีกว่ารอผู้เรียนยกมือ
    เช็คแค่ว่า TCP ต่อติดไหม ไม่ได้ล็อกอินจริง
    """
    import socket
    from concurrent.futures import ThreadPoolExecutor

    detail = api_lab_detail(lab_name)
    machines = detail.get("machines") or []

    def probe(m):
        port = 3389 if m["is_windows"] else 22
        t0 = time.time()
        try:
            with socket.create_connection((m["public_ip"], port), timeout=timeout):
                return {"user": m["user"], "port": port, "reachable": True,
                        "ms": int((time.time() - t0) * 1000)}
        except OSError as e:
            return {"user": m["user"], "port": port, "reachable": False,
                    "error": e.strerror or str(e)}

    if not machines:
        return {"checked_at": time.time(), "results": [], "ready": 0, "total": 0}
    # ยิงพร้อมกัน — 10 เครื่องต่อกันทีละตัวด้วย timeout 3 วิ ใช้เวลาเกินครึ่งนาที
    with ThreadPoolExecutor(max_workers=min(10, len(machines))) as ex:
        results = list(ex.map(probe, machines))
    return {"checked_at": time.time(), "results": results,
            "ready": sum(1 for r in results if r["reachable"]), "total": len(results)}


# ---------------------------------------------------------------------------
# lab guide — แนบ/เปลี่ยน guide หลัง lab ถูกสร้างไปแล้ว
# ---------------------------------------------------------------------------

@app.get("/api/lab-guides")
def api_lab_guides():
    return {"guides": labctl.list_lab_guides()}


class DeleteGuideBody(BaseModel):
    path: str


@app.post("/api/lab-guides/delete")
def api_delete_guide(body: DeleteGuideBody):
    """ลบ guide ที่อัปโหลดเอง — กันไม่ให้ลบตัวที่ lab ซึ่งยังไม่ถูก destroy ใช้อยู่"""
    in_use = [lab["lab_name"] for lab in db.list_labs()
              if lab["status"] != "destroyed"
              and (json.loads(lab["config_json"]) if lab.get("config_json") else {}
                   ).get("lab_guide") == body.path]
    if in_use:
        raise HTTPException(409, f"ลบไม่ได้ — ยังถูกใช้โดย lab: {', '.join(in_use)}")
    try:
        labctl.delete_custom_guide(body.path)
    except labctl.ConfigError as e:
        raise HTTPException(400, str(e))
    return {"deleted": body.path}


class GuideBody(BaseModel):
    # เลือกไฟล์ที่มีอยู่แล้ว…
    guide: Optional[str] = None
    # …หรือวาง markdown เข้ามาตรง ๆ (จะถูกเก็บเป็น lab-guides/<lab>-custom.md)
    markdown: Optional[str] = None
    # …หรืออัปโหลดเป็น PDF (เก็บเป็น lab-guides/<lab>-custom.pdf)
    pdf: Optional[str] = None


@app.get("/api/labs/{lab_name}/guide")
def api_get_guide(lab_name: str):
    """guide ที่ lab นี้ใช้อยู่ + เนื้อหา (ให้หน้าเว็บเอาไปแก้ต่อได้)"""
    cfg = _find_config_for(lab_name)
    if cfg is None:
        raise HTTPException(404, f"ไม่พบ config ของ lab '{lab_name}'")
    path = cfg.get("lab_guide") or None
    content, exists, kind = None, False, None
    if path:
        p = labctl.BASE_DIR / path
        exists = p.exists()
        kind = "pdf" if path.lower().endswith(".pdf") else "markdown"
        # PDF ไม่ส่งเนื้อไฟล์กลับ — หน้าเว็บเอาไปแก้ต่อไม่ได้อยู่แล้ว และไฟล์ใหญ่
        if exists and kind == "markdown":
            content = p.read_text(encoding="utf-8")
    return {"lab_guide": path, "exists": exists, "kind": kind, "content": content,
            "size": (labctl.BASE_DIR / path).stat().st_size if path and exists else None}


@app.put("/api/labs/{lab_name}/guide", status_code=202)
def api_set_guide(lab_name: str, body: GuideBody):
    """แนบ/เปลี่ยน lab guide แล้วสร้าง handout ใหม่ — ไม่แตะ resource บน AWS"""
    row = db.get_lab(lab_name)
    file_cfg = _find_config_for(lab_name)
    if not row and not file_cfg:
        raise HTTPException(404, f"ไม่พบ lab '{lab_name}'")
    # lab ที่สร้างจาก CLI ไม่มีแถวใน db — ถือว่า active เหมือนที่หน้า detail ทำ
    status = row["status"] if row else "active"
    if status != "active":
        raise HTTPException(409, f"lab '{lab_name}' สถานะ {status} — "
                                 "สร้าง handout ใหม่ได้เฉพาะ lab ที่ active")

    guide_path = _resolve_guide(lab_name, body.guide, body.markdown, body.pdf)
    if not guide_path:
        raise HTTPException(400, "ต้องระบุ guide: เลือกไฟล์ที่มีอยู่ วาง markdown หรืออัปโหลด PDF")

    if row and row.get("config_json"):
        stored = json.loads(row["config_json"])
    else:
        stored = dict(file_cfg or {})
    stored["lab_guide"] = guide_path
    if row:
        db.update_lab_config(lab_name, stored)
    else:
        db.upsert_lab(lab_name, stored.get("product"), "active",
                      stored.get("expire_date"), stored)

    try:
        cfg = labctl.apply_product(labctl.validate_config(dict(stored)))
    except labctl.ConfigError as e:
        raise HTTPException(400, str(e))
    job_id = jobs.enqueue("export", cfg)
    return {"job_id": job_id, "lab_name": lab_name, "lab_guide": guide_path}


# ---------------------------------------------------------------------------
# ต่อเวลา / reset เครื่องรายคน
# ---------------------------------------------------------------------------

class ExtendBody(BaseModel):
    hours: float = 1.0


@app.post("/api/labs/{lab_name}/extend")
def api_extend(lab_name: str, body: ExtendBody):
    """เลื่อนเวลาลบอัตโนมัติออกไป — ใช้ตอนคลาสยืดเวลา จะได้ไม่ต้องสร้าง lab ใหม่"""
    row = db.get_lab(lab_name)
    if not row:
        raise HTTPException(404, f"ไม่พบ lab '{lab_name}'")
    if row["status"] != "active":
        raise HTTPException(409, f"lab '{lab_name}' สถานะ {row['status']} — ต่อเวลาได้เฉพาะ lab ที่ active")
    if body.hours <= 0 or body.hours > labctl.TTL_MAX_HOURS:
        raise HTTPException(400, f"ต่อเวลาได้ครั้งละ 0–{labctl.TTL_MAX_HOURS} ชั่วโมง")
    import time as _t
    now = _t.time()
    # ต่อจากเวลาเดิมถ้ายังไม่หมด · ต่อจากตอนนี้ถ้าเลยกำหนดไปแล้ว
    base = max(row["expires_at"] or now, now)
    new_exp = base + body.hours * 3600
    total_ttl = (new_exp - now) / 3600
    new_date = labctl.expire_date_from_ttl(total_ttl)
    db.update_lab_expiry(lab_name, new_exp, ttl_hours=total_ttl, expire_date=new_date)
    if row.get("config_json"):
        stored = json.loads(row["config_json"])
        stored["expire_date"] = new_date
        db.update_lab_config(lab_name, stored)
    jobs.clear_expiry_warning(lab_name)
    return {"lab_name": lab_name, "expires_at": new_exp, "expire_date": new_date,
            "added_hours": body.hours,
            # tag Expire บน AWS ยังเป็นค่าเดิมจนกว่าจะ apply รอบถัดไป — บอกให้ชัด
            "note": "tag Expire บน resource จะอัปเดตเมื่อ apply ครั้งถัดไป"}


class PowerBody(BaseModel):
    action: str          # stop | start


@app.post("/api/labs/{lab_name}/power")
def api_power(lab_name: str, body: PowerBody):
    """หยุด/เปิดเครื่องทั้ง lab — ประหยัดค่า compute ตอนคลาสข้ามคืน

    ไม่ลบอะไร: EBS ยังอยู่ ข้อมูลในเครื่องยังอยู่ · public IP จะเปลี่ยนหลัง start
    จึงต้องสร้าง handout ใหม่ตามด้วย
    """
    if body.action not in ("stop", "start"):
        raise HTTPException(400, "action ต้องเป็น stop หรือ start")
    row = db.get_lab(lab_name)
    if row and row["status"] not in ("active",):
        raise HTTPException(409, f"lab '{lab_name}' สถานะ {row['status']}")
    try:
        outputs = jobs.tf_outputs_for(lab_name)
    except (jobs.JobFailure, ValueError, OSError) as e:
        raise HTTPException(400, f"อ่านข้อมูลเครื่องไม่ได้: {e}")
    if not outputs:
        raise HTTPException(404, f"ไม่พบเครื่องของ lab '{lab_name}'")
    ids = [vm["instance_id"] for vm in outputs["lab_info"].values()]
    cfg = _find_config_for(lab_name) or {}
    try:
        res = aws.power_instances(ids, body.action, region=cfg.get("region"))
    except aws.AwsError as e:
        # spot แบบ one-time หยุดไม่ได้ตามข้อกำหนดของ AWS — เป็นคู่ที่คนเลือกผิดบ่อย
        # (เลือก spot เพื่อประหยัด แล้วอยากหยุดเครื่องข้ามคืนเพื่อประหยัดอีก)
        if e.code == "UnsupportedOperation" and cfg.get("use_spot"):
            raise HTTPException(400,
                "lab นี้ใช้ spot instance ซึ่ง AWS ไม่ให้หยุดชั่วคราว — "
                "ถ้าต้องการ stop/start ข้ามคืน ให้สร้าง lab แบบปกติ (ไม่ใส่ spot) "
                "หรือถ้าจบคลาสแล้วก็ destroy ทิ้งได้เลย")
        raise HTTPException(400, str(e))
    if body.action == "start":
        # IP ใหม่หลังเปิดเครื่อง — handout เดิมจะพาผู้เรียนไปผิดที่
        res["note"] = "IP จะเปลี่ยนหลังเครื่องบูตเสร็จ — กด Refresh เพื่อสร้าง handout ใหม่"
    return res


@app.post("/api/labs/{lab_name}/users/{user}/reset", status_code=202)
def api_reset_user(lab_name: str, user: str):
    """สร้างเครื่องของผู้เรียนคนเดียวใหม่ — เครื่องคนอื่นในคลาสไม่ถูกแตะ"""
    cfg = _prepared_cfg(lab_name)
    if user not in cfg.get("users", []):
        raise HTTPException(404, f"ไม่มีผู้เรียนชื่อ '{user}' ใน lab นี้")
    cfg = dict(cfg)
    cfg["_reset_user"] = user
    job_id = jobs.enqueue("reset", cfg)
    return {"job_id": job_id, "lab_name": lab_name, "user": user}


MAX_USERS_PER_LAB = 200


class AddUsersBody(BaseModel):
    count: Optional[int] = None          # สร้างชื่อ userNN ต่อจากเลขสุดท้ายให้เอง
    users: Optional[List[str]] = None    # หรือระบุชื่อเอง
    os: Optional[str] = None             # ไม่ระบุ = OS เดียวกับคลาส


def _next_user_names(existing, count):
    """userNN ถัดจากเลขที่มากที่สุดที่มีอยู่ — ไม่ชนชื่อเดิม"""
    nums = [int(m.group(1)) for u in existing
            if (m := re.fullmatch(r"user(\d+)", u))]
    start = max(nums, default=0) + 1
    return [f"user{n:02d}" for n in range(start, start + count)]


@app.post("/api/labs/{lab_name}/users", status_code=202)
def api_add_users(lab_name: str, body: AddUsersBody):
    """เพิ่มเครื่องให้ผู้เรียนใหม่ใน lab ที่รันอยู่ — terraform for_each สร้างเฉพาะคนที่เพิ่ม"""
    row = db.get_lab(lab_name)
    if not row or not row.get("config_json"):
        raise HTTPException(404, f"ไม่พบ lab '{lab_name}' ที่สร้างจากหน้าเว็บ")
    if row["status"] != "active":
        raise HTTPException(409, f"lab '{lab_name}' สถานะ {row['status']} — เพิ่มเครื่องได้เฉพาะ lab ที่ active")
    stored = json.loads(row["config_json"])
    existing = list(stored.get("users", []))
    if body.users:
        new = [u.strip().lower() for u in body.users if u and u.strip()]
    elif body.count:
        if body.count < 1:
            raise HTTPException(400, "จำนวนเครื่องต้องมากกว่า 0")
        new = _next_user_names(existing, body.count)
    else:
        raise HTTPException(400, "ระบุ count หรือ users")
    dupes = sorted(set(new) & set(existing)) or sorted({u for u in new if new.count(u) > 1})
    if dupes:
        raise HTTPException(400, f"มีชื่อนี้อยู่แล้ว: {', '.join(dupes)}")
    if len(existing) + len(new) > MAX_USERS_PER_LAB:
        raise HTTPException(400, f"lab หนึ่งมีได้ไม่เกิน {MAX_USERS_PER_LAB} เครื่อง")
    updated = {**stored, "users": existing + new}
    lab_os = None if stored.get("custom_ami") else stored.get("os", "amazon-linux-2023")
    if body.os and body.os != lab_os:
        if body.os not in labctl.SUPPORTED_OS:
            raise HTTPException(400, f"os '{body.os}' ไม่รองรับ")
        updated["user_os"] = {**stored.get("user_os", {}), **{u: body.os for u in new}}
    try:
        cfg = labctl.apply_product(labctl.validate_config(dict(updated)))
    except labctl.ConfigError as e:
        raise HTTPException(400, str(e))
    try:
        aws.resolve_verified()
    except aws.AwsError as e:
        raise HTTPException(400, str(e))
    # บันทึกก่อน apply — ถ้า apply ล้มกลางทาง กดเพิ่ม/Refresh รอบหน้าจะได้ไม่ลบเครื่องที่สร้างไปแล้ว
    db.update_lab_config(lab_name, updated)
    job_id = jobs.enqueue("add_users", {**cfg, "_new_users": new})
    return {"job_id": job_id, "lab_name": lab_name, "users": new}


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
    # ลบไฟล์ guide ที่อัปโหลดไว้ให้ lab นี้ด้วยไหม (เฉพาะ *-custom.md ของ lab นี้)
    delete_guide: Optional[bool] = False   # null = ไม่ลบ (หน้าเว็บเวอร์ชันเก่าส่ง null มา)


@app.delete("/api/labs/{lab_name}", status_code=202)
def api_destroy(lab_name: str, body: DestroyBody):
    if body.confirm != lab_name:
        raise HTTPException(400, "ต้องพิมพ์ชื่อ lab ให้ตรงเพื่อยืนยันการลบ")
    cfg = _prepared_cfg(lab_name)
    if body.delete_guide:
        cfg["_delete_guide"] = True  # ขึ้นต้นด้วย _ = ไม่ถูกเก็บลง config ของ lab
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
