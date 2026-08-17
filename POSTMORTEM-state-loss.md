# state หาย → resource หลุดการติดตาม (2026-08-05)

บันทึกเหตุการณ์และกติกาที่ตามมา — **เคลียร์เรียบร้อยแล้ว ไม่มีอะไรค้างบน AWS**

## เกิดอะไรขึ้น

`training-trendai-swp-linux` และ `training-trendai-swp-windows` apply สำเร็จ
(36 resources ต่อ lab) แต่ไฟล์ state ใน `terraform/terraform.tfstate.d/<lab>/`
หายไปทั้งคู่ terraform จึงลบให้ไม่ได้ — EC2 รวม 20 เครื่องรันค้างอยู่ราว 1 วัน
โดยที่ dashboard แสดงเป็นแค่การ์ด ERROR และหัวจอขึ้น `0 VM · ~$0.00/ชม.`
เพราะตัวเลขนั้นอ่านจาก state ที่หายไปแล้ว

สาเหตุที่เป็นไปได้มากที่สุดคือโปรเจกต์อยู่ใต้ path ที่ OneDrive ซิงก์
(`Group Containers/UBF8T346G9.OneDriveStandaloneSuite/...`) — พบหลักฐานประกอบว่า
OneDrive เขียนทับ metadata ไฟล์ในโฟลเดอร์นี้จริง (`backend/app.py` มีเนื้อไฟล์
เวอร์ชันใหม่แต่ mtime ย้อนไปเป็นของวันก่อน)

การกู้ทำได้เพราะ log ของ job ที่สร้าง lab (`logs/jobs/*.log`) ยังเก็บ resource id
ครบทุกตัว — **อย่าลบ `logs/jobs/`** มันเป็นหลักฐานชั้นสุดท้ายเมื่อ state หาย

## กติกาที่ตามมา

**ทั้งโปรเจกต์ย้ายออกจาก OneDrive แล้ว** (2026-08-17) — อยู่ที่ `~/lab-generator`
นอกจากเหตุผลเรื่อง state หายข้างล่างนี้ ยังมีอีกข้อที่เจอทีหลัง: process ที่ launchd
ปลุกขึ้นมาอ่านไฟล์ใน `~/Library/CloudStorage/...` ไม่ได้เลย (`Operation not permitted`)
แม้ให้ Full Disk Access แล้ว ทำให้ตัวลบ lab อัตโนมัติทำงานไม่ได้ · สำเนาเดิมถูกเปลี่ยนชื่อ
เป็น `lab-generator.moved-2026-08-17` ไว้ใน OneDrive

**state อยู่นอก OneDrive** — `~/.lab-generator/tfstate/`
ทำโดยใส่ `backend "local" {}` ใน `terraform/versions.tf` แล้วส่ง path ตอน init ผ่าน
`-backend-config=workspace_dir=...` (ดู `labctl.tf_init_args()`) ไฟล์ `.tf` ยังอยู่
ในโปรเจกต์ตามเดิม ย้ายเฉพาะ state · เปลี่ยน path ได้ด้วย env `LABCTL_TF_STATE_DIR`

> ถ้าลบ `terraform/.terraform` ทิ้งเมื่อไหร่ ต้อง init ใหม่ผ่าน `tf_init_args()` เสมอ
> (แอปทำให้อัตโนมัติทั้ง CLI และ job runner) ไม่งั้น terraform จะกลับไปเขียน state
> ในโฟลเดอร์ OneDrive อีก

**`labs.db` อยู่นอก OneDrive** — `~/.lab-generator/labs.db`
เปลี่ยน path ได้ด้วย env `LABCTL_DB_PATH` · SQLite บนโฟลเดอร์ซิงก์เสี่ยงไฟล์เสีย
ด้วยเหตุผลเดียวกัน

**lab ที่ destroy ล้มยังถูกตามเก็บต่อ** (เพิ่ม 2026-08-17 หลังเจอซ้ำอีกครั้ง) — เดิม
destroy ที่ล้มจะตั้งสถานะ lab เป็น `error` แต่ตัวจับเวลาเก็บเฉพาะ `active` ผลคือ lab ที่
ลบไม่สำเร็จ (สาเหตุยอดฮิต: คีย์ AWS หมดอายุ) หลุดจากการเก็บอัตโนมัติทันที ทั้งที่เป็นตัวที่
resource ยังเดินเงินอยู่จริง — แก้ที่ `labctl.due_labs()` ให้รวมสถานะ `error` ด้วย และมี
`should_attempt_destroy()` หน่วง 15 นาทีก่อนลองซ้ำ จะได้ไม่ยิง job รัว ๆ ตอนคีย์ยังไม่ถูกเปลี่ยน

**guard ตอน destroy** — `_assert_state()` ใน `backend/jobs.py` ไม่ยอมให้ job destroy
จบแบบ "สำเร็จ" ถ้าไม่มี state จะขึ้น error พร้อมบอกวิธีลบมือแทน ไม่ปล่อยให้ lab
หายจากจอเงียบ ๆ พร้อม resource ที่ยังเดินเงิน

**ทางออกจาก lab ที่ error** — เดิม lab สถานะ error กดการ์ดแล้วเด้งไปหน้า log เสมอ
และหน้า log ของ destroy ก็ซ่อนปุ่มกลับไป dashboard ทำให้กด Destroy ซ้ำไม่ได้เลย
แก้แล้วใน `frontend/app.js` (`open()` และปุ่มท้ายหน้า job)

**เครื่องมือตรวจ resource ค้าง** (เพิ่มภายหลัง 2026-08-17) — `python3 labctl.py scan`
หรือปุ่มในหน้า "การเชื่อมต่อ AWS" ถาม EC2 API ตาม tag ตรง ๆ ไม่พึ่ง state จึงตอบได้
แม้ state หายไปแล้ว ใช้ยืนยันหลัง destroy ทุกครั้งที่ไม่แน่ใจ

> ตรวจย้อนหลังเมื่อ 2026-08-17 ด้วยเครื่องมือนี้: `clean: true` — ยืนยันว่าเหตุการณ์นี้
> ถูกเคลียร์หมดจริง ไฟล์ state เก่าที่ค้างอยู่ใน `terraform/terraform.tfstate.d/`
> (ตำแหน่งก่อนย้ายออกนอก OneDrive) ถูกลบทิ้งแล้วหลังยืนยัน — resource id ของสอง lab นั้น
> ยังหาได้จาก `logs/jobs/91d544a98bc0.log` และ `e0e9f4d8c175.log` ถ้าต้องสอบย้อนหลัง
>
> ไฟล์ชุดนั้นเคยหลอกให้เข้าใจผิดว่ายังมีของค้างอยู่ เพราะ OneDrive ไปแตะ mtime ของ
> *โฟลเดอร์* ให้ดูเหมือนเพิ่งถูกเขียนเมื่อ 14 ส.ค. ทั้งที่ตัวไฟล์ข้างในยังเป็นของ 4 ส.ค.
> วันเกิดเหตุ — เวลาสอบสวนให้ดู mtime ของไฟล์ ไม่ใช่ของโฟลเดอร์

## ถ้าเกิดซ้ำ — วิธีตามลบ

resource ทุกตัวติด tag `LabName=<ชื่อ lab>` และ `Project=hands-on-lab` จาก
`default_tags` ใน `terraform/versions.tf` เข้า AWS console → Resource Groups &
Tag Editor → กรองด้วย tag แล้วลบตามลำดับ:

```
EC2 (รอ terminated ก่อน) → security group → subnet → IGW → route table → VPC → key pair
```

จากนั้นล้างของในเครื่องให้ตรงกับที่ flow destroy ของแอปทำ: `keys/<lab>/`,
`handouts/<lab>/`, `handouts/<lab>-all.zip`, `terraform/<lab>.tfvars.json`
แล้วค่อยตั้งสถานะ lab ใน `labs.db` เป็น `destroyed`
