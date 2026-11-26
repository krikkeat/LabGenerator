"""งานระดับ AWS Organization — ถอนสิทธิ์ SSO และปิด AWS account

อันตรายและย้อนไม่ได้ (ปิด account = AWS ลบถาวรหลัง 90 วัน) จึง:
- ต้องระบุโปรไฟล์ของ management account (หรือ delegated admin) เอง — ไม่ใช้ account ที่เลือกอยู่
- ผู้เรียกต้องยืนยันด้วยการพิมพ์ account ID (ตรวจที่ API)
- ทุกคำสั่งผ่าน AWS CLI ด้วย --profile ของ admin โดยตรง ไม่แตะ state อื่นของ labctl
"""
import json
import subprocess

from .accounts import AccountError, _aws_cli

CLI_TIMEOUT = 30


def _cli(profile, *args):
    try:
        res = subprocess.run([_aws_cli(), *args, "--profile", profile, "--output", "json"],
                             capture_output=True, text=True, timeout=CLI_TIMEOUT)
    except subprocess.TimeoutExpired as e:
        raise AccountError(f"AWS ไม่ตอบภายใน {CLI_TIMEOUT} วินาที") from e
    if res.returncode != 0:
        err = (res.stderr or res.stdout).strip()
        if "AccessDenied" in err or "not authorized" in err:
            raise AccountError(f"โปรไฟล์ {profile} ไม่มีสิทธิ์ทำสิ่งนี้ — ต้องใช้ management account "
                               f"ของ organization (หรือ delegated admin)\n{err[-300:]}")
        raise AccountError(err[-400:] or "คำสั่ง AWS ล้มเหลว")
    return json.loads(res.stdout or "{}")


def close_account(admin_profile, account_id):
    """ปิด member account (organizations:CloseAccount) — AWS เก็บไว้ 90 วันก่อนลบถาวร"""
    _cli(admin_profile, "organizations", "close-account", "--account-id", account_id)
    return {"account_id": account_id, "status": "PENDING_CLOSURE"}


def remove_access(admin_profile, account_id, permission_set_name, user_name):
    """ถอน permission set ของผู้ใช้ออกจาก account (sso-admin:DeleteAccountAssignment)"""
    inst = _cli(admin_profile, "sso-admin", "list-instances").get("Instances", [])
    if not inst:
        raise AccountError("ไม่พบ IAM Identity Center instance ที่โปรไฟล์นี้เห็น")
    instance_arn, store_id = inst[0]["InstanceArn"], inst[0]["IdentityStoreId"]
    ps_arn = None
    for arn in _cli(admin_profile, "sso-admin", "list-permission-sets-provisioned-to-account",
                    "--instance-arn", instance_arn, "--account-id", account_id).get("PermissionSets", []):
        ps = _cli(admin_profile, "sso-admin", "describe-permission-set",
                  "--instance-arn", instance_arn, "--permission-set-arn", arn)["PermissionSet"]
        if ps.get("Name") == permission_set_name:
            ps_arn = arn
            break
    if not ps_arn:
        raise AccountError(f"ไม่พบ permission set '{permission_set_name}' ใน account {account_id}")
    users = _cli(admin_profile, "identitystore", "list-users", "--identity-store-id", store_id,
                 "--filters", f"AttributePath=UserName,AttributeValue={user_name}").get("Users", [])
    if not users:
        raise AccountError(f"ไม่พบผู้ใช้ '{user_name}' ใน Identity Center")
    res = _cli(admin_profile, "sso-admin", "delete-account-assignment",
               "--instance-arn", instance_arn, "--target-id", account_id,
               "--target-type", "AWS_ACCOUNT", "--permission-set-arn", ps_arn,
               "--principal-type", "USER", "--principal-id", users[0]["UserId"])
    return {"account_id": account_id, "permission_set": permission_set_name, "user": user_name,
            "status": res.get("AccountAssignmentDeletionStatus", {}).get("Status")}
