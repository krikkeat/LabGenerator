# Hands-on Lab Generator

ระบบสร้าง lab environment บน AWS สำหรับ training อัตโนมัติ:

- สร้าง **VM แยกหนึ่งเครื่องต่อหนึ่ง user** — แต่ละคนได้เครื่องของตัวเอง ไม่ปนกัน
- **เลือก OS ได้**: Amazon Linux 2023/2, Ubuntu 22.04/24.04, Windows Server 2022/2019
- **เลือก Product ได้**: มี product catalog สำเร็จรูป (Trend Vision One / TrendAI, Palo Alto Cortex XDR)
  ที่ติดตั้ง agent ให้อัตโนมัติพร้อม lab guide เฉพาะ product — เพิ่ม product ใหม่เองได้
- แต่ละ user ได้ **key pair ของตัวเอง** (Linux = SSH key, Windows = รหัสผ่านที่ถอดจาก key)
- generate **handout (HTML) ต่อ user** อัตโนมัติ: ข้อมูลเชื่อมต่อ + lab guide พร้อม zip ส่งให้ผู้เรียนได้เลย
- มี **Instructor Dashboard** รวมข้อมูลทุกเครื่องในหน้าเดียว
- จบ training แล้ว **ลบทิ้งทั้งหมดในคำสั่งเดียว** (VM, network, key ทุกอย่าง)

## สถาปัตยกรรม

```
config (JSON)  ──▶  labctl.py  ──▶  Terraform  ──▶  AWS
                        │                            ├─ VPC + Subnet + SG (แยกต่อ lab)
                        │                            ├─ VM ต่อ user (EC2)
                        │                            └─ Key pair ต่อ user
                        ▼
                handouts/<lab>/
                  ├─ index.html            ◀─ instructor dashboard
                  ├─ <lab>-<user>.zip      ◀─ ส่งให้ผู้เรียนแต่ละคน
                  └─ <user>/index.html     ◀─ ข้อมูลเชื่อมต่อ + lab guide
```

แต่ละ lab ใช้ Terraform **workspace แยกกัน** — เปิดหลาย lab พร้อมกันได้โดย state ไม่ตีกัน

## สิ่งที่ต้องติดตั้งก่อน (เครื่องผู้ดูแล)

```bash
brew install terraform awscli        # macOS
aws configure                        # ใส่ AWS credentials (ต้องมีสิทธิ์ EC2 + VPC)
```

Python 3.9+ (มากับ macOS อยู่แล้ว) — CLI ไม่ต้องลง library เพิ่ม

## Web UI

นอกจาก CLI มี web UI ให้ใช้ในเครื่อง: เชื่อมต่อ AWS → เลือก product จาก card →
กรอกข้อมูล tenant → ดู terraform รันสด ๆ → dashboard รวมทุกเครื่อง
พร้อมปุ่มดาวน์โหลด handout / refresh / destroy

```bash
pip3 install --user fastapi 'uvicorn[standard]'   # ครั้งแรกครั้งเดียว
python3 labctl.py serve                            # เปิด http://127.0.0.1:8787
```

**เชื่อมต่อ AWS จากหน้าเว็บได้เลย** — ไม่ต้องมี aws CLI:
มุมขวาบนมีสถานะการเชื่อมต่อ ถ้ายังไม่เชื่อมต่อ กดเข้าไปใส่ Access Key ID / Secret Access Key
(+ Session Token ถ้าใช้ credential ชั่วคราว) ระบบตรวจกับ AWS (STS) ให้ก่อนใช้จริง
เลือก "จำไว้ในเครื่องนี้" จะบันทึกลง `~/.aws/credentials` โปรไฟล์ `labctl`
(ที่เดียวกับ `aws configure`) — เชื่อมต่อสำเร็จแล้วกดสร้าง hands-on lab ต่อได้ทันที

- ผูกกับ `127.0.0.1` เท่านั้น — ไม่เปิดให้เครื่องอื่นในวง Wi-Fi เห็น (ใน handout มี key/รหัสผ่าน)
- lab ที่สร้างจาก CLI ก็โผล่ในหน้า web และกลับกัน — ใช้ Terraform workspace ชุดเดียวกัน
- ระหว่าง provisioning ปิดหน้าเว็บได้ งานรันต่อเบื้องหลังจนจบ
- ไฟล์ `labs.db` (สถานะ lab/job) กับ `logs/` (log ของแต่ละงาน) สร้างอัตโนมัติ — อยู่ใน `.gitignore` แล้ว
- frontend ไม่ต้องมี Node/npm — เป็น static file ใน `frontend/` เสิร์ฟตรงจาก server

## วิธีใช้งาน

### 1. สร้างไฟล์ config ของ lab

ดูตัวอย่างใน `configs/` แล้วปรับตามต้องการ:

```json
{
  "lab_name": "docker-101",
  "title": "Docker 101 — Container พื้นฐาน",
  "region": "ap-southeast-1",
  "os": "ubuntu-24.04",
  "instance_type": "t3.medium",
  "users": ["user01", "user02", "user03"],
  "allowed_cidr": "0.0.0.0/0",
  "extra_open_ports": [80, 8080],
  "root_volume_gb": 30,
  "expire_date": "2026-07-31",
  "lab_guide": "lab-guides/docker-101.md",
  "bootstrap_script": "bootstrap/docker.sh"
}
```

| Field | ความหมาย |
|---|---|
| `lab_name` | ชื่อ lab (a-z, 0-9, `-`) ใช้เป็น prefix ของ resource ทั้งหมด |
| `os` | `amazon-linux-2023`, `amazon-linux-2`, `ubuntu-20.04`, `ubuntu-22.04`, `ubuntu-24.04`, `debian-11`, `debian-12`, `suse-15`, `windows-2025`, `windows-2022`, `windows-2019`, `windows-2016` |
| `users` | รายชื่อผู้เรียน — หนึ่งชื่อ = หนึ่ง VM |
| `allowed_cidr` | จำกัด IP ที่เข้าถึง lab ได้ (แนะนำใส่ IP ของสถานที่อบรม เช่น `203.0.113.0/24`) |
| `extra_open_ports` | พอร์ตเพิ่มเติมนอกจาก SSH/RDP เช่น `[80, 8080]` |
| `lab_guide` | ไฟล์ markdown ที่จะฝังลงใน handout ของผู้เรียน |
| `bootstrap_script` | สคริปต์ติดตั้งโปรแกรมอัตโนมัติตอน VM บูต (optional) |
| `expire_date` | ติด tag `Expire` บนทุก resource เพื่อช่วยตามลบ |

### 2. สร้าง lab

```bash
python3 labctl.py create configs/docker-101.json
```

เสร็จแล้วจะได้:

- `handouts/docker-101/index.html` — dashboard สำหรับผู้สอน (IP, credential ทุกเครื่อง)
- `handouts/docker-101/docker-101-user01.zip` — ส่งให้ user01 (มี handout + SSH key)
- `keys/docker-101/*.pem` — private key สำรองฝั่งผู้ดูแล

### 3. แจก handout ให้ผู้เรียน

ส่งไฟล์ zip ของแต่ละคนให้ **เฉพาะเจ้าของ** — ในนั้นมีทุกอย่างที่ผู้เรียนต้องใช้:
ข้อมูลเชื่อมต่อ, วิธี SSH/RDP และ lab guide ฉบับเต็ม เปิดอ่านได้จาก browser เลย

### 4. ระหว่าง training

```bash
python3 labctl.py status configs/docker-101.json    # ดู IP / instance ทุกเครื่อง
python3 labctl.py refresh configs/docker-101.json   # ดึงข้อมูลล่าสุด + สร้าง handout ใหม่
```

**หมายเหตุ Windows lab:** รหัสผ่าน Administrator จะพร้อมหลัง VM บูต ~4-5 นาที
ถ้าตอน `create` ยังไม่ได้รหัสผ่าน ให้รอสักครู่แล้วรัน `refresh` — handout จะถูกอัปเดตให้เอง

### 5. จบ training — ลบทุกอย่าง

```bash
python3 labctl.py destroy configs/docker-101.json
```

(ระบบจะให้พิมพ์ชื่อ lab ยืนยันก่อนลบ เพื่อกันลบผิด)

## Product-based Lab (Trend Vision One / Cortex XDR)

นอกจาก lab แบบกำหนด OS เอง ระบบมี **product catalog** ที่กำหนดทุกอย่างให้แล้ว
(OS, สเปคเครื่อง, สคริปต์ติดตั้ง agent, lab guide) — ดูรายชื่อ product:

```bash
python3 labctl.py products
```

### วิธีใช้

config ระบุแค่ `product` + ข้อมูล tenant ของคุณใน `product_params`:

```json
{
  "lab_name": "cortex-training",
  "product": "cortex-xdr",
  "users": ["user01", "user02", "user03"],
  "product_params": {
    "CONSOLE_URL": "https://<tenant>.xdr.sg.paloaltonetworks.com",
    "AGENT_INSTALLER_URL": "https://<ลิงก์ installer จาก tenant ของคุณ>.msi"
  }
}
```

ตอน VM บูต ระบบจะดาวน์โหลด agent installer จากลิงก์ที่ระบุและติดตั้งอัตโนมัติ (รองรับ .msi / .zip / .exe)
เครื่องของผู้เรียนจะขึ้นทะเบียนใน console ของ tenant เองภายใน ~5-10 นาที
handout ของผู้เรียนจะมี lab guide ของ product นั้นพร้อม URL console ฝังให้เรียบร้อย

**วิธีเอาลิงก์ installer:**

- **Trend Vision One**: console → Endpoint Inventory → Agent Installer → คัดลอกลิงก์ดาวน์โหลดสำหรับ Windows Server
- **Cortex XDR**: console → Settings → Agent Installations → สร้าง installer → คัดลอกลิงก์ .msi

ค่าใน config เขียนทับค่า default ของ product ได้ทุกตัว (เช่น อยากใช้ `instance_type` ใหญ่ขึ้นก็ระบุเพิ่มได้)

### การเพิ่ม product ใหม่

สร้างโฟลเดอร์ `products/<ชื่อ>/` ที่มี 3 ไฟล์:

1. `product.json` — ชื่อ product, OS/สเปค default, รายการ `params` ที่ต้องการจากผู้สอน
2. `bootstrap.ps1.tpl` (หรือ `.sh.tpl` สำหรับ Linux) — สคริปต์ติดตั้ง ใช้ `{{PARAM_NAME}}` เป็น placeholder
3. `guide.md` — lab guide (ใช้ `{{PARAM_NAME}}` ได้เช่นกัน เช่น `{{CONSOLE_URL}}`)

## การเพิ่ม lab ใหม่

1. เขียน lab guide เป็น markdown ไว้ใน `lab-guides/` (รองรับ heading, code block, list, bold, link)
2. (ถ้าต้องการ) เขียน bootstrap script ไว้ใน `bootstrap/` เพื่อติดตั้งโปรแกรมล่วงหน้า
3. สร้าง config JSON ใน `configs/`
4. `python3 labctl.py create configs/<ไฟล์>.json`

## การเพิ่ม OS ใหม่

เพิ่ม entry ใน `terraform/main.tf` ที่ `locals.os_catalog` (ระบุ SSM parameter ของ AMI, username เริ่มต้น และ flag `is_windows`) และเพิ่มชื่อเดียวกันใน `SUPPORTED_OS` ของ `labctl.py`

## ประมาณการค่าใช้จ่าย (ap-southeast-1, on-demand)

| Instance | ราคา/ชม./เครื่อง | 10 คน × 8 ชม. |
|---|---|---|
| t3.small | ~$0.026 | ~$2.1 |
| t3.medium | ~$0.053 | ~$4.2 |
| t3.large | ~$0.105 | ~$8.4 |

(+ ค่า EBS gp3 เล็กน้อย) — **อย่าลืม `destroy` หลังจบ training ทุกครั้ง**

## ความปลอดภัย

- ตั้ง `allowed_cidr` ให้แคบที่สุดเท่าที่ทำได้ — อย่าเปิด `0.0.0.0/0` ถ้าไม่จำเป็น
- โฟลเดอร์ `keys/` และ `handouts/` มี credential — อย่า commit ลง git (มี `.gitignore` ให้แล้ว)
- ทุก resource ติด tag `Project=hands-on-lab`, `LabName=<ชื่อ>` และ `Expire=<วันที่>` ตามหาง่ายใน AWS Console
