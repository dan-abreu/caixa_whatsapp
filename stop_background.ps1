$ErrorActionPreference = "Continue"

Get-Process -Name python,pythonw,ngrok -ErrorAction SilentlyContinue | Stop-Process -Force
Write-Host "Background services stopped."
