# PowerPOS Local Printer & Cash Drawer Bridge — Windows Service Installer
param(
    [string]$PythonExe = "python.exe",
    [string]$InstallDir = "C:\ProgramData\PowerPOS\LocalBridge",
    [string]$ServiceName = "PowerPOSLocalBridge"
)

$ErrorActionPreference = "Stop"
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null

$ScriptPath = Join-Path $PSScriptRoot "powerpos_windows_service.py"
$BinPath = "`"$PythonExe`" `"$ScriptPath`""

if (Get-Service -Name $ServiceName -ErrorAction SilentlyContinue) {
    Stop-Service -Name $ServiceName -Force -ErrorAction SilentlyContinue
    sc.exe delete $ServiceName | Out-Null
}

sc.exe create $ServiceName binPath= $BinPath start= auto DisplayName= "PowerPOS SUNMI Local Hardware Bridge"
sc.exe failure $ServiceName reset= 86400 actions= restart/5000/restart/10000/restart/30000
Write-Host "Registered Windows Service: $ServiceName"

