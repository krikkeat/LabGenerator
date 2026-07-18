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
from . import db

BASE_DIR = Path(__file__).resolve().parent.parent
TF_DIR = BASE_DIR / "terraform"
LOGS_DIR = BASE_DIR / "logs" / "jobs"

_executor = ThreadPoolExecutor(max_workers=1)
# กัน terraform สอง process แตะ backend/plugin dir พร้อมกันตอน init
_tf_init_lock = threading.Lock()


class JobFailure(Exception):
    pass


def _tfrun(args, lab=None, log=None, capture=False):
    """รัน terraform; lab → ส่ง TF_WORKSPACE, log → stream ทีละบรรทัดลงไฟล์"""
    cmd = ["terraform", f"-chdir={TF_DIR}"] + args
    env = dict(os.environ)
    if lab:
        env["TF_WORKSPACE"] = lab
    else:
        env.pop("TF_WORKSPACE", None)
    if log is not None:
        log.write(f"$ terraform {' '.join(args)}\n")
        log.flush()
        proc = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True)
        for line in proc.stdout:
            log.write(line)
            log.flush()
        proc.wait()
        if proc.returncode != 0:
            raise JobFailure(f"terraform {' '.join(args)} ล้มเหลว (exit {proc.returncode})")
        return None
    res = subprocess.run(cmd, env=env, capture_output=True, text=True)
    if res.returncode != 0:
        raise JobFailure(f"terraform {' '.join(args)} ล้มเหลว:\n{res.stderr}")
    return res.stdout if capture else None


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
            _tfrun(["init", "-input=false"], log=log)


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
    และเก็บข้อความ info() ลง log — job ถูก serialize อยู่แล้วจึงแตะ environ ได้"""
    os.environ["TF_WORKSPACE"] = lab
    try:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            labctl.export_lab(cfg)
        log.write(buf.getvalue())
        log.flush()
    finally:
        os.environ.pop("TF_WORKSPACE", None)


def _run_job(job_id, kind, cfg):
    lab = cfg["lab_name"]
    log_path = Path(db.get_job(job_id)["log_path"])
    log_path.parent.mkdir(parents=True, exist_ok=True)
    db.update_job(job_id, state="running", started_at=time.time())
    try:
        with open(log_path, "w", encoding="utf-8") as log:
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
                _export_lab(cfg, lab, log)
                db.set_lab_status(lab, "active")
            elif kind == "refresh":
                _set_phase(job_id, "refresh")
                _tfrun(["apply", "-refresh-only", "-auto-approve", "-input=false",
                        f"-var-file={varfile.name}"], lab=lab, log=log)
                _set_phase(job_id, "export")
                _export_lab(cfg, lab, log)
            elif kind == "destroy":
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
                db.set_lab_status(lab, "destroyed")
            log.write("\n[job] เสร็จสิ้น ✔\n")
        db.update_job(job_id, state="success", phase="done", finished_at=time.time())
    except BaseException as e:  # รวม SystemExit จาก die() ภายใน labctl
        msg = str(e) or e.__class__.__name__
        db.update_job(job_id, state="error", error=msg, finished_at=time.time())
        if kind != "refresh":
            db.set_lab_status(lab, "error")
        with open(log_path, "a", encoding="utf-8") as log:
            log.write(f"\n[job] ล้มเหลว: {msg}\n")


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
