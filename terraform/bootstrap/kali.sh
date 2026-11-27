#!/bin/bash
# แปลง Debian 12 เป็น Kali Linux (kali-rolling) ตอนบูตครั้งแรก
#
# ทำไมไม่ใช้ AMI ของ Kali: AMI ทางการอยู่ใน AWS Marketplace ต้องกด Subscribe
# ทุก account ก่อน ซึ่ง account ของลูกค้า/องค์กรมักกดไม่ได้ — ทางนี้ใช้ AMI
# Debian สาธารณะ แล้วสลับ apt ไปที่ repo ของ Kali ตามวิธีที่ Kali รองรับ
#
# ใช้เวลาประมาณ 10-15 นาที (full-upgrade + kali-linux-headless) ติดตามได้ที่
# /var/log/kali-bootstrap.log — เสร็จแล้วมีไฟล์ /var/lib/kali-ready
set -euo pipefail
exec > >(tee -a /var/log/kali-bootstrap.log) 2>&1
echo "[kali] start $(date -Is)"

export DEBIAN_FRONTEND=noninteractive
# force-overwrite: แพ็กเกจ t64 ของ Kali เขียนไฟล์ทับของ Debian ตัวเก่า (เช่น libcurl3-gnutls)
# ที่ยังไม่ถูกถอด — เป็นการเปลี่ยนชื่อแพ็กเกจปกติ dpkg แค่ไม่รู้ว่าตัวใหม่แทนตัวเก่า
APT_OPTS=(-y -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold
          -o Dpkg::Options::=--force-overwrite)

# repo ของ Kali อย่างเดียว — ผสม bookworm กับ kali-rolling แล้ว apt แก้ dependency ไม่ได้
curl -fsSL --retry 5 https://archive.kali.org/archive-key.asc \
  -o /etc/apt/trusted.gpg.d/kali-archive-keyring.asc
rm -f /etc/apt/sources.list.d/debian.sources /etc/apt/sources.list
echo "deb http://http.kali.org/kali kali-rolling main contrib non-free non-free-firmware" \
  > /etc/apt/sources.list.d/kali.list

for attempt in 1 2 3; do
  apt-get update && break
  echo "[kali] apt-get update failed (attempt ${attempt}) — retrying"; sleep 20
done
# GRUB ต้องขึ้นเวอร์ชัน Kali ก่อน kernel — hook ของ kernel ใหม่เรียก update-grub
# ซึ่งในเวอร์ชันของ Debian ยังไม่มี version_find_latest แล้ว dpkg ล้มทั้งชุด
apt-get "${APT_OPTS[@]}" install grub-common grub2-common
apt-get "${APT_OPTS[@]}" full-upgrade || {
  echo "[kali] full-upgrade failed — configuring leftovers and retrying"
  dpkg --configure -a
  apt-get "${APT_OPTS[@]}" -f install
  apt-get "${APT_OPTS[@]}" full-upgrade
}
apt-get "${APT_OPTS[@]}" install kali-linux-headless
apt-get -y autoremove

# บอกผู้ใช้ตอน SSH เข้ามาว่าเครื่องพร้อมแล้ว
cat > /etc/motd <<'EOF'
Kali Linux (kali-rolling) — สร้างโดย labctl จาก Debian 12
เครื่องมือหลักติดตั้งแล้ว (kali-linux-headless) · log: /var/log/kali-bootstrap.log
EOF
touch /var/lib/kali-ready
echo "[kali] ready $(date -Is)"
