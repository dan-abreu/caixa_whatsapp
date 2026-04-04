$ErrorActionPreference = "Continue"

$healthOk = $false
$healthBody = $null
$pythonRunning = @(Get-Process -Name python,pythonw -ErrorAction SilentlyContinue).Count -gt 0
$ngrokRunning = @(Get-Process -Name ngrok -ErrorAction SilentlyContinue).Count -gt 0

try {
  $healthBody = Invoke-RestMethod -Method GET -Uri "http://127.0.0.1:8000/health" -TimeoutSec 5
  if ($healthBody.status -eq "ok") { $healthOk = $true }
} catch {
  $healthBody = $_.Exception.Message
}

$ngrokUrl = $null
try {
  $tunnels = Invoke-RestMethod -Method GET -Uri "http://127.0.0.1:4040/api/tunnels" -TimeoutSec 5
  $ngrokUrl = $tunnels.tunnels[0].public_url
} catch {
  $ngrokUrl = $_.Exception.Message
}

Write-Host "API health OK: $healthOk"
Write-Host "Health response: $healthBody"
Write-Host "ngrok URL: $ngrokUrl"
Write-Host "Python process running: $pythonRunning"
Write-Host "ngrok process running: $ngrokRunning"

if (-not $healthOk) {
  Write-Host "If API is down, inspect logs: api_stdout.log and api_stderr.log" -ForegroundColor Yellow
}
