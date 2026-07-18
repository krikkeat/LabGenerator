"""เชื่อมต่อ AWS จาก web UI — ตรวจ/ตั้งค่า credential โดยไม่ต้องมี aws CLI หรือ boto3

- ตรวจ credential ด้วย STS GetCallerIdentity (เซ็น SigV4 เองด้วย stdlib ล้วน)
- login: ตรวจ key กับ AWS จริงก่อน แล้วตั้งเป็น env ของ process นี้
  (terraform ทุกตัวที่ job รันจะเห็นทันที) + เลือกจำลงดิสก์ได้ที่
  ~/.aws/credentials โปรไฟล์ "labctl" — ที่เดียวกับที่ aws configure ใช้
- ไม่เก็บ secret ลง labs.db และไม่เขียนลง log
"""

import configparser
import hashlib
import hmac
import os
import stat
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree

AWS_DIR = Path.home() / ".aws"
PROFILE = "labctl"
DEFAULT_REGION = "ap-southeast-1"

_lock = threading.Lock()
_cache = {"t": 0.0, "res": None}


class AwsError(Exception):
    pass


# ---------------------------------------------------------------------------
# SigV4 — STS GetCallerIdentity (endpoint global, เซ็นเป็น us-east-1)
# ---------------------------------------------------------------------------

def _hmac(key, msg):
    return hmac.new(key, msg.encode(), hashlib.sha256).digest()


def sts_get_caller_identity(access_key, secret_key, session_token=None, timeout=12):
    host = "sts.amazonaws.com"
    body = "Action=GetCallerIdentity&Version=2011-06-15"
    payload_hash = hashlib.sha256(body.encode()).hexdigest()
    now = datetime.now(timezone.utc)
    amzdate = now.strftime("%Y%m%dT%H%M%SZ")
    datestamp = now.strftime("%Y%m%d")
    region = "us-east-1"

    headers = {
        "content-type": "application/x-www-form-urlencoded; charset=utf-8",
        "host": host,
        "x-amz-date": amzdate,
    }
    if session_token:
        headers["x-amz-security-token"] = session_token

    signed = ";".join(sorted(headers))
    canonical = "\n".join([
        "POST", "/", "",
        "".join(f"{k}:{headers[k]}\n" for k in sorted(headers)),
        signed, payload_hash,
    ])
    scope = f"{datestamp}/{region}/sts/aws4_request"
    to_sign = "\n".join(["AWS4-HMAC-SHA256", amzdate, scope,
                         hashlib.sha256(canonical.encode()).hexdigest()])
    k = _hmac(("AWS4" + secret_key).encode(), datestamp)
    k = _hmac(k, region)
    k = _hmac(k, "sts")
    k = _hmac(k, "aws4_request")
    signature = hmac.new(k, to_sign.encode(), hashlib.sha256).hexdigest()

    req_headers = dict(headers)
    req_headers["Authorization"] = (
        f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, "
        f"SignedHeaders={signed}, Signature={signature}")
    req = urllib.request.Request(f"https://{host}/", data=body.encode(),
                                 headers=req_headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            xml = resp.read().decode()
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        try:
            root = ElementTree.fromstring(detail)
            code = root.findtext(".//{*}Code") or e.code
            msg = root.findtext(".//{*}Message") or ""
            raise AwsError(f"AWS ปฏิเสธ credential ({code}): {msg}")
        except ElementTree.ParseError:
            raise AwsError(f"AWS ปฏิเสธ credential (HTTP {e.code})")
    except urllib.error.URLError as e:
        raise AwsError(f"ติดต่อ AWS ไม่ได้: {e.reason}")

    root = ElementTree.fromstring(xml)
    account = root.findtext(".//{*}Account")
    arn = root.findtext(".//{*}Arn")
    if not account:
        raise AwsError("อ่านผลตอบจาก STS ไม่ได้")
    return {"account": account, "arn": arn}


# ---------------------------------------------------------------------------
# หา credential ตามลำดับเดียวกับที่ terraform จะหาเจอ
# ---------------------------------------------------------------------------

def _read_profiles():
    cp = configparser.ConfigParser()
    cred_file = AWS_DIR / "credentials"
    if cred_file.exists():
        cp.read(cred_file)
    return cp


def resolve_credentials():
    """คืน (method, key, secret, token) หรือ None — ลำดับ: env → AWS_PROFILE →
    default → labctl (ถ้าเจอเฉพาะ labctl จะตั้ง AWS_PROFILE ให้ terraform เห็นด้วย)"""
    if os.environ.get("AWS_ACCESS_KEY_ID") and os.environ.get("AWS_SECRET_ACCESS_KEY"):
        return ("env", os.environ["AWS_ACCESS_KEY_ID"],
                os.environ["AWS_SECRET_ACCESS_KEY"],
                os.environ.get("AWS_SESSION_TOKEN"))
    cp = _read_profiles()
    order = []
    if os.environ.get("AWS_PROFILE"):
        order.append(os.environ["AWS_PROFILE"])
    order += ["default", PROFILE]
    for name in order:
        if cp.has_section(name) or (name == "default" and cp.defaults()):
            sec = cp[name] if cp.has_section(name) else cp.defaults()
            key = sec.get("aws_access_key_id")
            secret = sec.get("aws_secret_access_key")
            if key and secret:
                if name == PROFILE and os.environ.get("AWS_PROFILE") != PROFILE:
                    # ให้ terraform (จาก job) ใช้โปรไฟล์เดียวกันโดยอัตโนมัติ
                    os.environ["AWS_PROFILE"] = PROFILE
                return (f"profile:{name}", key, secret, sec.get("aws_session_token"))
    return None


def default_region():
    for var in ("AWS_REGION", "AWS_DEFAULT_REGION"):
        if os.environ.get(var):
            return os.environ[var]
    cp = configparser.ConfigParser()
    cfg = AWS_DIR / "config"
    if cfg.exists():
        cp.read(cfg)
        for sec in (f"profile {os.environ.get('AWS_PROFILE', '')}", "default",
                    f"profile {PROFILE}"):
            if cp.has_section(sec) and cp[sec].get("region"):
                return cp[sec]["region"]
    return None


# ---------------------------------------------------------------------------
# public: status / login
# ---------------------------------------------------------------------------

def status(force=False):
    """สถานะการเชื่อมต่อ (cache 60 วิ — จะได้ไม่ยิง STS ทุกครั้งที่หน้าเว็บโหลด)"""
    with _lock:
        if not force and _cache["res"] and time.time() - _cache["t"] < 60:
            return _cache["res"]
        creds = resolve_credentials()
        if not creds:
            res = {"connected": False, "reason": "ยังไม่มี credential ในเครื่อง"}
        else:
            method, key, secret, token = creds
            try:
                ident = sts_get_caller_identity(key, secret, token)
                res = {"connected": True, "method": method,
                       "account": ident["account"], "arn": ident["arn"],
                       "region": default_region() or DEFAULT_REGION}
            except AwsError as e:
                res = {"connected": False, "method": method, "reason": str(e)}
        _cache["t"] = time.time()
        _cache["res"] = res
        return res


def login(access_key, secret_key, session_token=None, region=None, remember=False):
    """ตรวจ key กับ AWS ก่อน — ผ่านแล้วค่อยตั้งใช้งาน ไม่ผ่านคือ error กลับไป"""
    access_key = (access_key or "").strip()
    secret_key = (secret_key or "").strip()
    session_token = (session_token or "").strip() or None
    region = (region or "").strip() or DEFAULT_REGION
    if not access_key or not secret_key:
        raise AwsError("ต้องใส่ทั้ง Access Key ID และ Secret Access Key")

    ident = sts_get_caller_identity(access_key, secret_key, session_token)

    # ตั้งเป็น env ของ process — terraform ที่ job รันต่อจากนี้เห็นทันที
    os.environ["AWS_ACCESS_KEY_ID"] = access_key
    os.environ["AWS_SECRET_ACCESS_KEY"] = secret_key
    if session_token:
        os.environ["AWS_SESSION_TOKEN"] = session_token
    else:
        os.environ.pop("AWS_SESSION_TOKEN", None)
    os.environ["AWS_DEFAULT_REGION"] = region
    os.environ.pop("AWS_PROFILE", None)

    if remember:
        _write_profile(access_key, secret_key, session_token, region)

    with _lock:
        _cache["t"] = 0.0
        _cache["res"] = None
    return {"connected": True, "account": ident["account"], "arn": ident["arn"],
            "region": region, "method": "env" if not remember else f"profile:{PROFILE}",
            "remembered": bool(remember)}


def _write_profile(access_key, secret_key, session_token, region):
    """เขียนโปรไฟล์ [labctl] ลง ~/.aws/credentials + region ลง ~/.aws/config
    (แตะเฉพาะ section ของเรา — โปรไฟล์อื่นที่มีอยู่ไม่ถูกยุ่ง)"""
    AWS_DIR.mkdir(mode=0o700, exist_ok=True)

    cred_path = AWS_DIR / "credentials"
    cp = configparser.ConfigParser()
    if cred_path.exists():
        cp.read(cred_path)
    if not cp.has_section(PROFILE):
        cp.add_section(PROFILE)
    cp[PROFILE]["aws_access_key_id"] = access_key
    cp[PROFILE]["aws_secret_access_key"] = secret_key
    if session_token:
        cp[PROFILE]["aws_session_token"] = session_token
    elif cp.has_option(PROFILE, "aws_session_token"):
        cp.remove_option(PROFILE, "aws_session_token")
    with open(cred_path, "w") as f:
        cp.write(f)
    cred_path.chmod(stat.S_IRUSR | stat.S_IWUSR)

    cfg_path = AWS_DIR / "config"
    cp2 = configparser.ConfigParser()
    if cfg_path.exists():
        cp2.read(cfg_path)
    sec = f"profile {PROFILE}"
    if not cp2.has_section(sec):
        cp2.add_section(sec)
    cp2[sec]["region"] = region
    with open(cfg_path, "w") as f:
        cp2.write(f)
    cfg_path.chmod(stat.S_IRUSR | stat.S_IWUSR)

    # ให้ terraform รอบหน้า (หลัง restart server) เจอโปรไฟล์นี้เอง
    os.environ["AWS_PROFILE"] = PROFILE
