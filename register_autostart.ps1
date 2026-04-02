$ErrorActionPreference = "Stop"

$repo = Split-Path -Parent $MyInvocation.MyCommand.Path
$startScript = Join-Path $repo "start_background.ps1"

$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$startScript`""
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Hours 0) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable

Register-ScheduledTask -TaskName "CaixaWhatsappAutostart" -Action $action -Trigger $trigger -Settings $settings -Description "Start Caixa WhatsApp API and ngrok at logon" -Force

Write-Host "Task CaixaWhatsappAutostart created."
Write-Host "It will run at user logon."
