# Bootstrap: Cortex XDR agent (รันอัตโนมัติตอน VM บูตครั้งแรก)
$ErrorActionPreference = "Stop"
Start-Transcript -Path C:\LabSetup\bootstrap.log -Append

New-Item -ItemType Directory -Force -Path C:\LabSetup | Out-Null

# ปิด IE Enhanced Security เพื่อให้ผู้เรียนเปิด console จากในเครื่อง lab ได้
Set-ItemProperty -Path "HKLM:\SOFTWARE\Microsoft\Active Setup\Installed Components\{A509B1A7-37EF-4b3f-8CFC-4F3A74704073}" -Name IsInstalled -Value 0 -ErrorAction SilentlyContinue
Set-ItemProperty -Path "HKLM:\SOFTWARE\Microsoft\Active Setup\Installed Components\{A509B1A8-37EF-4b3f-8CFC-4F3A74704073}" -Name IsInstalled -Value 0 -ErrorAction SilentlyContinue

# ดาวน์โหลดและติดตั้ง agent
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$url  = "{{AGENT_INSTALLER_URL}}"
$file = Join-Path "C:\LabSetup" ([uri]$url).Segments[-1]
Invoke-WebRequest -Uri $url -OutFile $file -UseBasicParsing

$extraArgs = "{{AGENT_INSTALL_ARGS}}"
switch -Wildcard ($file) {
    "*.msi" {
        $msiArgs = @("/i", $file, "/qn", "/norestart")
        if ($extraArgs) { $msiArgs += $extraArgs.Split(" ") }
        Start-Process msiexec.exe -ArgumentList $msiArgs -Wait
    }
    "*.zip" {
        Expand-Archive -Path $file -DestinationPath C:\LabSetup\agent -Force
        $exe = Get-ChildItem C:\LabSetup\agent -Filter *.exe -Recurse | Select-Object -First 1
        if ($extraArgs) { Start-Process $exe.FullName -ArgumentList $extraArgs -Wait }
        else            { Start-Process $exe.FullName -Wait }
    }
    "*.exe" {
        if ($extraArgs) { Start-Process $file -ArgumentList $extraArgs -Wait }
        else            { Start-Process $file -Wait }
    }
}

Stop-Transcript
