"""AWS accounts ที่ labctl จำไว้ — อ่านจากโปรไฟล์ใน ~/.aws ตรง ๆ

ทำไมไม่เก็บคีย์เอง: ~/.aws คือที่ที่ AWS CLI/SDK/terraform ใช้อยู่แล้ว ผู้ใช้จัดการ
ต่อได้ด้วยเครื่องมือเดิม และไม่มีความลับก้อนที่สองให้หลุด labctl จำแค่ว่า
"ตอนนี้ใช้โปรไฟล์ไหน" (ไฟล์ active-account.json) กับรายชื่อโปรไฟล์ที่ตัวเองสร้าง

โปรไฟล์มีสองแบบ:
- sso  — มี sso_session / sso_start_url ใน ~/.aws/config: ขอคีย์ชั่วคราวผ่าน
         `aws configure export-credentials` (ใช้ token ที่ `aws sso login` cache ไว้)
         หมดอายุเมื่อไหร่ก็กด sign in ใหม่ในเบราว์เซอร์ ไม่ต้องก๊อปคีย์
- keys — มี aws_access_key_id ใน ~/.aws/credentials (คีย์ถาวรหรือคีย์ชั่วคราวจาก portal)
"""
import configparser
import json
import os
import re
import shutil
import stat
import subprocess
import threading
import time
from pathlib import Path

from . import tlstrust

AWS_DIR = Path.home() / ".aws"
STATE_FILE = Path.home() / ".lab-generator" / "aws-accounts.json"
NAME_RE = re.compile(r"[A-Za-z0-9_.-]{1,64}")
EXPORT_TIMEOUT = 20
EXPIRY_MARGIN = 120          # ขอคีย์ใหม่ก่อนหมดจริง 2 นาที — apply ยาว ๆ จะได้ไม่ตายกลางทาง
LOGIN_TIMEOUT = 600


class AccountError(Exception):
    def __init__(self, msg, needs_login=False):
        super().__init__(msg)
        self.needs_login = needs_login


# ---------------------------------------------------------------------------
# state: โปรไฟล์ที่ใช้อยู่ + โปรไฟล์ที่ labctl สร้าง
# ---------------------------------------------------------------------------

_state_lock = threading.Lock()


def _load_state():
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(STATE_FILE)


def active_profile():
    return _load_state().get("active")


def set_active(name):
    with _state_lock:
        state = _load_state()
        _save_state({**state, "active": name})


def _mark_managed(name, managed=True):
    with _state_lock:
        state = _load_state()
        names = set(state.get("managed", []))
        (names.add if managed else names.discard)(name)
        new = {**state, "managed": sorted(names)}
        if not managed and new.get("active") == name:
            new.pop("active", None)
        _save_state(new)


# ---------------------------------------------------------------------------
# อ่านโปรไฟล์
# ---------------------------------------------------------------------------

def _read(path):
    cp = configparser.ConfigParser(interpolation=None)
    if path.exists():
        cp.read(path)
    return cp


def _config_section(cfg, name):
    sec = "default" if name == "default" else f"profile {name}"
    return cfg[sec] if cfg.has_section(sec) else None


def list_profiles():
    """รายชื่อโปรไฟล์ที่ใช้ได้ทั้งหมด (ไม่มีความลับในผลลัพธ์)"""
    cfg = _read(AWS_DIR / "config")
    creds = _read(AWS_DIR / "credentials")
    state = _load_state()
    managed = set(state.get("managed", []))
    names = []
    for sec in cfg.sections():
        if sec == "default" or sec.startswith("profile "):
            names.append(sec.removeprefix("profile "))
    names += [s for s in creds.sections() if s not in names]
    out = []
    for name in names:
        c = _config_section(cfg, name)
        kind = None
        info = {"name": name, "region": (c or {}).get("region") if c else None,
                "managed": name in managed, "active": name == state.get("active")}
        if c is not None and (c.get("sso_session") or c.get("sso_start_url")):
            kind = "sso"
            info["account_id"] = c.get("sso_account_id")
            info["role"] = c.get("sso_role_name")
        elif creds.has_section(name) and creds[name].get("aws_access_key_id"):
            kind = "keys"
            info["temporary"] = bool(creds[name].get("aws_session_token"))
        if kind:
            out.append({**info, "kind": kind})
    return out


def get_profile(name):
    for p in list_profiles():
        if p["name"] == name:
            return p
    raise AccountError(f"ไม่พบโปรไฟล์ AWS '{name}' ใน ~/.aws")


# ---------------------------------------------------------------------------
# คีย์ของโปรไฟล์
# ---------------------------------------------------------------------------

_cred_cache = {}      # name → (creds, expires_epoch)
_cache_lock = threading.Lock()


def _aws_cli():
    exe = shutil.which("aws")
    if not exe:
        raise AccountError("ไม่พบ AWS CLI v2 — ติดตั้งด้วย `brew install awscli` "
                           "เพื่อใช้ SSO ได้ (หรือเพิ่ม account แบบใส่คีย์แทน)")
    return exe


def _export_sso(name):
    try:
        res = subprocess.run([_aws_cli(), "configure", "export-credentials",
                              "--profile", name, "--format", "process"],
                             capture_output=True, text=True, timeout=EXPORT_TIMEOUT,
                             env=tlstrust.cli_env())
    except subprocess.TimeoutExpired as e:
        raise AccountError(f"ขอคีย์ของ {name} ไม่ทันเวลา") from e
    if res.returncode != 0:
        err = (res.stderr or res.stdout).strip()
        expired = any(m in err.lower() for m in (
            "token has expired", "sso session", "error loading sso token",
            "the sso session associated", "refresh failed", "unauthorized"))
        if expired:
            raise AccountError(f"SSO ของ {name} หมดอายุหรือยังไม่ได้ sign in", needs_login=True)
        raise AccountError(f"ขอคีย์ของ {name} ไม่ได้: {err[-300:]}")
    data = json.loads(res.stdout)
    exp = data.get("Expiration")
    exp_t = time.mktime(time.strptime(exp[:19], "%Y-%m-%dT%H:%M:%S")) - time.timezone if exp else None
    return ({"access_key": data["AccessKeyId"], "secret_key": data["SecretAccessKey"],
             "session_token": data.get("SessionToken")}, exp_t)


def credentials(name):
    """(creds dict, expires_epoch|None) ของโปรไฟล์ — SSO ขอผ่าน CLI แล้ว cache จนใกล้หมด"""
    with _cache_lock:
        hit = _cred_cache.get(name)
        if hit and (hit[1] is None or hit[1] - EXPIRY_MARGIN > time.time()):
            return hit
    prof = get_profile(name)
    if prof["kind"] == "sso":
        creds, exp = _export_sso(name)
    else:
        sec = _read(AWS_DIR / "credentials")[name]
        creds = {"access_key": sec["aws_access_key_id"],
                 "secret_key": sec["aws_secret_access_key"],
                 "session_token": sec.get("aws_session_token")}
        exp = None
    with _cache_lock:
        _cred_cache[name] = (creds, exp)
    return creds, exp


def forget_cached(name=None):
    with _cache_lock:
        if name:
            _cred_cache.pop(name, None)
        else:
            _cred_cache.clear()


# ---------------------------------------------------------------------------
# SSO sign-in — รัน `aws sso login` แล้วให้หน้าเว็บ poll สถานะ
# ---------------------------------------------------------------------------

_logins = {}          # name → {"state", "url", "code", "error", "started"}
_login_lock = threading.Lock()
_URL_RE = re.compile(r"https://\S+")
_CODE_RE = re.compile(r"\b[A-Z0-9]{4}-[A-Z0-9]{4}\b")


def start_sso_login(name, session=False):
    """session=False → name คือโปรไฟล์ · session=True → name คือ sso-session (ใช้ตอนเพิ่ม account)"""
    if session:
        _get_session(name)
        key, flag = "session:" + name, "--sso-session"
    else:
        prof = get_profile(name)
        if prof["kind"] != "sso":
            raise AccountError(f"'{name}' ไม่ใช่โปรไฟล์ SSO")
        key, flag = name, "--profile"
    exe = _aws_cli()
    name_arg = name
    name = key
    with _login_lock:
        cur = _logins.get(name)
        if cur and cur["state"] == "waiting":
            return dict(cur)
        _logins[name] = {"state": "waiting", "url": None, "code": None,
                         "error": None, "started": time.time()}
    threading.Thread(target=_run_login, args=(exe, name, flag, name_arg), daemon=True).start()
    return login_status(name)


def _run_login(exe, name, flag="--profile", target=None):
    def upd(**kw):
        with _login_lock:
            _logins[name] = {**_logins[name], **kw}
    try:
        proc = subprocess.Popen([exe, "sso", "login", flag, target or name],
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                env=tlstrust.cli_env())
        tail = []
        start = time.time()
        for line in proc.stdout:
            tail = (tail + [line.strip()])[-6:]
            if (m := _URL_RE.search(line)) and not _logins[name]["url"]:
                upd(url=m.group(0))
            if (m := _CODE_RE.search(line)):
                upd(code=m.group(0))
            if time.time() - start > LOGIN_TIMEOUT:
                proc.kill()
                break
        proc.wait(timeout=10)
        if proc.returncode == 0:
            forget_cached(name)
            upd(state="done")
        else:
            upd(state="error", error="\n".join(t for t in tail if t) or "sign in ไม่สำเร็จ")
    except (OSError, subprocess.SubprocessError) as e:
        upd(state="error", error=str(e))


def login_status(name):
    with _login_lock:
        cur = _logins.get(name)
        return dict(cur) if cur else {"state": "idle"}


# ---------------------------------------------------------------------------
# เพิ่ม/ลบ account
# ---------------------------------------------------------------------------

def _check_name(name, allow_existing_managed=True):
    if not NAME_RE.fullmatch(name or ""):
        raise AccountError("ชื่อ account ใช้ได้แค่ a-z, A-Z, 0-9, - _ . (ไม่เกิน 64 ตัว)")
    existing = {p["name"]: p for p in list_profiles()}
    if name in existing and not (allow_existing_managed and existing[name]["managed"]):
        raise AccountError(f"มีโปรไฟล์ชื่อ '{name}' อยู่แล้วใน ~/.aws — ใช้ชื่ออื่น")


def _backup(path):
    """สำเนาก่อนแก้ — ConfigParser เขียนไฟล์ใหม่ทั้งไฟล์ (comment หาย) จึงต้องมีทางย้อน"""
    if path.exists():
        bak = path.with_name(path.name + ".labctl-bak")
        bak.write_bytes(path.read_bytes())
        bak.chmod(stat.S_IRUSR | stat.S_IWUSR)


def _write(path, cp):
    AWS_DIR.mkdir(mode=0o700, exist_ok=True)
    _backup(path)
    with open(path, "w") as f:
        cp.write(f)
    path.chmod(stat.S_IRUSR | stat.S_IWUSR)


def add_keys_profile(name, access_key, secret_key, session_token=None, region=None):
    """เขียนโปรไฟล์คีย์ลง ~/.aws (แตะเฉพาะ section ของชื่อนี้)"""
    _check_name(name)
    creds_path, cfg_path = AWS_DIR / "credentials", AWS_DIR / "config"
    creds = _read(creds_path)
    if not creds.has_section(name):
        creds.add_section(name)
    creds[name]["aws_access_key_id"] = access_key
    creds[name]["aws_secret_access_key"] = secret_key
    if session_token:
        creds[name]["aws_session_token"] = session_token
    elif creds.has_option(name, "aws_session_token"):
        creds.remove_option(name, "aws_session_token")
    _write(creds_path, creds)
    if region:
        cfg = _read(cfg_path)
        sec = f"profile {name}"
        if not cfg.has_section(sec):
            cfg.add_section(sec)
        cfg[sec]["region"] = region
        _write(cfg_path, cfg)
    forget_cached(name)
    _mark_managed(name)


def add_sso_profile(name, start_url, sso_region, account_id, role_name, region):
    _check_name(name)
    if not start_url.startswith("https://"):
        raise AccountError("SSO start URL ต้องขึ้นต้นด้วย https://")
    if not re.fullmatch(r"\d{12}", account_id or ""):
        raise AccountError("Account ID ต้องเป็นตัวเลข 12 หลัก")
    if not role_name:
        raise AccountError("ต้องระบุ role (permission set) ที่จะใช้")
    cfg_path = AWS_DIR / "config"
    cfg = _read(cfg_path)
    session = f"labctl-{name}"
    ssec = f"sso-session {session}"
    if not cfg.has_section(ssec):
        cfg.add_section(ssec)
    cfg[ssec]["sso_start_url"] = start_url
    cfg[ssec]["sso_region"] = sso_region
    cfg[ssec]["sso_registration_scopes"] = "sso:account:access"
    psec = f"profile {name}"
    if not cfg.has_section(psec):
        cfg.add_section(psec)
    cfg[psec]["sso_session"] = session
    cfg[psec]["sso_account_id"] = account_id
    cfg[psec]["sso_role_name"] = role_name
    cfg[psec]["region"] = region
    _write(cfg_path, cfg)
    forget_cached(name)
    _mark_managed(name)


def removable(prof):
    """SSO ลบได้ทุกตัว (ไม่มีความลับในโปรไฟล์ — เพิ่มกลับได้จากรายการ SSO)
    ส่วนคีย์ลบได้เฉพาะที่ labctl สร้าง: คีย์ที่ผู้ใช้ใส่เองหายแล้วเอากลับมาไม่ได้"""
    return prof["kind"] == "sso" or prof["managed"]


def remove_profile(name):
    prof = get_profile(name)
    if not removable(prof):
        raise AccountError(f"'{name}' เป็นคีย์ที่ไม่ได้ใส่ผ่าน labctl — ลบเองใน ~/.aws/credentials ถ้าต้องการ")
    creds_path, cfg_path = AWS_DIR / "credentials", AWS_DIR / "config"
    creds = _read(creds_path)
    if creds.remove_section(name):
        _write(creds_path, creds)
    cfg = _read(cfg_path)
    changed = cfg.remove_section(f"profile {name}")
    changed = cfg.remove_section(f"sso-session labctl-{name}") or changed
    if changed:
        _write(cfg_path, cfg)
    forget_cached(name)
    _mark_managed(name, managed=False)
    with _state_lock:
        state = _load_state()
        if state.get("active") == name:
            state.pop("active", None)
            _save_state(state)


# ---------------------------------------------------------------------------
# SSO sessions — sign in ครั้งเดียวแล้วเลือก account/role จากรายการ
# (แบบเดียวกับ `aws configure sso` แต่ทำในหน้าเว็บ)
# ---------------------------------------------------------------------------

SSO_CACHE = AWS_DIR / "sso" / "cache"
SESSION_PREFIX = "sso-session "


def list_sso_sessions():
    cfg = _read(AWS_DIR / "config")
    out = []
    for sec in cfg.sections():
        if sec.startswith(SESSION_PREFIX):
            c = cfg[sec]
            sname = sec.removeprefix(SESSION_PREFIX)
            out.append({"name": sname,
                        "profiles": [p for p in cfg.sections() if p.startswith("profile ")
                                     and cfg[p].get("sso_session") == sname],
                        "start_url": c.get("sso_start_url"),
                        "region": c.get("sso_region"),
                        "signed_in": _session_token(sname) is not None})
    return out


def _get_session(name):
    for s in list_sso_sessions():
        if s["name"] == name:
            return s
    raise AccountError(f"ไม่พบ SSO session '{name}' ใน ~/.aws/config")


def add_sso_session(name, start_url, sso_region):
    _check_name(name)
    if not start_url.startswith("https://"):
        raise AccountError("SSO start URL ต้องขึ้นต้นด้วย https://")
    cfg_path = AWS_DIR / "config"
    cfg = _read(cfg_path)
    sec = SESSION_PREFIX + name
    if cfg.has_section(sec):
        raise AccountError(f"มี SSO session ชื่อ '{name}' อยู่แล้ว")
    cfg.add_section(sec)
    cfg[sec]["sso_start_url"] = start_url
    cfg[sec]["sso_region"] = sso_region
    cfg[sec]["sso_registration_scopes"] = "sso:account:access"
    _write(cfg_path, cfg)


def _session_token(name):
    """access token ที่ `aws sso login --sso-session` cache ไว้ — ไฟล์ชื่อ sha1(ชื่อ session)"""
    import hashlib
    path = SSO_CACHE / (hashlib.sha1(name.encode()).hexdigest() + ".json")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    exp = data.get("expiresAt", "")
    try:
        exp_t = time.mktime(time.strptime(exp[:19], "%Y-%m-%dT%H:%M:%S")) - time.timezone
    except ValueError:
        return None
    if not data.get("accessToken") or exp_t - EXPIRY_MARGIN < time.time():
        return None
    return data["accessToken"]


def _sso_call(args, region):
    try:
        res = subprocess.run([_aws_cli(), "sso", *args, "--region", region, "--output", "json"],
                             capture_output=True, text=True, timeout=EXPORT_TIMEOUT,
                             env=tlstrust.cli_env())
    except subprocess.TimeoutExpired as e:
        raise AccountError("ถาม AWS SSO ไม่ทันเวลา") from e
    if res.returncode != 0:
        err = (res.stderr or res.stdout).strip()
        if "unauthorized" in err.lower() or "expired" in err.lower():
            raise AccountError("SSO หมดอายุ — sign in ใหม่", needs_login=True)
        raise AccountError(f"ถาม AWS SSO ไม่สำเร็จ: {err[-300:]}")
    return json.loads(res.stdout or "{}")


def list_sso_accounts(session):
    """account + role ทั้งหมดที่ผู้ใช้เข้าได้ผ่าน session นี้ (ต้อง sign in แล้ว)"""
    sess = _get_session(session)
    token = _session_token(session)
    if not token:
        raise AccountError(f"ยังไม่ได้ sign in SSO '{session}'", needs_login=True)
    region = sess["region"] or "us-east-1"
    accts = _sso_call(["list-accounts", "--access-token", token], region).get("accountList", [])
    existing = {(p.get("account_id"), p.get("role")): p["name"] for p in list_profiles() if p["kind"] == "sso"}
    out = []
    for a in sorted(accts, key=lambda x: x.get("accountName", "").lower()):
        roles = _sso_call(["list-account-roles", "--access-token", token,
                           "--account-id", a["accountId"]], region).get("roleList", [])
        out.append({"account_id": a["accountId"], "account_name": a.get("accountName"),
                    "email": a.get("emailAddress"),
                    "roles": [{"name": r["roleName"],
                               "profile": existing.get((a["accountId"], r["roleName"]))}
                              for r in roles]})
    return out


def add_profile_from_session(name, session, account_id, role_name, region):
    """โปรไฟล์ใหม่ที่ใช้ sso-session เดิม — sign in ครั้งเดียวใช้ได้ทุก account ใน org"""
    _check_name(name)
    _get_session(session)
    if not re.fullmatch(r"\d{12}", account_id or ""):
        raise AccountError("Account ID ต้องเป็นตัวเลข 12 หลัก")
    cfg_path = AWS_DIR / "config"
    cfg = _read(cfg_path)
    psec = f"profile {name}"
    if not cfg.has_section(psec):
        cfg.add_section(psec)
    cfg[psec]["sso_session"] = session
    cfg[psec]["sso_account_id"] = account_id
    cfg[psec]["sso_role_name"] = role_name
    cfg[psec]["region"] = region
    _write(cfg_path, cfg)
    forget_cached(name)
    _mark_managed(name)


def remove_sso_session(name):
    """ลบ SSO login ที่ไม่มีโปรไฟล์ไหนใช้แล้ว"""
    sess = _get_session(name)
    if sess["profiles"]:
        names = ", ".join(p.removeprefix("profile ") for p in sess["profiles"])
        raise AccountError(f"ยังมี account ที่ใช้ SSO login นี้: {names} — ลบ account ก่อน")
    cfg_path = AWS_DIR / "config"
    cfg = _read(cfg_path)
    if cfg.remove_section(SESSION_PREFIX + name):
        _write(cfg_path, cfg)


# ---------------------------------------------------------------------------
# sign out SSO — เพิกถอน token ฝั่ง AWS แล้วลบ cache ของ session นั้น
# (ไม่ใช้ `aws sso logout` เพราะคำสั่งนั้น sign out ทุก session ในเครื่อง)
# ---------------------------------------------------------------------------

def sso_logout(session):
    """คืน True ถ้าเพิกถอนกับ AWS สำเร็จ (False = token หมดอายุ/ไม่มีอยู่แล้ว — ลบ cache อย่างเดียว)"""
    import hashlib
    import urllib.error
    import urllib.request
    sess = _get_session(session)
    token = _session_token(session)
    revoked = False
    if token:
        region = sess["region"] or "us-east-1"
        req = urllib.request.Request(f"https://portal.sso.{region}.amazonaws.com/logout",
                                     method="POST", data=b"",
                                     headers={"x-amz-sso_bearer_token": token})
        try:
            with urllib.request.urlopen(req, timeout=15, context=tlstrust.ssl_context()):
                revoked = True
        except urllib.error.HTTPError as e:
            if e.code not in (401, 403):        # token ตายแล้ว = เป้าหมายสำเร็จอยู่แล้ว
                raise AccountError(f"sign out SSO ไม่สำเร็จ (HTTP {e.code})") from e
        except urllib.error.URLError as e:
            raise AccountError(f"ติดต่อ AWS SSO ไม่ได้: {e.reason}") from e
    cache = SSO_CACHE / (hashlib.sha1(session.encode()).hexdigest() + ".json")
    cache.unlink(missing_ok=True)
    for p in list_profiles():
        forget_cached(p["name"])
    return revoked


def profile_session(name):
    """sso-session ที่โปรไฟล์นี้ใช้ (None = โปรไฟล์คีย์ หรือ SSO แบบเก่าที่ไม่มี session)"""
    c = _config_section(_read(AWS_DIR / "config"), name)
    return c.get("sso_session") if c is not None else None
