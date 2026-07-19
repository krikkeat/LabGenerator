#!/usr/bin/env python3
"""labctl — Hands-on Lab Generator CLI

สร้าง/จัดการ lab environment บน AWS สำหรับ training:
  - สร้าง VM แยกหนึ่งเครื่องต่อหนึ่ง user (เลือก OS ได้)
  - สร้าง key pair แยกต่อ user
  - generate handout (HTML) ต่อ user: ข้อมูลเชื่อมต่อ + lab guide

ใช้งาน:
  python3 labctl.py products                        ดูรายชื่อ product ที่รองรับ
  python3 labctl.py create  configs/my-lab.json     สร้าง lab + handout
  python3 labctl.py status  configs/my-lab.json     ดูสถานะ / ข้อมูลเชื่อมต่อ
  python3 labctl.py refresh configs/my-lab.json     ดึงข้อมูลล่าสุด + สร้าง handout ใหม่
                                                     (ใช้กรณีรหัสผ่าน Windows ยังไม่มาตอน create)
  python3 labctl.py destroy configs/my-lab.json     ลบ lab ทั้งหมด

ต้องมี: terraform >= 1.5, AWS credentials (aws configure / env vars), openssl
"""

import argparse
import base64
import html
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
TF_DIR = BASE_DIR / "terraform"
KEYS_DIR = BASE_DIR / "keys"
HANDOUTS_DIR = BASE_DIR / "handouts"
PRODUCTS_DIR = BASE_DIR / "products"

SUPPORTED_OS = [
    "amazon-linux-2023",
    "amazon-linux-2",
    "ubuntu-20.04",
    "ubuntu-22.04",
    "ubuntu-24.04",
    "debian-11",
    "debian-12",
    "suse-15",
    "windows-2025",
    "windows-2022",
    "windows-2019",
    "windows-2016",
]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def die(msg):
    print(f"[labctl] ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def info(msg):
    print(f"[labctl] {msg}")


class ConfigError(ValueError):
    """config ไม่ผ่านการตรวจสอบ — CLI แปลงเป็น die(), ฝั่ง API แปลงเป็น HTTP 400"""


def validate_config(cfg):
    for field in ("lab_name", "users"):
        if field not in cfg:
            raise ConfigError(f"config ต้องมี field '{field}'")
    if not re.fullmatch(r"[a-z0-9-]{1,32}", cfg["lab_name"]):
        raise ConfigError("lab_name ต้องเป็น a-z, 0-9, - เท่านั้น (ไม่เกิน 32 ตัว)")
    if not cfg["users"]:
        raise ConfigError("ต้องมี user อย่างน้อย 1 คน")
    for u in cfg["users"]:
        if not re.fullmatch(r"[a-z0-9-]{1,30}", u):
            raise ConfigError(f"ชื่อ user '{u}' ต้องเป็น a-z, 0-9, - เท่านั้น")
    if cfg.get("custom_ami"):
        # BYOL — เช่น Windows 10/11 ที่ import license เอง
        if not re.fullmatch(r"ami-[0-9a-f]{8,17}", cfg["custom_ami"]):
            raise ConfigError("custom_ami ต้องเป็นรูปแบบ ami-xxxxxxxx")
        if not cfg.get("custom_username"):
            raise ConfigError("ใช้ custom_ami ต้องระบุ custom_username ด้วย "
                              "(เช่น Administrator สำหรับ Windows)")
    else:
        osname = cfg.get("os", "amazon-linux-2023")
        if osname not in SUPPORTED_OS:
            raise ConfigError(f"os '{osname}' ไม่รองรับ — เลือกจาก: {', '.join(SUPPORTED_OS)}")
    return cfg


def load_config(path):
    p = Path(path)
    if not p.exists():
        die(f"ไม่พบไฟล์ config: {path}")
    try:
        cfg = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        die(f"config ไม่ใช่ JSON ที่ถูกต้อง: {e}")
    try:
        return validate_config(cfg)
    except ConfigError as e:
        die(str(e))


def render_template(text, params):
    """แทนที่ {{KEY}} ในข้อความด้วยค่าจาก params (key ที่ไม่รู้จักจะกลายเป็นค่าว่าง)"""
    return re.sub(r"\{\{([A-Z0-9_]+)\}\}",
                  lambda m: str(params.get(m.group(1), "")), text)


def list_products():
    if not PRODUCTS_DIR.exists():
        return {}
    products = {}
    for p in sorted(PRODUCTS_DIR.iterdir()):
        meta_path = p / "product.json"
        if meta_path.exists():
            products[p.name] = json.loads(meta_path.read_text(encoding="utf-8"))
    return products


def apply_product(cfg):
    """ถ้า config ระบุ product — merge ค่า default ของ product ลง config
    และ render bootstrap script / lab guide ของ product"""
    name = cfg.get("product")
    if not name:
        return cfg

    products = list_products()
    if name not in products:
        raise ConfigError(f"ไม่พบ product '{name}' — มีให้เลือก: {', '.join(products) or '(ยังไม่มี)'}")
    meta = products[name]
    pdir = PRODUCTS_DIR / name

    # ตรวจ product_params: ตัวที่ required ต้องมี ตัว optional เติม default ให้
    params = dict(cfg.get("product_params", {}))
    missing = []
    for key, spec in meta.get("params", {}).items():
        if key not in params:
            if spec.get("required"):
                missing.append(f"    {key}  — {spec.get('description', '')}")
            else:
                params[key] = spec.get("default", "")
    if missing:
        raise ConfigError(f"product '{name}' ต้องระบุ product_params เพิ่มใน config:\n" + "\n".join(missing))
    cfg["_product_params"] = params

    # ค่า default จาก product (config เขียนทับได้)
    for field in ("os", "instance_type", "root_volume_gb", "extra_open_ports"):
        if field in meta:
            cfg.setdefault(field, meta[field])
    cfg.setdefault("title", meta.get("product_name", name))
    if meta.get("lab_guide"):
        cfg.setdefault("lab_guide", f"products/{name}/{meta['lab_guide']}")

    # render bootstrap template → user_data (config ที่ระบุ bootstrap_script เองจะชนะ)
    tpl_name = meta.get("bootstrap_template")
    if tpl_name and "bootstrap_script" not in cfg:
        tpl_path = pdir / tpl_name
        if not tpl_path.exists():
            raise ConfigError(f"product '{name}' อ้างถึง template ที่ไม่มีอยู่: {tpl_path}")
        script = render_template(tpl_path.read_text(encoding="utf-8"), params)
        if tpl_name.endswith(".ps1.tpl"):
            script = "<powershell>\n" + script + "\n</powershell>"
        cfg["_user_data"] = script
    return cfg


def check_prereqs():
    for tool in ("terraform", "openssl"):
        if shutil.which(tool) is None:
            die(f"ไม่พบคำสั่ง '{tool}' — กรุณาติดตั้งก่อน (ดู README.md)")


def tf(args, capture=False):
    cmd = ["terraform", f"-chdir={TF_DIR}"] + args
    if capture:
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            die(f"terraform {' '.join(args)} ล้มเหลว:\n{res.stderr}")
        return res.stdout
    res = subprocess.run(cmd)
    if res.returncode != 0:
        die(f"terraform {' '.join(args)} ล้มเหลว (exit {res.returncode})")
    return None


def select_workspace(lab_name):
    existing = tf(["workspace", "list"], capture=True)
    names = [w.strip().lstrip("* ").strip() for w in existing.splitlines()]
    if lab_name in names:
        tf(["workspace", "select", lab_name], capture=True)
    else:
        tf(["workspace", "new", lab_name], capture=True)


def build_tfvars(cfg):
    tfvars = {
        "lab_name": cfg["lab_name"],
        "users": cfg["users"],
        "os": cfg.get("os", "amazon-linux-2023"),
        "region": cfg.get("region", "ap-southeast-1"),
        "instance_type": cfg.get("instance_type", "t3.small"),
        "allowed_cidr": cfg.get("allowed_cidr", "0.0.0.0/0"),
        "extra_open_ports": cfg.get("extra_open_ports", []),
        "root_volume_gb": cfg.get("root_volume_gb", 30),
        "expire_date": cfg.get("expire_date", ""),
        "custom_ami": cfg.get("custom_ami", ""),
        "custom_username": cfg.get("custom_username", ""),
        "custom_is_windows": bool(cfg.get("custom_is_windows", False)),
    }
    bootstrap = cfg.get("bootstrap_script", "")
    if bootstrap:
        bpath = BASE_DIR / bootstrap
        if not bpath.exists():
            die(f"ไม่พบ bootstrap script: {bpath}")
        tfvars["user_data"] = bpath.read_text(encoding="utf-8")
    elif cfg.get("_user_data"):
        tfvars["user_data"] = cfg["_user_data"]
    varfile = TF_DIR / f"{cfg['lab_name']}.tfvars.json"
    varfile.write_text(json.dumps(tfvars, ensure_ascii=False, indent=2), encoding="utf-8")
    return varfile


def tf_outputs():
    raw = tf(["output", "-json"], capture=True)
    out = json.loads(raw)
    if "lab_info" not in out:
        die("ยังไม่มีข้อมูล lab ใน workspace นี้ — รัน create ก่อน")
    return {k: v["value"] for k, v in out.items()}


def decrypt_windows_password(encrypted_b64, key_path):
    """ถอดรหัสผ่าน Windows Administrator ด้วย private key ของ user (ผ่าน openssl)"""
    if not encrypted_b64:
        return None
    try:
        blob = base64.b64decode(encrypted_b64)
    except Exception:
        return None
    with tempfile.NamedTemporaryFile(delete=False) as f:
        f.write(blob)
        tmp = f.name
    try:
        res = subprocess.run(
            ["openssl", "pkeyutl", "-decrypt", "-inkey", str(key_path), "-in", tmp],
            capture_output=True, text=True,
        )
        return res.stdout.strip() if res.returncode == 0 and res.stdout else None
    finally:
        os.unlink(tmp)


# ---------------------------------------------------------------------------
# markdown → html (ตัวแปลงอย่างง่าย ไม่ต้องพึ่ง library ภายนอก)
# ---------------------------------------------------------------------------

def md_inline(text):
    text = html.escape(text, quote=False)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', text)
    return text


def md_to_html(md):
    lines = md.splitlines()
    out, i, in_list = [], 0, False

    def close_list():
        nonlocal in_list
        if in_list:
            out.append("</ul>")
            in_list = False

    while i < len(lines):
        line = lines[i]
        if line.startswith("```"):
            close_list()
            code = []
            i += 1
            while i < len(lines) and not lines[i].startswith("```"):
                code.append(lines[i])
                i += 1
            out.append("<pre><code>" + html.escape("\n".join(code)) + "</code></pre>")
            i += 1
            continue
        m = re.match(r"^(#{1,4})\s+(.*)", line)
        if m:
            close_list()
            level = len(m.group(1))
            out.append(f"<h{level}>{md_inline(m.group(2))}</h{level}>")
        elif re.match(r"^\s*[-*]\s+", line):
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append("<li>" + md_inline(re.sub(r"^\s*[-*]\s+", "", line)) + "</li>")
        elif line.strip() == "":
            close_list()
        elif line.startswith("> "):
            close_list()
            out.append(f"<blockquote>{md_inline(line[2:])}</blockquote>")
        else:
            close_list()
            out.append(f"<p>{md_inline(line)}</p>")
        i += 1
    close_list()
    return "\n".join(out)


# ---------------------------------------------------------------------------
# handout generation
# ---------------------------------------------------------------------------

CSS = """
:root { color-scheme: light; }
html { background: #ffffff; }
body { font-family: -apple-system, 'Segoe UI', Tahoma, sans-serif; max-width: 860px;
       margin: 2rem auto; padding: 0 1.25rem; line-height: 1.7;
       color: #1a2733; background: #ffffff; }
h1 { border-bottom: 3px solid #0d6efd; padding-bottom: .4rem; }
h2 { margin-top: 2rem; color: #0d6efd; }
code { background: #eef1f5; padding: .15em .4em; border-radius: 4px; font-size: .92em; }
pre { background: #101418; color: #e6edf3; padding: 1rem; border-radius: 8px; overflow-x: auto; }
pre code { background: none; padding: 0; color: inherit; }
table { border-collapse: collapse; width: 100%; margin: 1rem 0; }
th, td { border: 1px solid #d0d7de; padding: .5rem .8rem; text-align: left; }
th { background: #f6f8fa; }
.connbox { background: #f0f7ff; border: 1px solid #b6d4fe; border-radius: 8px; padding: 1rem 1.4rem; }
.warn { background: #fff8e6; border: 1px solid #ffe08a; border-radius: 8px; padding: .8rem 1.2rem; }
blockquote { border-left: 4px solid #0d6efd; margin: 1rem 0; padding: .3rem 1rem; background: #f6f8fa; }
"""


def render_user_handout(cfg, user, vm, guide_html, win_password):
    lab = cfg["lab_name"]
    key_file = f"{user}.pem"
    if vm["is_windows"]:
        if win_password:
            pw_row = f"<tr><th>รหัสผ่าน</th><td><code>{html.escape(win_password)}</code></td></tr>"
            pw_note = ""
        else:
            pw_row = "<tr><th>รหัสผ่าน</th><td><em>ยังไม่พร้อม — รอ VM บูตประมาณ 4-5 นาที</em></td></tr>"
            pw_note = ('<div class="warn">รหัสผ่าน Windows จะพร้อมหลัง VM บูตเสร็จ (~4-5 นาที) '
                       'ให้ผู้ดูแลรัน <code>python3 labctl.py refresh</code> แล้วส่ง handout ใหม่</div>')
        conn = f"""
<div class="connbox">
<h2 style="margin-top:0">ข้อมูลเครื่องของคุณ (Windows — Remote Desktop)</h2>
<table>
<tr><th>Public IP</th><td><code>{vm['public_ip']}</code></td></tr>
<tr><th>Username</th><td><code>{vm['username']}</code></td></tr>
{pw_row}
<tr><th>OS</th><td>{vm['os']}</td></tr>
</table>
<p><strong>วิธีเชื่อมต่อ:</strong> เปิดโปรแกรม Remote Desktop (Windows: <code>mstsc</code>,
macOS: Windows App จาก App Store) → ใส่ IP ด้านบน → login ด้วย username/password</p>
{pw_note}
</div>"""
    else:
        conn = f"""
<div class="connbox">
<h2 style="margin-top:0">ข้อมูลเครื่องของคุณ (Linux — SSH)</h2>
<table>
<tr><th>Public IP</th><td><code>{vm['public_ip']}</code></td></tr>
<tr><th>Username</th><td><code>{vm['username']}</code></td></tr>
<tr><th>Key file</th><td><code>{key_file}</code> (อยู่ในโฟลเดอร์เดียวกับไฟล์นี้)</td></tr>
<tr><th>OS</th><td>{vm['os']}</td></tr>
</table>
<p><strong>วิธีเชื่อมต่อ</strong> — เปิด Terminal ในโฟลเดอร์นี้แล้วรัน:</p>
<pre><code>chmod 600 {key_file}
ssh -i {key_file} {vm['username']}@{vm['public_ip']}</code></pre>
</div>"""

    return f"""<!DOCTYPE html>
<html lang="th">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Lab: {html.escape(lab)} — {html.escape(user)}</title>
<style>{CSS}</style>
</head>
<body>
<h1>Hands-on Lab: {html.escape(cfg.get('title', lab))}</h1>
<p>ผู้เรียน: <strong>{html.escape(user)}</strong></p>
{conn}
<hr style="margin:2.5rem 0">
{guide_html}
</body>
</html>"""


def render_instructor_index(cfg, lab_info, passwords):
    rows = []
    for user, vm in sorted(lab_info.items()):
        pw = passwords.get(user) or ("-" if not vm["is_windows"] else "(ยังไม่พร้อม)")
        access = "RDP" if vm["is_windows"] else "SSH"
        rows.append(
            f"<tr><td><a href='{user}/index.html'>{html.escape(user)}</a></td>"
            f"<td><code>{vm['public_ip']}</code></td>"
            f"<td>{access}</td><td><code>{vm['username']}</code></td>"
            f"<td><code>{html.escape(pw) if pw != '-' else '-'}</code></td>"
            f"<td><code>{vm['instance_id']}</code></td></tr>"
        )
    return f"""<!DOCTYPE html>
<html lang="th"><head><meta charset="utf-8"><title>Lab: {cfg['lab_name']} — Instructor</title>
<style>{CSS}</style></head><body>
<h1>Instructor Dashboard — {html.escape(cfg.get('title', cfg['lab_name']))}</h1>
<p>OS: <code>{cfg.get('os', 'amazon-linux-2023')}</code> |
Region: <code>{cfg.get('region', 'ap-southeast-1')}</code> |
จำนวนผู้เรียน: <strong>{len(lab_info)}</strong></p>
<div class="warn">โฟลเดอร์ของแต่ละ user (มี handout + key) ถูก zip ไว้ให้แล้ว —
ส่งไฟล์ zip ให้ผู้เรียนแต่ละคน <strong>เฉพาะของตัวเอง</strong> เท่านั้น</div>
<table>
<tr><th>User</th><th>Public IP</th><th>เข้าผ่าน</th><th>Username</th><th>Password (Win)</th><th>Instance</th></tr>
{''.join(rows)}
</table>
</body></html>"""


def export_lab(cfg):
    """ดึง output จาก terraform → เขียน key, ถอดรหัสผ่าน, สร้าง handout + zip"""
    outputs = tf_outputs()
    lab_info = outputs["lab_info"]
    private_keys = outputs["private_keys"]
    win_pw_encrypted = outputs.get("windows_passwords_encrypted", {})
    lab = cfg["lab_name"]

    key_dir = KEYS_DIR / lab
    key_dir.mkdir(parents=True, exist_ok=True)
    handout_dir = HANDOUTS_DIR / lab
    handout_dir.mkdir(parents=True, exist_ok=True)

    guide_html = "<p><em>lab นี้ไม่ได้ระบุ lab guide</em></p>"
    guide_path = cfg.get("lab_guide", "")
    if guide_path:
        gp = BASE_DIR / guide_path
        if gp.exists():
            guide_md = gp.read_text(encoding="utf-8")
            if cfg.get("_product_params"):
                guide_md = render_template(guide_md, cfg["_product_params"])
            guide_html = md_to_html(guide_md)
        else:
            info(f"คำเตือน: ไม่พบ lab guide {gp} — handout จะไม่มีเนื้อหา guide")

    passwords = {}
    for user, vm in lab_info.items():
        # เขียน private key
        key_path = key_dir / f"{user}.pem"
        key_path.write_text(private_keys[user], encoding="utf-8")
        key_path.chmod(stat.S_IRUSR | stat.S_IWUSR)

        # ถอดรหัสผ่าน Windows (ถ้าเป็น Windows และ VM บูตเสร็จแล้ว)
        win_pw = None
        if vm["is_windows"]:
            win_pw = decrypt_windows_password(win_pw_encrypted.get(user), key_path)
            if win_pw:
                passwords[user] = win_pw

        # โฟลเดอร์ handout ต่อ user
        udir = handout_dir / user
        udir.mkdir(parents=True, exist_ok=True)
        (udir / "index.html").write_text(
            render_user_handout(cfg, user, vm, guide_html, win_pw), encoding="utf-8")
        if not vm["is_windows"]:
            dst = udir / f"{user}.pem"
            shutil.copy(key_path, dst)
            dst.chmod(stat.S_IRUSR | stat.S_IWUSR)

        # zip สำหรับส่งให้ผู้เรียน
        zip_base = handout_dir / f"{lab}-{user}"
        if zip_base.with_suffix(".zip").exists():
            zip_base.with_suffix(".zip").unlink()
        shutil.make_archive(str(zip_base), "zip", root_dir=udir)

    (handout_dir / "index.html").write_text(
        render_instructor_index(cfg, lab_info, passwords), encoding="utf-8")

    info(f"สร้าง handout เรียบร้อย: {handout_dir}/index.html (instructor dashboard)")
    pending = [u for u, vm in lab_info.items() if vm["is_windows"] and u not in passwords]
    if pending:
        info(f"รหัสผ่าน Windows ยังไม่พร้อมสำหรับ: {', '.join(pending)}")
        info("รอ ~5 นาทีแล้วรัน: python3 labctl.py refresh <config>")


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------

def cmd_create(cfg):
    varfile = build_tfvars(cfg)
    if not (TF_DIR / ".terraform").exists():
        info("terraform init ...")
        tf(["init", "-input=false"])
    select_workspace(cfg["lab_name"])
    info(f"กำลังสร้าง lab '{cfg['lab_name']}' — {len(cfg['users'])} VM "
         f"({cfg.get('os', 'amazon-linux-2023')}) ...")
    tf(["apply", "-auto-approve", "-input=false", f"-var-file={varfile.name}"])
    export_lab(cfg)
    info("เสร็จสิ้น ✔")


def cmd_refresh(cfg):
    varfile = build_tfvars(cfg)
    select_workspace(cfg["lab_name"])
    tf(["apply", "-refresh-only", "-auto-approve", "-input=false",
        f"-var-file={varfile.name}"])
    export_lab(cfg)


def cmd_status(cfg):
    select_workspace(cfg["lab_name"])
    outputs = tf_outputs()
    print(f"\nLab: {cfg['lab_name']}  (region: {cfg.get('region', 'ap-southeast-1')})")
    print(f"{'USER':<16}{'PUBLIC IP':<18}{'OS':<20}{'INSTANCE':<22}")
    print("-" * 76)
    for user, vm in sorted(outputs["lab_info"].items()):
        print(f"{user:<16}{vm['public_ip']:<18}{vm['os']:<20}{vm['instance_id']:<22}")
    print()


def cmd_destroy(cfg):
    varfile = build_tfvars(cfg)
    select_workspace(cfg["lab_name"])
    confirm = input(f"พิมพ์ชื่อ lab '{cfg['lab_name']}' เพื่อยืนยันการลบทั้งหมด: ")
    if confirm.strip() != cfg["lab_name"]:
        die("ยกเลิกการลบ")
    tf(["destroy", "-auto-approve", "-input=false", f"-var-file={varfile.name}"])
    tf(["workspace", "select", "default"], capture=True)
    tf(["workspace", "delete", cfg["lab_name"]], capture=True)
    for d in (KEYS_DIR / cfg["lab_name"], HANDOUTS_DIR / cfg["lab_name"]):
        if d.exists():
            shutil.rmtree(d)
    varfile.unlink(missing_ok=True)
    info(f"ลบ lab '{cfg['lab_name']}' เรียบร้อย ✔")


def cmd_serve():
    """เปิด web UI บนเครื่องนี้ (127.0.0.1 เท่านั้น — ขอบเขตความเชื่อถือเดียวกับ CLI)"""
    try:
        import uvicorn
    except ImportError:
        die("ยังไม่ได้ติดตั้ง dependencies ของ web UI — รัน:\n"
            "    pip3 install --user fastapi 'uvicorn[standard]'")
    import threading
    import webbrowser
    from backend.app import app

    url = "http://127.0.0.1:8787"
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    info(f"เปิด web UI ที่ {url}  (Ctrl+C เพื่อหยุด)")
    uvicorn.run(app, host="127.0.0.1", port=8787, log_level="warning")


def cmd_products():
    products = list_products()
    if not products:
        print("ยังไม่มี product ใน catalog — เพิ่มได้ที่โฟลเดอร์ products/")
        return
    print("\nProduct ที่รองรับ (ใส่ใน config เป็น \"product\": \"<ชื่อ>\"):\n")
    for name, meta in products.items():
        print(f"  {name}")
        print(f"      {meta.get('product_name', '')} — {meta.get('description', '')}")
        print(f"      OS: {meta.get('os', '-')}  |  instance: {meta.get('instance_type', '-')}")
        required = [k for k, v in meta.get("params", {}).items() if v.get("required")]
        if required:
            print(f"      product_params ที่ต้องระบุ: {', '.join(required)}")
        print()


def main():
    parser = argparse.ArgumentParser(
        description="Hands-on Lab Generator — สร้าง VM lab บน AWS แยกต่อ user")
    parser.add_argument("command",
                        choices=["create", "status", "refresh", "destroy", "products", "serve"])
    parser.add_argument("config", nargs="?", help="ไฟล์ config ของ lab (JSON)")
    args = parser.parse_args()

    if args.command == "products":
        cmd_products()
        return
    if args.command == "serve":
        cmd_serve()
        return

    if not args.config:
        die(f"คำสั่ง {args.command} ต้องระบุไฟล์ config ด้วย")
    check_prereqs()
    try:
        cfg = apply_product(load_config(args.config))
    except ConfigError as e:
        die(str(e))
    {"create": cmd_create, "status": cmd_status,
     "refresh": cmd_refresh, "destroy": cmd_destroy}[args.command](cfg)


if __name__ == "__main__":
    main()
