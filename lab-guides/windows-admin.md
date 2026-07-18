# Lab Guide: Windows Server Administration

> เวลาโดยประมาณ: 90 นาที | ระดับ: เริ่มต้น-กลาง

## เตรียมความพร้อม

เชื่อมต่อเข้าเครื่อง lab ผ่าน **Remote Desktop** ตามข้อมูล IP / username / password ที่หัวเอกสารนี้

## Lab 1: สำรวจระบบด้วย PowerShell (20 นาที)

เปิด **PowerShell (Run as Administrator)** แล้วลองคำสั่งต่อไปนี้:

```
Get-ComputerInfo | Select-Object OsName, OsVersion, CsProcessors
Get-Volume
Get-Process | Sort-Object CPU -Descending | Select-Object -First 10
Get-Service | Where-Object Status -eq "Running" | Measure-Object
```

**คำถามท้าย lab:** เครื่องนี้มี service ที่กำลังรันอยู่กี่ตัว?

## Lab 2: จัดการ User และ Group (20 นาที)

- สร้าง local user ใหม่

```
New-LocalUser -Name "trainee" -Password (Read-Host -AsSecureString "ตั้งรหัสผ่าน") -FullName "Trainee User"
Add-LocalGroupMember -Group "Remote Desktop Users" -Member "trainee"
Get-LocalUser
```

- ทดสอบ: sign out แล้ว login ด้วย user `trainee` ผ่าน RDP จากนั้นกลับมา login เป็น Administrator ตามเดิม

## Lab 3: ติดตั้ง Role — Web Server (IIS) (25 นาที)

```
Install-WindowsFeature -Name Web-Server -IncludeManagementTools
```

- ตรวจสอบว่า IIS ทำงาน

```
Get-Service W3SVC
Invoke-WebRequest http://localhost -UseBasicParsing | Select-Object StatusCode
```

- แก้หน้าเว็บ default

```
Set-Content C:\inetpub\wwwroot\index.html "<h1>Hello from $env:COMPUTERNAME</h1>"
```

- เปิด browser ในเครื่อง lab ไปที่ `http://localhost` เพื่อดูผล

## Lab 4: Windows Firewall (15 นาที)

```
Get-NetFirewallProfile | Select-Object Name, Enabled
New-NetFirewallRule -DisplayName "Lab HTTP" -Direction Inbound -Protocol TCP -LocalPort 80 -Action Allow
Get-NetFirewallRule -DisplayName "Lab HTTP"
```

## Lab 5: Event Log และ Task Scheduler (10 นาที)

```
Get-EventLog -LogName System -Newest 10
Get-WinEvent -LogName Application -MaxEvents 5 | Format-List TimeCreated, ProviderName, Message
```

- สร้าง scheduled task ง่าย ๆ

```
$action  = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-Command Get-Date >> C:\lab-heartbeat.txt"
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName "LabHeartbeat" -Action $action -Trigger $trigger
```

รอ 2-3 นาที แล้วดูไฟล์ `C:\lab-heartbeat.txt`

## สรุป

- PowerShell คือเครื่องมือหลักในการดูแล Windows Server
- จัดการ user/group ด้วย `*-LocalUser` / `*-LocalGroupMember`
- ติดตั้ง role ด้วย `Install-WindowsFeature`
- จัดการ firewall และ scheduled task ผ่าน PowerShell ได้ทั้งหมด

> เครื่อง lab นี้จะถูกลบหลังจบ training
