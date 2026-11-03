# Compile Alfred for Windows: dist\Alfred.exe (and the installer, if Inno Setup is installed).
#   Needs Python 3.10+ from python.org (tick "Add python.exe to PATH").
#   Run in PowerShell:   .\build.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

python -m venv .build-venv
& .\.build-venv\Scripts\python.exe -m pip install --quiet --upgrade pip
& .\.build-venv\Scripts\python.exe -m pip install --quiet . pyinstaller
# sounddevice's wheel carries PortAudio, so the microphone/speaker work out of the box
& .\.build-venv\Scripts\pyinstaller.exe --onefile --name Alfred --icon packaging\alfred.ico `
    --clean --noconfirm --log-level WARN run_alfred.py
& .\dist\Alfred.exe --version
Write-Host "Built: $PWD\dist\Alfred.exe"

$iscc = @(
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "${env:ProgramFiles}\Inno Setup 6\ISCC.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if ($iscc) {
    & $iscc /Qp installer\alfred.iss
    Write-Host "Installer: $PWD\dist\Alfred-Setup.exe"
} else {
    Write-Host "Inno Setup not found: skipping the installer (https://jrsoftware.org/isinfo.php)."
}
