# Lab Guide: Linux พื้นฐานสำหรับผู้เริ่มต้น

> เวลาโดยประมาณ: 90 นาที | ระดับ: เริ่มต้น

## เตรียมความพร้อม

เชื่อมต่อเข้าเครื่อง lab ของคุณด้วยคำสั่ง SSH ตามข้อมูลด้านบนของเอกสารนี้ เมื่อเข้าได้แล้วจะเห็น prompt ประมาณนี้:

```
ubuntu@ip-10-42-1-xx:~$
```

## Lab 1: สำรวจระบบ (15 นาที)

ลองรันคำสั่งต่อไปนี้ทีละคำสั่ง และสังเกตผลลัพธ์

```
whoami          # ดูว่าเราคือ user อะไร
hostname        # ชื่อเครื่อง
uname -a        # ข้อมูล kernel และสถาปัตยกรรม
lsb_release -a  # เวอร์ชันของ Ubuntu
uptime          # เครื่องเปิดมานานแค่ไหน โหลดเท่าไหร่
df -h           # พื้นที่ disk
free -h         # หน่วยความจำ
```

**คำถามท้าย lab:** เครื่องของคุณมี RAM เท่าไหร่ และเหลือพื้นที่ disk กี่ GB?

## Lab 2: จัดการไฟล์และโฟลเดอร์ (20 นาที)

- สร้างโครงสร้างโฟลเดอร์สำหรับงานของเรา

```
mkdir -p ~/workshop/docs ~/workshop/scripts
cd ~/workshop
ls -la
```

- สร้างและแก้ไขไฟล์ด้วย `nano`

```
nano docs/note.txt
```

พิมพ์ข้อความอะไรก็ได้ 2-3 บรรทัด แล้วกด `Ctrl+O` เพื่อบันทึก และ `Ctrl+X` เพื่อออก

- คัดลอก ย้าย และลบไฟล์

```
cp docs/note.txt docs/note-backup.txt
mv docs/note-backup.txt /tmp/
rm /tmp/note-backup.txt
```

## Lab 3: สิทธิ์ของไฟล์ (Permission) (20 นาที)

```
touch scripts/hello.sh
ls -l scripts/hello.sh
```

สังเกตค่า `-rw-rw-r--` ทางซ้าย — นี่คือสิทธิ์ของไฟล์

- เขียนสคริปต์ง่าย ๆ ลงไป

```
echo '#!/bin/bash' > scripts/hello.sh
echo 'echo "สวัสดี $(whoami) วันนี้คือ $(date)"' >> scripts/hello.sh
```

- ลองรันดู แล้วแก้ให้รันได้

```
./scripts/hello.sh        # จะ error: Permission denied
chmod +x scripts/hello.sh
./scripts/hello.sh        # คราวนี้รันได้
```

**คำถามท้าย lab:** `chmod 755` กับ `chmod +x` ต่างกันอย่างไร?

## Lab 4: จัดการ Package และ Service (20 นาที)

- ติดตั้ง web server nginx

```
sudo apt update
sudo apt install -y nginx
```

- ตรวจสอบสถานะ service

```
systemctl status nginx
curl http://localhost
```

- ลอง stop / start service

```
sudo systemctl stop nginx
curl http://localhost      # จะต่อไม่ได้
sudo systemctl start nginx
curl http://localhost      # กลับมาได้ตามเดิม
```

## Lab 5: Process และ Log (15 นาที)

```
ps aux | head -20          # ดู process ที่รันอยู่
top                        # ดูแบบ realtime (กด q เพื่อออก)
sudo tail -f /var/log/nginx/access.log   # ดู log แบบ realtime (Ctrl+C เพื่อออก)
```

เปิดอีก terminal หนึ่ง SSH เข้ามาแล้วรัน `curl http://localhost` หลาย ๆ ครั้ง — สังเกตว่า log ขึ้นทันที

## สรุป

ใน lab นี้คุณได้เรียนรู้:

- คำสั่งพื้นฐานสำหรับสำรวจระบบ Linux
- การจัดการไฟล์ โฟลเดอร์ และสิทธิ์การเข้าถึง
- การติดตั้งโปรแกรมด้วย `apt` และจัดการ service ด้วย `systemctl`
- การดู process และอ่าน log

> อย่าลืม: เครื่อง lab นี้จะถูกลบหลังจบ training — ถ้ามีไฟล์ที่อยากเก็บ ให้ดาวน์โหลดออกมาก่อน
