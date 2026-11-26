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

**Terraform** (>= 1.5) — มีสามทางเลือก ระบบหาเจอเองทุกทาง:

```bash
brew tap hashicorp/tap && brew install hashicorp/tap/terraform   # ถ้ามี Homebrew
```

ไม่มี Homebrew ก็วาง binary ไว้ในโปรเจกต์ได้เลย (ดาวน์โหลดจาก
<https://developer.hashicorp.com/terraform/install> แล้วตรวจ checksum กับไฟล์
`SHA256SUMS` ที่ HashiCorp เผยแพร่คู่กัน):

```bash
mkdir -p bin && unzip -o ~/Downloads/terraform_*_darwin_arm64.zip -d bin && chmod +x bin/terraform
```

หรือชี้ไปที่ตำแหน่งอื่นด้วย env `TERRAFORM_BIN=/path/to/terraform`

> ลำดับที่ระบบมองหา: `TERRAFORM_BIN` → `PATH` → `bin/terraform` ในโปรเจกต์ →
> `/opt/homebrew/bin` → `/usr/local/bin` — ที่ต้องมองหลายที่เพราะตอนเปิดผ่าน
> `Labctl.app` (Finder) `PATH` จะเป็นค่า default ที่ไม่มีโฟลเดอร์ของ Homebrew

**AWS credentials** — ใส่จากหน้าเว็บได้เลย ไม่ต้องมี aws CLI (ดูหัวข้อ Web UI)
หรือถ้าถนัด CLI: `brew install awscli && aws configure`

Python 3.9+ (มากับ macOS อยู่แล้ว) — CLI ไม่ต้องลง library เพิ่ม

## Web UI

นอกจาก CLI มี web UI ให้ใช้ในเครื่อง: เชื่อมต่อ AWS → เลือก product จาก card →
กรอกข้อมูล tenant → ดู terraform รันสด ๆ → dashboard รวมทุกเครื่อง
พร้อมปุ่มดาวน์โหลด handout / refresh / destroy

**เปิดด้วยคลิกเดียว:** ดับเบิลคลิก **`Labctl.command`** (หรือ **`Labctl.app`** — ลากไป Dock ได้)
ระบบติดตั้ง dependencies ให้เองครั้งแรก เปิด server + browser อัตโนมัติ
หยุดด้วย Ctrl+C หรือปิดหน้าต่าง Terminal · ถ้า server เปิดอยู่แล้ว คลิกซ้ำ = เปิด browser เฉย ๆ
(เครื่องอื่นที่ได้ไฟล์ผ่าน OneDrive ครั้งแรกอาจต้องคลิกขวา → Open เพราะ Gatekeeper)

หรือเปิดจาก terminal เอง:

```bash
pip3 install --user fastapi 'uvicorn[standard]'   # ครั้งแรกครั้งเดียว
python3 labctl.py serve                            # เปิด http://127.0.0.1:8787
```

**เข้าสู่ระบบก่อนใช้งาน** — เปิดครั้งแรกระบบให้ตั้งบัญชีผู้ดูแล (username + รหัสผ่าน ≥8 ตัว)
เก็บเป็น hash ใน `labs.db` ในเครื่องเท่านั้น ทุก API ต้อง login ก่อนใช้
(ติ๊ก "จำไว้ในเครื่องนี้" = 30 วัน ไม่ติ๊ก = 12 ชั่วโมง) — มีปุ่มสลับ Light/Dark และออกจากระบบใน sidebar

**เชื่อมต่อ AWS จากหน้าเว็บได้เลย** — ไม่ต้องมี aws CLI:
มุมขวาบนมีสถานะการเชื่อมต่อ ถ้ายังไม่เชื่อมต่อ กดเข้าไปใส่ Access Key ID / Secret Access Key
(+ Session Token ถ้าใช้ credential ชั่วคราว) ระบบตรวจกับ AWS (STS) ให้ก่อนใช้จริง
"จำไว้ในเครื่องนี้" **ติ๊กไว้ให้เป็นค่าเริ่มต้น** — บันทึกลง `~/.aws/credentials`
โปรไฟล์ `labctl` (ที่เดียวกับ `aws configure`) เชื่อมต่อสำเร็จแล้วกดสร้าง hands-on lab
ต่อได้ทันที

> ถ้าปลดติ๊ก คีย์จะอยู่แค่ในหน่วยความจำของ server ตัวนั้น — **หายทุกครั้งที่ restart**
> และคำสั่ง `labctl.py` ที่รันแยก process จะมองไม่เห็นคีย์นั้นด้วย (จะขึ้น
> `ExpiredToken` จากคีย์เก่าบนดิสก์แทน) หน้าเว็บจะเตือนให้ตอนปลดติ๊ก

- **Auto-expire**: lab ที่สร้างจาก web UI ถูกลบอัตโนมัติหลังครบเวลา session (default 8 ชั่วโมง —
  กำหนดเองได้ตอนสร้าง หรือใส่ 0 เพื่อปิด) กันลืม destroy แล้วโดนคิดเงินข้ามคืน
  ตัวจับเวลาทำงานตอน server เปิดอยู่ — ถ้าปิด server ไปตอนครบเวลา จะลบทันทีที่เปิดครั้งถัดไป
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
| `custom_ami` | ใช้ AMI ของตัวเองแทน `os` (BYOL) — ต้องระบุ `custom_username` ด้วย และใส่ `custom_is_windows: true` ถ้าเป็น Windows |

### Windows 10/11 (Windows Client)

AWS **ไม่มี AMI สาธารณะของ Windows 10/11** — Microsoft อนุญาตให้รัน Windows client OS
บน cloud เฉพาะ image ที่องค์กร **import เองพร้อม license ของตัวเอง** (BYOL ผ่าน
[VM Import/Export](https://docs.aws.amazon.com/vm-import/latest/userguide/what-is-vmimport.html)
และตามเงื่อนไข licensing ของ Microsoft อาจต้องใช้ Dedicated Host)
ถ้ามี AMI ที่ import ไว้แล้ว ใส่ `custom_ami` ใน config หรือติ๊ก "ใช้ AMI ของตัวเอง (BYOL)"
ใน web UI ได้เลย — สำหรับ training ทั่วไป **Windows Server 2025 (desktop experience)**
หน้าตาใกล้ Windows 11 มากและใช้แทนได้โดยไม่ติดปัญหา license

### 2. สร้าง lab

```bash
python3 labctl.py create configs/docker-101.json
python3 labctl.py create configs/docker-101.json --ttl 8   # ลบอัตโนมัติใน 8 ชม.
```

`--ttl` บันทึก lab ลง `labs.db` พร้อมเวลาหมดอายุ ตัวจับเวลาตัวเดียวกับที่ web UI ใช้
จะสั่ง destroy ให้เอง — **ทำงานเฉพาะตอน `labctl.py serve` เปิดค้างอยู่** (ดูหัวข้อ
"ลบอัตโนมัติแม้ไม่ได้เปิดหน้าเว็บ") · ไม่ใส่ `--ttl` = ต้อง destroy เอง (พฤติกรรมเดิม)

> **`expire_date` ถูกคำนวณจาก TTL ให้อัตโนมัติ** — ตั้ง TTL เมื่อไหร่ ค่าที่พิมพ์ไว้ใน
> config จะถูกแทนด้วยวันที่หมดอายุจริง (ทั้งจาก CLI และหน้าเว็บ) ค่านี้ไปเป็น tag
> `Expire` บน resource ทุกตัว จะได้ตรงกับความจริงเสมอ ไม่ต้องมาไล่แก้เองเวลาใช้
> config เดิมซ้ำรอบหน้า · ถ้าไม่ตั้ง TTL ระบบใช้ค่าใน config ตามเดิม

เสร็จแล้วจะได้:

- `handouts/docker-101/index.html` — dashboard สำหรับผู้สอน (IP, credential ทุกเครื่อง)
- `handouts/docker-101/docker-101-user01.zip` — ส่งให้ user01 (มี handout + SSH key)
- `keys/docker-101/*.pem` — private key สำรองฝั่งผู้ดูแล

### 3. แจก handout ให้ผู้เรียน

ส่งไฟล์ zip ของแต่ละคนให้ **เฉพาะเจ้าของ** — ในนั้นมีทุกอย่างที่ผู้เรียนต้องใช้
เปิดอ่านได้จาก browser เลย ไม่ต้องติดตั้งอะไร:

- **วิธีเข้าเครื่องของตัวเอง** แยกตาม client จริง — Linux/SSH มีทั้งฝั่ง macOS/Linux
  (`chmod` + `ssh`) และฝั่ง Windows (`icacls` + ssh ในตัว หรือ PuTTY พร้อมวิธีแปลง
  `.pem` → `.ppk`), วิธีรับส่งไฟล์ด้วย `scp` · Windows/RDP มีทั้ง `mstsc` และ
  Windows App บน macOS
- **ตารางแก้ปัญหาที่เจอบ่อย** — key permission, ต่อไม่ติดตอน VM ยังบูต,
  ชื่อผู้ใช้ผิด, host key เปลี่ยน (ใส่ IP จริงในคำสั่งให้พร้อมคัดลอก)
- **lab guide ฉบับเต็ม** ต่อท้ายในไฟล์เดียวกัน
- ข้อมูลขอบเขตการเข้าถึง (พอร์ตที่เปิด, `allowed_cidr`) เพื่อไม่ต้องเดาว่าทำไมต่อไม่ได้

### 4. ระหว่าง training

```bash
python3 labctl.py status configs/docker-101.json    # ดู IP / instance ทุกเครื่อง
python3 labctl.py refresh configs/docker-101.json   # ดึงข้อมูลล่าสุด + สร้าง handout ใหม่
```

**หมายเหตุ Windows lab:** ปกติ handout มีรหัสผ่าน Administrator ให้ตั้งแต่รอบแรก เพราะ
`get_password_data = true` ใน [terraform/main.tf](terraform/main.tf) ทำให้ terraform รอจนกว่า
AWS จะปล่อยรหัสผ่านก่อนจบ apply (วัดจริง: instance เสร็จใน 40 วินาที พร้อมรหัสผ่าน)

ถ้ารอบแรกยังไม่มีรหัสผ่าน (ถอดรหัสไม่ผ่าน / provider รอจนหมดเวลา):

- **สร้างจากหน้าเว็บ**: ระบบเฝ้ารอให้เอง — เช็คทุก 30 วินาที (นานสุด 20 นาที) แล้ว
  สร้าง handout ใหม่ทันทีที่ถอดรหัสได้ครบ ไม่ต้องกด Refresh เอง
- **สร้างจาก CLI**: รอสักครู่แล้วรัน `refresh` เอง (CLI จบ process ไปแล้วจึงเฝ้าต่อให้ไม่ได้)

### 5. จบ training — ลบทุกอย่าง

```bash
python3 labctl.py destroy configs/docker-101.json
python3 labctl.py scan                              # ยืนยันว่าไม่มีอะไรค้างบน AWS
```

(ระบบจะให้พิมพ์ชื่อ lab ยืนยันก่อนลบ เพื่อกันลบผิด)

## Hands-on Lab Guide

handout ของผู้เรียน = **lab guide + วิธีเข้าเครื่อง** ในไฟล์เดียว โดยวิธีเข้าเครื่อง
ถูกเติมให้ตอน generate เสมอ (ต้องรอ IP/รหัสผ่านจริงจาก AWS ก่อน) ส่วน guide เลือกได้
3 ทาง:

| วิธี | ใช้เมื่อ |
|---|---|
| อัปโหลดไฟล์ `.md` ตอนสร้าง lab | ปกติที่สุด — handout รอบแรกมี guide ครบเลย |
| อัปโหลดไฟล์ `.pdf` | คู่มือที่ทำจาก Word/Canva/Slides อยู่แล้ว — แนบไปทั้งไฟล์ |
| เลือกจาก guide ที่มีอยู่ | ใช้ซ้ำของเดิมใน `lab-guides/` หรือ guide ประจำ product |
| แนบ/เปลี่ยนทีหลัง | แก้ guide ระหว่างสอน แล้วสร้าง handout ใหม่โดยไม่แตะ VM |

**Markdown vs PDF** — `.md` ถูกแปลงเป็น HTML ฝังลงในหน้า handout เลย (ผู้เรียนเลื่อนอ่าน
ต่อจากวิธีเข้าเครื่องได้ในหน้าเดียว) ส่วน `.pdf` แนบเป็นไฟล์ `lab-guide.pdf` ไปในโฟลเดอร์
ของผู้เรียนทุกคน แล้ว handout จะแสดงผลในหน้าให้ผ่าน `<object>` พร้อมลิงก์เปิดแยกสำรองไว้
— เปิดออฟไลน์ได้เหมือนกันเพราะไฟล์อยู่ใน zip เดียวกัน

เพดานขนาด: markdown 512 KB · PDF 20 MB (PDF ถูกคัดลอกเข้า zip ของผู้เรียน *ทุกคน*
ไฟล์ 20 MB กับผู้เรียน 20 คน = 400 MB บนดิสก์) และระบบตรวจหัวไฟล์ว่าเป็น PDF จริง
ไม่ใช่แค่ดูนามสกุล

ไฟล์ที่อัปโหลดถูกเก็บเป็น `lab-guides/<lab>-custom.md` หรือ `-custom.pdf` — แก้ด้วย editor ต่อได้
และเป็นไฟล์เดียวที่ลบได้จากหน้าเว็บ (guide ประจำ product ลบไม่ได้)

จาก CLI:

```bash
python3 labctl.py guides                                          # ดู guide ทั้งหมด
python3 labctl.py guide configs/docker-101.json my-guide.md       # แนบ + สร้าง handout ใหม่
```

`guide` รับได้ทั้ง path ที่อยู่ในรายการ และไฟล์ `.md` ในเครื่อง (จะถูกคัดลอกเข้ามาให้)
อย่าลืมเพิ่ม `"lab_guide"` ลงไฟล์ config ด้วย ไม่งั้น `refresh` ครั้งถัดไปจะกลับไปใช้ของเดิม

## ลบอัตโนมัติแม้ไม่ได้เปิดหน้าเว็บ

ตัวจับเวลาใน `serve` ทำงานเฉพาะตอน server เปิดอยู่ — ปิดเครื่องกลับบ้านแล้ว lab
จะเดินเงินข้ามคืน ใช้ launchd agent กวาดให้แทน:

```bash
python3 labctl.py expire --dry-run                 # ดูว่าจะลบอะไรบ้าง
python3 labctl.py expire                           # กวาดหนึ่งรอบ
python3 labctl.py autoexpire install --interval 10 # ให้ launchd กวาดทุก 10 นาที
python3 labctl.py autoexpire status                # ดูว่าโหลดอยู่ไหม + log อยู่ที่ไหน
python3 labctl.py autoexpire uninstall             # ถอนออก
```

`install` จะสั่งรันทดสอบหนึ่งรอบให้ทันทีแล้วบอกผลตรงนั้น

> **โปรเจกต์ต้องไม่อยู่ในโฟลเดอร์ที่ OneDrive/iCloud ซิงก์** (`~/Library/CloudStorage/...`)
> ไม่งั้น agent จะอ่านไฟล์ไม่ได้ (`Operation not permitted`) — **Full Disk Access
> แก้ไม่ได้** ทดสอบยืนยันแล้วเมื่อ 2026-08-17: process เดียวกันที่ launchd ปลุกขึ้นมา
> อ่าน `~/.lab-generator/labs.db` ได้ปกติ แต่อ่านไฟล์ใน CloudStorage ไม่ได้ แม้ให้สิทธิ์
> FDA กับ binary ที่ถูกต้องแล้ว (macOS กัน File Provider แยกจาก FDA)
>
> ด้วยเหตุนี้โปรเจกต์จึงย้ายมาอยู่ที่ `~/lab-generator` — ซึ่งตรงกับกติกาใน
> POSTMORTEM ที่ห้ามวางของสำคัญไว้ใต้โฟลเดอร์ซิงก์อยู่แล้ว

## คำสั่งที่เพิ่มเข้ามาภายหลัง

```bash
python3 labctl.py extend <config> --hours 2    # ต่อเวลา lab ที่กำลังจะหมดอายุ
python3 labctl.py reset  <config> --user somchai # สร้างเครื่องคนเดียวใหม่ คนอื่นไม่กระทบ
python3 labctl.py adopt  <config> [--dry-run]  # ดึง resource ที่กำพร้ากลับเข้า state
python3 labctl.py report <ชื่อ lab> --out x.csv # สรุปหลังคลาส (ใครใช้กี่ชั่วโมง/ค่าใช้จ่าย)
python3 labctl.py bake   <config>              # อบ AMI จาก lab ที่ติดตั้งของครบแล้ว
python3 labctl.py share  <config> --hours 4    # ให้ผู้เรียนโหลด handout เองผ่าน Wi-Fi
python3 labctl.py create <config> --spot       # ใช้ spot instance (ถูกกว่า 60-70%)
```

**ต่อเวลา** — ระบบเตือนล่วงหน้า 15 นาทีก่อนลบอัตโนมัติ (notification + แถบในหน้า lab
พร้อมปุ่ม +1/+2 ชม.) ป้องกันเครื่องหายกลางคลาสตอนสอนยืดเวลา

**stop/start** — หน้าเว็บมีปุ่มหยุด/เปิดเครื่องทั้ง lab (`POST /api/labs/<lab>/power`)
ประหยัดค่า compute ตอนคลาสข้ามคืน · EBS และข้อมูลในเครื่องยังอยู่ · **IP เปลี่ยนหลังเปิดใหม่
ต้องกด Refresh เพื่อสร้าง handout ใหม่**

**adopt** — ต่อยอดจาก scanner: ถ้า state หายแต่ของยังรันอยู่บน AWS ตัวนี้ `terraform import`
กลับเข้ามาให้ จะได้ destroy ผ่านระบบตามปกติแทนที่จะไล่ลบมือใน console

**share** — เปิดพอร์ตแยก (default 8899) ให้ผู้เรียนโหลด zip ของตัวเองผ่าน Wi-Fi ห้องอบรม
ลิงก์เฉพาะคน token สุ่ม เทียบแบบ constant-time หมดอายุตามเวลาที่ตั้ง และขึ้นให้เห็นว่าใคร
โหลดไปแล้วบ้าง — **server หลักยังผูก 127.0.0.1 เหมือนเดิม** เพราะมีสิทธิ์สั่ง AWS

**spot** — ใส่ `"use_spot": true` ใน config หรือ `--spot` ตอนสร้าง เหมาะกับ lab ที่สร้างใหม่
ได้เร็ว ยอมรับความเสี่ยงถูกยึดคืนแลกกับค่าเครื่องที่ถูกลงมาก

## IAM สิทธิ์น้อยที่สุด

`docs/iam-policy.json` — policy ที่ให้สิทธิ์เฉพาะเท่าที่ระบบต้องใช้ การลบ/แก้ผูกเงื่อนไข
`aws:ResourceTag/Project = hands-on-lab` จึงแตะได้เฉพาะของที่ระบบสร้างเอง ไม่กระทบ
resource อื่นในบัญชี — สร้าง IAM role/user แยกสำหรับงานนี้แทนการใช้ AdministratorAccess

## ตรวจสุขภาพระบบ

```bash
python3 labctl.py doctor
```

ตรวจในคำสั่งเดียว: terraform, openssl, ที่ตั้งโปรเจกต์ (ต้องไม่อยู่ใต้โฟลเดอร์ที่ซิงก์),
โฟลเดอร์ state เขียนได้ไหม, AWS credential ใช้ได้ไหมและคาดว่าเหลืออีกกี่ชั่วโมง,
auto-expire agent ทำงานอยู่ไหม, server เปิดอยู่ไหม, lab ที่ยังไม่ถูกลบ และ resource
ค้างบน AWS · ออกด้วย exit code ไม่ใช่ 0 ถ้ามีข้อที่ต้องแก้ (เอาไปใส่ใน script ได้)

**เรื่องอายุ credential**: AWS ไม่มี API บอกว่า session token เหลืออีกเท่าไร ระบบจึง
ประมาณจากเวลาที่ใส่คีย์ (บันทึกไว้ตอน login) เทียบกับอายุ session มาตรฐาน 8 ชั่วโมง
— ปรับได้ด้วย env `LABCTL_CRED_SESSION_HOURS` ให้ตรงกับที่องค์กรตั้งไว้

เมื่อสร้าง lab ด้วย TTL ที่ยาวกว่าอายุคีย์ที่เหลือ ระบบจะเตือนทันที เพราะเคสนั้น
ตัวลบอัตโนมัติจะล้มตอนถึงเวลา แล้ว VM จะค้างเดินเงิน (เคยเกิดจริง ดู POSTMORTEM)

และถ้า auto-expire agent ลบ lab ไม่สำเร็จ จะเด้ง **notification ของ macOS** ให้ทราบ
ไม่ใช่เขียนลง log เงียบ ๆ ที่ไม่มีใครเปิดดู

## ตรวจ resource ค้างบน AWS

ถ้า destroy ล้มกลางคัน (คีย์หมดอายุ, เน็ตหลุด) หรือ terraform state หาย
resource จะยังเดินเงินอยู่โดยที่ระบบมองไม่เห็น — ใช้คำสั่งนี้ถาม AWS ตรง ๆ
ตาม tag `Project=hands-on-lab` ที่ทุก resource ติดไว้ ไม่พึ่ง state:

```bash
python3 labctl.py scan                  # ทุก lab
python3 labctl.py scan docker-101       # เฉพาะ lab เดียว
```

ในหน้าเว็บอยู่ที่ **การเชื่อมต่อ AWS → ตรวจ resource ค้าง**

- ไม่นับ instance ที่ `terminated` (ค้างใน API อีกราวชั่วโมงหลังลบ) จึงไม่เตือนผิด
- ถ้าถาม AWS ไม่สำเร็จจะ**ไม่**รายงานว่าสะอาด แต่จะบอกว่าถามไม่ผ่านตรงไหน

## รันชุดทดสอบ

```bash
python3 -m unittest discover -s tests -v
```

ครอบ config validation, การ render handout ทั้ง SSH/RDP, markdown, การจัดการไฟล์
lab guide และตัวสแกน resource (stub ตัวลงนาม SigV4 ไว้) — **ไม่ยิง AWS
และไม่แตะ `labs.db` ของจริง** จึงรันได้ตลอดเวลา

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
