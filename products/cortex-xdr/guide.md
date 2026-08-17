# Lab Guide: Palo Alto Cortex XDR — Endpoint Protection

> เวลาโดยประมาณ: 2 ชั่วโมง | ระดับ: เริ่มต้น-กลาง
> เครื่อง lab ของคุณติดตั้ง **Cortex XDR Agent** ให้เรียบร้อยแล้วตอนบูตเครื่อง
> เนื้อหาอ้างอิงจาก [เอกสาร official ของ Cortex XDR](https://docs-cortex.paloaltonetworks.com/) — แต่ละ lab มีลิงก์ให้อ่านเจาะลึกต่อ

## เตรียมความพร้อม

- เชื่อมต่อเข้าเครื่อง lab ผ่าน Remote Desktop ตามข้อมูลด้านบนของเอกสารนี้
- เปิด browser ไปที่ console: **{{CONSOLE_URL}}**
- login ด้วย account ที่ผู้สอนแจกให้

> agent อาจใช้เวลา 5-10 นาทีหลังบูตกว่าจะขึ้นทะเบียนใน console — ระหว่างรอให้ทำ Lab 1 ไปก่อน

## Lab 1: รู้จัก Console (15 นาที)

สำรวจเมนูหลักของ Cortex XDR:

- **Incidents** — เหตุการณ์ที่ระบบรวม alert ที่เกี่ยวข้องกันไว้ให้
- **Endpoints → All Endpoints** — รายชื่อ endpoint ทั้งหมด
- **Query Builder / XQL Search** — ค้นหาข้อมูล activity ย้อนหลัง
- **Endpoints → Policy Management** — โปรไฟล์การป้องกันของแต่ละกลุ่มเครื่อง

**ภารกิจ:** tenant นี้มี endpoint สถานะ Connected กี่เครื่อง?

## Lab 2: ตรวจสอบเครื่องของคุณใน Console (15 นาที)

- ที่เครื่อง lab เปิด PowerShell:

```
Get-Service cyserver, cyverak -ErrorAction SilentlyContinue
& "C:\Program Files\Palo Alto Networks\Traps\cytool.exe" info
```

- ที่ console: **Endpoints → All Endpoints** ค้นหาชื่อเครื่องของคุณ (`hostname`)
- เปิดดูรายละเอียด: agent version, สถานะ, policy ที่ได้รับ, last seen

**ภารกิจ:** เครื่องของคุณได้รับ Prevention Profile ชื่ออะไร?

## Lab 3: ทดสอบการตรวจจับด้วยไฟล์ EICAR (20 นาที)

EICAR คือไฟล์ทดสอบมาตรฐานสากลของอุตสาหกรรม antivirus — **ไม่ใช่ไวรัสจริง** แต่ผลิตภัณฑ์ security จะตรวจจับเสมือนเป็น malware เพื่อใช้ทดสอบระบบ

- ที่เครื่อง lab เปิด browser ไปที่ [eicar.org](https://www.eicar.org/download-anti-malware-testfile/) แล้วดาวน์โหลดไฟล์ `eicar.com`
- สังเกต notification ของ agent — ไฟล์ควรถูก block/quarantine ทันที
- ที่ console: ไปดู alert ที่เกิดขึ้น (Incidents หรือ Alerts Table)

**ภารกิจ:** จด alert name, severity, action (Prevented/Detected) และ module ที่ตรวจจับ

## Lab 4: วิเคราะห์ Incident และ Causality Chain (25 นาที)

- เปิด **Incidents** แล้วหา incident ของเครื่องคุณ
- ศึกษาหน้า incident: alert ที่ถูกรวมไว้, key artifacts, affected hosts
- คลิกที่ alert แล้วเปิด **Causality View** — กราฟที่ไล่ลำดับ process ทั้งสาย (Causality Chain) จนถึงต้นตอที่เรียกว่า **Causality Group Owner (CGO)**

**ภารกิจ:** จาก Causality View — CGO ของเหตุการณ์นี้คือ process อะไร?

📖 อ่านเพิ่มเติม: [Causality View (official docs)](https://docs-cortex.paloaltonetworks.com/r/Cortex-XDR/Cortex-XDR-4.x-Documentation/Causality-view)

## Lab 5: XQL Search — ค้นหาข้อมูลย้อนหลัง (20 นาที)

**XQL (XDR Query Language)** คือภาษา query หลักของ Cortex — โครงสร้างเป็น pipeline `dataset → filter → fields` ต่อกันด้วย `|`

- ไปที่ **Query Builder** หรือ XQL Search แล้วลอง query:

```
dataset = xdr_data
| filter agent_hostname = "<ชื่อเครื่องของคุณ>" and event_type = ENUM.FILE
| filter action_file_name contains "eicar"
| fields _time, agent_hostname, action_file_name, action_file_path, actor_process_image_name
```

**ภารกิจ:** ไฟล์ eicar ถูกเขียนที่ path ไหน และ process อะไรเป็นคนเขียน?

📖 อ่านเพิ่มเติม: [Get started with XQL queries](https://docs-cortex.paloaltonetworks.com/r/Cortex-XDR/Cortex-XDR-3.x-Documentation/Get-started-with-XQL-queries) · [Review XQL query results](https://docs-cortex.paloaltonetworks.com/r/Cortex-XDR/Cortex-XDR-3.x-Documentation/Review-XQL-query-results)

## Lab 6: Response — ตอบสนองต่อเหตุการณ์ (20 นาที)

- ที่ **Endpoints** เลือกเครื่องของคุณ → Actions → **Isolate**
- ที่เครื่อง lab: ลองเปิดเว็บ — จะออก internet ไม่ได้ (RDP ยังใช้ได้เพราะ agent อนุญาต traffic ที่จำเป็นต่อการจัดการเครื่อง)
- ลองใช้ **Live Terminal** จาก console เพื่อรันคำสั่งบนเครื่องจากระยะไกล เช่นดู process หรือไฟล์ใน quarantine — ทุกคำสั่งที่รันผ่าน Live Terminal ถูกบันทึกใน Management Audit Log
- สั่ง **Cancel Isolation** แล้วตรวจสอบว่าเครื่องออก internet ได้ตามเดิม

**ภารกิจ:** ระหว่าง isolate — สถานะเครื่องใน console แสดงเป็นอะไร? และหา log ของคำสั่งที่คุณรันผ่าน Live Terminal ให้เจอ

📖 อ่านเพิ่มเติม: [Isolate an endpoint](https://docs-cortex.paloaltonetworks.com/r/Cortex-XDR/Cortex-XDR-3.x-Documentation/Isolate-an-endpoint) · [Initiate a Live Terminal session](https://docs-cortex.paloaltonetworks.com/r/Cortex-XDR/Cortex-XDR-3.x-Documentation/Initiate-a-Live-Terminal-session)

## สรุป

- Agent ขึ้นทะเบียน tenant อัตโนมัติผ่าน installer ที่ผูกกับ distribution ของทีม
- EICAR ใช้ยืนยันว่า prevention pipeline ทำงานครบวงจร
- Incident + Causality View ช่วยไล่หา Causality Group Owner (root cause) ได้รวดเร็ว
- XQL คือภาษา query หลักสำหรับ threat hunting ใน Cortex
- Isolate / Live Terminal คือเครื่องมือ containment และ remote response — มี audit log ครบ

## เอกสารอ้างอิง (Official)

- [Cortex XDR Documentation Portal](https://docs-cortex.paloaltonetworks.com/)
- [Causality View](https://docs-cortex.paloaltonetworks.com/r/Cortex-XDR/Cortex-XDR-4.x-Documentation/Causality-view)
- [Get started with XQL queries](https://docs-cortex.paloaltonetworks.com/r/Cortex-XDR/Cortex-XDR-3.x-Documentation/Get-started-with-XQL-queries)
- [Isolate an endpoint](https://docs-cortex.paloaltonetworks.com/r/Cortex-XDR/Cortex-XDR-3.x-Documentation/Isolate-an-endpoint)
- [Initiate a Live Terminal session](https://docs-cortex.paloaltonetworks.com/r/Cortex-XDR/Cortex-XDR-3.x-Documentation/Initiate-a-Live-Terminal-session)

> เครื่อง lab จะถูกลบหลังจบ training — ผู้สอนจะลบ endpoint ที่ค้างใน console ให้ภายหลัง
