$ErrorActionPreference='Stop'
$token=(Get-Content .env | Where-Object {$_ -match '^WEBHOOK_TOKEN='} | ForEach-Object {($_ -split '=',2)[1]})

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
$pythonExe = '.\.venv\Scripts\python.exe'
if (-not (Test-Path $pythonExe)) {
  throw 'Virtualenv não encontrada. Execute .\setup.ps1 primeiro.'
}

# Garante estado limpo de sessão para não herdar fluxo guiado pendente entre execuções.
& $pythonExe -c "from database import DatabaseClient; db=DatabaseClient(); [db.clear_conversation_session(p) for p in ['+559891438754','+59711111111','+59700000000']]"

function Run-Test {
  param([string]$Name, [scriptblock]$Action, [scriptblock]$Assert)
  try {
    $res = & $Action
    $ok = & $Assert $res $null
    [pscustomobject]@{Test=$Name;Status=($(if($ok){'PASS'}else{'FAIL'}));Evidence=($res | ConvertTo-Json -Depth 8 -Compress)}
  } catch {
    $code = $null
    try { if ($_.Exception.Response -and $_.Exception.Response.StatusCode) { $code = [int]$_.Exception.Response.StatusCode } } catch {}
    $err = [pscustomobject]@{code=$code;message=$_.Exception.Message}
    $ok = & $Assert $null $err
    [pscustomobject]@{Test=$Name;Status=($(if($ok){'PASS'}else{'FAIL'}));Evidence=($err | ConvertTo-Json -Compress)}
  }
}

$results = @()
$results += Run-Test '1.health' { Invoke-RestMethod -Method GET -Uri 'http://127.0.0.1:8000/health' } { param($r,$e) $r.status -eq 'ok' }
$results += Run-Test '2.compra' { .\invoke_whatsapp.ps1 -Remetente '+559891438754' -Mensagem 'Comprei 1g de ouro' | ConvertFrom-Json } { param($r,$e) $r.dados.intencao -eq 'registrar_operacao' -and $r.dados.tipo_operacao -eq 'compra' }
$results += Run-Test '3.venda' { .\invoke_whatsapp.ps1 -Remetente '+559891438754' -Mensagem 'Vendi 1g de ouro' | ConvertFrom-Json } { param($r,$e) $r.dados.tipo_operacao -eq 'venda' -and $null -ne $r.dados.analise_multiagente }
$results += Run-Test '4.permissao_operador_taxa' { .\invoke_whatsapp.ps1 -Remetente '+59711111111' -Mensagem 'Taxa ouro 75' | ConvertFrom-Json } { param($r,$e) ($r -and $r.dados.erro -eq 403) -or ($e -and $e.code -eq 403) }
$results += Run-Test '5.nao_autorizado' { .\invoke_whatsapp.ps1 -Remetente '+559800000000' -Mensagem 'Comprei 1g de ouro' | ConvertFrom-Json } { param($r,$e) ($r -and $r.dados.erro -eq 403) -or ($e -and $e.code -eq 403) }
$results += Run-Test '6.token_invalido' { .\invoke_whatsapp.ps1 -Remetente '+559891438754' -Mensagem 'Comprei 1g de ouro' -Token 'token-invalido' | ConvertFrom-Json } { param($r,$e) ($r -and $r.dados.erro -eq 401) -or ($e -and $e.code -eq 401) }
$results += Run-Test '7.idempotencia' {
  $h=@{'Content-Type'='application/json';'X-Webhook-Token'=$token;'X-Provider-Message-Id'='full-test-dup-001'}
  $r1=Invoke-RestMethod -Uri 'http://127.0.0.1:8000/webhook/whatsapp' -Method POST -Headers $h -Body (@{remetente='+559891438754';mensagem='Comprei 2g de ouro'}|ConvertTo-Json)
  $r2=Invoke-RestMethod -Uri 'http://127.0.0.1:8000/webhook/whatsapp' -Method POST -Headers $h -Body (@{remetente='+559891438754';mensagem='Comprei 99g de ouro'}|ConvertTo-Json)
  [pscustomobject]@{first=$r1.dados.valor_total;second=$r2.dados.valor_total;same=($r1.dados.valor_total -eq $r2.dados.valor_total)}
} { param($r,$e) $r.same -eq $true }
$results += Run-Test '8.report_risk' { Invoke-RestMethod -Method GET -Uri 'http://127.0.0.1:8000/reports/risk-alerts' } { param($r,$e) $null -ne $r.total_alertas }
$results += Run-Test '9.report_daily' { Invoke-RestMethod -Method GET -Uri 'http://127.0.0.1:8000/reports/daily-closure' } { param($r,$e) $null -ne $r.summary }
$results += Run-Test '10.report_top_div' {
  $start='2026-04-02T00:00:00+00:00'; $end='2026-04-03T00:00:00+00:00'
  Invoke-RestMethod -Method GET -Uri "http://127.0.0.1:8000/reports/top-divergences?start=$([uri]::EscapeDataString($start))&end=$([uri]::EscapeDataString($end))&limit=5"
} { param($r,$e) $null -ne $r.items }
$results += Run-Test '11.multi_agent_runs' { Invoke-RestMethod -Method GET -Uri 'http://127.0.0.1:8000/ai/multi-agent/runs?limit=5' } { param($r,$e) $null -ne $r.items -and $r.items.Count -ge 1 }

$results | Format-Table -AutoSize
''
'JSON_RESULTS'
$results | ConvertTo-Json -Depth 6
