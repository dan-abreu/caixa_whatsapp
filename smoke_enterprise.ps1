param(
    [string]$Remetente = "+559891438754",
    [string]$BaseUrl = "http://127.0.0.1:8000"
)

$ErrorActionPreference = "Stop"

function Get-EnvValue {
    param(
        [string]$Name,
        [string]$EnvPath = ".env"
    )

    if (-not (Test-Path $EnvPath)) {
        throw "Arquivo .env não encontrado em $EnvPath"
    }

    $line = Get-Content $EnvPath | Where-Object { $_ -match "^$Name=" } | Select-Object -First 1
    if (-not $line) {
        throw "Variável $Name não encontrada no .env"
    }

    return ($line -split "=", 2)[1]
}

function Send-WhatsAppStep {
    param(
        [string]$Mensagem,
        [string]$Token,
        [string]$Url,
        [string]$Phone
    )

    $headers = @{
        "X-Webhook-Token" = $Token
        "Content-Type" = "application/json"
    }

    $body = @{
        remetente = $Phone
        mensagem = $Mensagem
    } | ConvertTo-Json

    $response = Invoke-RestMethod -Method POST -Uri "$Url/webhook/whatsapp" -Headers $headers -Body $body
    Write-Host "`n> $Mensagem" -ForegroundColor Cyan
    Write-Host "< $($response.mensagem)" -ForegroundColor Green
    return $response
}

$token = Get-EnvValue -Name "WEBHOOK_TOKEN"

$steps = @(
    "compra",
    "balcao",
    "fundido",
    "90",
    "10",
    "70",
    "USD e SRD",
    "300",
    "5000",
    "40",
    "8",
    "parcial",
    "Teste Cliente",
    "dinheiro",
    "nenhuma",
    "sim"
)

Write-Host "=== Smoke Test Enterprise ===" -ForegroundColor Yellow
Write-Host "BaseUrl: $BaseUrl"
Write-Host "Remetente: $Remetente"

# Health
$health = Invoke-RestMethod -Method GET -Uri "$BaseUrl/health"
Write-Host "`nHealth: $($health.status)" -ForegroundColor Green

# Guided flow
$last = $null
foreach ($m in $steps) {
    $last = Send-WhatsAppStep -Mensagem $m -Token $token -Url $BaseUrl -Phone $Remetente
}

Write-Host "`nResultado final do fluxo guiado:" -ForegroundColor Yellow
$last | ConvertTo-Json -Depth 6

if ($last.dados.analise_multiagente) {
    Write-Host "`nAnalise multiagente automatica: OK" -ForegroundColor Green
    $last.dados.analise_multiagente | ConvertTo-Json -Depth 6
} else {
    Write-Host "`nAnalise multiagente automatica: nao anexada" -ForegroundColor Yellow
}

# Reports (UTC day range)
$start = [DateTime]::UtcNow.Date.ToString("yyyy-MM-ddTHH:mm:ssK")
$end = ([DateTime]::UtcNow.Date.AddDays(1)).ToString("yyyy-MM-ddTHH:mm:ssK")
$startEscaped = [uri]::EscapeDataString($start)
$endEscaped = [uri]::EscapeDataString($end)

Write-Host "`nRelatório diário:" -ForegroundColor Yellow
Invoke-RestMethod -Method GET -Uri "$BaseUrl/reports/daily-closure" | ConvertTo-Json -Depth 6

Write-Host "`nTop divergências:" -ForegroundColor Yellow
Invoke-RestMethod -Method GET -Uri "$BaseUrl/reports/top-divergences?start=$startEscaped&end=$endEscaped&limit=5" | ConvertTo-Json -Depth 6

Write-Host "`nReconciliação por moeda:" -ForegroundColor Yellow
Invoke-RestMethod -Method GET -Uri "$BaseUrl/reports/reconciliation-by-currency?start=$startEscaped&end=$endEscaped" | ConvertTo-Json -Depth 6

Write-Host "`nCSV de fechamento (primeiras linhas):" -ForegroundColor Yellow
$csv = (Invoke-WebRequest -Method GET -UseBasicParsing -Uri "$BaseUrl/reports/closure-csv?start=$startEscaped&end=$endEscaped").Content
$csv.Split("`n") | Select-Object -First 12 | ForEach-Object { $_ }

Write-Host "`nSmoke test concluído." -ForegroundColor Green
