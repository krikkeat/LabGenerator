# ---------------------------------------------------------------------------
# OS catalog — เพิ่ม OS ใหม่ได้โดยเพิ่ม entry ตรงนี้
# ใช้ SSM public parameter เพื่อให้ได้ AMI ล่าสุดของแต่ละ OS เสมอ
# ---------------------------------------------------------------------------
locals {
  os_catalog = {
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
  }

  os = local.os_catalog[var.os]

  # พอร์ตพื้นฐานตาม OS + พอร์ตเพิ่มเติมจาก config
  base_ports = local.os.is_windows ? [3389] : [22]
  open_ports = distinct(concat(local.base_ports, var.extra_open_ports))
}

data "aws_ssm_parameter" "ami" {
  name = local.os.ssm_parameter
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

  ami                    = data.aws_ssm_parameter.ami.value
  instance_type          = var.instance_type
  subnet_id              = aws_subnet.public.id
  vpc_security_group_ids = [aws_security_group.lab.id]
  key_name               = aws_key_pair.user[each.key].key_name
  get_password_data      = local.os.is_windows

  user_data = var.user_data != "" ? var.user_data : null

  root_block_device {
    volume_size = var.root_volume_gb
    volume_type = "gp3"
  }

  tags = {
    Name    = "${var.lab_name}-${each.key}"
    LabUser = each.key
    Expire  = var.expire_date
  }
}
