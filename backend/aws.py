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
import json
import os
import stat
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree

AWS_DIR = Path.home() / ".aws"
PROFILE = "labctl"
DEFAULT_REGION = "ap-southeast-1"

# บันทึกว่าใส่คีย์เมื่อไหร่ — AWS ไม่บอกอายุที่เหลือของ session token ผ่าน API ใด ๆ
# (ตัว token เป็น opaque) เก็บเวลาที่ใส่ไว้เองจึงเป็นข้อมูลเดียวที่ใช้ประมาณได้
CRED_META = Path.home() / ".lab-generator" / "cred-meta.json"

# อายุ session ที่ใช้ประมาณเวลาเตือน — องค์กรตั้งได้ต่างกัน เปลี่ยนผ่าน env ได้
CRED_SESSION_HOURS = float(os.environ.get("LABCTL_CRED_SESSION_HOURS", "8"))

RELOGIN_HINT = ("→ ไปหน้า 'เชื่อมต่อ AWS' แล้วใส่คีย์ชุดใหม่จาก AWS access portal "
                "(คีย์ชั่วคราวมีอายุแค่ไม่กี่ชั่วโมง) แล้วสั่งงานใหม่อีกครั้ง")

_lock = threading.Lock()
_cache = {"t": 0.0, "res": None}


class AwsError(Exception):
    def __init__(self, message, code=None, aws_message=None):
        super().__init__(message)
        self.code = code
        # ข้อความดิบจาก AWS — ให้ผู้เรียกเอาไปเรียบเรียงใหม่ตามบริบทของตัวเองได้
        self.aws_message = aws_message or ""


# ---------------------------------------------------------------------------
# SigV4 — ผู้ลงนามกลาง ใช้ได้กับทุก Query API (sts, ec2) เพราะรูปแบบ request
# เหมือนกันหมด: POST form-encoded ต่างแค่ host / service / region ใน scope
# ---------------------------------------------------------------------------

def _hmac(key, msg):
    return hmac.new(key, msg.encode(), hashlib.sha256).digest()


def _signed_post(service, region, host, params, access_key, secret_key,
                 session_token=None, timeout=12):
    """เซ็น SigV4 แล้วยิง POST — คืน XML root ที่ parse แล้ว"""
    body = urllib.parse.urlencode(params)
    payload_hash = hashlib.sha256(body.encode()).hexdigest()
    now = datetime.now(timezone.utc)
    amzdate = now.strftime("%Y%m%dT%H%M%SZ")
    datestamp = now.strftime("%Y%m%d")

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
    scope = f"{datestamp}/{region}/{service}/aws4_request"
    to_sign = "\n".join(["AWS4-HMAC-SHA256", amzdate, scope,
                         hashlib.sha256(canonical.encode()).hexdigest()])
    k = _hmac(("AWS4" + secret_key).encode(), datestamp)
    k = _hmac(k, region)
    k = _hmac(k, service)
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
        except ElementTree.ParseError:
            raise AwsError(f"AWS ตอบกลับเป็น error (HTTP {e.code})")
        code = root.findtext(".//{*}Code") or str(e.code)
        msg = root.findtext(".//{*}Message") or ""
        raise AwsError(f"AWS ตอบกลับเป็น error ({code}): {msg}",
                       code=code, aws_message=msg)
    except urllib.error.URLError as e:
        raise AwsError(f"ติดต่อ AWS ไม่ได้: {e.reason}")
    return ElementTree.fromstring(xml)


# ---------------------------------------------------------------------------
# STS GetCallerIdentity (endpoint global, เซ็นเป็น us-east-1)
# ---------------------------------------------------------------------------

def sts_get_caller_identity(access_key, secret_key, session_token=None, timeout=12):
    """ลายเซ็น SigV4 มีอายุ 15 นาที — ถ้าเครื่อง sleep คาระหว่างส่ง request
    ลายเซ็นจะเก่าเกินตอนที่ AWS ได้รับ แล้วตอบ SignatureDoesNotMatch ทั้งที่
    คีย์ยังดีอยู่ เคสนี้เซ็นใหม่แล้วยิงซ้ำอีกรอบก็ผ่าน"""
    try:
        return _sts_call(access_key, secret_key, session_token, timeout)
    except AwsError as e:
        if e.code != "SignatureDoesNotMatch":
            raise
    return _sts_call(access_key, secret_key, session_token, timeout)


def _sts_call(access_key, secret_key, session_token=None, timeout=12):
    try:
        root = _signed_post("sts", "us-east-1", "sts.amazonaws.com",
                            {"Action": "GetCallerIdentity", "Version": "2011-06-15"},
                            access_key, secret_key, session_token, timeout)
    except AwsError as e:
        if e.code:  # AWS ตอบมาจริง = คีย์มีปัญหา ไม่ใช่เน็ตมีปัญหา
            raise AwsError(f"AWS ปฏิเสธ credential ({e.code}): {e.aws_message}",
                           code=e.code, aws_message=e.aws_message)
        raise
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


def credential_candidates():
    """credential ทุกแหล่งที่หาเจอ เรียงลำดับเดียวกับที่ terraform ใช้:
    env → AWS_PROFILE → default → labctl

    คืนมาทั้งหมดแทนที่จะคืนตัวแรก เพราะคีย์ที่ค้างอยู่ใน env ของ process อาจ
    หมดอายุไปแล้วในขณะที่โปรไฟล์บนดิสก์เพิ่งถูกอัปเดต — resolve_verified()
    จะได้ข้ามตัวที่ใช้ไม่ได้ไปหยิบตัวถัดไปเอง"""
    out = []
    if os.environ.get("AWS_ACCESS_KEY_ID") and os.environ.get("AWS_SECRET_ACCESS_KEY"):
        out.append(("env", os.environ["AWS_ACCESS_KEY_ID"],
                    os.environ["AWS_SECRET_ACCESS_KEY"],
                    os.environ.get("AWS_SESSION_TOKEN")))
    cp = _read_profiles()
    order = []
    if os.environ.get("AWS_PROFILE"):
        order.append(os.environ["AWS_PROFILE"])
    order += ["default", PROFILE]
    seen = set()
    for name in order:
        if name in seen:
            continue
        seen.add(name)
        if cp.has_section(name):
            sec = cp[name]
        elif name == "default" and cp.defaults():
            sec = cp.defaults()
        else:
            continue
        key = sec.get("aws_access_key_id")
        secret = sec.get("aws_secret_access_key")
        if key and secret:
            out.append((f"profile:{name}", key, secret, sec.get("aws_session_token")))
    return out


def resolve_credentials():
    """คืน (method, key, secret, token) ตัวแรกที่หาเจอ หรือ None — เช็คแค่ว่า
    'มี credential อยู่ไหม' ไม่ได้ยืนยันกับ AWS ว่ายังใช้ได้"""
    cands = credential_candidates()
    if not cands:
        return None
    if cands[0][0] == f"profile:{PROFILE}" and os.environ.get("AWS_PROFILE") != PROFILE:
        # ให้ terraform (จาก job) ใช้โปรไฟล์เดียวกันโดยอัตโนมัติ
        os.environ["AWS_PROFILE"] = PROFILE
    return cands[0]


def resolve_verified(timeout=12):
    """ไล่ credential ทุกแหล่งแล้วยืนยันกับ STS จริง — คืน dict ของตัวแรกที่ผ่าน

    ใช้ก่อนเริ่ม job: ล้มตรงนี้พร้อมข้อความที่อ่านรู้เรื่อง ดีกว่าปล่อยให้ terraform
    ไปพังกลางทางด้วย ExpiredToken หรือ 'no EC2 IMDS role found'"""
    cands = credential_candidates()
    if not cands:
        raise AwsError("ยังไม่ได้เชื่อมต่อ AWS — ไม่พบ credential ในเครื่อง\n" + RELOGIN_HINT)
    # รวมแหล่งที่พังด้วยเหตุผลเดียวกันเข้าบรรทัดเดียว — ปกติ env กับ profile คือ
    # คีย์ชุดเดียวกัน ข้อความจะซ้ำกันเป๊ะ ไม่มีประโยชน์ที่จะโชว์สองรอบ
    problems = {}
    for method, key, secret, token in cands:
        try:
            ident = sts_get_caller_identity(key, secret, token, timeout=timeout)
        except AwsError as e:
            problems.setdefault(str(e), []).append(method)
            continue
        return {"method": method, "access_key": key, "secret_key": secret,
                "session_token": token, "region": default_region() or DEFAULT_REGION,
                "account": ident["account"], "arn": ident["arn"]}
    detail = "\n".join(f"  · {', '.join(ms)}: {msg}" for msg, ms in problems.items())
    raise AwsError("credential ที่มีอยู่ใช้ไม่ได้:\n" + detail + "\n" + RELOGIN_HINT)


def credential_env(creds):
    """env ที่จะยัดให้ terraform — ส่งคีย์ตรง ๆ ไม่พึ่ง AWS_PROFILE เพราะ
    provider ใน versions.tf ไม่ได้ระบุ profile ไว้ และเครื่องอาจไม่มีโปรไฟล์
    [default] ให้ SDK หยิบ (จะตกไปถาม EC2 IMDS แล้ว timeout)"""
    env = {"AWS_ACCESS_KEY_ID": creds["access_key"],
           "AWS_SECRET_ACCESS_KEY": creds["secret_key"],
           "AWS_REGION": creds["region"],
           "AWS_DEFAULT_REGION": creds["region"]}
    if creds["session_token"]:
        env["AWS_SESSION_TOKEN"] = creds["session_token"]
    return env


_env_cache = {"t": 0.0, "env": None}


def terraform_env(base=None, ttl=300, timeout=12):
    """env สำหรับ subprocess terraform — ยัด credential ลงไปตรง ๆ

    ทำไมไม่ปล่อยให้ provider หาเอง: provider ใน versions.tf ไม่ได้ระบุ profile และ
    เครื่องที่ล็อกอินผ่านหน้าเว็บจะมีแต่โปรไฟล์ [labctl] ไม่มี [default] ให้ SDK หยิบ
    terraform จะไปถาม EC2 IMDS แล้ว timeout จบด้วย 'No valid credential sources found'
    ทั้งที่คีย์อยู่ในเครื่องพร้อมใช้

    cache ไว้ ttl วินาที — apply ครั้งหนึ่งเรียก terraform หลายรอบ ไม่ต้องยิง STS ซ้ำทุกรอบ
    """
    global _env_cache
    now = time.time()
    if not _env_cache["env"] or now - _env_cache["t"] > ttl:
        _env_cache = {"t": now, "env": credential_env(resolve_verified(timeout=timeout))}
    env = dict(os.environ if base is None else base)
    env.update(_env_cache["env"])
    return env


def _write_cred_meta(temporary, method):
    try:
        CRED_META.parent.mkdir(parents=True, exist_ok=True)
        CRED_META.write_text(json.dumps(
            {"stored_at": time.time(), "temporary": bool(temporary), "method": method}),
            encoding="utf-8")
    except OSError:
        pass        # บันทึกไม่ได้ก็ไม่ควรทำให้ล็อกอินล้ม


def credential_info():
    """ข้อมูลเท่าที่รู้เกี่ยวกับคีย์ที่ใช้อยู่ — อายุที่ผ่านไป และเป็นคีย์ชั่วคราวไหม

    AWS ไม่มี API บอกว่า session token เหลืออีกกี่ชั่วโมง เราจึงประมาณจากเวลาที่
    ใส่คีย์ (บันทึกไว้ตอน login) หรือ mtime ของไฟล์โปรไฟล์เป็นทางเลือกรอง
    """
    stored_at, method = None, None
    if CRED_META.exists():
        try:
            meta = json.loads(CRED_META.read_text(encoding="utf-8"))
            stored_at, method = meta.get("stored_at"), meta.get("method")
        except (ValueError, OSError):
            pass
    cred_file = AWS_DIR / "credentials"
    if stored_at is None and cred_file.exists():
        stored_at = cred_file.stat().st_mtime
    cands = credential_candidates()
    temporary = bool(cands and cands[0][3])      # มี session token = คีย์ชั่วคราว
    age_hours = (time.time() - stored_at) / 3600 if stored_at else None
    left = (CRED_SESSION_HOURS - age_hours) if (temporary and age_hours is not None) else None
    return {"stored_at": stored_at, "age_hours": age_hours, "temporary": temporary,
            "method": method, "assumed_session_hours": CRED_SESSION_HOURS,
            "estimated_hours_left": left}


def ttl_outlives_credentials(ttl_hours):
    """lab จะอยู่นานกว่าคีย์หรือเปล่า — ถ้าใช่ auto-expire มีสิทธิ์ล้มตอนถึงเวลา

    คืน (True, ชั่วโมงที่คาดว่าเหลือ) เมื่อควรเตือน · (False, ...) เมื่อไม่ต้อง
    """
    if not ttl_hours:
        return False, None
    info = credential_info()
    left = info["estimated_hours_left"]
    if left is None:
        return False, None
    return ttl_hours > left, left


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


def login(access_key, secret_key, session_token=None, region=None, remember=True):
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
    _write_cred_meta(temporary=bool(session_token),
                     method="profile:" + PROFILE if remember else "env")

    with _lock:
        _cache["t"] = 0.0
        _cache["res"] = None
    global _env_cache
    _env_cache = {"t": 0.0, "env": None}   # คีย์ใหม่ต้องมีผลกับ terraform ทันที
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


# ---------------------------------------------------------------------------
# ตรวจหา resource ค้างตาม tag — ถาม AWS ตรง ๆ ไม่ผ่าน terraform state
#
# ทำไมต้องมี: state หายเมื่อไหร่ terraform ก็มองไม่เห็น resource ที่ตัวเองสร้าง
# แล้ว dashboard จะขึ้น 0 VM ทั้งที่ของยังเดินเงินอยู่ (ดู POSTMORTEM-state-loss.md)
# ตัวนี้ถาม EC2 API ด้วย tag ที่ default_tags ใน versions.tf ติดไว้ทุก resource
# จึงเป็นคำตอบที่เชื่อได้แม้ state จะหายไปแล้ว
# ---------------------------------------------------------------------------

EC2_API_VERSION = "2016-11-15"
PROJECT_TAG = "hands-on-lab"

# ไม่รวม terminated — instance ที่ลบแล้วยังค้างใน API อีกราวชั่วโมง
# ถ้านับด้วยจะเตือนผิดว่ายังมีของค้างทั้งที่ลบไปแล้ว
LIVE_INSTANCE_STATES = ["pending", "running", "shutting-down", "stopping", "stopped"]

# region ที่บัญชีถูกปิดกั้นไว้ (SCP ขององค์กร / region ที่ยังไม่ opt-in) — สร้าง
# resource ที่นั่นไม่ได้อยู่แล้ว จึงนับเป็น "ข้าม" ไม่ใช่ "ตรวจไม่สำเร็จ"
_REGION_BLOCKED_CODES = {"UnauthorizedOperation", "AccessDenied", "AccessDeniedException",
                         "AuthFailure", "OptInRequired", "UnauthorizedAccess"}

# (action, container ใน XML, tag ที่เป็น id, ป้ายที่โชว์)
# เรียงตามลำดับที่ต้องลบจริง (EC2 ก่อน → VPC ท้ายสุด) เพื่อให้ผลลัพธ์อ่านแล้ว
# ลงมือตามได้เลย — ลำดับเดียวกับใน POSTMORTEM-state-loss.md
_EC2_SCANS = [
    ("DescribeInstances", "instancesSet", "instanceId", "EC2 instance"),
    ("DescribeSecurityGroups", "securityGroupInfo", "groupId", "security group"),
    ("DescribeSubnets", "subnetSet", "subnetId", "subnet"),
    ("DescribeInternetGateways", "internetGatewaySet", "internetGatewayId", "internet gateway"),
    ("DescribeRouteTables", "routeTableSet", "routeTableId", "route table"),
    ("DescribeVpcs", "vpcSet", "vpcId", "VPC"),
    ("DescribeKeyPairs", "keySet", "keyName", "key pair"),
]


def create_image(instance_id, name, description="", region=None, timeout=30):
    """สั่งอบ AMI จาก instance ที่รันอยู่ — คืน ami-id ทันที (ยังไม่พร้อมใช้)"""
    creds = resolve_verified(timeout=timeout)
    region = region or creds["region"]
    params = {"Action": "CreateImage", "Version": EC2_API_VERSION,
              "InstanceId": instance_id, "Name": name, "Description": description,
              # ไม่ปิดเครื่องก่อน = ภาพอาจไม่ consistent · ยอมให้ reboot ดีกว่าได้ภาพเสีย
              "NoReboot": "false"}
    root = _signed_post("ec2", region, f"ec2.{region}.amazonaws.com", params,
                        creds["access_key"], creds["secret_key"],
                        creds["session_token"], timeout)
    ami = root.findtext(".//{*}imageId")
    if not ami:
        raise AwsError("AWS ไม่ได้คืน imageId กลับมา")
    return ami


def image_state(ami_id, region=None, timeout=20):
    creds = resolve_verified(timeout=timeout)
    region = region or creds["region"]
    root = _signed_post("ec2", region, f"ec2.{region}.amazonaws.com",
                        {"Action": "DescribeImages", "Version": EC2_API_VERSION,
                         "ImageId.1": ami_id},
                        creds["access_key"], creds["secret_key"],
                        creds["session_token"], timeout)
    return root.findtext(".//{*}imagesSet/{*}item/{*}imageState") or "unknown"


def wait_image_available(ami_id, region=None, timeout_minutes=20, interval=20):
    deadline = time.time() + timeout_minutes * 60
    while time.time() < deadline:
        state = image_state(ami_id, region=region)
        if state in ("available", "failed", "error", "invalid"):
            return state
        time.sleep(interval)
    return "pending"


def delete_image(ami_id, region=None, timeout=30):
    """ลบ AMI พร้อม snapshot ที่ผูกอยู่ — ไม่ลบ snapshot ด้วยจะเสียค่าเก็บไปเรื่อย ๆ"""
    creds = resolve_verified(timeout=timeout)
    region = region or creds["region"]
    host = f"ec2.{region}.amazonaws.com"

    root = _signed_post("ec2", region, host,
                        {"Action": "DescribeImages", "Version": EC2_API_VERSION,
                         "ImageId.1": ami_id},
                        creds["access_key"], creds["secret_key"],
                        creds["session_token"], timeout)
    snaps = [e.text for e in root.findall(".//{*}ebs/{*}snapshotId") if e.text]

    _signed_post("ec2", region, host,
                 {"Action": "DeregisterImage", "Version": EC2_API_VERSION, "ImageId": ami_id},
                 creds["access_key"], creds["secret_key"], creds["session_token"], timeout)
    deleted = []
    for snap in snaps:
        try:
            _signed_post("ec2", region, host,
                         {"Action": "DeleteSnapshot", "Version": EC2_API_VERSION,
                          "SnapshotId": snap},
                         creds["access_key"], creds["secret_key"],
                         creds["session_token"], timeout)
            deleted.append(snap)
        except AwsError:
            pass          # snapshot อาจถูกใช้โดย AMI อื่น — ปล่อยไว้ดีกว่าพัง
    return {"ami": ami_id, "snapshots_deleted": deleted}


def power_instances(instance_ids, action, region=None, timeout=30):
    """สั่ง stop/start instance ตรงผ่าน EC2 API — ไม่ผ่าน terraform

    ทำไมไม่ใช้ terraform: การ stop ไม่ได้เปลี่ยนรูปทรงของ infrastructure จึงไม่ควร
    ไปแตะ state · แถม terraform ไม่มี resource สำหรับ "หยุดเครื่องชั่วคราว" อยู่แล้ว
    """
    if action not in ("stop", "start"):
        raise AwsError(f"ไม่รู้จักคำสั่ง '{action}'")
    if not instance_ids:
        return {"action": action, "instances": []}
    creds = resolve_verified(timeout=timeout)
    region = region or creds["region"]
    params = {"Action": "StopInstances" if action == "stop" else "StartInstances",
              "Version": EC2_API_VERSION}
    for i, iid in enumerate(instance_ids, 1):
        params[f"InstanceId.{i}"] = iid
    root = _signed_post("ec2", region, f"ec2.{region}.amazonaws.com", params,
                        creds["access_key"], creds["secret_key"],
                        creds["session_token"], timeout)
    out = []
    for item in root.findall(".//{*}instancesSet/{*}item"):
        out.append({"instance_id": item.findtext("{*}instanceId"),
                    "previous": item.findtext("{*}previousState/{*}name"),
                    "current": item.findtext("{*}currentState/{*}name")})
    return {"action": action, "region": region, "instances": out}


_regions_cache = {"t": 0.0, "list": None}


def enabled_regions(creds=None, timeout=15, ttl=3600):
    """ทุก region ที่บัญชีนี้เปิดใช้ — จาก DescribeRegions

    ใช้ตอนอยากสแกนให้ทั่วจริง ๆ: lab ที่สร้างไว้ region อื่นแล้วแถวใน db หายไป
    จะมองไม่เห็นเลยถ้าสแกนแค่ region ที่รู้จัก
    """
    now = time.time()
    if _regions_cache["list"] and now - _regions_cache["t"] < ttl:
        return list(_regions_cache["list"])
    creds = creds or resolve_verified(timeout=timeout)
    root = _signed_post("ec2", creds["region"], f"ec2.{creds['region']}.amazonaws.com",
                        {"Action": "DescribeRegions", "Version": EC2_API_VERSION},
                        creds["access_key"], creds["secret_key"],
                        creds["session_token"], timeout)
    names = sorted(r.findtext("{*}regionName")
                   for r in root.findall(".//{*}regionInfo/{*}item")
                   if r.findtext("{*}regionName"))
    _regions_cache.update({"t": now, "list": names})
    return list(names)


def _filter_params(filters):
    out = {}
    for i, (name, values) in enumerate(filters, 1):
        out[f"Filter.{i}.Name"] = name
        for j, v in enumerate(values, 1):
            out[f"Filter.{i}.Value.{j}"] = v
    return out


def _tags_of(item):
    return {t.findtext("{*}key"): t.findtext("{*}value")
            for t in item.findall("{*}tagSet/{*}item")}


def _ec2_query(action, region, creds, filters, container, timeout=15):
    """ยิง EC2 Query API แล้วคืน item ทั้งหมด (ตาม nextToken จนครบ)"""
    items = []
    token = None
    while True:
        params = {"Action": action, "Version": EC2_API_VERSION}
        params.update(_filter_params(filters))
        if token:
            params["NextToken"] = token
        root = _signed_post("ec2", region, f"ec2.{region}.amazonaws.com", params,
                            creds["access_key"], creds["secret_key"],
                            creds["session_token"], timeout)
        items += root.findall(f".//{{*}}{container}/{{*}}item")
        token = root.findtext("{*}nextToken")
        if not token:
            return items


def scan_tagged_resources(regions=None, project=PROJECT_TAG, lab_name=None, timeout=15):
    """หา resource ที่ยังมีอยู่จริงบน AWS ตาม tag Project (+ LabName ถ้าระบุ)

    regions=None → region เดียวตาม credential ปัจจุบัน
    คืน dict พร้อม clean=True เมื่อ 'ยืนยันแล้วว่าไม่เหลืออะไร' — ถ้ามี region ไหน
    ถามไม่สำเร็จ clean จะเป็น False เสมอ เพราะยังสรุปไม่ได้ว่าสะอาดจริง
    """
    creds = resolve_verified(timeout=timeout)
    if regions == "all":
        regions = enabled_regions(creds, timeout=timeout)
    regions = [r for r in (regions or [creds["region"]]) if r]

    base_filters = [("tag:Project", [project])]
    if lab_name:
        base_filters.append(("tag:LabName", [lab_name]))

    found, errors, blocked = [], [], []
    for region in regions:
        for action, container, id_tag, kind in _EC2_SCANS:
            filters = list(base_filters)
            if action == "DescribeInstances":
                filters.append(("instance-state-name", LIVE_INSTANCE_STATES))
            try:
                items = _ec2_query(action, region, creds, filters, container, timeout)
            except AwsError as e:
                if e.code in _REGION_BLOCKED_CODES:
                    # นโยบายองค์กร/บัญชีปิด region นี้ไว้ — สร้าง lab ที่นี่ก็ไม่ได้
                    # อยู่แล้ว ถือเป็น "ข้าม" ไม่ใช่ "ตรวจไม่ผ่าน" และไม่ต้องลองต่ออีก 6 รอบ
                    blocked.append({"region": region, "code": e.code})
                    break
                errors.append({"region": region, "kind": kind, "error": str(e)})
                continue
            for item in items:
                tags = _tags_of(item)
                res = {
                    "region": region,
                    "kind": kind,
                    "id": item.findtext(f"{{*}}{id_tag}"),
                    "lab": tags.get("LabName"),
                    "name": tags.get("Name"),
                }
                if action == "DescribeInstances":
                    res["state"] = item.findtext("{*}instanceState/{*}name")
                    res["instance_type"] = item.findtext("{*}instanceType")
                    res["public_ip"] = item.findtext("{*}ipAddress")
                found.append(res)

    by_lab = {}
    for r in found:
        by_lab.setdefault(r["lab"] or "(ไม่มี tag LabName)", []).append(r)
    counts = {}
    for r in found:
        counts[r["kind"]] = counts.get(r["kind"], 0) + 1

    return {
        "checked_at": time.time(),
        "account": creds["account"],
        "arn": creds["arn"],
        "project_tag": project,
        "lab_name": lab_name,
        "regions": regions,
        "resources": found,
        "counts": counts,
        "labs": sorted(by_lab),
        "total": len(found),
        "errors": errors,
        # region ที่ถูกปิดกั้นไม่ทำให้ผลเป็น "ตรวจไม่ได้" — ที่นั่นสร้าง lab ไม่ได้อยู่แล้ว
        "blocked_regions": sorted({b["region"] for b in blocked}),
        "scanned_regions": [r for r in regions
                            if r not in {b["region"] for b in blocked}],
        "clean": not found and not errors,
    }
