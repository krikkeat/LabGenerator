variable "lab_name" {
  description = "ชื่อ lab (ใช้เป็น prefix ของทุก resource)"
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9-]{1,32}$", var.lab_name))
    error_message = "lab_name ต้องเป็น a-z, 0-9, - เท่านั้น (ไม่เกิน 32 ตัวอักษร)"
  }
}

variable "region" {
  description = "AWS region ที่จะสร้าง lab"
  type        = string
  default     = "ap-southeast-1"
}

variable "os" {
  description = "OS ของ VM ใน lab นี้ (ดูรายชื่อที่รองรับใน locals.os_catalog)"
  type        = string
  default     = "amazon-linux-2023"
}

variable "instance_type" {
  description = "ขนาดของ VM ต่อ user"
  type        = string
  default     = "t3.small"
}

variable "users" {
  description = "รายชื่อ user/ลูกค้า (หนึ่งคนได้หนึ่ง VM)"
  type        = list(string)

  validation {
    condition     = alltrue([for u in var.users : can(regex("^[a-z0-9-]{1,30}$", u))])
    error_message = "ชื่อ user ต้องเป็น a-z, 0-9, - เท่านั้น"
  }
}

variable "allowed_cidr" {
  description = "CIDR ที่อนุญาตให้เข้าถึง VM (แนะนำให้จำกัดเป็น IP ของสถานที่อบรม)"
  type        = string
  default     = "0.0.0.0/0"
}

variable "extra_open_ports" {
  description = "พอร์ตเพิ่มเติมที่ต้องเปิดสำหรับ lab (เช่น 80, 8080)"
  type        = list(number)
  default     = []
}

variable "root_volume_gb" {
  description = "ขนาด disk ของ VM (GB)"
  type        = number
  default     = 30
}

variable "custom_ami" {
  description = "AMI ID ของตัวเอง (BYOL เช่น Windows 10/11 ที่ import มา) — ถ้าระบุจะไม่ใช้ os_catalog"
  type        = string
  default     = ""
}

variable "custom_username" {
  description = "username สำหรับเข้าเครื่องเมื่อใช้ custom_ami"
  type        = string
  default     = ""
}

variable "custom_is_windows" {
  description = "custom_ami เป็น Windows หรือไม่ (มีผลกับ RDP/SSH และการถอดรหัสผ่าน)"
  type        = bool
  default     = false
}

variable "user_data" {
  description = "สคริปต์ bootstrap ที่จะรันตอน VM บูตครั้งแรก (cloud-init / PowerShell)"
  type        = string
  default     = ""
}

variable "expire_date" {
  description = "วันหมดอายุของ lab (แสดงเป็น tag เพื่อช่วยตามลบ resource)"
  type        = string
  default     = ""
}
