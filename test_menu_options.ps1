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
$Token = $env:WEBHOOK_TOKEN
if (-not $Token) { throw 'WEBHOOK_TOKEN nao encontrado no .env' }

$BaseUrl = 'http://127.0.0.1:8000'
$pythonExe = '.\.venv\Scripts\python.exe'

# Create deterministic authorized test users to isolate server-side session cache per phone.
& $pythonExe -c "from database import DatabaseClient; db=DatabaseClient(); users=[('+59720000001','Teste Op 1','operador'),('+59720000002','Teste Op 2','operador'),('+59720000003','Teste Op 3','operador'),('+59720000004','Teste Op 4','operador'),('+59720000005','Teste Op 5','operador'),('+59720000006','Teste Op 6','operador'),('+59700000000','Administrador','admin')]; [db.client.table('usuarios').upsert({'telefone':p,'nome':n,'tipo_usuario':t,'ativo':True}, on_conflict='telefone').execute() for p,n,t in users]" | Out-Null

function Clear-Session {
  param([string]$Phone)
  & $pythonExe -c "from database import DatabaseClient; db=DatabaseClient(); db.clear_conversation_session('$Phone')" | Out-Null
}

function Send-Webhook {
  param([string]$Phone,[string]$Message,[string]$TokenValue)
  $headers = @{
    'Content-Type'='application/json'
    'X-Webhook-Token'=$TokenValue
  }
  $body = @{remetente=$Phone; mensagem=$Message} | ConvertTo-Json
  Invoke-RestMethod -Method POST -Uri "$BaseUrl/webhook/whatsapp" -Headers $headers -Body $body
}

function Add-Result {
  param([string]$Name,[bool]$Ok,[string]$Details)
  [pscustomobject]@{ test=$Name; status=$(if($Ok){'PASS'}else{'FAIL'}); details=$Details }
}

$operator = '+59711111111'
$admin = '+59700000000'
$op1 = '+59720000001'
$op2 = '+59720000002'
$op3 = '+59720000003'
$op4 = '+59720000004'
$op5 = '+59720000005'
$op6 = '+59720000006'
$results = @()

# Baseline
$health = Invoke-RestMethod -Method GET -Uri "$BaseUrl/health"
$results += Add-Result 'health' ($health.status -eq 'ok') ("status=" + $health.status)

# Option 1
Clear-Session -Phone $op1
$r = Send-Webhook -Phone $op1 -Message 'menu' -TokenValue $Token
$okMenu = ($r.dados.etapa -eq 'await_menu_option') -or ($r.mensagem -like '*Responda com 1*')
$results += Add-Result 'menu_open_for_option1' $okMenu ($r.mensagem -replace "`n",' | ')
$r = Send-Webhook -Phone $op1 -Message '1' -TokenValue $Token
$ok1 = ($r.dados.acao -eq 'registrar_operacao')
$results += Add-Result 'option_1_register_operation' $ok1 (($r.dados | ConvertTo-Json -Compress) + ' :: ' + ($r.mensagem -replace "`n",' | '))

# Option 2
Clear-Session -Phone $op2
[void](Send-Webhook -Phone $op2 -Message 'menu' -TokenValue $Token)
$r = Send-Webhook -Phone $op2 -Message '2' -TokenValue $Token
$ok2 = ($r.dados.intencao -eq 'consultar_relatorio')
$results += Add-Result 'option_2_view_cashbox' $ok2 (($r.dados | ConvertTo-Json -Compress) + ' :: ' + ($r.mensagem -replace "`n",' | '))

# Option 3 operator denied
Clear-Session -Phone $op3
[void](Send-Webhook -Phone $op3 -Message 'menu' -TokenValue $Token)
$r = Send-Webhook -Phone $op3 -Message '3' -TokenValue $Token
$ok3op = ($r.dados.acao -eq 'atualizar_taxa' -and $r.dados.permitido -eq $false)
$results += Add-Result 'option_3_rate_update_denied_for_operator' $ok3op (($r.dados | ConvertTo-Json -Compress) + ' :: ' + ($r.mensagem -replace "`n",' | '))

# Option 3 admin allowed
Clear-Session -Phone $admin
[void](Send-Webhook -Phone $admin -Message 'menu' -TokenValue $Token)
$r = Send-Webhook -Phone $admin -Message '3' -TokenValue $Token
$ok3ad = ($r.dados.acao -eq 'atualizar_taxa' -and $r.dados.permitido -eq $true)
$results += Add-Result 'option_3_rate_update_allowed_for_admin' $ok3ad (($r.dados | ConvertTo-Json -Compress) + ' :: ' + ($r.mensagem -replace "`n",' | '))

# Option 4
Clear-Session -Phone $op4
[void](Send-Webhook -Phone $op4 -Message 'menu' -TokenValue $Token)
$r = Send-Webhook -Phone $op4 -Message '4' -TokenValue $Token
$ok4 = ($r.dados.acao -eq 'editar_operacao')
$results += Add-Result 'option_4_edit_operation_help' $ok4 (($r.dados | ConvertTo-Json -Compress) + ' :: ' + ($r.mensagem -replace "`n",' | '))

# Option 5
Clear-Session -Phone $op5
[void](Send-Webhook -Phone $op5 -Message 'menu' -TokenValue $Token)
$r = Send-Webhook -Phone $op5 -Message '5' -TokenValue $Token
$ok5 = ($r.dados.acao -eq 'cancelar_operacao')
$results += Add-Result 'option_5_cancel_operation_help' $ok5 (($r.dados | ConvertTo-Json -Compress) + ' :: ' + ($r.mensagem -replace "`n",' | '))

# Invalid option
Clear-Session -Phone $op6
[void](Send-Webhook -Phone $op6 -Message 'menu' -TokenValue $Token)
$r = Send-Webhook -Phone $op6 -Message '9' -TokenValue $Token
$okInv = ($r.dados.etapa -eq 'await_menu_option')
$results += Add-Result 'menu_invalid_option' $okInv (($r.dados | ConvertTo-Json -Compress) + ' :: ' + ($r.mensagem -replace "`n",' | '))

# Token invalid security
$bad = Send-Webhook -Phone $operator -Message 'menu' -TokenValue 'token-invalido'
$okSec = ($bad.dados.erro -eq 401)
$results += Add-Result 'security_invalid_token' $okSec (($bad.dados | ConvertTo-Json -Compress) + ' :: ' + ($bad.mensagem -replace "`n",' | '))

# Unauthorized sender
$unauth = Send-Webhook -Phone '+559800000000' -Message 'menu' -TokenValue $Token
$okAuth = ($unauth.dados.erro -eq 403)
$results += Add-Result 'security_unauthorized_sender' $okAuth (($unauth.dados | ConvertTo-Json -Compress) + ' :: ' + ($unauth.mensagem -replace "`n",' | '))

$pass = ($results | Where-Object {$_.status -eq 'PASS'}).Count
$total = $results.Count
$fail = $total - $pass

$summary = [pscustomobject]@{ total=$total; pass=$pass; fail=$fail }

'RESULTS_TABLE'
$results | Format-Table -AutoSize
''
'SUMMARY_JSON'
$summary | ConvertTo-Json -Compress
''
'RESULTS_JSON'
$results | ConvertTo-Json -Depth 6

if ($fail -gt 0) { exit 1 }
