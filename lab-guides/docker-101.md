# Lab Guide: Docker 101 — Container พื้นฐาน

> เวลาโดยประมาณ: 2 ชั่วโมง | ระดับ: เริ่มต้น-กลาง
> เครื่อง lab ของคุณติดตั้ง Docker มาให้เรียบร้อยแล้ว

## Lab 1: ตรวจสอบว่า Docker พร้อมใช้งาน (10 นาที)

SSH เข้าเครื่อง lab แล้วรัน:

```
docker version
docker run hello-world
```

ถ้าเห็นข้อความ **Hello from Docker!** แปลว่าพร้อมแล้ว

> ถ้าเจอ `permission denied` ให้ logout แล้ว SSH เข้าใหม่หนึ่งครั้ง (เพื่อให้ group docker มีผล)

## Lab 2: รัน Container แรกของคุณ (20 นาที)

- รัน nginx แบบ background และ map port

```
docker run -d --name web -p 80:80 nginx
docker ps
```

- ทดสอบจากในเครื่อง

```
curl http://localhost
```

- ทดสอบจากเครื่องของคุณเอง: เปิด browser ไปที่ `http://<PUBLIC_IP_ของเครื่อง_lab>` (ดู IP จากหัวเอกสารนี้)

- ดู log และเข้าไปใน container

```
docker logs web
docker exec -it web bash
ls /usr/share/nginx/html
exit
```

## Lab 3: สร้าง Image ของตัวเอง (30 นาที)

- สร้างโปรเจกต์เล็ก ๆ

```
mkdir ~/myapp && cd ~/myapp
```

- สร้างไฟล์ `index.html`

```
echo "<h1>Hello from $(whoami) container!</h1>" > index.html
```

- สร้างไฟล์ `Dockerfile` ด้วย nano โดยมีเนื้อหา:

```
FROM nginx:alpine
COPY index.html /usr/share/nginx/html/index.html
```

- build และรัน

```
docker build -t myweb:1.0 .
docker rm -f web
docker run -d --name myweb -p 80:80 myweb:1.0
curl http://localhost
```

**คำถามท้าย lab:** ลอง `docker images` — image `myweb:1.0` มีขนาดเท่าไหร่ ทำไมเล็กกว่า `nginx` ปกติ?

## Lab 4: Volume และข้อมูลถาวร (20 นาที)

```
docker run -d --name db -e POSTGRES_PASSWORD=labpass -v pgdata:/var/lib/postgresql/data postgres:16-alpine
docker exec -it db psql -U postgres -c "CREATE TABLE demo(id int); INSERT INTO demo VALUES (1),(2);"
docker rm -f db
docker run -d --name db2 -e POSTGRES_PASSWORD=labpass -v pgdata:/var/lib/postgresql/data postgres:16-alpine
sleep 5
docker exec -it db2 psql -U postgres -c "SELECT * FROM demo;"
```

สังเกตว่าข้อมูลยังอยู่แม้ container ตัวแรกถูกลบไปแล้ว — นี่คือพลังของ **volume**

## Lab 5: Docker Compose (30 นาที)

- สร้างไฟล์ `~/myapp/compose.yaml`:

```
services:
  web:
    image: nginx:alpine
    ports:
      - "8080:80"
  redis:
    image: redis:alpine
```

- รันทั้ง stack

```
cd ~/myapp
docker compose up -d
docker compose ps
curl http://localhost:8080
```

- ปิดทั้ง stack

```
docker compose down
```

## สรุป

- `docker run / ps / logs / exec` — วงจรชีวิตพื้นฐานของ container
- `Dockerfile` + `docker build` — สร้าง image ของตัวเอง
- volume ทำให้ข้อมูลอยู่รอดข้าม container
- `docker compose` จัดการหลาย service พร้อมกัน

> เครื่อง lab จะถูกลบหลังจบ training — push image ที่อยากเก็บขึ้น registry ของคุณก่อน
