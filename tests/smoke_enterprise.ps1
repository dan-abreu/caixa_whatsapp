param(
    [string]$Remetente = "+559891438754",
    [string]$BaseUrl = "http://127.0.0.1:8000"
)

$ErrorActionPreference = "Stop"

# Force UTF-8 in console/session to reduce encoding artifacts on Windows PowerShell.
try {
    [Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)
    [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
    $OutputEncoding = [System.Text.UTF8Encoding]::new($false)
    chcp 65001 | Out-Null
} catch {
    # Best-effort only.
}

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

function Fix-Mojibake {
    param([string]$Text)

    if ([string]::IsNullOrWhiteSpace($Text)) {
        return $Text
    }

    # Best-effort fix for UTF-8 bytes decoded as Windows-1252/Latin-1.
    if ($Text -notmatch "\u00C3|\u00C2|\u00E2") {
        return $Text
    }

    try {
        $bytes = [System.Text.Encoding]::GetEncoding(1252).GetBytes($Text)
        return [System.Text.Encoding]::UTF8.GetString($bytes)
    } catch {
        return $Text
    }
}

function Normalize-DisplayText {
    param([string]$Text)

    if ([string]::IsNullOrWhiteSpace($Text)) {
        return $Text
    }

    $normalized = $Text
    $normalized = $normalized -replace "✅", "[OK]"
    $normalized = $normalized -replace "⚠️|⚠", "[ALERTA]"

    # Remove replacement-char artifacts often left by mixed encodings.
    $normalized = $normalized -replace "�+", ""

    # Normalize known status lines even if they contain unknown leading artifacts.
    $lines = $normalized -split "`r?`n"
    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($lines[$i] -match "salva com sucesso") {
            $lines[$i] = "[OK] Operacao salva com sucesso."
            continue
        }

        if ($lines[$i] -match "Aten") {
            $lines[$i] = "[ALERTA] Atencao: diferenca acima do limite de risco."
            continue
        }
    }
    $normalized = ($lines -join "`n")

    # Collapse accidental marker remnants after stripping broken symbols.
    $normalized = $normalized -replace "\s{2,}", " "

    return $normalized.Trim()
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
    $msg = Fix-Mojibake -Text ([string]$response.mensagem)
    $msg = Normalize-DisplayText -Text $msg
    Write-Host "< $msg" -ForegroundColor Green
    return $response
}

$token = Get-EnvValue -Name "WEBHOOK_TOKEN"

$steps = @(
    "compra",
    "balcao",
    "90",
    "10",
    "USD",
    "70",
    "USD e SRD",
    "300",
    "40",
    "5000",
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

# Reset pending conversation state to keep smoke deterministic.
try {
    $null = Send-WhatsAppStep -Mensagem "cancelar" -Token $token -Url $BaseUrl -Phone $Remetente
    Write-Host "Sessao anterior resetada." -ForegroundColor DarkGray
} catch {
    Write-Host "Nao foi possivel resetar sessao anterior. Continuando smoke..." -ForegroundColor Yellow
}

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

Write-Host "`nRelatorio diario:" -ForegroundColor Yellow
Invoke-RestMethod -Method GET -Uri "$BaseUrl/reports/daily-closure" | ConvertTo-Json -Depth 6

Write-Host "`nTop divergencias:" -ForegroundColor Yellow
Invoke-RestMethod -Method GET -Uri "$BaseUrl/reports/top-divergences?start=$startEscaped&end=$endEscaped&limit=5" | ConvertTo-Json -Depth 6

Write-Host "`nReconciliacao por moeda:" -ForegroundColor Yellow
Invoke-RestMethod -Method GET -Uri "$BaseUrl/reports/reconciliation-by-currency?start=$startEscaped&end=$endEscaped" | ConvertTo-Json -Depth 6

Write-Host "`nCSV de fechamento (primeiras linhas):" -ForegroundColor Yellow
$csv = (Invoke-WebRequest -Method GET -UseBasicParsing -Uri "$BaseUrl/reports/closure-csv?start=$startEscaped&end=$endEscaped").Content
$csv.Split("`n") | Select-Object -First 12 | ForEach-Object { $_ }

Write-Host "`nSmoke test concluido." -ForegroundColor Green
