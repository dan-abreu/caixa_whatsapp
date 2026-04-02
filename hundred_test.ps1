$ErrorActionPreference = 'Stop'

function Import-DotEnv {
  param([string]$Path)
  if (-not (Test-Path $Path)) { return }
  foreach ($line in Get-Content $Path) {
    $trimmed = $line.Trim()
    if (-not $trimmed -or $trimmed.StartsWith('#')) { continue }
    $parts = $trimmed -split '=', 2
    if ($parts.Count -ne 2) { continue }
    [Environment]::SetEnvironmentVariable($parts[0].Trim(), $parts[1].Trim().Trim('"').Trim("'"), 'Process')
  }
}

Import-DotEnv -Path '.\.env'

$BaseUrl = 'http://127.0.0.1:8000'
$Token = $env:WEBHOOK_TOKEN
if (-not $Token) { throw 'WEBHOOK_TOKEN não encontrado no ambiente.' }

$pythonExe = '.\.venv\Scripts\python.exe'
if (-not (Test-Path $pythonExe)) {
  throw 'Virtualenv não encontrada. Execute .\setup.ps1 primeiro.'
}

# Limpa sessões para execução determinística.
& $pythonExe -c "from database import DatabaseClient; db=DatabaseClient(); [db.clear_conversation_session(p) for p in ['+559891438754','+59711111111','+59700000000']]"

function Invoke-Webhook {
  param(
    [string]$Phone,
    [string]$Message,
    [string]$TokenValue,
    [string]$ProviderMessageId
  )

  $headers = @{
    'Content-Type' = 'application/json'
    'X-Webhook-Token' = $TokenValue
  }
  if ($ProviderMessageId) {
    $headers['X-Provider-Message-Id'] = $ProviderMessageId
  }

  $body = @{ remetente = $Phone; mensagem = $Message } | ConvertTo-Json
  return Invoke-RestMethod -Method POST -Uri "$BaseUrl/webhook/whatsapp" -Headers $headers -Body $body
}

function Run-Test {
  param([int]$Id, [string]$Name, [scriptblock]$Action, [scriptblock]$Assert)

  try {
    $res = & $Action
    $ok = & $Assert $res $null
    [pscustomobject]@{
      Id = $Id
      Test = $Name
      Status = $(if ($ok) { 'PASS' } else { 'FAIL' })
      Evidence = ($res | ConvertTo-Json -Depth 8 -Compress)
    }
  } catch {
    $code = $null
    try {
      if ($_.Exception.Response -and $_.Exception.Response.StatusCode) { $code = [int]$_.Exception.Response.StatusCode }
    } catch {}
    $err = [pscustomobject]@{ code = $code; message = $_.Exception.Message }
    $ok = & $Assert $null $err
    [pscustomobject]@{
      Id = $Id
      Test = $Name
      Status = $(if ($ok) { 'PASS' } else { 'FAIL' })
      Evidence = ($err | ConvertTo-Json -Compress)
    }
  }
}

$results = @()
$caseId = 1

# 1-11: baseline funcional e segurança
$results += Run-Test $caseId 'health' { Invoke-RestMethod -Method GET -Uri "$BaseUrl/health" } { param($r,$e) $r.status -eq 'ok' }; $caseId++
$results += Run-Test $caseId 'admin_update_rate' { Invoke-Webhook -Phone '+59700000000' -Message 'Taxa ouro 70.00' -TokenValue $Token } { param($r,$e) $r.dados.intencao -eq 'atualizar_taxa' }; $caseId++
$results += Run-Test $caseId 'basic_compra' { Invoke-Webhook -Phone '+559891438754' -Message 'Comprei 1g de ouro' -TokenValue $Token } { param($r,$e) $r.dados.tipo_operacao -eq 'compra' }; $caseId++
$results += Run-Test $caseId 'basic_venda' { Invoke-Webhook -Phone '+559891438754' -Message 'Vendi 1g de ouro' -TokenValue $Token } { param($r,$e) $r.dados.tipo_operacao -eq 'venda' -and $null -ne $r.dados.analise_multiagente }; $caseId++
$results += Run-Test $caseId 'operator_cannot_update_rate' { Invoke-Webhook -Phone '+59711111111' -Message 'Taxa ouro 75' -TokenValue $Token } { param($r,$e) ($r -and $r.dados.erro -eq 403) -or ($e -and $e.code -eq 403) }; $caseId++
$results += Run-Test $caseId 'unauthorized_sender' { Invoke-Webhook -Phone '+559800000000' -Message 'Comprei 1g de ouro' -TokenValue $Token } { param($r,$e) ($r -and $r.dados.erro -eq 403) -or ($e -and $e.code -eq 403) }; $caseId++
$results += Run-Test $caseId 'invalid_token' { Invoke-Webhook -Phone '+559891438754' -Message 'Comprei 1g de ouro' -TokenValue 'token-invalido' } { param($r,$e) ($r -and $r.dados.erro -eq 401) -or ($e -and $e.code -eq 401) }; $caseId++
$results += Run-Test $caseId 'idempotency' {
  $sid = 'hundred-test-dup-001'
  $r1 = Invoke-Webhook -Phone '+559891438754' -Message 'Comprei 2g de ouro' -TokenValue $Token -ProviderMessageId $sid
  $r2 = Invoke-Webhook -Phone '+559891438754' -Message 'Comprei 99g de ouro' -TokenValue $Token -ProviderMessageId $sid
  [pscustomobject]@{ same = ($r1.dados.valor_total -eq $r2.dados.valor_total) }
} { param($r,$e) $r.same -eq $true }; $caseId++
$results += Run-Test $caseId 'report_risk' { Invoke-RestMethod -Method GET -Uri "$BaseUrl/reports/risk-alerts" } { param($r,$e) $null -ne $r.total_alertas }; $caseId++
$results += Run-Test $caseId 'report_daily' { Invoke-RestMethod -Method GET -Uri "$BaseUrl/reports/daily-closure" } { param($r,$e) $null -ne $r.summary }; $caseId++
$results += Run-Test $caseId 'report_multi_agent_runs' { Invoke-RestMethod -Method GET -Uri "$BaseUrl/ai/multi-agent/runs?limit=5" } { param($r,$e) $null -ne $r.items -and $r.items.Count -ge 1 }; $caseId++

# 12-86: 75 operações para carga funcional
for ($i = 1; $i -le 75; $i++) {
  $isVenda = ($i % 2 -eq 0)
  $qty = if ($i % 5 -eq 0) { 10 } else { (($i % 4) + 1) }
  $msg = if ($isVenda) { "Vendi $qty g de ouro" } else { "Comprei $qty g de ouro" }
  $name = if ($isVenda) { "batch_venda_$i" } else { "batch_compra_$i" }

  $results += Run-Test $caseId $name { Invoke-Webhook -Phone '+559891438754' -Message $msg -TokenValue $Token } {
    param($r,$e)
    if (-not $r) { return $false }
    if ($isVenda) {
      return $r.dados.tipo_operacao -eq 'venda' -and $null -ne $r.dados.analise_multiagente
    }

    $okCompra = $r.dados.tipo_operacao -eq 'compra'
    $expectsReview = [decimal]$qty -ge 10
    if ($expectsReview) {
      return $okCompra -and $null -ne $r.dados.analise_multiagente
    }
    return $okCompra
  }
  $caseId++
}

# 87-100: fluxo guiado completo (14 verificações)
$guidedPhone = '+59711111111'
$guidedSteps = @(
  @{m='compra'; etapa='await_origem'},
  @{m='balcao'; etapa='await_gold_type'},
  @{m='fundido'; etapa='await_teor'},
  @{m='90'; etapa='await_peso'},
  @{m='10'; etapa='await_preco_usd'},
  @{m='70'; etapa='await_moedas'},
  @{m='USD'; etapa='await_valor_moeda'},
  @{m='700'; etapa='await_fechamento_gramas'},
  @{m='10'; etapa='await_fechamento_tipo'},
  @{m='total'; etapa='await_pessoa'},
  @{m='Cliente Teste'; etapa='await_forma_pagamento'},
  @{m='dinheiro'; etapa='await_observacoes'},
  @{m='nenhuma'; etapa='await_confirmacao'},
  @{m='sim'; etapa='final'}
)

foreach ($step in $guidedSteps) {
  $stepMsg = $step.m
  $stepEtapa = $step.etapa
  $name = "guided_$stepEtapa"

  $results += Run-Test $caseId $name { Invoke-Webhook -Phone $guidedPhone -Message $stepMsg -TokenValue $Token } {
    param($r,$e)
    if (-not $r) { return $false }
    if ($stepEtapa -eq 'final') {
      return $r.dados.intencao -eq 'fluxo_guiado_confirmado'
    }
    return $r.dados.etapa -eq $stepEtapa
  }
  $caseId++
}

$pass = ($results | Where-Object { $_.Status -eq 'PASS' }).Count
$fail = ($results | Where-Object { $_.Status -eq 'FAIL' }).Count
$total = $results.Count

$results | Format-Table Id, Test, Status -AutoSize
""
"SUMMARY"
[pscustomobject]@{ total = $total; pass = $pass; fail = $fail } | ConvertTo-Json -Compress
""
"JSON_RESULTS"
$results | ConvertTo-Json -Depth 8

if ($total -ne 100) {
  throw "Suite inválida: esperado 100 testes, encontrado $total"
}

if ($fail -gt 0) {
  exit 1
}
