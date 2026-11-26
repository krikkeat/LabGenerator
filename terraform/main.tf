# ---------------------------------------------------------------------------
# OS catalog — เพิ่ม OS ใหม่ได้โดยเพิ่ม entry ตรงนี้
# ใช้ SSM public parameter เพื่อให้ได้ AMI ล่าสุดของแต่ละ OS เสมอ
# ---------------------------------------------------------------------------
locals {
  os_catalog_raw = {
    "amazon-linux-2023" = {
      ssm_parameter = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
      username      = "ec2-user"
      is_windows    = false
    }
    "amazon-linux-2" = {
      ssm_parameter = "/aws/service/ami-amazon-linux-latest/amzn2-ami-hvm-x86_64-gp2"
      username      = "ec2-user"
      is_windows    = false
    }
    "ubuntu-20.04" = {
      ssm_parameter = "/aws/service/canonical/ubuntu/server/20.04/stable/current/amd64/hvm/ebs-gp2/ami-id"
      username      = "ubuntu"
      is_windows    = false
    }
    "ubuntu-22.04" = {
      ssm_parameter = "/aws/service/canonical/ubuntu/server/22.04/stable/current/amd64/hvm/ebs-gp2/ami-id"
      username      = "ubuntu"
      is_windows    = false
    }
    "ubuntu-24.04" = {
      ssm_parameter = "/aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id"
      username      = "ubuntu"
      is_windows    = false
    }
    "debian-11" = {
      ssm_parameter = "/aws/service/debian/release/11/latest/amd64"
      username      = "admin"
      is_windows    = false
    }
    "debian-12" = {
      ssm_parameter = "/aws/service/debian/release/12/latest/amd64"
      username      = "admin"
      is_windows    = false
    }
    "suse-15" = {
      ssm_parameter = "/aws/service/suse/sles/15-sp6/x86_64/latest"
      username      = "ec2-user"
      is_windows    = false
    }
    "windows-2025" = {
      ssm_parameter = "/aws/service/ami-windows-latest/Windows_Server-2025-English-Full-Base"
      username      = "Administrator"
      is_windows    = true
    }
    "windows-2022" = {
      ssm_parameter = "/aws/service/ami-windows-latest/Windows_Server-2022-English-Full-Base"
      username      = "Administrator"
      is_windows    = true
    }
    "windows-2019" = {
      ssm_parameter = "/aws/service/ami-windows-latest/Windows_Server-2019-English-Full-Base"
      username      = "Administrator"
      is_windows    = true
    }
    "windows-2016" = {
      ssm_parameter = "/aws/service/ami-windows-latest/Windows_Server-2016-English-Full-Base"
      username      = "Administrator"
      is_windows    = true
    }
    # Kali ไม่มี SSM public parameter — หา AMI ทางการจาก AWS Marketplace ตามชื่อแทน
    # (804fcc46 คือรหัส product ของ Kali Linux เอง ไม่ใช่ AMI ที่บุคคลที่สามทำมาขาย)
    # ต้องกด Subscribe ฟรีใน Marketplace ครั้งแรกต่อ account ไม่งั้น RunInstances ถูกปฏิเสธ
    "kali-linux" = {
      ami_owner  = "aws-marketplace"
      ami_name   = "debian-kali-last-snapshot-amd64-*-804fcc46-*"
      username   = "kali"
      is_windows = false
    }
  }
  # ทุก entry ต้องมี key ครบชุดเดียวกัน — ternary ข้างล่างเลือกระหว่าง entry กับ custom object
  os_catalog = { for k, v in local.os_catalog_raw : k => merge({ ssm_parameter = "", ami_owner = "", ami_name = "" }, v) }

  # custom_ami (BYOL — เช่น Windows 10/11 ที่ import เอง) ชนะ os_catalog
  use_custom = var.custom_ami != ""
  os = local.use_custom ? {
    ssm_parameter = ""
    ami_owner     = ""
    ami_name      = ""
    username      = var.custom_username
    is_windows    = var.custom_is_windows
  } : local.os_catalog[var.os]

  os_display = local.use_custom ? "custom (${var.custom_ami})" : var.os

  # OS รายเครื่อง — override เฉพาะคนที่อยู่ใน var.user_os (เช่นเครื่องที่เพิ่มทีหลัง)
  user_os_name    = { for u in var.users : u => lookup(var.user_os, u, "") }
  user_os         = { for u, o in local.user_os_name : u => o != "" ? local.os_catalog[o] : local.os }
  user_os_display = { for u, o in local.user_os_name : u => o != "" ? o : local.os_display }
  # user_data ของ lab เขียนมาสำหรับ OS หลัก — ส่งให้เครื่อง OS อื่นจะรันพังหรือผิดภาษา
  user_gets_user_data = { for u, o in local.user_os_name : u => o == "" || (!local.use_custom && o == var.os) }

  # พอร์ตพื้นฐานตาม OS ของทุกเครื่องในคลาส + พอร์ตเพิ่มเติมจาก config
  base_ports = distinct([for u, os in local.user_os : os.is_windows ? 3389 : 22])
  open_ports = distinct(concat(local.base_ports, var.extra_open_ports))
}

# AMI ของทุก OS ที่ lab นี้ใช้ (OS หลัก + OS รายเครื่อง) — หนึ่ง lookup ต่อหนึ่ง OS ไม่ใช่ต่อเครื่อง
locals {
  used_os = toset(compact(concat([local.use_custom ? "" : var.os], values(local.user_os_name))))
}

data "aws_ssm_parameter" "os" {
  for_each = { for o in local.used_os : o => local.os_catalog[o] if local.os_catalog[o].ssm_parameter != "" }
  name     = each.value.ssm_parameter
}

data "aws_ami" "os" {
  for_each    = { for o in local.used_os : o => local.os_catalog[o] if local.os_catalog[o].ami_name != "" }
  most_recent = true
  owners      = [each.value.ami_owner]
  filter {
    name   = "name"
    values = [each.value.ami_name]
  }
  filter {
    name   = "architecture"
    values = ["x86_64"]
  }
}

locals {
  os_ami = merge({ for k, v in data.aws_ssm_parameter.os : k => v.value },
  { for k, v in data.aws_ami.os : k => v.id })
}

data "aws_availability_zones" "available" {
  state = "available"
}

# ---------------------------------------------------------------------------
# Network — VPC แยกของ lab นี้โดยเฉพาะ (ลบทิ้งได้หมดจดตอนจบ training)
# ---------------------------------------------------------------------------
resource "aws_vpc" "lab" {
  cidr_block           = "10.42.0.0/16"
  enable_dns_hostnames = true

  tags = { Name = "${var.lab_name}-vpc" }
}

resource "aws_subnet" "public" {
  vpc_id                  = aws_vpc.lab.id
  cidr_block              = "10.42.1.0/24"
  availability_zone       = data.aws_availability_zones.available.names[0]
  map_public_ip_on_launch = true

  tags = { Name = "${var.lab_name}-public" }
}

resource "aws_internet_gateway" "lab" {
  vpc_id = aws_vpc.lab.id

  tags = { Name = "${var.lab_name}-igw" }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.lab.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.lab.id
  }

  tags = { Name = "${var.lab_name}-rt" }
}

resource "aws_route_table_association" "public" {
  subnet_id      = aws_subnet.public.id
  route_table_id = aws_route_table.public.id
}

resource "aws_security_group" "lab" {
  name        = "${var.lab_name}-sg"
  description = "Access for hands-on lab ${var.lab_name}"
  vpc_id      = aws_vpc.lab.id

  dynamic "ingress" {
    for_each = toset([for p in local.open_ports : tostring(p)])
    content {
      description = "lab port ${ingress.value}"
      from_port   = tonumber(ingress.value)
      to_port     = tonumber(ingress.value)
      protocol    = "tcp"
      cidr_blocks = [var.allowed_cidr]
    }
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${var.lab_name}-sg" }
}

# ---------------------------------------------------------------------------
# Per-user resources — key pair + VM หนึ่งเครื่องต่อหนึ่ง user
# ---------------------------------------------------------------------------
resource "tls_private_key" "user" {
  for_each = toset(var.users)

  algorithm = "RSA"
  rsa_bits  = 4096
}

resource "aws_key_pair" "user" {
  for_each = toset(var.users)

  key_name   = "${var.lab_name}-${each.key}"
  public_key = tls_private_key.user[each.key].public_key_openssh
}

resource "aws_instance" "lab" {
  for_each = toset(var.users)

  ami = (local.user_os_name[each.key] != "" ? local.os_ami[local.user_os_name[each.key]]
  : local.use_custom ? var.custom_ami : local.os_ami[var.os])
  instance_type          = var.instance_type
  subnet_id              = aws_subnet.public.id
  vpc_security_group_ids = [aws_security_group.lab.id]
  key_name               = aws_key_pair.user[each.key].key_name
  get_password_data      = local.user_os[each.key].is_windows

  user_data = var.user_data != "" && local.user_gets_user_data[each.key] ? var.user_data : null

  # spot: ไม่กำหนด max_price = ยอมจ่ายไม่เกินราคา on-demand
  # ใช้ one-time เพราะถ้าโดนยึดคืนแล้วต้องให้ terraform สร้างใหม่ ไม่ใช่ AWS หามาแทนเงียบ ๆ
  dynamic "instance_market_options" {
    for_each = var.use_spot ? [1] : []
    content {
      market_type = "spot"
      spot_options {
        spot_instance_type             = "one-time"
        instance_interruption_behavior = "terminate"
      }
    }
  }

  root_block_device {
    volume_size = var.root_volume_gb
    volume_type = "gp3"
  }

  # AMI มาจาก SSM "latest" ซึ่งเปลี่ยนเมื่อ AWS ออก image ใหม่ — ถ้าไม่ ignore
  # apply ครั้งถัดไป (เช่นเพิ่มเครื่อง) จะลบเครื่องเดิมสร้างใหม่ ข้อมูลผู้เรียนหาย
  # reset ใช้ -replace จึงยังได้ AMI ล่าสุดตามที่ตั้งใจ
  lifecycle {
    ignore_changes = [ami]
  }

  tags = {
    Name    = "${var.lab_name}-${each.key}"
    LabUser = each.key
    Expire  = var.expire_date
  }
}
