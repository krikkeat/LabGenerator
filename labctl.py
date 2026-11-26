#!/usr/bin/env python3
"""labctl — Hands-on Lab Generator CLI

สร้าง/จัดการ lab environment บน AWS สำหรับ training:
  - สร้าง VM แยกหนึ่งเครื่องต่อหนึ่ง user (เลือก OS ได้)
  - สร้าง key pair แยกต่อ user
  - generate handout (HTML) ต่อ user: ข้อมูลเชื่อมต่อ + lab guide

ใช้งาน:
  python3 labctl.py products                        ดูรายชื่อ product ที่รองรับ
  python3 labctl.py create  configs/my-lab.json     สร้าง lab + handout
  python3 labctl.py create  configs/my-lab.json --ttl 8
                                                     สร้างแล้วลบอัตโนมัติใน 8 ชม.
                                                     (ต้องเปิด serve ค้างไว้)
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
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
TF_DIR = BASE_DIR / "terraform"
KEYS_DIR = BASE_DIR / "keys"
HANDOUTS_DIR = BASE_DIR / "handouts"
PRODUCTS_DIR = BASE_DIR / "products"

# state ของ terraform ต้องอยู่นอกโฟลเดอร์ที่ OneDrive/iCloud ซิงก์ — ตัวซิงก์
# ย้าย/ดึงไฟล์กลับได้เองโดย terraform ไม่รู้ตัว ซึ่งเคยทำ state ของ 2 lab หาย
# มาแล้วจนต้องไล่ลบ resource เองใน console (ดู POSTMORTEM-state-loss.md)
TF_STATE_DIR = Path(os.environ.get("LABCTL_TF_STATE_DIR",
                                   Path.home() / ".lab-generator" / "tfstate"))


def tf_init_args():
    """args ของ terraform init — ต้องส่ง workspace_dir ทุกครั้งที่ init ใหม่
    เพราะ backend block ในไฟล์ .tf interpolate ไม่ได้ เลยส่ง path ทาง -backend-config"""
    TF_STATE_DIR.mkdir(parents=True, exist_ok=True)
    return ["init", "-input=false", f"-backend-config=workspace_dir={TF_STATE_DIR}"]

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
    "kali-linux",
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
    # OS รายเครื่อง (เครื่องที่เพิ่มทีหลังด้วย OS ต่างจากคลาส)
    for u, osname in (cfg.get("user_os") or {}).items():
        if u not in cfg["users"]:
            raise ConfigError(f"user_os อ้างถึง '{u}' ที่ไม่อยู่ใน users")
        if osname not in SUPPORTED_OS:
            raise ConfigError(f"os '{osname}' ของ {u} ไม่รองรับ — เลือกจาก: {', '.join(SUPPORTED_OS)}")
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


TF_MISSING_MSG = (
    "ไม่พบคำสั่ง 'terraform' บนเครื่องนี้ — ติดตั้งก่อนแล้วลองใหม่\n"
    "  • brew tap hashicorp/tap && brew install hashicorp/tap/terraform\n"
    "  • หรือดาวน์โหลด binary จาก https://developer.hashicorp.com/terraform/install "
    f"แล้ววางไว้ที่ {BASE_DIR / 'bin' / 'terraform'}\n"
    "  • หรือชี้ path เองด้วย env var TERRAFORM_BIN=/path/to/terraform"
)


def terraform_bin():
    """หา terraform: TERRAFORM_BIN → PATH → ที่อยู่ที่พบบ่อยบน macOS → bin/ ในโปรเจกต์

    ทำไมไม่พึ่ง PATH อย่างเดียว: เวลาเปิดผ่าน Labctl.app (Finder/launchd) PATH
    เป็นค่า default ที่ไม่มี /usr/local/bin หรือ /opt/homebrew/bin — subprocess
    จะโยน FileNotFoundError ทั้งที่ผู้ใช้ติดตั้ง terraform ไว้แล้ว
    """
    explicit = os.environ.get("TERRAFORM_BIN")
    if explicit:
        return explicit if os.access(explicit, os.X_OK) else None
    found = shutil.which("terraform")
    if found:
        return found
    for cand in (BASE_DIR / "bin" / "terraform",
                 Path("/opt/homebrew/bin/terraform"),
                 Path("/usr/local/bin/terraform")):
        if os.access(cand, os.X_OK):
            return str(cand)
    return None


def check_prereqs():
    if terraform_bin() is None:
        die(TF_MISSING_MSG)
    if shutil.which("openssl") is None:
        die("ไม่พบคำสั่ง 'openssl' — กรุณาติดตั้งก่อน (ดู README.md)")


def tf_env():
    """env ที่ terraform ต้องใช้ — มี AWS credential ยัดมาให้พร้อม

    ถ้าไม่ยัด: เครื่องที่ล็อกอินผ่านหน้าเว็บจะมีแต่โปรไฟล์ [labctl] ไม่มี [default]
    ให้ SDK หยิบ terraform เลยไปถาม EC2 IMDS แล้วจบด้วย 'No valid credential
    sources found' ทั้งที่ labctl.py scan บอกว่าเชื่อมต่อได้
    """
    from backend import aws
    try:
        return aws.terraform_env()
    except aws.AwsError as e:
        die(str(e))


def tf(args, capture=False, need_aws=True):
    exe = terraform_bin()
    if exe is None:
        die(TF_MISSING_MSG)
    cmd = [exe, f"-chdir={TF_DIR}"] + args
    # คำสั่งที่ไม่แตะ AWS (workspace/init) ไม่ต้องรอ STS ยืนยันคีย์ก่อน
    env = tf_env() if need_aws else None
    if capture:
        res = subprocess.run(cmd, capture_output=True, text=True, env=env)
        if res.returncode != 0:
            die(f"terraform {' '.join(args)} ล้มเหลว:\n{res.stderr}")
        return res.stdout
    res = subprocess.run(cmd, env=env)
    if res.returncode != 0:
        die(f"terraform {' '.join(args)} ล้มเหลว (exit {res.returncode})")
    return None


def select_workspace(lab_name):
    existing = tf(["workspace", "list"], capture=True, need_aws=False)
    names = [w.strip().lstrip("* ").strip() for w in existing.splitlines()]
    if lab_name in names:
        tf(["workspace", "select", lab_name], capture=True, need_aws=False)
    else:
        tf(["workspace", "new", lab_name], capture=True, need_aws=False)


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
        "use_spot": bool(cfg.get("use_spot", False)),
        "user_os": cfg.get("user_os") or {},
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
h3 { margin: 1.6rem 0 .5rem; font-size: 1.05rem; }
details { margin-top: 1.2rem; border-top: 1px solid #c7dcf7; padding-top: .8rem; }
summary { cursor: pointer; }
details table { margin-top: .8rem; }
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


def _extra_ports_row(cfg):
    ports = cfg.get("extra_open_ports") or []
    if not ports:
        return ""
    listed = ", ".join(f"<code>{int(p)}</code>" for p in ports)
    return f"<tr><th>พอร์ตอื่นที่เปิดไว้</th><td>{listed}</td></tr>"


def _access_from_row(cfg):
    cidr = cfg.get("allowed_cidr", "0.0.0.0/0")
    note = "ทุกที่" if cidr == "0.0.0.0/0" else f"เฉพาะ <code>{html.escape(cidr)}</code>"
    return f"<tr><th>เข้าได้จาก</th><td>{note}</td></tr>"


def render_ssh_section(cfg, user, vm, key_file):
    """คู่มือ SSH ครบทั้ง macOS/Linux และ Windows + การรับส่งไฟล์ + ปัญหาที่เจอบ่อย

    ทำไมต้องมี Windows แยก: ผู้เรียนบนวินโดวส์ใช้ chmod ไม่ได้ ต้องใช้ icacls
    ไม่งั้น ssh จะปฏิเสธ key ด้วย 'unprotected private key file' แล้วไปต่อไม่ถูก
    """
    ip, username, lab = vm["public_ip"], vm["username"], cfg["lab_name"]
    return f"""
<div class="connbox">
<h2 style="margin-top:0">เชื่อมต่อเครื่องของคุณ (Linux — SSH)</h2>
<table>
<tr><th>Public IP</th><td><code>{ip}</code></td></tr>
<tr><th>Username</th><td><code>{username}</code></td></tr>
<tr><th>Key file</th><td><code>{key_file}</code> (อยู่ในโฟลเดอร์เดียวกับไฟล์นี้)</td></tr>
<tr><th>พอร์ต</th><td><code>22</code> (SSH)</td></tr>
{_extra_ports_row(cfg)}
{_access_from_row(cfg)}
<tr><th>OS</th><td>{vm['os']}</td></tr>
</table>

<h3>ทางลัด — ไม่ต้องพิมพ์คำสั่งเอง</h3>
<p>ในโฟลเดอร์ที่แตก zip มีไฟล์ช่วยไว้ให้แล้ว:</p>
<ul>
<li><code>connect.command</code> — <strong>ดับเบิลคลิกได้เลยบน macOS</strong>
    (จัดการ chmod และต่อ ssh ให้เอง) · Linux รันด้วย <code>./connect.command</code></li>
<li><code>ssh_config</code> — ถ้าถนัดพิมพ์เอง:
    <code>ssh -F ssh_config {lab}-{user}</code></li>
</ul>

<h3>macOS / Linux</h3>
<p>เปิด Terminal แล้ว <code>cd</code> เข้าโฟลเดอร์ที่แตก zip ไว้ จากนั้น:</p>
<pre><code>chmod 600 {key_file}
ssh -i {key_file} {username}@{ip}</code></pre>

<h3>Windows 10/11</h3>
<p>เปิด PowerShell แล้ว <code>cd</code> เข้าโฟลเดอร์ที่แตก zip ไว้ (Windows มี ssh มาให้ในตัวแล้ว):</p>
<pre><code>icacls {key_file} /inheritance:r
icacls {key_file} /grant:r "$env:USERNAME:(R)"
ssh -i {key_file} {username}@{ip}</code></pre>
<p>ถ้าใช้ PuTTY: เปิด PuTTYgen → Load <code>{key_file}</code> → Save private key เป็น
<code>.ppk</code> → ใน PuTTY ใส่ Host <code>{username}@{ip}</code> แล้วเลือกไฟล์ .ppk ที่
Connection → SSH → Auth → Credentials</p>

<h3>คัดลอกไฟล์ระหว่างเครื่อง</h3>
<pre><code># ส่งไฟล์จากเครื่องคุณขึ้น VM
scp -i {key_file} myfile.txt {username}@{ip}:~/

# ดึงไฟล์จาก VM กลับมา
scp -i {key_file} {username}@{ip}:~/result.log .</code></pre>

<details>
<summary><strong>ต่อไม่ติด? ดูตรงนี้</strong></summary>
<table>
<tr><th>สิ่งที่เห็น</th><th>ทำอย่างไร</th></tr>
<tr><td><code>UNPROTECTED PRIVATE KEY FILE</code></td>
    <td>ยังไม่ได้จำกัดสิทธิ์ไฟล์ key — รัน <code>chmod 600</code> (macOS/Linux)
        หรือ <code>icacls</code> สองบรรทัดข้างบน (Windows) ก่อน</td></tr>
<tr><td><code>Connection timed out</code></td>
    <td>เครื่องมักยังบูตไม่เสร็จในนาทีแรก — รอสัก 1-2 นาทีแล้วลองใหม่
        ถ้ายังไม่ได้ แจ้งผู้สอนว่า IP อาจเปลี่ยนหรือเน็ตที่คุณใช้ถูกกันไว้</td></tr>
<tr><td><code>Permission denied (publickey)</code></td>
    <td>ชื่อผู้ใช้ไม่ตรงกับ OS — ของเครื่องนี้คือ <code>{username}</code>
        และต้องชี้ <code>-i</code> ไปที่ <code>{key_file}</code> ของคุณเอง (ของคนอื่นใช้ไม่ได้)</td></tr>
<tr><td><code>REMOTE HOST IDENTIFICATION HAS CHANGED</code></td>
    <td>เคยต่อ IP นี้กับ lab เก่ามาก่อน — ลบบรรทัดเก่าออกด้วย
        <code>ssh-keygen -R {ip}</code> แล้วต่อใหม่</td></tr>
</table>
</details>
</div>"""


def render_rdp_section(cfg, user, vm, key_file, win_password):
    """คู่มือ RDP ครบทั้ง client บน Windows และ macOS + ปัญหาที่เจอบ่อย"""
    ip, username = vm["public_ip"], vm["username"]
    if win_password:
        pw_row = f"<tr><th>รหัสผ่าน</th><td><code>{html.escape(win_password)}</code></td></tr>"
        pw_note = ""
    else:
        pw_row = "<tr><th>รหัสผ่าน</th><td><em>ยังไม่พร้อม — รอ VM บูตประมาณ 4-5 นาที</em></td></tr>"
        pw_note = ('<div class="warn">รหัสผ่าน Windows ถอดได้หลัง VM บูตเสร็จ (~4-5 นาที) '
                   'ให้ผู้สอนกด Refresh ในหน้า lab แล้วส่ง handout ฉบับใหม่ให้</div>')
    return f"""
<div class="connbox">
<h2 style="margin-top:0">เชื่อมต่อเครื่องของคุณ (Windows — Remote Desktop)</h2>
<table>
<tr><th>Public IP</th><td><code>{ip}</code></td></tr>
<tr><th>Username</th><td><code>{username}</code></td></tr>
{pw_row}
<tr><th>พอร์ต</th><td><code>3389</code> (RDP)</td></tr>
{_extra_ports_row(cfg)}
{_access_from_row(cfg)}
<tr><th>OS</th><td>{vm['os']}</td></tr>
</table>
{pw_note}

<h3>ทางลัด — ดับเบิลคลิกไฟล์ .rdp</h3>
<p>ในโฟลเดอร์ที่แตก zip มีไฟล์ <code>{cfg['lab_name']}-{user}.rdp</code> อยู่แล้ว
ดับเบิลคลิกได้ทั้งบน Windows และ macOS (ต้องติดตั้ง Windows App ก่อน) แล้วใส่รหัสผ่าน
ตามตารางด้านบน — ไม่ต้องพิมพ์ IP เอง</p>

<h3>จากเครื่อง Windows</h3>
<p>กด <code>Win + R</code> → พิมพ์ <code>mstsc</code> → Enter → ช่อง Computer ใส่
<code>{ip}</code> → Connect → ใส่ username/password ตามตารางด้านบน</p>
<p>ถ้าขึ้นเตือนเรื่อง certificate ให้กด <strong>Yes</strong> ผ่านไปได้ — เป็นเรื่องปกติของ
เครื่อง lab ที่ใช้ self-signed certificate</p>
<p>อยากคัดลอกข้อความ/ไฟล์ข้ามเครื่อง: ที่หน้าต่าง mstsc กด Show Options →
แท็บ Local Resources → ติ๊ก Clipboard (กด More… เพื่อเลือก Drives ด้วยได้)</p>

<h3>จากเครื่อง macOS</h3>
<p>ติดตั้ง <strong>Windows App</strong> จาก Mac App Store (ชื่อเดิมคือ Microsoft Remote
Desktop) → กด <strong>+</strong> → Add PC → ช่อง PC name ใส่ <code>{ip}</code> →
ที่ User account เลือก Add User Account แล้วใส่ username/password → ดับเบิลคลิกเพื่อเข้า</p>

<details>
<summary><strong>ต่อไม่ติด? ดูตรงนี้</strong></summary>
<table>
<tr><th>สิ่งที่เห็น</th><th>ทำอย่างไร</th></tr>
<tr><td>รหัสผ่านในเอกสารว่าง</td>
    <td>AWS ยังไม่ปล่อยรหัสผ่านตอนที่สร้างเอกสาร — แจ้งผู้สอนให้กด Refresh
        แล้วส่ง handout ใหม่</td></tr>
<tr><td>ต่อไม่ติดใน 5 นาทีแรก</td>
    <td>Windows บูตนานกว่า Linux มาก (~5 นาที) รอแล้วลองใหม่</td></tr>
<tr><td>รหัสผ่านไม่ถูกต้อง</td>
    <td>ตรวจว่าไม่ได้ลอกช่องว่างท้ายรหัสมาด้วย และช่อง username ใช้
        <code>{username}</code> เฉย ๆ ไม่ต้องใส่ชื่อโดเมนนำหน้า</td></tr>
<tr><td>ต่อได้แต่ค้างที่จอดำ</td>
    <td>ปิดหน้าต่างแล้วต่อใหม่อีกครั้ง ถ้ายังเหมือนเดิมแจ้งผู้สอนให้ reboot เครื่องให้</td></tr>
</table>
</details>
</div>"""


GUIDE_PDF_NAME = "lab-guide.pdf"


def render_pdf_guide_section(filename, size_bytes):
    """ส่วน lab guide เมื่อผู้สอนแนบมาเป็น PDF

    ใช้ <object> เพราะ browser ส่วนใหญ่แสดง PDF ในหน้าได้เลย และมีลิงก์สำรอง
    ข้างในเผื่อ browser ที่แสดงไม่ได้ (หรือเปิดไฟล์จากดิสก์แล้วโดนบล็อก)
    """
    mb = size_bytes / (1024 * 1024)
    return f"""
<h1>Lab Guide</h1>
<p>คู่มือของ lab นี้เป็นไฟล์ PDF แนบมาในโฟลเดอร์เดียวกับเอกสารนี้
(<code>{html.escape(filename)}</code> · {mb:.1f} MB)</p>
<p><a href="{html.escape(filename)}" target="_blank"><strong>เปิด lab guide (PDF) →</strong></a></p>
<object data="{html.escape(filename)}" type="application/pdf" width="100%" height="800">
  <div class="warn">เบราว์เซอร์นี้แสดง PDF ในหน้าไม่ได้ —
    กดลิงก์ด้านบนเพื่อเปิดด้วยโปรแกรมอ่าน PDF แทน</div>
</object>"""


def render_user_handout(cfg, user, vm, guide_html, win_password):
    lab = cfg["lab_name"]
    key_file = f"{user}.pem"
    if vm["is_windows"]:
        conn = render_rdp_section(cfg, user, vm, key_file, win_password)
    else:
        conn = render_ssh_section(cfg, user, vm, key_file)

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


GUIDES_DIR = BASE_DIR / "lab-guides"


def list_lab_guides():
    """lab guide ทุกไฟล์ที่เลือกใส่ให้ lab ได้ — จาก lab-guides/ และของแต่ละ product

    คืน path แบบ relative กับ BASE_DIR เพราะ cfg['lab_guide'] เก็บแบบนั้น
    (export_lab เอาไป join กับ BASE_DIR อีกที)
    """
    out = []
    if GUIDES_DIR.exists():
        for p in sorted(list(GUIDES_DIR.glob("*.md")) + list(GUIDES_DIR.glob("*.pdf"))):
            rel = f"lab-guides/{p.name}"
            out.append({"path": rel, "name": p.stem, "source": "lab-guides",
                        "kind": "pdf" if p.suffix == ".pdf" else "markdown",
                        "size": p.stat().st_size, "deletable": is_custom_guide(rel)})
    for meta_path in sorted(PRODUCTS_DIR.glob("*/product.json")):
        pdir = meta_path.parent
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        gname = meta.get("lab_guide")
        if gname and (pdir / gname).exists():
            out.append({"kind": "pdf" if gname.endswith(".pdf") else "markdown",
                        "path": f"products/{pdir.name}/{gname}",
                        "name": meta.get("product_name") or pdir.name,
                        "source": f"product:{pdir.name}",
                        "size": (pdir / gname).stat().st_size,
                        "deletable": False})
    return out


def is_custom_guide(path):
    """guide ที่ผู้สอนอัปโหลด/พิมพ์เอง — ลบได้ ต่างจาก guide ที่มากับโปรเจกต์
    หรือของ product ซึ่งเป็นเนื้อหาที่ใช้ร่วมกันทุก lab"""
    return path.startswith("lab-guides/") and (
        path.endswith("-custom.md") or path.endswith("-custom.pdf"))


def delete_custom_guide(path):
    if not is_custom_guide(path):
        raise ConfigError(f"ลบได้เฉพาะ guide ที่อัปโหลดเอง (*-custom.md) — '{path}' ไม่ใช่")
    p = BASE_DIR / path
    if p.resolve().parent != GUIDES_DIR.resolve():
        raise ConfigError("path ไม่ถูกต้อง")
    p.unlink(missing_ok=True)


PDF_MAGIC = b"%PDF-"


def save_custom_guide_pdf(lab_name, data):
    """เก็บ lab guide ที่อัปโหลดมาเป็น PDF

    ตรวจ magic bytes ก่อน: ไฟล์นี้จะถูกแจกต่อให้ผู้เรียนทุกคน ถ้าปล่อยให้อัปโหลด
    อะไรก็ได้แล้วตั้งนามสกุล .pdf ก็เท่ากับใช้ระบบเป็นช่องทางส่งไฟล์อะไรก็ได้
    """
    if not re.fullmatch(r"[a-z0-9-]{1,32}", lab_name):
        raise ConfigError("ชื่อ lab ไม่ถูกต้อง")
    if not data[:5] == PDF_MAGIC:
        raise ConfigError("ไฟล์นี้ไม่ใช่ PDF (ตรวจจากหัวไฟล์)")
    GUIDES_DIR.mkdir(parents=True, exist_ok=True)
    p = GUIDES_DIR / f"{lab_name}-custom.pdf"
    p.write_bytes(data)
    return f"lab-guides/{p.name}"


def save_custom_guide(lab_name, markdown):
    """เก็บ guide ที่ผู้สอนพิมพ์/วางเองเป็นไฟล์ แล้วคืน path แบบ relative

    เขียนเป็นไฟล์แทนที่จะยัดลง db เพราะ export_lab อ่านจากไฟล์อยู่แล้ว และผู้สอน
    แก้ต่อด้วย editor ปกติได้
    """
    if not re.fullmatch(r"[a-z0-9-]{1,32}", lab_name):
        raise ConfigError("ชื่อ lab ไม่ถูกต้อง")  # กันชื่อที่พาไฟล์ออกนอกโฟลเดอร์
    GUIDES_DIR.mkdir(parents=True, exist_ok=True)
    p = GUIDES_DIR / f"{lab_name}-custom.md"
    p.write_text(markdown, encoding="utf-8")
    return f"lab-guides/{p.name}"


def write_connect_files(udir, cfg, user, vm, win_password):
    """ไฟล์เชื่อมต่อสำเร็จรูปใน zip — ผู้เรียนดับเบิลคลิกแล้วเข้าเครื่องได้เลย

    ตัดขั้นตอนที่ผิดพลาดบ่อยที่สุดออก: พิมพ์ IP ผิด, ลืม chmod/icacls, ใส่ชื่อ
    ผู้ใช้ไม่ตรงกับ OS
    """
    lab, ip = cfg["lab_name"], vm["public_ip"]
    written = []
    if vm["is_windows"]:
        # .rdp เปิดได้ทั้ง mstsc บน Windows และ Windows App บน macOS
        rdp = "\n".join([
            "screen mode id:i:2",
            "use multimon:i:0",
            "session bpp:i:32",
            "redirectclipboard:i:1",
            f"full address:s:{ip}",
            f"username:s:{vm['username']}",
            "authentication level:i:0",     # ข้ามเตือน certificate ของเครื่อง lab
            "prompt for credentials:i:1",
            "",
        ])
        p = udir / f"{lab}-{user}.rdp"
        p.write_text(rdp, encoding="utf-8")
        written.append(p.name)
    else:
        # ssh config: ต่อด้วย `ssh -F ssh_config <ชื่อ lab>` ไม่ต้องจำ IP หรือ path key
        host = f"{lab}-{user}"
        cfg_txt = "\n".join([
            f"Host {host}",
            f"    HostName {ip}",
            f"    User {vm['username']}",
            f"    IdentityFile {user}.pem",
            "    IdentitiesOnly yes",
            "    StrictHostKeyChecking accept-new",
            "",
        ])
        p = udir / "ssh_config"
        p.write_text(cfg_txt, encoding="utf-8")
        written.append(p.name)

        # ดับเบิลคลิกบน macOS แล้วเปิด Terminal เข้าเครื่องได้เลย
        sh = "\n".join([
            "#!/bin/bash",
            '# ดับเบิลคลิกไฟล์นี้เพื่อเข้าเครื่องของคุณ (macOS/Linux)',
            'cd "$(dirname "$0")" || exit 1',
            f'chmod 600 {user}.pem 2>/dev/null',
            f'exec ssh -F ssh_config {host}',
            "",
        ])
        p = udir / "connect.command"
        p.write_text(sh, encoding="utf-8")
        p.chmod(0o755)
        written.append(p.name)
    return written


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

    guide_html = ("<p><em>ผู้สอนยังไม่ได้แนบ lab guide สำหรับ lab นี้ — "
                  "แนบภายหลังแล้วสร้าง handout ใหม่ได้จากหน้า lab</em></p>")
    guide_path = cfg.get("lab_guide", "")
    guide_pdf = None          # ไฟล์ PDF ที่ต้องคัดลอกเข้าโฟลเดอร์ของผู้เรียนทุกคน
    if guide_path:
        gp = BASE_DIR / guide_path
        if not gp.exists():
            info(f"คำเตือน: ไม่พบ lab guide {gp} — handout จะไม่มีเนื้อหา guide")
        elif gp.suffix.lower() == ".pdf":
            # PDF ฝังเป็นข้อความในหน้าไม่ได้ — แนบไฟล์ไปใน zip แล้วให้ handout
            # ลิงก์/แสดงผลจากไฟล์ข้าง ๆ แทน ผู้เรียนเปิดออฟไลน์ได้เหมือนกัน
            guide_pdf = gp
            guide_html = render_pdf_guide_section(GUIDE_PDF_NAME, gp.stat().st_size)
        else:
            guide_md = gp.read_text(encoding="utf-8")
            if cfg.get("_product_params"):
                guide_md = render_template(guide_md, cfg["_product_params"])
            guide_html = md_to_html(guide_md)

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
        if guide_pdf:
            shutil.copy(guide_pdf, udir / GUIDE_PDF_NAME)
        write_connect_files(udir, cfg, user, vm, win_pw)

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
    return pending


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------

TTL_MAX_HOURS = 24 * 14


def expire_date_from_ttl(ttl_hours, now=None):
    """แปลง TTL เป็นวันที่หมดอายุจริง (YYYY-MM-DD) — None ถ้าไม่ได้ตั้ง TTL

    ทำไมต้อง derive: expire_date ไปเป็น tag `Expire` บน resource ทุกตัวและโชว์ในหน้าเว็บ
    ถ้าปล่อยให้พิมพ์มือใน config มันจะกลายเป็นวันที่ย้อนหลังทันทีที่ config ถูกใช้ซ้ำ
    รอบหน้า — เคยค้างเป็นอดีตพร้อมกัน 3 ไฟล์มาแล้ว
    """
    if not ttl_hours:
        return None
    import datetime
    base = datetime.datetime.now() if now is None else now
    return (base + datetime.timedelta(hours=ttl_hours)).strftime("%Y-%m-%d")


def validate_ttl(ttl):
    """ชั่วโมงก่อนลบอัตโนมัติ — เพดานเท่ากับฝั่ง web UI (14 วัน) · None/0 = ปิด"""
    if ttl is None:
        return None
    if ttl < 0 or ttl > TTL_MAX_HOURS:
        raise ConfigError(f"--ttl ต้องอยู่ระหว่าง 0 (ปิด) ถึง {TTL_MAX_HOURS} (14 วัน)")
    return ttl or None


def register_lab(cfg, ttl_hours=None, status="active"):
    """บันทึก lab ลง labs.db — จำเป็นสำหรับ auto-expire เพราะตัวจับเวลาใน server
    ไล่ดูจากตารางนี้ ไม่ได้ไล่ดูไฟล์ config"""
    import time
    from backend import db
    stored = {k: v for k, v in cfg.items() if not k.startswith("_")}
    expires_at = (time.time() + ttl_hours * 3600) if ttl_hours else None
    db.upsert_lab(cfg["lab_name"], cfg.get("product"), status,
                  cfg.get("expire_date"), stored,
                  ttl_hours=ttl_hours, expires_at=expires_at)
    return expires_at


def cmd_create(cfg, ttl_hours=None):
    # ตั้ง TTL แล้วให้ tag Expire ตรงกับเวลาที่จะถูกลบจริง ไม่ใช่วันที่ในไฟล์ config
    derived = expire_date_from_ttl(ttl_hours)
    if derived:
        if cfg.get("expire_date") and cfg["expire_date"] != derived:
            info(f"expire_date ใน config ({cfg['expire_date']}) ถูกแทนด้วยวันที่จาก "
                 f"--ttl: {derived}")
        cfg["expire_date"] = derived
    warn_if_ttl_outlives_credentials(ttl_hours)
    varfile = build_tfvars(cfg)
    if not (TF_DIR / ".terraform").exists():
        info("terraform init ...")
        tf(tf_init_args(), need_aws=False)
    select_workspace(cfg["lab_name"])
    info(f"กำลังสร้าง lab '{cfg['lab_name']}' — {len(cfg['users'])} VM "
         f"({cfg.get('os', 'amazon-linux-2023')}) ...")
    tf(["apply", "-auto-approve", "-input=false", f"-var-file={varfile.name}"])
    export_lab(cfg)
    if ttl_hours:
        import time
        expires_at = register_lab(cfg, ttl_hours)
        info(f"ตั้งลบอัตโนมัติไว้: {time.strftime('%d/%m %H:%M', time.localtime(expires_at))} "
             f"(อีก {ttl_hours} ชม.)")
        info("ตัวจับเวลาทำงานตอน server เปิดอยู่เท่านั้น — เปิดค้างไว้ด้วย "
             "(python3 labctl.py serve) ไม่งั้นจะลบให้ตอนเปิดครั้งถัดไป")
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


def destroy_lab(cfg):
    """ลบ resource + เก็บกวาดไฟล์ — ส่วนที่ใช้ร่วมกันระหว่าง destroy กับ expire"""
    varfile = build_tfvars(cfg)
    lab = cfg["lab_name"]
    select_workspace(lab)
    tf(["destroy", "-auto-approve", "-input=false", f"-var-file={varfile.name}"])
    tf(["workspace", "select", "default"], capture=True, need_aws=False)
    tf(["workspace", "delete", lab], capture=True, need_aws=False)
    for d in (KEYS_DIR / lab, HANDOUTS_DIR / lab):
        if d.exists():
            shutil.rmtree(d)
    (HANDOUTS_DIR / f"{lab}-all.zip").unlink(missing_ok=True)
    varfile.unlink(missing_ok=True)
    from backend import db
    if db.get_lab(lab):
        db.set_lab_status(lab, "destroyed")


# สถานะที่ยังต้องตามเก็บเมื่อครบกำหนด
#
# ทำไมต้องรวม error ด้วย: destroy ที่ล้ม (สาเหตุที่พบบ่อยที่สุดคือคีย์ AWS หมดอายุ)
# จะตั้งสถานะ lab เป็น error — ถ้าเก็บเฉพาะ active lab ที่ลบไม่สำเร็จจะหลุดจาก
# การติดตามทันที ทั้งที่เป็นตัวที่ต้องตามเก็บที่สุด (ดู POSTMORTEM-state-loss.md)
#
# provisioning/destroying/queued ไม่รวม เพราะมีงานทำอยู่ ไปสั่งซ้ำจะชนกันเอง
EXPIRABLE_STATUSES = ("active", "error")

# destroy ที่เพิ่งล้มไป อย่าเพิ่งยิงซ้ำทันที — ถ้าล้มเพราะคีย์หมดอายุ ยิงทุกนาที
# ก็ล้มทุกนาที ได้แค่ log บวมกับ job ขยะเต็มหน้าจอ
DESTROY_RETRY_COOLDOWN = 15 * 60

# เตือนก่อน lab ถูกลบอัตโนมัติกี่นาที — ต้องยาวพอให้ผู้สอนกดต่อเวลาทัน
# แต่ไม่ยาวจนเตือนตั้งแต่คลาสยังไม่เริ่ม
EXPIRY_WARN_MINUTES = 15


def notify(title, message):
    """เด้ง notification ของ macOS — ใช้ตอนงานอัตโนมัติล้ม

    ถ้าเขียนแต่ log ไว้เฉย ๆ จะไม่มีใครเปิดดูจนกว่าจะเห็นบิล AWS
    """
    try:
        subprocess.run(
            ["osascript", "-e",
             f'display notification {json.dumps(message)} with title {json.dumps(title)}'],
            capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        pass        # แจ้งเตือนไม่ได้ก็ไม่ควรทำให้งานหลักล้ม


def warn_if_ttl_outlives_credentials(ttl_hours):
    """เตือนเมื่อ lab จะอยู่นานกว่าคีย์ AWS — เคสนี้ auto-expire จะล้มตอนถึงเวลา
    ซึ่งเป็นสาเหตุของเหตุการณ์ VM ค้างข้ามคืนที่เคยเกิดมาแล้ว"""
    from backend import aws
    should_warn, left = aws.ttl_outlives_credentials(ttl_hours)
    if not should_warn:
        return False
    info(f"⚠ TTL {ttl_hours} ชม. ยาวกว่าอายุคีย์ที่เหลือ (~{max(left, 0):.1f} ชม.)")
    info("  ถ้าคีย์หมดอายุก่อน ตัวลบอัตโนมัติจะล้ม แล้ว VM จะค้างเดินเงิน")
    info("  → ใส่คีย์ชุดใหม่ก่อนถึงเวลา หรือกด destroy เองตอนจบคลาส")
    return True


def due_labs(labs, now):
    """lab ที่ถึงเวลาลบแล้ว — แยกออกมาให้ทดสอบได้โดยไม่ต้องมี db จริง
    ตัดสินใจผิดตรงนี้ = ลบ lab ที่ผู้เรียนยังใช้อยู่ หรือปล่อยเครื่องเดินเงินทิ้งไว้"""
    return [lab for lab in labs
            if lab.get("status") in EXPIRABLE_STATUSES
            and lab.get("expires_at")
            and lab["expires_at"] <= now]


def should_attempt_destroy(last_job, now, cooldown=DESTROY_RETRY_COOLDOWN):
    """ดู job ล่าสุดของ lab แล้วบอกว่าควรสั่ง destroy ตอนนี้ไหม"""
    if not last_job or last_job.get("kind") != "destroy":
        return True
    if last_job.get("state") in ("queued", "running"):
        return False                                  # กำลังลบอยู่แล้ว
    if last_job.get("state") == "error":
        return (now - (last_job.get("finished_at") or 0)) >= cooldown
    return True


def cmd_expire(dry_run=False):
    """ลบ lab ที่ครบกำหนดแล้ว — ออกแบบให้รันซ้ำ ๆ จาก launchd ได้

    ทำไมต้องมีทั้งที่ server ก็มีตัวจับเวลาอยู่แล้ว: ตัวจับเวลาในนั้นทำงานเฉพาะ
    ตอน server เปิดอยู่ ปิดโน้ตบุ๊กกลับบ้าน = ไม่มีใครลบให้ แล้ว VM เดินเงินข้ามคืน
    """
    import time
    from backend import db
    now = time.time()
    due = due_labs(db.list_labs(), now)
    if not due:
        info("ไม่มี lab ที่ครบกำหนด")
        return
    failed = []
    for lab in due:
        name = lab["lab_name"]
        late = int((now - lab["expires_at"]) / 60)
        note = " · สถานะ error จากรอบก่อน" if lab["status"] == "error" else ""
        if not should_attempt_destroy(db.latest_job_for_lab(name), now):
            info(f"ข้าม '{name}' — กำลังลบอยู่ หรือเพิ่งลองแล้วล้ม รอ "
                 f"{DESTROY_RETRY_COOLDOWN // 60} นาทีค่อยลองใหม่")
            continue
        if dry_run:
            info(f"[dry-run] จะลบ '{name}' (เลยกำหนดมา {late} นาที{note})")
            continue
        info(f"ลบ '{name}' — เลยกำหนดมา {late} นาที{note}")
        try:
            cfg = json.loads(lab["config_json"]) if lab.get("config_json") else None
            if not cfg:
                info(f"  ข้าม '{name}' — ไม่มี config เก็บไว้ ลบเองผ่าน CLI ด้วย config เดิม")
                continue
            destroy_lab(apply_product(validate_config(cfg)))
            info(f"  ลบ '{name}' เรียบร้อย ✔")
        except (ConfigError, SystemExit) as e:
            # SystemExit มาจาก die() ข้างใน tf() — ตัวหนึ่งพังต้องไม่ทำให้ตัวอื่นไม่ถูกลบ
            info(f"  ลบ '{name}' ไม่สำเร็จ: {e}")
            failed.append(name)
        except Exception as e:
            info(f"  ลบ '{name}' ไม่สำเร็จ: {e}")
            failed.append(name)
    if failed:
        # งานนี้มักถูกเรียกจาก launchd ที่ไม่มีใครนั่งดู log — ต้องเด้งบอก
        notify("labctl: ลบ lab อัตโนมัติไม่สำเร็จ",
               f"{', '.join(failed)} — ตรวจคีย์ AWS แล้วสั่ง destroy เอง "
               f"(จะลองใหม่ให้ในอีก {DESTROY_RETRY_COOLDOWN // 60} นาที)")


LAUNCH_LABEL = "com.labctl.autoexpire"
LAUNCH_PLIST = Path.home() / "Library" / "LaunchAgents" / f"{LAUNCH_LABEL}.plist"
EXPIRE_LOG = BASE_DIR / "logs" / "autoexpire.log"


def agent_python():
    """binary ที่ TCC มองจริงตอน launchd รัน

    `/usr/bin/python3` เป็น symlink ไปยัง framework python ซึ่ง exec ต่อไปที่
    `Python.app/Contents/MacOS/Python` อีกที — สิทธิ์ Full Disk Access ผูกกับไฟล์
    ปลายทางนั้น ถ้า plist ชี้ที่ symlink ผู้ใช้จะให้สิทธิ์ผิดไฟล์แล้วงงว่าทำไมไม่ผ่าน
    """
    real = Path(os.path.realpath(sys.executable))
    app = real.parent.parent / "Resources" / "Python.app" / "Contents" / "MacOS" / "Python"
    return str(app if app.exists() else real)


def _launch_plist_xml(interval_seconds):
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>{LAUNCH_LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>{agent_python()}</string>
    <string>{BASE_DIR / 'labctl.py'}</string>
    <string>expire</string>
  </array>
  <key>WorkingDirectory</key><string>{BASE_DIR}</string>
  <key>StartInterval</key><integer>{interval_seconds}</integer>
  <key>RunAtLoad</key><false/>
  <key>StandardOutPath</key><string>{EXPIRE_LOG}</string>
  <key>StandardErrorPath</key><string>{EXPIRE_LOG}</string>
</dict>
</plist>
"""


def cmd_autoexpire(action, interval_minutes=10):
    """ติดตั้ง/ถอน launchd agent ที่เรียก `labctl.py expire` เป็นระยะ

    ต่างจากตัวจับเวลาใน server ตรงที่ไม่ต้องเปิดหน้าเว็บค้างไว้ — launchd ปลุกให้เอง
    ตราบใดที่ผู้ใช้ล็อกอินอยู่ (agent ระดับผู้ใช้ ไม่ใช่ daemon ระดับเครื่อง)
    """
    if action == "status":
        if not LAUNCH_PLIST.exists():
            info("ยังไม่ได้ติดตั้ง — ติดตั้งด้วย: python3 labctl.py autoexpire install")
            return
        info(f"ไฟล์ agent: {LAUNCH_PLIST}")
        res = subprocess.run(["launchctl", "list", LAUNCH_LABEL],
                             capture_output=True, text=True)
        if res.returncode == 0:
            info("สถานะ: โหลดอยู่ ✔")
            print(res.stdout.strip())
        else:
            info("สถานะ: มีไฟล์แต่ยังไม่ถูกโหลด — สั่ง install อีกครั้งได้")
        if EXPIRE_LOG.exists():
            info(f"log: {EXPIRE_LOG}")
        return

    if action == "uninstall":
        subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{LAUNCH_LABEL}"],
                       capture_output=True, text=True)
        LAUNCH_PLIST.unlink(missing_ok=True)
        info("ถอน auto-expire agent แล้ว — ตัวจับเวลาจะเหลือเฉพาะตอนเปิด server")
        return

    if action != "install":
        die("ใช้: python3 labctl.py autoexpire install|uninstall|status")

    LAUNCH_PLIST.parent.mkdir(parents=True, exist_ok=True)
    EXPIRE_LOG.parent.mkdir(parents=True, exist_ok=True)
    LAUNCH_PLIST.write_text(_launch_plist_xml(interval_minutes * 60), encoding="utf-8")
    subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{LAUNCH_LABEL}"],
                   capture_output=True, text=True)   # ถ้ามีของเก่าค้างอยู่
    res = subprocess.run(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(LAUNCH_PLIST)],
                         capture_output=True, text=True)
    if res.returncode != 0:
        die(f"โหลด agent ไม่สำเร็จ: {res.stderr.strip() or res.stdout.strip()}")
    info(f"ติดตั้งแล้ว — จะเช็ค lab ที่หมดอายุทุก {interval_minutes} นาที "
         f"แม้ไม่ได้เปิดหน้าเว็บ")
    info(f"log: {EXPIRE_LOG}")
    info("ถอนออกด้วย: python3 labctl.py autoexpire uninstall")
    _autoexpire_selftest()


def _autoexpire_selftest():
    """สั่งให้ agent รันหนึ่งรอบแล้วอ่าน log — ถ้าติด TCC จะได้รู้ตั้งแต่ตอนติดตั้ง
    ไม่ใช่ไปรู้ตอน lab ไม่ถูกลบจริงตอนตีสอง"""
    import time
    EXPIRE_LOG.write_text("", encoding="utf-8")
    subprocess.run(["launchctl", "kickstart", "-p", f"gui/{os.getuid()}/{LAUNCH_LABEL}"],
                   capture_output=True, text=True)
    for _ in range(10):
        time.sleep(1)
        out = EXPIRE_LOG.read_text(encoding="utf-8", errors="replace") if EXPIRE_LOG.exists() else ""
        if not out.strip():
            continue
        if "Operation not permitted" in out or "Errno 1" in out:
            info("")
            info("⚠ ทดสอบรันแล้วไม่ผ่าน — macOS ไม่ยอมให้ process ที่ launchd เรียก")
            info(f"  อ่านไฟล์ในโฟลเดอร์ที่ OneDrive ซิงก์ ({BASE_DIR})")
            info("  แก้โดยให้สิทธิ์ Full Disk Access กับ binary นี้ (ไม่ใช่ symlink /usr/bin/python3):")
            info("    System Settings → Privacy & Security → Full Disk Access → +")
            info("    กด Cmd+Shift+G ใน dialog แล้ววาง path นี้:")
            info(f"      {agent_python()}")
            info("  (หรือย้ายโปรเจกต์ออกจากโฟลเดอร์ที่ซิงก์) จากนั้นสั่ง install ซ้ำอีกครั้ง")
            return False
        info("ทดสอบรันหนึ่งรอบผ่าน ✔")
        return True
    info("ทดสอบรันแล้วไม่มี output — ตรวจ log เองอีกที")
    return None


# ---------------------------------------------------------------------------
# doctor — ตรวจทุกอย่างที่เคยพังมาแล้วในคำสั่งเดียว
# ---------------------------------------------------------------------------

OK, WARN, FAIL = "ok", "warn", "fail"


def is_synced_folder(path):
    """โปรเจกต์อยู่ใต้โฟลเดอร์ที่ผู้ให้บริการคลาวด์ซิงก์หรือเปล่า

    สำคัญเพราะ (1) เคยทำ terraform state หายมาแล้ว (2) process ที่ launchd ปลุก
    อ่านไฟล์ในนั้นไม่ได้เลย ทำให้ตัวลบอัตโนมัติใช้ไม่ได้
    """
    p = str(path)
    return any(m in p for m in ("/Library/CloudStorage/", "/Library/Mobile Documents/",
                                "/Dropbox/", "/Google Drive"))


def _c(level, title, detail=""):
    return {"level": level, "title": title, "detail": detail}


def _check_terraform():
    exe = terraform_bin()
    if not exe:
        return _c(FAIL, "terraform", "ไม่พบ — ดูวิธีติดตั้งใน README")
    try:
        out = subprocess.run([exe, "version"], capture_output=True, text=True, timeout=20)
        ver = out.stdout.splitlines()[0] if out.stdout else "?"
    except (OSError, subprocess.SubprocessError) as e:
        return _c(FAIL, "terraform", f"รันไม่ได้: {e}")
    return _c(OK, "terraform", f"{ver} · {exe}")


def _check_openssl():
    if shutil.which("openssl"):
        return _c(OK, "openssl", "พร้อมใช้ (ใช้ถอดรหัสผ่าน Windows)")
    return _c(WARN, "openssl", "ไม่พบ — lab Windows จะถอดรหัสผ่านไม่ได้")


def _check_location():
    if is_synced_folder(BASE_DIR):
        return _c(FAIL, "ที่ตั้งโปรเจกต์",
                  f"{BASE_DIR}\n    อยู่ใต้โฟลเดอร์ที่ซิงก์ — state เสี่ยงหาย และ "
                  "auto-expire agent จะอ่านไฟล์ไม่ได้ (ดู POSTMORTEM)")
    return _c(OK, "ที่ตั้งโปรเจกต์", str(BASE_DIR))


def _check_state_dir():
    try:
        TF_STATE_DIR.mkdir(parents=True, exist_ok=True)
        probe = TF_STATE_DIR / ".doctor-probe"
        probe.write_text("x", encoding="utf-8")
        probe.unlink()
    except OSError as e:
        return _c(FAIL, "โฟลเดอร์ state", f"{TF_STATE_DIR} เขียนไม่ได้: {e}")
    n = len([p for p in TF_STATE_DIR.iterdir() if p.is_dir()]) if TF_STATE_DIR.exists() else 0
    return _c(OK, "โฟลเดอร์ state", f"{TF_STATE_DIR} · {n} workspace")


def _check_credentials():
    from backend import aws
    try:
        creds = aws.resolve_verified(timeout=15)
    except aws.AwsError as e:
        return _c(FAIL, "AWS credential", str(e).splitlines()[0])
    inf = aws.credential_info()
    detail = f"{creds['account']} · {creds['region']} · {creds['method']}"
    if inf["temporary"] and inf["age_hours"] is not None:
        left = inf["estimated_hours_left"]
        detail += f"\n    คีย์ชั่วคราว ใส่มาแล้ว {inf['age_hours']:.1f} ชม."
        if left is not None and left < 1:
            return _c(WARN, "AWS credential",
                      detail + f" — คาดว่าเหลือ ~{max(left, 0):.1f} ชม. ควรใส่ชุดใหม่")
        if left is not None:
            detail += f" · คาดว่าเหลือ ~{left:.1f} ชม."
    return _c(OK, "AWS credential", detail)


def _check_agent():
    if not LAUNCH_PLIST.exists():
        return _c(WARN, "auto-expire agent",
                  "ยังไม่ติดตั้ง — lab จะถูกลบเฉพาะตอนเปิด server ค้างไว้\n"
                  "    ติดตั้ง: python3 labctl.py autoexpire install")
    loaded = subprocess.run(["launchctl", "list", LAUNCH_LABEL],
                            capture_output=True, text=True).returncode == 0
    if not loaded:
        return _c(FAIL, "auto-expire agent", "มีไฟล์แต่ไม่ได้โหลด — สั่ง install ใหม่")
    log = EXPIRE_LOG.read_text(encoding="utf-8", errors="replace") if EXPIRE_LOG.exists() else ""
    if "Operation not permitted" in log:
        return _c(FAIL, "auto-expire agent",
                  "โหลดอยู่แต่รันแล้วอ่านไฟล์โปรเจกต์ไม่ได้ (ดูข้อ 'ที่ตั้งโปรเจกต์')")
    if "ไม่สำเร็จ" in log:
        return _c(WARN, "auto-expire agent", f"โหลดอยู่ แต่เคยลบไม่สำเร็จ — ดู {EXPIRE_LOG}")
    return _c(OK, "auto-expire agent", "โหลดอยู่ · ทำงานปกติ")


def _check_server():
    running = subprocess.run(["pgrep", "-f", "labctl.py serve"],
                             capture_output=True).returncode == 0
    return _c(OK, "web server", "เปิดอยู่ที่ 127.0.0.1:8787" if running
              else "ปิดอยู่ (ไม่จำเป็นถ้ามี auto-expire agent)")


def _check_labs():
    from backend import db
    import time as _t
    live = [l for l in db.list_labs() if l["status"] not in ("destroyed",)]
    if not live:
        return _c(OK, "lab ที่ยังไม่ถูกลบ", "ไม่มี")
    lines = []
    level = OK
    for l in live:
        exp = l.get("expires_at")
        if exp:
            mins = (exp - _t.time()) / 60
            when = f"อีก {mins:.0f} นาที" if mins > 0 else f"เลยมาแล้ว {-mins:.0f} นาที"
        else:
            when = "ไม่ตั้ง TTL — ต้องลบเอง"
            level = WARN if level == OK else level
        if l["status"] == "error":
            level = WARN
        lines.append(f"    {l['lab_name']} · {l['status']} · {when}")
    return _c(level, f"lab ที่ยังไม่ถูกลบ ({len(live)})", "\n" + "\n".join(lines))


def _check_orphans():
    from backend import aws
    try:
        res = aws.scan_tagged_resources(timeout=20)
    except aws.AwsError as e:
        return _c(WARN, "resource ค้างบน AWS", f"ตรวจไม่ได้: {str(e).splitlines()[0]}")
    if res["clean"]:
        return _c(OK, "resource ค้างบน AWS", f"ไม่มี · ตรวจที่ {', '.join(res['regions'])}")
    return _c(FAIL, "resource ค้างบน AWS",
              f"พบ {res['total']} รายการ ({', '.join(res['labs']) or 'ไม่ทราบ lab'}) "
              f"— ดูรายละเอียด: python3 labctl.py scan")


def run_doctor():
    checks = [_check_terraform(), _check_openssl(), _check_location(), _check_state_dir(),
              _check_credentials(), _check_agent(), _check_server(), _check_labs()]
    if any(c["level"] != FAIL for c in checks if c["title"] == "AWS credential"):
        checks.append(_check_orphans())
    return checks


def cmd_doctor():
    icon = {OK: "✔", WARN: "!", FAIL: "✘"}
    checks = run_doctor()
    print()
    for c in checks:
        print(f"  {icon[c['level']]}  {c['title']:<22} {c['detail']}")
    bad = [c for c in checks if c["level"] == FAIL]
    warn = [c for c in checks if c["level"] == WARN]
    print()
    if bad:
        info(f"พบปัญหาที่ต้องแก้ {len(bad)} ข้อ" + (f" · คำเตือน {len(warn)} ข้อ" if warn else ""))
        sys.exit(1)
    info("ผ่านทั้งหมด ✔" if not warn else f"ใช้งานได้ · มีคำเตือน {len(warn)} ข้อ")


# ---------------------------------------------------------------------------
# สรุปการใช้งานและค่าใช้จ่าย จากประวัติ job ที่บันทึกไว้
# ---------------------------------------------------------------------------

# ราคาโดยประมาณ on-demand ap-southeast-1 (USD/ชม.) — ใช้ประมาณการเท่านั้น
# ไม่ใช่ยอดจริงจาก AWS Cost Explorer
HOURLY_USD = {
    "t3.micro": 0.0126, "t3.small": 0.0252, "t3.medium": 0.0504,
    "t3.large": 0.1008, "t3.xlarge": 0.2016, "t3.2xlarge": 0.4032,
    "m5.large": 0.118, "m5.xlarge": 0.236, "c5.large": 0.105,
}
DEFAULT_HOURLY = 0.05


def lab_usage(labs, jobs_rows, now=None):
    """จับคู่ create/destroy ของแต่ละ lab → ชั่วโมงที่เครื่องเปิดจริง + ค่าใช้จ่ายประมาณ

    lab ที่ยังไม่ถูกลบนับถึงตอนนี้ (กำลังเดินเงินอยู่)
    """
    import json as _json
    import time as _time
    now = now or _time.time()
    by_lab = {}
    for j in jobs_rows:
        by_lab.setdefault(j["lab_name"], []).append(j)
    out = []
    for lab in labs:
        name = lab["lab_name"]
        rows = sorted(by_lab.get(name, []), key=lambda r: r.get("created_at") or 0)
        started = next((r.get("finished_at") or r.get("created_at") for r in rows
                        if r["kind"] == "create" and r["state"] == "success"), None)
        from_job = started is not None
        if not started:
            # lab ที่สร้างจาก CLI ไม่มีแถว job — ใช้เวลาที่บันทึก lab ลง db แทน
            # (lab ที่สร้างจาก CLI โดยไม่ใส่ --ttl ไม่มีแถวใน db เลย จึงไม่ปรากฏที่นี่)
            started = lab.get("created_at")
        if not started:
            continue
        ended = next((r.get("finished_at") for r in reversed(rows)
                      if r["kind"] == "destroy" and r["state"] == "success"), None)
        estimated = not from_job
        if ended is None and lab["status"] == "destroyed":
            # ถูกทำเครื่องหมายว่าลบแล้วแต่ไม่มี job destroy ที่สำเร็จ (เช่น destroy ล้ม
            # แล้วไปเคลียร์มือใน console) — ไม่รู้เวลาจริง ใช้ job สุดท้ายเป็นขอบล่าง
            ended = max((r.get("finished_at") or 0 for r in rows), default=None) or None
            estimated = True
        live = ended is None and lab["status"] != "destroyed"
        stop = ended or now
        hours = max((stop - started) / 3600, 0)
        try:
            cfg = _json.loads(lab["config_json"]) if lab.get("config_json") else {}
        except ValueError:
            cfg = {}
        n = len(cfg.get("users") or []) or 1
        itype = cfg.get("instance_type") or "t3.medium"
        rate = HOURLY_USD.get(itype, DEFAULT_HOURLY)
        out.append({"lab_name": name, "vms": n, "instance_type": itype,
                    "hours": hours, "vm_hours": hours * n,
                    "usd": hours * n * rate, "live": live, "estimated": estimated,
                    "started_at": started, "ended_at": ended})
    return sorted(out, key=lambda r: r["started_at"], reverse=True)


def cmd_usage():
    from backend import db
    rows = lab_usage(db.list_labs(), db.list_jobs())
    if not rows:
        info("ยังไม่มีประวัติการสร้าง lab")
        return
    print(f"\n{'LAB':<26}{'VM':>3} {'TYPE':<12}{'ชม.':>7}{'VM-ชม.':>9}{'~USD':>8}  สถานะ")
    print("-" * 78)
    for r in rows:
        print(f"{r['lab_name']:<26}{r['vms']:>3} {r['instance_type']:<12}"
              f"{r['hours']:>7.1f}{r['vm_hours']:>9.1f}{r['usd']:>8.2f}"
              f"  {'กำลังเดินเงิน' if r['live'] else 'จบแล้ว' + (' (ประมาณ)' if r['estimated'] else '')}")
    total = sum(r["usd"] for r in rows)
    live = sum(r["usd"] for r in rows if r["live"])
    print("-" * 78)
    print(f"{'รวม':<26}{'':>3} {'':<12}{'':>7}{sum(r['vm_hours'] for r in rows):>9.1f}"
          f"{total:>8.2f}")
    if live:
        info(f"ในนั้นเป็นของ lab ที่ยังเปิดอยู่ ~${live:.2f} และยังเพิ่มขึ้นเรื่อย ๆ")
    info("ราคาเป็นการประมาณจากตารางในโค้ด (on-demand ap-southeast-1) "
         "ไม่ใช่ยอดจริงจาก AWS")


# terraform address ของแต่ละชนิด resource ที่ scanner หาเจอ
# key pair กับ instance ผูกกับ user จึงต้องใส่ index ตอน import
_ADOPT_ADDRESSES = {
    "VPC": lambda r, u: "aws_vpc.lab",
    "subnet": lambda r, u: "aws_subnet.public",
    "internet gateway": lambda r, u: "aws_internet_gateway.lab",
    "route table": lambda r, u: "aws_route_table.public",
    "security group": lambda r, u: "aws_security_group.lab",
    "EC2 instance": lambda r, u: f'aws_instance.lab["{u}"]',
    "key pair": lambda r, u: f'aws_key_pair.user["{u}"]',
}


def cmd_share(cfg, port=8899, hours=4):
    """เปิดให้ผู้เรียนโหลด handout เองผ่าน Wi-Fi ห้องอบรม — ลิงก์เฉพาะคน

    แทนที่จะส่ง zip ทีละคนทางแชท/อีเมล ให้ฉายลิงก์บนจอแล้วให้แต่ละคนกดของตัวเอง
    """
    from backend import handoutserver as hs
    lab = cfg["lab_name"]
    users = cfg.get("users", [])
    missing = [u for u in users if not (HANDOUTS_DIR / lab / f"{lab}-{u}.zip").exists()]
    if missing:
        die(f"ยังไม่มี handout ของ: {', '.join(missing)} — รัน refresh ก่อน")

    share = hs.HandoutShare(lab, users, port=port, ttl_hours=hours).start()
    ip = hs.local_ip()
    links = share.links(ip)
    print(f"\nเปิดให้โหลด handout ของ '{lab}' — ลิงก์หมดอายุใน {hours} ชม.")
    print(f"เครื่องผู้เรียนต้องอยู่วง Wi-Fi เดียวกับเครื่องนี้ ({ip})\n")
    for u in users:
        print(f"  {u:<16} {links[u]}")
    print(f"\nใครโหลดแล้วบ้างจะขึ้นด้านล่างนี้ · กด Ctrl+C เพื่อปิด")
    seen = set()
    try:
        while not share.expired():
            time.sleep(2)
            for u, t in sorted(share.downloaded.items(), key=lambda kv: kv[1]):
                if u not in seen:
                    seen.add(u)
                    print(f"  ✔ {u} โหลดแล้ว {time.strftime('%H:%M:%S', time.localtime(t))} "
                          f"({len(seen)}/{len(users)})")
        info("หมดเวลาแล้ว — ปิดการแชร์")
    except KeyboardInterrupt:
        info(f"ปิดการแชร์ · โหลดไปแล้ว {len(seen)}/{len(users)} คน"
             + (f" · ยังไม่ได้โหลด: {', '.join(u for u in users if u not in seen)}"
                if len(seen) < len(users) else ""))


def cmd_bake_delete(ami_id, region=None):
    """ลบ AMI ที่อบไว้ พร้อม snapshot — AMI ที่ลืมทิ้งไว้เสียค่าเก็บทุกเดือน"""
    from backend import aws
    try:
        res = aws.delete_image(ami_id, region=region)
    except aws.AwsError as e:
        die(str(e))
    info(f"ลบ AMI {res['ami']} แล้ว")
    for s in res["snapshots_deleted"]:
        info(f"  ลบ snapshot {s}")
    if not res["snapshots_deleted"]:
        info("  (ไม่มี snapshot ที่ลบได้ — อาจถูก AMI อื่นใช้อยู่)")


def cmd_bake(cfg, wait_minutes=20):
    """อบ AMI จาก lab ที่ติดตั้งของครบแล้ว — รอบหน้าเครื่องพร้อมใช้ใน 40 วินาที

    ทำไมคุ้ม: bootstrap ของ product ต้องโหลด agent installer จากอินเทอร์เน็ตทุกครั้ง
    ใช้เวลาหลายนาทีและพังได้ถ้าลิงก์ล่ม — อบครั้งเดียวแล้วชี้ custom_ami ไปที่ภาพนั้น
    """
    from backend import aws
    lab = cfg["lab_name"]
    select_workspace(lab)
    outputs = tf_outputs()
    if not outputs or not outputs.get("lab_info"):
        die(f"ไม่พบเครื่องของ '{lab}' — ต้องมี lab ที่รันอยู่ก่อนถึงจะอบ AMI ได้")
    first_user = sorted(outputs["lab_info"])[0]
    vm = outputs["lab_info"][first_user]
    name = f"{lab}-{time.strftime('%Y%m%d-%H%M')}"
    info(f"อบ AMI จากเครื่องของ {first_user} ({vm['instance_id']}) → {name}")
    info("  AWS จะ reboot เครื่องนั้นระหว่างอบเพื่อให้ภาพ consistent")
    try:
        ami_id = aws.create_image(vm["instance_id"], name,
                                  description=f"hands-on lab {lab} ({cfg.get('os', '?')})",
                                  region=cfg.get("region"))
    except aws.AwsError as e:
        die(str(e))
    info(f"  สั่งอบแล้ว: {ami_id}")
    info(f"  รอ AWS ประมวลผล (ไม่เกิน {wait_minutes} นาที) …")
    try:
        state = aws.wait_image_available(ami_id, region=cfg.get("region"),
                                         timeout_minutes=wait_minutes)
    except aws.AwsError as e:
        die(str(e))
    if state != "available":
        info(f"  ยังไม่พร้อม (สถานะ {state}) — เช็คเองใน console ได้ที่ {ami_id}")
        return
    info(f"  AMI พร้อมใช้แล้ว ✔  {ami_id}")
    info("")
    info("ใช้กับ lab รอบหน้าโดยใส่ในไฟล์ config:")
    info(f'    "custom_ami": "{ami_id}",')
    info(f'    "custom_username": "{vm["username"]}",')
    info(f'    "custom_is_windows": {str(vm["is_windows"]).lower()}')


def cmd_report(lab_name, out_path=None):
    """สรุปผลหลังคลาสเป็นตาราง/CSV — ใครได้เครื่องไหน ใช้ไปกี่ชั่วโมง ค่าใช้จ่ายเท่าไร

    เอาไว้แนบรายงานการอบรมหรือส่งให้บัญชี ข้อมูลมาจาก labs.db ล้วน ไม่ต้องยิง AWS
    """
    import csv
    import io
    import time
    from backend import db
    row = db.get_lab(lab_name)
    if not row:
        die(f"ไม่พบ '{lab_name}' ใน labs.db — รายงานได้เฉพาะ lab ที่ระบบบันทึกไว้")
    usage = {u["lab_name"]: u
             for u in lab_usage(db.list_labs(), db.list_jobs())}.get(lab_name)
    cfg = json.loads(row["config_json"]) if row.get("config_json") else {}
    users = cfg.get("users", [])
    itype = cfg.get("instance_type", "?")
    hours = usage["hours"] if usage else 0
    rate = HOURLY_USD.get(itype, DEFAULT_HOURLY)

    rows = [{"lab": lab_name, "user": u, "os": cfg.get("os", "?"), "instance_type": itype,
             "ชั่วโมง": f"{hours:.2f}", "ค่าใช้จ่าย_usd": f"{hours * rate:.3f}"} for u in users]
    total = hours * rate * len(users)

    print(f"\nรายงานการใช้งาน: {lab_name}")
    print(f"  ชื่อคลาส   : {cfg.get('title', '-')}")
    print(f"  สร้างเมื่อ  : {time.strftime('%d/%m/%Y %H:%M', time.localtime(row['created_at']))}")
    print(f"  สถานะ      : {row['status']}")
    print(f"  เครื่อง     : {len(users)} × {itype} ({cfg.get('os', '?')})")
    print(f"  ระยะเวลา   : {hours:.2f} ชม./เครื่อง")
    print(f"  ค่าใช้จ่าย  : ~${total:.2f} (ประมาณจากราคา on-demand)")
    print(f"\n  {'ผู้เรียน':<20}{'ชั่วโมง':>10}{'~USD':>10}")
    print("  " + "-" * 40)
    for r in rows:
        print(f"  {r['user']:<20}{r['ชั่วโมง']:>10}{r['ค่าใช้จ่าย_usd']:>10}")
    print()

    if out_path:
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=list(rows[0].keys()) if rows else ["lab"])
        w.writeheader()
        w.writerows(rows)
        Path(out_path).write_text(buf.getvalue(), encoding="utf-8-sig")  # BOM ให้ Excel ไทยอ่านได้
        info(f"เขียน CSV แล้ว: {out_path}")


def cmd_adopt(cfg, dry_run=False):
    """ดึง resource ที่มีอยู่จริงบน AWS กลับเข้า terraform state

    ใช้ตอน state หาย (เครื่องผู้สอนพัง / ไฟล์ถูกลบ) แต่ของยังรันอยู่บน AWS —
    scanner หาเจอจาก tag อยู่แล้ว ตัวนี้แค่ต่อยอดเป็น terraform import ให้
    เพื่อจะได้ destroy ผ่านระบบตามปกติ แทนที่จะไปไล่ลบมือใน console
    """
    from backend import aws
    lab = cfg["lab_name"]
    try:
        res = aws.scan_tagged_resources(lab_name=lab,
                                        regions=[cfg.get("region")] if cfg.get("region") else None)
    except aws.AwsError as e:
        die(str(e))
    if not res["resources"]:
        info(f"ไม่พบ resource ของ '{lab}' บน AWS — ไม่มีอะไรให้ดึงกลับ")
        return
    info(f"พบ {res['total']} resource ของ '{lab}'")

    varfile = build_tfvars(cfg)
    select_workspace(lab)
    # state ว่างคือเคสปกติของ adopt (นั่นแหละเหตุผลที่ต้องใช้คำสั่งนี้) จึงเรียกตรง
    # ไม่ผ่าน tf() ที่ die() ใส่หน้าผู้ใช้เวลาไม่มี state
    existing = set()
    exe = terraform_bin()
    if exe:
        listed = subprocess.run([exe, f"-chdir={TF_DIR}", "state", "list"],
                                capture_output=True, text=True, env=os.environ)
        if listed.returncode == 0:
            existing = {ln.strip() for ln in listed.stdout.splitlines() if ln.strip()}

    planned, skipped = [], []
    for r in res["resources"]:
        maker = _ADOPT_ADDRESSES.get(r["kind"])
        if not maker:
            skipped.append((r["kind"], r["id"], "ไม่รู้จักชนิดนี้"))
            continue
        # key pair / instance ต้องรู้ว่าเป็นของ user ไหน — อ่านจาก tag
        user = (r.get("name") or "").replace(f"{lab}-", "") if r["kind"] == "key pair" else None
        if r["kind"] == "EC2 instance":
            user = (r.get("name") or "").replace(f"{lab}-", "") or None
        if r["kind"] in ("EC2 instance", "key pair"):
            if r["kind"] == "key pair":
                user = (r["id"] or "").replace(f"{lab}-", "")
            if not user or user not in cfg.get("users", []):
                skipped.append((r["kind"], r["id"], "ระบุ user ไม่ได้"))
                continue
        addr = maker(r, user)
        if addr in existing:
            skipped.append((r["kind"], r["id"], "อยู่ใน state แล้ว"))
            continue
        planned.append((addr, r["id"], r["kind"]))

    for addr, rid, kind in planned:
        if dry_run:
            info(f"  [dry-run] import {addr} ← {rid} ({kind})")
            continue
        info(f"  import {addr} ← {rid}")
        try:
            tf(["import", "-input=false", f"-var-file={varfile.name}", addr, rid], capture=True)
        except SystemExit:
            info(f"    ‼ import ไม่สำเร็จ — ข้ามตัวนี้")
    for kind, rid, why in skipped:
        info(f"  ข้าม {kind} {rid} — {why}")
    if not dry_run:
        info("เสร็จแล้ว — ตรวจด้วย: python3 labctl.py status <config> "
             "แล้วค่อย destroy ตามปกติได้")


def cmd_extend(cfg, hours):
    """ต่อเวลา lab ที่กำลังจะหมดอายุ"""
    import time
    from backend import db
    lab = cfg["lab_name"]
    row = db.get_lab(lab)
    if not row:
        die(f"lab '{lab}' ไม่มีใน labs.db — ต่อเวลาได้เฉพาะ lab ที่สร้างด้วย --ttl หรือจากหน้าเว็บ")
    if row["status"] != "active":
        die(f"lab '{lab}' สถานะ {row['status']} — ต่อเวลาได้เฉพาะ lab ที่ active")
    now = time.time()
    base = max(row["expires_at"] or now, now)
    new_exp = base + hours * 3600
    total = (new_exp - now) / 3600
    new_date = expire_date_from_ttl(total)
    db.update_lab_expiry(lab, new_exp, ttl_hours=total, expire_date=new_date)
    stored = json.loads(row["config_json"]) if row.get("config_json") else dict(cfg)
    stored["expire_date"] = new_date
    db.update_lab_config(lab, stored)
    info(f"ต่อเวลา '{lab}' อีก {hours} ชม. → ลบอัตโนมัติ "
         f"{time.strftime('%d/%m %H:%M', time.localtime(new_exp))}")
    info("(tag Expire บน resource จะอัปเดตเมื่อ apply ครั้งถัดไป)")


def cmd_reset(cfg, user):
    """สร้างเครื่องของผู้เรียนคนเดียวใหม่"""
    if user not in cfg.get("users", []):
        die(f"ไม่มีผู้เรียนชื่อ '{user}' ใน lab นี้ — มี: {', '.join(cfg.get('users', []))}")
    varfile = build_tfvars(cfg)
    select_workspace(cfg["lab_name"])
    info(f"สร้างเครื่องของ {user} ใหม่ (เครื่องคนอื่นไม่ถูกแตะ) ...")
    tf(["apply", "-auto-approve", "-input=false",
        f'-replace=aws_instance.lab["{user}"]', f"-var-file={varfile.name}"])
    export_lab(cfg)
    info("เสร็จสิ้น ✔")


def cmd_destroy(cfg):
    select_workspace(cfg["lab_name"])
    confirm = input(f"พิมพ์ชื่อ lab '{cfg['lab_name']}' เพื่อยืนยันการลบทั้งหมด: ")
    if confirm.strip() != cfg["lab_name"]:
        die("ยกเลิกการลบ")
    destroy_lab(cfg)
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
    # access_log เปิดไว้เพื่อให้ไล่ปัญหาได้ว่า request มาถึงไหม/ตอบอะไรกลับ
    uvicorn.run(app, host="127.0.0.1", port=8787, log_level="info", access_log=True)


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


def cmd_guides():
    """ดู lab guide ทั้งหมดที่เลือกใส่ให้ lab ได้"""
    guides = list_lab_guides()
    if not guides:
        info("ยังไม่มี lab guide ในเครื่อง")
        return
    print(f"\n{'PATH':<40}{'SOURCE':<26}{'SIZE':>8}  ลบได้")
    print("-" * 84)
    for g in guides:
        print(f"{g['path']:<40}{g['source']:<26}{g['size']:>8}  {'ใช่' if g['deletable'] else '-'}")
    print()


def cmd_guide(cfg, guide_arg):
    """แนบ/เปลี่ยน lab guide ของ lab ที่สร้างไว้แล้ว → สร้าง handout ใหม่

    guide_arg เป็นได้ทั้ง path ในรายการ (lab-guides/x.md) หรือไฟล์ .md ในเครื่อง
    ที่จะถูกคัดลอกเข้ามาเป็น guide ของ lab นี้
    """
    known = {g["path"] for g in list_lab_guides()}
    if guide_arg in known:
        guide_path = guide_arg
    else:
        src = Path(guide_arg)
        if not src.exists():
            die(f"ไม่พบไฟล์ '{guide_arg}' และไม่ใช่ guide ที่มีอยู่ "
                f"(ดูรายการด้วย: python3 labctl.py guides)")
        guide_path = save_custom_guide(cfg["lab_name"], src.read_text(encoding="utf-8"))
        info(f"คัดลอก {src} → {guide_path}")
    cfg["lab_guide"] = guide_path
    select_workspace(cfg["lab_name"])
    export_lab(cfg)
    info(f"แนบ guide แล้ว: {guide_path}")
    info("หมายเหตุ: อัปเดตไฟล์ config ให้มี \"lab_guide\": " f"\"{guide_path}\" ด้วย "
         "ไม่งั้นครั้งหน้าที่รัน refresh จะกลับไปใช้ guide เดิม")


def cmd_scan(lab_name=None, all_regions=False):
    """ถาม AWS ตรง ๆ ว่ามี resource ค้างตาม tag ไหม — ไม่พึ่ง terraform state"""
    from backend import aws
    try:
        if all_regions:
            info("ดึงรายชื่อ region ที่เปิดใช้ …")
            res = aws.scan_tagged_resources(regions="all", lab_name=lab_name)
        else:
            res = aws.scan_tagged_resources(lab_name=lab_name)
    except aws.AwsError as e:
        die(str(e))
    print(f"\naccount {res['account']} · tag Project={res['project_tag']}"
          + (f" · LabName={lab_name}" if lab_name else ""))
    print(f"ตรวจแล้ว {len(res['scanned_regions'])} region: {', '.join(res['scanned_regions'])}")
    if res["blocked_regions"]:
        print(f"ข้าม {len(res['blocked_regions'])} region ที่บัญชีเข้าไม่ได้ "
              f"(นโยบายองค์กร/ยังไม่ opt-in): {', '.join(res['blocked_regions'])}")
    if res["clean"]:
        print("ไม่มี resource ค้าง ✔\n")
        return
    if res["total"]:
        print(f"\nพบ {res['total']} resource ที่ยังอยู่บน AWS:")
        print(f"{'KIND':<20}{'ID':<24}{'LAB':<22}STATE")
        print("-" * 78)
        for r in res["resources"]:
            print(f"{r['kind']:<20}{(r['id'] or '-'):<24}{(r['lab'] or '-'):<22}{r.get('state') or '-'}")
        print("\nลำดับการลบ: EC2 (รอ terminated) → security group → subnet → IGW "
              "→ route table → VPC → key pair")
    for e in res["errors"]:
        print(f"[!] ถาม {e['kind']} ที่ {e['region']} ไม่สำเร็จ: {e['error']}")
    print()


def main():
    parser = argparse.ArgumentParser(
        description="Hands-on Lab Generator — สร้าง VM lab บน AWS แยกต่อ user")
    parser.add_argument("command",
                        choices=["create", "status", "refresh", "destroy", "products",
                                 "guides", "guide", "scan", "expire", "autoexpire",
                                 "doctor", "usage", "extend", "reset", "adopt", "report", "bake", "unbake", "share", "serve"])
    parser.add_argument("config", nargs="?", help="ไฟล์ config ของ lab (JSON)")
    parser.add_argument("guide", nargs="?",
                        help="สำหรับคำสั่ง guide: path ของ guide ที่มีอยู่ หรือไฟล์ .md ในเครื่อง")
    parser.add_argument("--all-regions", action="store_true",
                        help="สำหรับคำสั่ง scan: ตรวจทุก region ที่บัญชีเปิดใช้ (ช้ากว่าแต่ครบ)")
    parser.add_argument("--dry-run", action="store_true",
                        help="สำหรับคำสั่ง expire: บอกว่าจะลบอะไรบ้างโดยไม่ลบจริง")
    parser.add_argument("--interval", type=int, default=10, metavar="นาที",
                        help="สำหรับ autoexpire install: ตรวจทุกกี่นาที (default 10)")
    parser.add_argument("--hours", type=float, default=1.0, metavar="ชม.",
                        help="สำหรับคำสั่ง extend: ต่อเวลาอีกกี่ชั่วโมง (default 1)")
    parser.add_argument("--user", metavar="ชื่อ",
                        help="สำหรับคำสั่ง reset: ชื่อผู้เรียนที่จะสร้างเครื่องใหม่")
    parser.add_argument("--port", type=int, default=8899, metavar="พอร์ต",
                        help="สำหรับคำสั่ง share: พอร์ตที่เปิดให้ผู้เรียนโหลด (default 8899)")
    parser.add_argument("--out", metavar="ไฟล์",
                        help="สำหรับคำสั่ง report: เขียนผลเป็นไฟล์ CSV")
    parser.add_argument("--spot", action="store_true",
                        help="สำหรับ create: ใช้ spot instance (ถูกกว่า 60-70%% · ถูกยึดคืนได้)")
    parser.add_argument("--ttl", type=float, metavar="ชั่วโมง",
                        help="สำหรับคำสั่ง create: ลบ lab อัตโนมัติหลังกี่ชั่วโมง "
                             "(ต้องเปิด server ค้างไว้ · ไม่ใส่ = ไม่ลบอัตโนมัติ)")
    args = parser.parse_args()

    if args.command == "products":
        cmd_products()
        return
    if args.command == "guides":
        cmd_guides()
        return
    if args.command == "serve":
        cmd_serve()
        return
    if args.command == "scan":
        # config เป็น optional — ใส่ชื่อ lab เพื่อกรองเฉพาะ lab นั้นได้
        cmd_scan(args.config, all_regions=args.all_regions)
        return
    if args.command == "expire":
        check_prereqs()
        cmd_expire(dry_run=(args.config == "--dry-run" or args.dry_run))
        return
    if args.command == "autoexpire":
        cmd_autoexpire(args.config or "status", args.interval)
        return
    if args.command == "doctor":
        cmd_doctor()
        return
    if args.command == "unbake":
        if not args.config:
            die("ใช้: python3 labctl.py unbake ami-xxxxxxxx")
        cmd_bake_delete(args.config)
        return
    if args.command == "report":
        if not args.config:
            die("คำสั่ง report ต้องระบุชื่อ lab เช่น: python3 labctl.py report docker-101")
        cmd_report(args.config, args.out)
        return
    if args.command == "usage":
        cmd_usage()
        return

    if not args.config:
        die(f"คำสั่ง {args.command} ต้องระบุไฟล์ config ด้วย")
    check_prereqs()
    try:
        cfg = apply_product(load_config(args.config))
    except ConfigError as e:
        die(str(e))
    if args.command == "guide":
        if not args.guide:
            die("คำสั่ง guide ต้องระบุไฟล์ guide ด้วย เช่น\n"
                "  python3 labctl.py guide configs/docker-101.json lab-guides/docker-101.md")
        cmd_guide(cfg, args.guide)
        return
    if args.ttl is not None and args.command != "create":
        die("--ttl ใช้ได้กับคำสั่ง create เท่านั้น")
    if args.command == "share":
        cmd_share(cfg, port=args.port, hours=args.hours)
        return
    if args.command == "bake":
        cmd_bake(cfg)
        return
    if args.command == "adopt":
        cmd_adopt(cfg, dry_run=args.dry_run)
        return
    if args.command == "extend":
        cmd_extend(cfg, args.hours)
        return
    if args.command == "reset":
        if not args.user:
            die("คำสั่ง reset ต้องระบุ --user ด้วย เช่น --user user01")
        cmd_reset(cfg, args.user)
        return
    if args.command == "create":
        if args.spot:
            cfg["use_spot"] = True
        try:
            cmd_create(cfg, validate_ttl(args.ttl))
        except ConfigError as e:
            die(str(e))
        return
    {"status": cmd_status, "refresh": cmd_refresh,
     "destroy": cmd_destroy}[args.command](cfg)


if __name__ == "__main__":
    main()
