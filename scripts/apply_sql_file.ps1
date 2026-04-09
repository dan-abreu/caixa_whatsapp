param(
    [string]$SqlFile = "sql/schema_clientes_upgrade.sql"
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $RepoRoot

$pythonExe = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $pythonExe)) {
    throw "Virtualenv nao encontrada. Execute .\setup.ps1 primeiro."
}

& $pythonExe ".\scripts\apply_sql_file.py" $SqlFile
