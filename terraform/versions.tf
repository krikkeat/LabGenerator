terraform {
  required_version = ">= 1.5.0"

  # workspace_dir ถูกส่งตอน init ผ่าน -backend-config (ดู labctl.tf_init_args)
  # เพื่อเก็บ state ไว้นอกโฟลเดอร์ที่ OneDrive ซิงก์ — backend block ใส่ค่าแบบ
  # interpolate ไม่ได้ จึงต้องปล่อยว่างไว้แล้วกำหนดตอน init
  backend "local" {}

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    tls = {
      source  = "hashicorp/tls"
      version = "~> 4.0"
    }
  }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project   = "hands-on-lab"
      LabName   = var.lab_name
      ManagedBy = "terraform"
    }
  }
}
