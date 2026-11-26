"""Job runner — รัน terraform เบื้องหลังทีละงาน (สร้าง/refresh/destroy lab)

ทำไม max_workers=1: terraform อนุญาต operation เดียวต่อ workspace อยู่แล้ว
และ deployment model คือ instructor คนเดียวบนเครื่องตัวเอง — คิวเดียวพอ

ทำไม TF_WORKSPACE แทน `terraform workspace select`: การ select เปลี่ยน
global state (.terraform/environment) ซึ่งจะชนกันถ้า API อ่าน output ของ
lab หนึ่งระหว่างที่ job กำลัง apply อีก lab หนึ่ง — ส่ง env var แยกต่อ
subprocess ปลอดภัยกว่า
"""

import contextlib
import io
import os
import subprocess
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import labctl
from . import aws, db

BASE_DIR = Path(__file__).resolve().parent.parent
TF_DIR = BASE_DIR / "terraform"
LOGS_DIR = BASE_DIR / "logs" / "jobs"

_executor = ThreadPoolExecutor(max_workers=1)
# กัน terraform สอง process แตะ backend/plugin dir พร้อมกันตอน init
_tf_init_lock = threading.Lock()

# ร่องรอยของ credential พังใน output ของ terraform — เอาไว้แปะคำแนะนำต่อท้าย
# ข้อความ error แทนที่จะโยน stack ดิบ ๆ ของ AWS SDK ใส่หน้าผู้ใช้
_CRED_MARKERS = ("ExpiredToken", "InvalidClientTokenId", "SignatureDoesNotMatch",
                 "UnrecognizedClientException", "AuthFailure",
                 "no EC2 IMDS role found", "failed to refresh cached credentials",
                 "no valid providers in chain", "NoCredentialProviders")


class JobFailure(Exception):
    pass


def _cred_fail_msg():
    return "\n\ncredential ของ AWS หมดอายุหรือใช้ไม่ได้แล้ว\n" + aws.RELOGIN_HINT


def _cred_suffix(text):
    return _cred_fail_msg() if any(m in text for m in _CRED_MARKERS) else ""


def _tfrun(args, lab=None, log=None, capture=False):
    """รัน terraform; lab → ส่ง TF_WORKSPACE, log → stream ทีละบรรทัดลงไฟล์"""
    exe = labctl.terraform_bin()
    if exe is None:
        raise JobFailure(labctl.TF_MISSING_MSG)
    cmd = [exe, f"-chdir={TF_DIR}"] + args
    try:
        env = aws.terraform_env()      # ทางเดียวกับที่ CLI ใช้ — กติกาไม่แยกกันเพี้ยน
    except aws.AwsError as e:
        raise JobFailure(str(e)) from e
    env.pop("AWS_PROFILE", None)       # คีย์ที่ยัดตรง ๆ ต้องชนะโปรไฟล์อื่น
    if lab:
        env["TF_WORKSPACE"] = lab
    else:
        env.pop("TF_WORKSPACE", None)
    if log is not None:
        log.write(f"$ terraform {' '.join(args)}\n")
        log.flush()
        try:
            proc = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True)
        except OSError as e:
            raise JobFailure(f"{labctl.TF_MISSING_MSG}\n(รายละเอียด: {e})") from e
        cred_err = False
        for line in proc.stdout:
            log.write(line)
            log.flush()
            if not cred_err and any(m in line for m in _CRED_MARKERS):
                cred_err = True
        proc.wait()
        if proc.returncode != 0:
            msg = f"terraform {' '.join(args)} ล้มเหลว (exit {proc.returncode})"
            if cred_err:
                msg += _cred_fail_msg()
            raise JobFailure(msg)
        return None
    try:
        res = subprocess.run(cmd, env=env, capture_output=True, text=True)
    except OSError as e:
        raise JobFailure(f"{labctl.TF_MISSING_MSG}\n(รายละเอียด: {e})") from e
    if res.returncode != 0:
        raise JobFailure(f"terraform {' '.join(args)} ล้มเหลว:\n{res.stderr}"
                         + _cred_suffix(res.stderr + res.stdout))
    return res.stdout if capture else None


def _assert_state(lab):
    """destroy ที่ไม่มี state = terraform ลบอะไรไม่ได้เลย แต่จะจบด้วย exit 0
    แล้วเราจะไปตั้งสถานะเป็น destroyed ทั้งที่ resource ยังรัน (และยังคิดเงิน) อยู่บน AWS
    — ต้องดักไว้ ไม่ใช่ปล่อยให้ lab หายไปจากจอเงียบ ๆ"""
    import json
    p = labctl.TF_STATE_DIR / lab / "terraform.tfstate"
    n = None
    if p.exists() and p.stat().st_size:
        try:
            data = json.loads(p.read_text())
            n = sum(len(r.get("instances", [])) for r in data.get("resources", []))
        except (ValueError, OSError):
            n = None
    if n:
        return
    raise JobFailure(
        f"ไม่พบ state ของ '{lab}' ที่ {p} — terraform ลบอะไรไม่ได้\n"
        "ถ้า lab นี้เคยสร้างสำเร็จ แปลว่า resource ยังอยู่บน AWS แต่หลุดจากการติดตามแล้ว\n"
        "→ ไปลบเองใน AWS console โดยกรองด้วย tag LabName=" + lab
        + " (Resource Groups & Tag Editor) แล้วค่อยเคลียร์ lab นี้ออกจากจอ")


def _preflight_aws(log):
    """ยืนยัน credential กับ STS ก่อนเริ่มงาน แล้วจำไว้ยัดให้ terraform ทุก process

    ต้องทำเพราะคีย์ที่ล็อกอินผ่านหน้าเว็บอยู่ใน env ของ process เท่านั้น (restart
    backend แล้วหาย) และ provider ไม่ได้ระบุ profile ไว้ — ถ้าไม่ยัดให้ SDK จะไล่
    หา credential ไม่เจอแล้วไปจบที่ EC2 IMDS timeout"""
    try:
        creds = aws.resolve_verified()
    except aws.AwsError as e:
        raise JobFailure(str(e)) from e
    # ให้ labctl.tf() ที่รันในโปรเซสนี้ (ขั้น export) เห็นคีย์ชุดเดียวกัน
    # ส่วน subprocess terraform รับ env จาก aws.terraform_env() ใน _tfrun อีกที
    os.environ.update(aws.credential_env(creds))
    os.environ.pop("AWS_PROFILE", None)
    log.write(f"AWS: {creds['arn']}\n"
              f"     account {creds['account']} · {creds['region']} · {creds['method']}\n\n")
    log.flush()


def tf_outputs_for(lab):
    """เหมือน labctl.tf_outputs แต่ระบุ workspace ผ่าน env — ใช้จาก API request ได้
    โดยไม่ไปสลับ workspace ใต้เท้า job ที่กำลังรัน"""
    import json
    raw = _tfrun(["output", "-json"], lab=lab, capture=True)
    out = json.loads(raw)
    if "lab_info" not in out:
        return None
    return {k: v["value"] for k, v in out.items()}


def _ensure_init(log):
    with _tf_init_lock:
        if not (TF_DIR / ".terraform").exists():
            _tfrun(labctl.tf_init_args(), log=log)


def _ensure_workspace(lab, log):
    existing = _tfrun(["workspace", "list"], capture=True)
    names = [w.strip().lstrip("* ").strip() for w in existing.splitlines()]
    if lab not in names:
        _tfrun(["workspace", "new", lab], capture=True)
    log.write(f"workspace: {lab}\n")
    log.flush()


def _set_phase(job_id, phase):
    db.update_job(job_id, phase=phase)


def _export_lab(cfg, lab, log):
    """เรียก labctl.export_lab โดยชี้ TF_WORKSPACE ให้ tf() ภายในถูก workspace
    และเก็บข้อความ info() ลง log — job ถูก serialize อยู่แล้วจึงแตะ environ ได้

    คืนรายชื่อ user ที่รหัสผ่าน Windows ยังถอดไม่ได้"""
    os.environ["TF_WORKSPACE"] = lab
    try:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            pending = labctl.export_lab(cfg)
        log.write(buf.getvalue())
        log.flush()
        return pending or []
    finally:
        os.environ.pop("TF_WORKSPACE", None)


# ---------------------------------------------------------------------------
# เฝ้ารอรหัสผ่าน Windows
#
# AWS ปล่อยรหัสผ่าน Administrator หลัง VM บูตเสร็จ (~4-5 นาที) ซึ่งช้ากว่าที่
# terraform apply จบมาก handout รอบแรกของ Windows lab จึงมักไม่มีรหัสผ่าน
# เดิมต้องให้ผู้สอนมานั่งกด Refresh เอง — ตัวนี้เฝ้าให้แล้วสร้าง handout ใหม่
# ทันทีที่ถอดรหัสได้ครบ
# ---------------------------------------------------------------------------

PW_WATCH_INTERVAL = 30
PW_WATCH_MAX_MINUTES = 20

_pw_watching = set()
_pw_lock = threading.Lock()


def _decrypt_pending(lab, pending):
    """ลองถอดรหัสผ่านของ user ที่ยังค้าง — คืนรายชื่อที่ยังถอดไม่ได้"""
    outputs = tf_outputs_for(lab)
    if not outputs:
        return pending
    enc = outputs.get("windows_passwords_encrypted", {})
    key_dir = labctl.KEYS_DIR / lab
    still = []
    for user in pending:
        key_path = key_dir / f"{user}.pem"
        if not key_path.exists() or not labctl.decrypt_windows_password(enc.get(user), key_path):
            still.append(user)
    return still


def start_password_watcher(cfg, pending):
    """เฝ้ารหัสผ่านใน thread แยก — ไม่กิน worker ของคิว job

    ใช้ thread แทนการนอนรอในตัว job เพราะคิวมี worker เดียว ถ้าไป sleep ในนั้น
    lab อื่นที่ผู้สอนสั่งต่อจะค้างรอไปด้วย
    """
    lab = cfg["lab_name"]
    if not pending:
        return
    with _pw_lock:
        if lab in _pw_watching:
            return          # มีตัวเฝ้าอยู่แล้ว อย่าซ้อน
        _pw_watching.add(lab)
    threading.Thread(target=_pw_watch_loop, args=(cfg, list(pending)), daemon=True).start()


def _pw_watch_loop(cfg, pending):
    lab = cfg["lab_name"]
    deadline = time.time() + PW_WATCH_MAX_MINUTES * 60
    try:
        while time.time() < deadline:
            time.sleep(PW_WATCH_INTERVAL)
            row = db.get_lab(lab)
            if row and row["status"] not in ("active", "provisioning"):
                return      # ถูกลบไประหว่างรอ — เลิกเฝ้า
            try:
                pending = _decrypt_pending(lab, pending)
            except (JobFailure, ValueError, OSError):
                continue    # อ่าน output ไม่ได้ชั่วคราว — ลองใหม่รอบหน้า
            if not pending:
                enqueue("export", cfg)
                return
    finally:
        with _pw_lock:
            _pw_watching.discard(lab)


def _run_job(job_id, kind, cfg):
    lab = cfg["lab_name"]
    log_path = Path(db.get_job(job_id)["log_path"])
    log_path.parent.mkdir(parents=True, exist_ok=True)
    db.update_job(job_id, state="running", started_at=time.time())
    try:
        with open(log_path, "w", encoding="utf-8") as log:
            # เช็ค credential ก่อนแตะอะไรทั้งนั้น — ล้มตั้งแต่ก่อนสร้าง workspace
            # ดีกว่าไปล้มตอน apply แล้วทิ้ง workspace เปล่าค้างไว้
            _preflight_aws(log)
            varfile = labctl.build_tfvars(cfg)
            if kind == "create":
                _set_phase(job_id, "init")
                _ensure_init(log)
                _set_phase(job_id, "workspace")
                _ensure_workspace(lab, log)
                _set_phase(job_id, "apply")
                db.set_lab_status(lab, "provisioning")
                _tfrun(["apply", "-auto-approve", "-input=false",
                        f"-var-file={varfile.name}"], lab=lab, log=log)
                _set_phase(job_id, "export")
                pending = _export_lab(cfg, lab, log)
                db.set_lab_status(lab, "active")
                if pending:
                    log.write(f"\n[job] เฝ้ารอรหัสผ่าน Windows ของ {', '.join(pending)} "
                              f"— จะสร้าง handout ใหม่ให้เองเมื่อถอดได้ "
                              f"(เช็คทุก {PW_WATCH_INTERVAL} วิ นานสุด {PW_WATCH_MAX_MINUTES} นาที)\n")
                    start_password_watcher(cfg, pending)
            elif kind == "reset":
                # -replace ทำให้ terraform สร้าง instance ตัวนั้นใหม่ตัวเดียว
                # เครื่องของผู้เรียนคนอื่นใน workspace เดียวกันไม่ถูกแตะ
                target = cfg["_reset_user"]
                _set_phase(job_id, "reset")
                log.write(f"สร้างเครื่องของ {target} ใหม่ (เครื่องคนอื่นไม่ถูกแตะ)\n")
                _tfrun(["apply", "-auto-approve", "-input=false",
                        f"-replace=aws_instance.lab[\"{target}\"]",
                        f"-var-file={varfile.name}"], lab=lab, log=log)
                _set_phase(job_id, "export")
                pending = _export_lab(cfg, lab, log)
                if pending:
                    start_password_watcher(cfg, pending)
            elif kind == "add_users":
                # users เป็น for_each — apply ด้วยรายชื่อใหม่จึงสร้างเฉพาะเครื่องที่เพิ่ม
                _set_phase(job_id, "apply")
                log.write(f"เพิ่มเครื่อง: {', '.join(cfg.get('_new_users', []))} "
                          f"(เครื่องเดิมไม่ถูกแตะ)\n")
                _tfrun(["apply", "-auto-approve", "-input=false",
                        f"-var-file={varfile.name}"], lab=lab, log=log)
                _set_phase(job_id, "export")
                pending = _export_lab(cfg, lab, log)
                if pending:
                    start_password_watcher(cfg, pending)
            elif kind == "export":
                # สร้าง handout ใหม่จาก state เดิม — ไม่เรียก apply/refresh จึงไม่
                # แตะ resource บน AWS เลย ใช้ตอนเปลี่ยน lab guide หรือแก้ title
                _set_phase(job_id, "export")
                _export_lab(cfg, lab, log)
            elif kind == "refresh":
                _set_phase(job_id, "refresh")
                _tfrun(["apply", "-refresh-only", "-auto-approve", "-input=false",
                        f"-var-file={varfile.name}"], lab=lab, log=log)
                _set_phase(job_id, "export")
                pending = _export_lab(cfg, lab, log)
                if pending:
                    log.write(f"\n[job] ยังเฝ้ารอรหัสผ่านของ {', '.join(pending)} ต่อให้\n")
                    start_password_watcher(cfg, pending)
            elif kind == "destroy":
                _assert_state(lab)
                _set_phase(job_id, "destroy")
                db.set_lab_status(lab, "destroying")
                _tfrun(["destroy", "-auto-approve", "-input=false",
                        f"-var-file={varfile.name}"], lab=lab, log=log)
                _set_phase(job_id, "cleanup")
                _tfrun(["workspace", "select", "default"], capture=True)
                try:
                    _tfrun(["workspace", "delete", lab], capture=True)
                except JobFailure as e:
                    log.write(f"(ลบ workspace ไม่ได้ — ข้าม: {e})\n")
                import shutil
                for d in (labctl.KEYS_DIR / lab, labctl.HANDOUTS_DIR / lab):
                    if d.exists():
                        shutil.rmtree(d)
                (labctl.HANDOUTS_DIR / f"{lab}-all.zip").unlink(missing_ok=True)
                varfile.unlink(missing_ok=True)
                # guide ที่ผู้สอนอัปโหลดไว้ให้ lab นี้ — ลบเฉพาะเมื่อสั่งมาชัดเจน
                # เพราะเป็นเนื้อหาที่พิมพ์เอง ไม่ใช่ของที่ terraform สร้างขึ้นใหม่ได้
                guide = cfg.get("lab_guide", "")
                if cfg.get("_delete_guide") and labctl.is_custom_guide(guide):
                    labctl.delete_custom_guide(guide)
                    log.write(f"ลบ lab guide: {guide}\n")
                db.set_lab_status(lab, "destroyed")
            log.write("\n[job] เสร็จสิ้น ✔\n")
        db.update_job(job_id, state="success", phase="done", finished_at=time.time())
    except BaseException as e:  # รวม SystemExit จาก die() ภายใน labctl
        msg = str(e) or e.__class__.__name__
        db.update_job(job_id, state="error", error=msg, finished_at=time.time())
        # refresh/export ไม่ได้แก้อะไรบน AWS — ล้มแล้ว lab ยังใช้งานได้ตามเดิม
        # อย่าไปปั๊ม error ทับสถานะที่ยังดีอยู่
        # add_users ล้มก็ไม่กระทบเครื่องเดิม — lab ยังใช้ต่อได้
        if kind not in ("refresh", "export", "add_users"):
            db.set_lab_status(lab, "error")
        with open(log_path, "a", encoding="utf-8") as log:
            log.write(f"\n[job] ล้มเหลว: {msg}\n")


def find_config_for(lab_name):
    """หา config ของ lab: DB ก่อน (สร้างจาก UI) แล้วค่อย scan configs/*.json (สร้างจาก CLI)"""
    import json
    row = db.get_lab(lab_name)
    if row and row.get("config_json"):
        return json.loads(row["config_json"])
    configs_dir = BASE_DIR / "configs"
    if configs_dir.exists():
        for p in sorted(configs_dir.glob("*.json")):
            try:
                cfg = json.loads(p.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            if cfg.get("lab_name") == lab_name:
                return cfg
    return None


# ---------------------------------------------------------------------------
# auto-expire — lab ที่ถึงเวลา expires_at ถูกลบเองแม้ผู้สอนลืม
# (ทำงานเฉพาะตอน server เปิดอยู่ — ถ้าปิดอยู่ตอนครบเวลา จะลบทันทีที่เปิดครั้งถัดไป)
# ---------------------------------------------------------------------------

_scheduler_started = False


def start_scheduler():
    global _scheduler_started
    if _scheduler_started:
        return
    _scheduler_started = True
    threading.Thread(target=_expiry_loop, daemon=True).start()


# lab ที่เตือนไปแล้ว — เก็บคู่กับ expires_at เพื่อให้ต่อเวลาแล้วเตือนใหม่ได้
_warned = {}
_warn_lock = threading.Lock()


def clear_expiry_warning(lab_name):
    with _warn_lock:
        _warned.pop(lab_name, None)


def _warn_soon_to_expire(labs, now):
    """เตือนก่อน lab ถูกลบอัตโนมัติ — ผู้เรียนกำลังทำ lab อยู่แล้วเครื่องหายพร้อมกัน
    หมดคือเรื่องใหญ่ ผู้สอนต้องมีจังหวะกดต่อเวลาทัน"""
    for lab in labs:
        exp = lab.get("expires_at")
        if lab.get("status") != "active" or not exp:
            continue
        mins = (exp - now) / 60
        if not (0 < mins <= labctl.EXPIRY_WARN_MINUTES):
            continue
        name = lab["lab_name"]
        with _warn_lock:
            if _warned.get(name) == exp:
                continue
            _warned[name] = exp
        labctl.notify(f"labctl: {name} จะถูกลบใน {mins:.0f} นาที",
                      "กด 'ต่อเวลา' ในหน้า lab ถ้าคลาสยังไม่จบ")


def _expiry_loop():
    while True:
        try:
            now = time.time()
            all_labs = db.list_labs()
            _warn_soon_to_expire(all_labs, now)
            for lab in labctl.due_labs(all_labs, now):
                last = db.latest_job_for_lab(lab["lab_name"])
                if not labctl.should_attempt_destroy(last, now):
                    continue  # กำลังลบอยู่ หรือเพิ่งล้มไป — รอ cooldown ก่อน
                cfg = find_config_for(lab["lab_name"])
                if not cfg:
                    continue
                try:
                    cfg = labctl.apply_product(labctl.validate_config(dict(cfg)))
                except labctl.ConfigError:
                    continue
                enqueue("destroy", cfg)
        except Exception:
            pass  # scheduler ต้องไม่ตายเพราะ error ชั่วคราว
        time.sleep(60)


def enqueue(kind, cfg):
    job_id = uuid.uuid4().hex[:12]
    log_path = LOGS_DIR / f"{job_id}.log"
    db.create_job(job_id, cfg["lab_name"], kind, log_path)
    _executor.submit(_run_job, job_id, kind, cfg)
    return job_id


def sse_events(job_id):
    """generator สำหรับ Server-Sent Events: log ใหม่ทีละบรรทัด + phase + สถานะจบ"""
    import json as _json
    job = db.get_job(job_id)
    if not job:
        yield "event: error\ndata: ไม่พบ job\n\n"
        return
    log_path = Path(job["log_path"])
    offset = 0
    last_phase = None
    while True:
        job = db.get_job(job_id)
        if job["phase"] != last_phase and job["phase"]:
            last_phase = job["phase"]
            yield f"event: phase\ndata: {_json.dumps({'phase': last_phase, 'state': job['state']})}\n\n"
        if log_path.exists():
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                f.seek(offset)
                chunk = f.read()
                offset = f.tell()
            for line in chunk.splitlines():
                yield f"event: log\ndata: {_json.dumps(line)}\n\n"
        if job["state"] in ("success", "error"):
            yield f"event: done\ndata: {_json.dumps({'state': job['state'], 'error': job['error']})}\n\n"
            return
        time.sleep(0.7)
