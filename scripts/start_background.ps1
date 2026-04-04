$ErrorActionPreference = "Stop"

$repo = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $repo

function Import-DotEnv {
    param([string]$Path)

    if (-not (Test-Path $Path)) {
        return
    }

    foreach ($line in Get-Content $Path) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith("#")) {
            continue
        }

        $parts = $trimmed -split '=', 2
        if ($parts.Count -ne 2) {
            continue
        }

        $name = $parts[0].Trim()
        $value = $parts[1].Trim().Trim('"').Trim("'")
        [Environment]::SetEnvironmentVariable($name, $value, "Process")
    }
}

Import-DotEnv -Path (Join-Path $repo ".env")

$pythonExe = Join-Path $repo ".venv\Scripts\python.exe"
if (-not (Test-Path $pythonExe)) {
    throw "Virtualenv not found. Run .\\setup.ps1 first."
}

$apiOutLog = Join-Path $repo "api_stdout.log"
$apiErrLog = Join-Path $repo "api_stderr.log"
$ngrokOutLog = Join-Path $repo "ngrok_stdout.log"
$ngrokErrLog = Join-Path $repo "ngrok_stderr.log"

# Stop previous processes to avoid duplicates
Get-Process -Name python,pythonw,ngrok -ErrorAction SilentlyContinue | Stop-Process -Force

# Start API in background
Start-Process -FilePath $pythonExe -WindowStyle Hidden -WorkingDirectory $repo -ArgumentList @(
    "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000"
) -RedirectStandardOutput $apiOutLog -RedirectStandardError $apiErrLog

# Start ngrok in background
Start-Process -FilePath "ngrok.exe" -WindowStyle Hidden -WorkingDirectory $repo -ArgumentList @(
  "http", "8000"
) -RedirectStandardOutput $ngrokOutLog -RedirectStandardError $ngrokErrLog

Start-Sleep -Seconds 3
Write-Host "Background services started."
Write-Host "Run .\\scripts\\status_background.ps1 to check health and ngrok URL."
