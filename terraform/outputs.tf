output "lab_info" {
  description = "ข้อมูลการเชื่อมต่อของแต่ละ user"
  value = {
    for u in var.users : u => {
      instance_id = aws_instance.lab[u].id
      public_ip   = aws_instance.lab[u].public_ip
      public_dns  = aws_instance.lab[u].public_dns
      os          = local.os_display
      username    = local.os.username
      is_windows  = local.os.is_windows
    }
  }
}

output "private_keys" {
  description = "Private key (PEM) ของแต่ละ user"
  sensitive   = true
  value = {
    for u in var.users : u => tls_private_key.user[u].private_key_pem
  }
}

output "windows_passwords_encrypted" {
  description = "รหัสผ่าน Windows แบบเข้ารหัส (ถอดรหัสด้วย private key ของ user นั้น)"
  sensitive   = true
  value = {
    for u in var.users : u => aws_instance.lab[u].password_data
  }
}

output "lab_meta" {
  value = {
    lab_name = var.lab_name
    region   = var.region
    os       = local.os_display
  }
}
