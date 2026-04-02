param(
    [string]$PythonVersion = "3.12",
    [switch]$RecreateVenv,
    [switch]$SkipEnvFile
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $RepoRoot

$venvPath = Join-Path $RepoRoot ".venv"
$pythonExe = Join-Path $venvPath "Scripts\python.exe"

if ($RecreateVenv -and (Test-Path $venvPath)) {
    Remove-Item $venvPath -Recurse -Force
}

if (-not (Test-Path $pythonExe)) {
    $pyLauncher = Get-Command py -ErrorAction SilentlyContinue
    if ($pyLauncher) {
        & py "-$PythonVersion" -m venv .venv
    }
    else {
        $python = Get-Command python -ErrorAction SilentlyContinue
        if (-not $python) {
            throw "Python não encontrado. Instale Python 3.12 e tente novamente."
        }
        & python -m venv .venv
    }
}

if (-not (Test-Path $pythonExe)) {
    throw "Falha ao criar a virtualenv em .venv"
}

& $pythonExe -m pip install --upgrade pip
& $pythonExe -m pip install -r requirements.txt

if (-not $SkipEnvFile -and -not (Test-Path ".env") -and (Test-Path ".env.example")) {
    Copy-Item ".env.example" ".env"
}

Write-Host ""
Write-Host "Setup concluído. Próximos passos:" -ForegroundColor Green
Write-Host "1. Preencha o arquivo .env com suas credenciais."
Write-Host "2. Execute .\run.ps1 para subir a API."
Write-Host "3. Execute .\invoke_whatsapp.ps1 -Remetente '+59711111111' -Mensagem 'Comprei 10g de ouro' para testar."