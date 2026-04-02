param(
    [string]$HostAddress,
    [int]$Port,
    [switch]$NoReload
)

$ErrorActionPreference = "Stop"

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

$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $RepoRoot

$pythonExe = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $pythonExe)) {
    throw "Virtualenv não encontrada. Execute .\setup.ps1 primeiro."
}

Import-DotEnv -Path (Join-Path $RepoRoot ".env")

if (-not $HostAddress) {
    $HostAddress = if ($env:APP_HOST) { $env:APP_HOST } else { "127.0.0.1" }
}

if (-not $Port) {
    $Port = if ($env:APP_PORT) { [int]$env:APP_PORT } else { 8000 }
}

$uvicornArgs = @("-m", "uvicorn", "main:app", "--host", $HostAddress, "--port", "$Port")
if (-not $NoReload) {
    $uvicornArgs += "--reload"
}

& $pythonExe @uvicornArgs