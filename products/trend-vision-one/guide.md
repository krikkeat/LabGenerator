# Lab Guide: Trend Vision One (TrendAI) — Endpoint Security

> เวลาโดยประมาณ: 2 ชั่วโมง | ระดับ: เริ่มต้น-กลาง
> เครื่อง lab ของคุณติดตั้ง **Vision One Endpoint Agent** ให้เรียบร้อยแล้วตอนบูตเครื่อง

## เตรียมความพร้อม

- เชื่อมต่อเข้าเครื่อง lab ผ่าน Remote Desktop ตามข้อมูลด้านบนของเอกสารนี้
- เปิด browser ไปที่ console: **{{CONSOLE_URL}}**
- login ด้วย account ที่ผู้สอนแจกให้

> ถ้าเพิ่งเปิดเครื่อง agent อาจใช้เวลา 5-10 นาทีกว่าจะขึ้นทะเบียนใน console — ระหว่างรอให้ทำ Lab 1 ไปก่อน

## Lab 1: รู้จัก Console (15 นาที)

สำรวจเมนูหลักของ Vision One:

- **Workbench** — ศูนย์รวม alert ที่ระบบ correlate ให้แล้ว
- **Endpoint Inventory** — รายชื่อ endpoint ทั้งหมดในองค์กร
- **XDR Threat Investigation → Search** — ค้นหา activity data ย้อนหลัง
- **Endpoint Security → Policy** — นโยบายป้องกันของแต่ละกลุ่มเครื่อง

**ภารกิจ:** หาให้เจอว่า tenant นี้มี endpoint ทั้งหมดกี่เครื่อง

## Lab 2: ตรวจสอบเครื่องของคุณใน Console (15 นาที)

- ที่เครื่อง lab: เปิด PowerShell แล้วดูว่า agent service ทำงานอยู่

```
Get-Service | Where-Object { $_.DisplayName -like "*Trend*" }
```

- ที่ console: ไปที่ **Endpoint Inventory** แล้วค้นหาชื่อเครื่องของคุณ (ดูชื่อเครื่องด้วยคำสั่ง `hostname`)
- เปิดดูรายละเอียด endpoint: OS, IP, agent version, สถานะการเชื่อมต่อ

**ภารกิจ:** เครื่องของคุณอยู่ใน policy กลุ่มไหน และ feature อะไรเปิดอยู่บ้าง?

## Lab 3: ทดสอบการตรวจจับด้วยไฟล์ EICAR (20 นาที)

EICAR คือไฟล์ทดสอบมาตรฐานสากลของอุตสาหกรรม antivirus — **ไม่ใช่ไวรัสจริง** แต่ทุกผลิตภัณฑ์จะตรวจจับเสมือนเป็น malware เพื่อใช้ทดสอบระบบ

- ที่เครื่อง lab เปิด browser ไปที่ `https://www.eicar.org/download-anti-malware-testfile/` แล้วดาวน์โหลดไฟล์ `eicar.com`
- สังเกตว่าเกิดอะไรขึ้น — agent ควร block/quarantine ไฟล์ทันที
- ที่ console: ไปดู detection ที่เกิดขึ้น (Endpoint Security → Detections หรือ Workbench)

**ภารกิจ:** จด detection name, ชื่อไฟล์, action ที่ระบบทำ (quarantine/delete) และเวลาที่ตรวจพบ

## Lab 4: Workbench — วิเคราะห์ Alert (25 นาที)

- เปิด **Workbench** แล้วหา alert ที่เกี่ยวกับเครื่องของคุณ
- ศึกษาหน้า alert: severity, impact scope, highlighted objects, execution profile (กราฟความเชื่อมโยงของ process)
- ลองคลิกที่ object ต่าง ๆ ในกราฟเพื่อดูรายละเอียด

**ภารกิจ:** จาก execution profile — process อะไรเป็นตัวเขียนไฟล์ EICAR ลงเครื่อง?

## Lab 5: Search — ค้นหาข้อมูลย้อนหลัง (20 นาที)

- ไปที่ **XDR Threat Investigation → Search**
- เลือก data source เป็น Endpoint Activity Data แล้วลองค้นหา เช่น

```
endpointHostName: <ชื่อเครื่องของคุณ>
```

- ลองกรองเฉพาะ event ประเภท file creation ในช่วง 1 ชั่วโมงที่ผ่านมา

**ภารกิจ:** หา event การสร้างไฟล์ eicar.com ให้เจอ พร้อมระบุ full path

## Lab 6: Response — ตอบสนองต่อเหตุการณ์ (20 นาที)

- จากหน้า endpoint หรือจาก Workbench alert เลือกเครื่องของคุณแล้วสั่ง **Isolate Endpoint**
- ที่เครื่อง lab: ลองเปิดเว็บอะไรก็ได้ — จะออก internet ไม่ได้แล้ว (แต่ RDP ของ lab ยังใช้ได้เพราะ agent อนุญาต traffic ที่จำเป็น)
- กลับไปที่ console แล้วสั่ง **Restore Connection**
- ตรวจสอบว่าเครื่องกลับมาออก internet ได้ตามปกติ

**ภารกิจ:** ระหว่าง isolate — สถานะของ endpoint ใน console เปลี่ยนเป็นอะไร?

## สรุป

- Agent ขึ้นทะเบียนกับ console อัตโนมัติและรายงานสถานะตลอดเวลา
- EICAR ใช้ทดสอบ pipeline การตรวจจับได้อย่างปลอดภัย
- Workbench รวม alert พร้อมกราฟ execution ให้วิเคราะห์ root cause ได้เร็ว
- Search ใช้ค้น activity data ย้อนหลังเพื่อ investigation
- Isolate/Restore คือเครื่องมือ containment มาตรฐานของ incident response

> เครื่อง lab จะถูกลบหลังจบ training — ผู้สอนจะลบ endpoint ที่ค้างใน console ให้ภายหลัง
