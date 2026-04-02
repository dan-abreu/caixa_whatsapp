param(
    [Parameter(Mandatory = $true)]
    [string]$Remetente,

    [Parameter(Mandatory = $true)]
    [string]$Mensagem,

    [string]$Url,
    [string]$Token
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
Import-DotEnv -Path (Join-Path $RepoRoot ".env")

if (-not $Url) {
    $hostAddress = if ($env:APP_HOST) { $env:APP_HOST } else { "127.0.0.1" }
    $port = if ($env:APP_PORT) { $env:APP_PORT } else { "8000" }
    $Url = "http://$hostAddress`:$port/webhook/whatsapp"
}

if (-not $Token) {
    $Token = $env:WEBHOOK_TOKEN
}

if (-not $Token) {
    throw "WEBHOOK_TOKEN não encontrado. Informe -Token ou configure no .env"
}

$body = @{
    remetente = $Remetente
    mensagem = $Mensagem
} | ConvertTo-Json

$headers = @{
    "Content-Type" = "application/json"
    "X-Webhook-Token" = $Token
}

$response = Invoke-RestMethod -Uri $Url -Method Post -Headers $headers -Body $body
$response | ConvertTo-Json -Depth 6