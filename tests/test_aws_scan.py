"""ทดสอบตัวสแกน resource ตาม tag โดยไม่ยิง AWS จริง — stub ตัวลงนาม SigV4
แล้วป้อน XML รูปแบบเดียวกับที่ EC2 Query API (2016-11-15) ตอบกลับ
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from xml.etree import ElementTree

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
os.environ.setdefault("LABCTL_DB_PATH", str(Path(tempfile.gettempdir()) / "labctl-test.db"))

from backend import aws  # noqa: E402

NS = 'xmlns="http://ec2.amazonaws.com/doc/2016-11-15/"'


def tags(lab):
    return ("<tagSet>"
            "<item><key>Project</key><value>hands-on-lab</value></item>"
            f"<item><key>LabName</key><value>{lab}</value></item></tagSet>")


RESPONSES = {
    ("DescribeInstances", None): f"""<r {NS}><reservationSet><item><instancesSet>
        <item><instanceId>i-aaa</instanceId><instanceType>t3.small</instanceType>
          <ipAddress>1.2.3.4</ipAddress><instanceState><name>running</name></instanceState>
          {tags('lab-a')}</item></instancesSet></item></reservationSet>
        <nextToken>P2</nextToken></r>""",
    ("DescribeInstances", "P2"): f"""<r {NS}><reservationSet><item><instancesSet>
        <item><instanceId>i-bbb</instanceId><instanceState><name>stopped</name></instanceState>
          {tags('lab-b')}</item></instancesSet></item></reservationSet></r>""",
    ("DescribeVpcs", None): f"<r {NS}><vpcSet><item><vpcId>vpc-1</vpcId>{tags('lab-a')}</item></vpcSet></r>",
    ("DescribeKeyPairs", None): f"<r {NS}><keySet><item><keyName>lab-a-user01</keyName>{tags('lab-a')}</item></keySet></r>",
}
EMPTY = f"<r {NS}></r>"

CREDS = {"method": "test", "access_key": "AK", "secret_key": "sk", "session_token": None,
         "region": "ap-southeast-1", "account": "111122223333", "arn": "arn:aws:iam::1:user/x"}


class ScanTestCase(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self._post, self._verify = aws._signed_post, aws.resolve_verified
        aws.resolve_verified = lambda timeout=12: dict(CREDS)

        def fake_post(service, region, host, params, ak, sk, token=None, timeout=12):
            self.calls.append((service, region, host, dict(params)))
            return ElementTree.fromstring(
                RESPONSES.get((params["Action"], params.get("NextToken")), EMPTY))

        aws._signed_post = fake_post

    def tearDown(self):
        aws._signed_post, aws.resolve_verified = self._post, self._verify


class TestScan(ScanTestCase):
    def test_ยิงถูก_service_และ_host(self):
        aws.scan_tagged_resources(regions=["ap-southeast-1"])
        for service, region, host, _ in self.calls:
            self.assertEqual(service, "ec2")
            self.assertEqual(host, f"ec2.{region}.amazonaws.com")

    def test_กรองด้วย_tag_project(self):
        aws.scan_tagged_resources(regions=["ap-southeast-1"])
        p = self.calls[0][3]
        self.assertEqual(p["Filter.1.Name"], "tag:Project")
        self.assertEqual(p["Filter.1.Value.1"], "hands-on-lab")

    def test_ระบุ_lab_แล้วกรองด้วย_labname_ด้วย(self):
        aws.scan_tagged_resources(regions=["ap-southeast-1"], lab_name="lab-a")
        p = self.calls[0][3]
        self.assertEqual(p["Filter.2.Name"], "tag:LabName")
        self.assertEqual(p["Filter.2.Value.1"], "lab-a")

    def test_ไม่นับ_instance_ที่_terminated(self):
        aws.scan_tagged_resources(regions=["ap-southeast-1"])
        inst = next(p for *_, p in self.calls if p["Action"] == "DescribeInstances")
        states = [v for k, v in inst.items() if k.startswith("Filter.") and ".Value" in k]
        self.assertIn("running", states)
        self.assertNotIn("terminated", states)

    def test_ตาม_nexttoken_จนครบทุกหน้า(self):
        res = aws.scan_tagged_resources(regions=["ap-southeast-1"])
        ids = [r["id"] for r in res["resources"]]
        self.assertIn("i-aaa", ids)
        self.assertIn("i-bbb", ids)

    def test_อ่านรายละเอียด_instance_ได้(self):
        res = aws.scan_tagged_resources(regions=["ap-southeast-1"])
        a = next(r for r in res["resources"] if r["id"] == "i-aaa")
        self.assertEqual(a["state"], "running")
        self.assertEqual(a["public_ip"], "1.2.3.4")
        self.assertEqual(a["lab"], "lab-a")
        self.assertEqual(a["kind"], "EC2 instance")

    def test_สแกนหลาย_region(self):
        res = aws.scan_tagged_resources(regions=["ap-southeast-1", "us-east-1"])
        self.assertEqual(sorted({r["region"] for r in res["resources"]}),
                         ["ap-southeast-1", "us-east-1"])

    def test_clean_เป็นเท็จเมื่อยังมีของ(self):
        res = aws.scan_tagged_resources(regions=["ap-southeast-1"])
        self.assertFalse(res["clean"])
        self.assertGreater(res["total"], 0)


class TestScanEmptyAndError(ScanTestCase):
    def test_ไม่เจออะไรเลยคือ_clean(self):
        aws._signed_post = lambda *a, **k: ElementTree.fromstring(EMPTY)
        res = aws.scan_tagged_resources(regions=["ap-southeast-1"])
        self.assertTrue(res["clean"])
        self.assertEqual(res["total"], 0)

    def test_ถามไม่สำเร็จต้องไม่รายงานว่าสะอาด(self):
        """error ที่ไม่ใช่เรื่องสิทธิ์ (เน็ตหลุด/โดน throttle) = ยังสรุปไม่ได้"""
        def boom(*a, **k):
            raise aws.AwsError("โดน throttle", code="RequestLimitExceeded")
        aws._signed_post = boom
        res = aws.scan_tagged_resources(regions=["ap-southeast-1"])
        self.assertFalse(res["clean"])       # ยังสรุปไม่ได้ = ต้องไม่บอกว่าสะอาด
        self.assertEqual(res["total"], 0)
        self.assertTrue(res["errors"])

    def test_region_ที่บัญชีเข้าไม่ได้นับเป็นข้ามไม่ใช่ล้มเหลว(self):
        """SCP ขององค์กรปิด region ไว้ = สร้าง lab ที่นั่นไม่ได้อยู่แล้ว
        (เจอจริงตอนสแกน --all-regions: 14 จาก 18 region ถูก SCP ปิด)"""
        def denied(*a, **k):
            raise aws.AwsError("explicit deny in a service control policy",
                               code="UnauthorizedOperation")
        aws._signed_post = denied
        res = aws.scan_tagged_resources(regions=["ap-southeast-1", "eu-west-1"])
        self.assertTrue(res["clean"])                  # สรุปได้ว่าไม่มีของค้าง
        self.assertEqual(res["errors"], [])
        self.assertEqual(res["blocked_regions"], ["ap-southeast-1", "eu-west-1"])
        self.assertEqual(res["scanned_regions"], [])

    def test_ปนกันทั้งregionที่ถูกปิดและที่ตรวจได้(self):
        real = aws._signed_post

        def mixed(service, region, host, params, *a, **k):
            if region == "eu-west-1":
                raise aws.AwsError("denied", code="AccessDenied")
            return real(service, region, host, params, *a, **k)

        aws._signed_post = mixed
        res = aws.scan_tagged_resources(regions=["ap-southeast-1", "eu-west-1"])
        self.assertEqual(res["blocked_regions"], ["eu-west-1"])
        self.assertEqual(res["scanned_regions"], ["ap-southeast-1"])
        self.assertTrue(any(r["region"] == "ap-southeast-1" for r in res["resources"]))


if __name__ == "__main__":
    unittest.main()
