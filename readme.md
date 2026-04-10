# Caixa Inteligente WhatsApp

Sistema backend em Python com FastAPI para operar compra e venda de ouro via WhatsApp, com controle de 5 caixas independentes (`XAU`, `USD`, `EUR`, `SRD`, `BRL`), persistência em Supabase e apoio de IA para interpretação de mensagens.

## O que já está pronto

- API principal estruturada em `app/` com entrypoint `app.main:app`.
- Fluxo guiado de operação (compra/venda) com etapas, validações e confirmação final.
- Menu conversacional (`menu`) com ações operacionais.
- Atualização de taxa com controle de permissão (admin).
- Edição e cancelamento de operação por comando.
- Extrato por período e relatórios de fechamento/risco.
- Caixa segregado por moeda/commodity (sem consolidar tudo em USD).
- Integração com webhook JSON e webhook Twilio.

## Estrutura atual

```text
caixa_whatsapp/
|- app/
|  |- __init__.py
|  |- ai_lexicon_data/
|  |  |- *.json
|  |- ai_service.py
|  |- database.py
|  |- main.py
|  |- multi_agent_system.py
|- scripts/
|  |- apply_schema.ps1
|  |- apply_schema.py
|  |- apply_sql_file.ps1
|  |- apply_sql_file.py
|  |- backfill_caixas.py
|  |- register_autostart.ps1
|  |- simulate_whatsapp.py
|  |- start_background.ps1
|  |- status_background.ps1
|  |- stop_background.ps1
|- sql/
|  |- schema.sql
|  |- schema/
|  |  |- 00_*.sql ... 04_*.sql
|  |- schema_caixas.sql
|  |- schema_clientes_upgrade.sql
|  |- schema_enterprise_upgrade.sql
|- tests/
|- setup.ps1
|- run.ps1
|- invoke_whatsapp.ps1
|- requirements.txt
|- .env.example
```

## Requisitos

- Windows PowerShell 5.1+
- Python 3.12 (recomendado)
- Supabase (URL e chave de serviço)
- Chave Gemini para IA

## Setup rápido

1. Criar ambiente e instalar dependências:

```powershell
.\setup.ps1
```

1. Copiar e preencher variáveis:

```powershell
Copy-Item .env.example .env
```

1. Subir API:

```powershell
.\run.ps1
```

Sem reload (útil para execução estável):

```powershell
.\run.ps1 -NoReload
```

## Variáveis de ambiente

Obrigatórias:

- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY` (ou `SUPABASE_KEY`)
- `WEBHOOK_TOKEN`
- `GEMINI_API_KEY`

Principais opcionais:

- `APP_HOST` (default: `127.0.0.1`)
- `APP_PORT` (default: `8000`)
- `DATABASE_RUNTIME_CACHE_TTL_SECONDS` (default: `15`)
- `REDIS_URL` ou `CACHE_REDIS_URL` (opcional, ativa cache compartilhado entre processos)
- `GEMINI_MODEL` (default: `gemini-2.5-flash`)
- `LOG_LEVEL` (default: `INFO`)
- `TZ_OFFSET_HOURS` (default: `-3`)
- `GUIDED_SESSION_IDLE_MINUTES` (default: `5`)
- `RISK_DIFF_LIMIT_USD` (default: `250`)
- `MULTI_AGENT_AUTO_ENABLED` (default: `true`)
- `MULTI_AGENT_AUTO_MIN_USD` (default: `500`)
- `MULTI_AGENT_AUTO_MIN_WEIGHT_GRAMS` (default: `10`)
- `AI_CONF_SAMPLES_TARGET` (default: `300`)
- `AI_CONF_RISK_WEIGHT` (default: `0.7`)
- `AI_CONF_FAILSAFE_WEIGHT` (default: `1.3`)
- `AI_CONF_WEIGHT_MATURITY` (default: `45`)
- `AI_CONF_WEIGHT_STABILITY` (default: `45`)
- `AI_CONF_WEIGHT_ALERTS` (default: `10`)
- `AI_CONF_BAND_EXCELLENT` (default: `85`)
- `AI_CONF_BAND_GOOD` (default: `70`)
- `AI_CONF_BAND_MODERATE` (default: `50`)
- `AI_CONF_PROFILE` (`balanced`, `conservative`, `aggressive`, `auto`; default: `balanced`)
- `TWILIO_REPLY_MODE` (`normal`, `silent_prefix`, `silent_all`)
- `TWILIO_SILENT_PREFIX` (default: `debug:`)
- `MARKET_CACHE_TTL_SECONDS` (default: `15`)
- `MARKET_NEWS_CACHE_TTL_SECONDS` (default: `900`)
- `MARKET_ALERT_THRESHOLD_PCT` (default: `0.50`)
- `LOT_MONITOR_ENABLED` (default: `true`)
- `LOT_MONITOR_INTERVAL_SECONDS` (default: `300`)
- `TWILIO_ACCOUNT_SID` (obrigatoria para alerta outbound de lote)
- `TWILIO_AUTH_TOKEN` (obrigatoria para alerta outbound de lote)
- `TWILIO_WHATSAPP_FROM` (ex.: `whatsapp:+14155238886`)
- `AI_LEXICON_PATH` (override opcional do léxico)

Notas de governança do confidence score:

- `AI_CONF_PROFILE` define os presets operacionais do score.
- `AI_CONF_PROFILE=auto` roteia por maturidade historica e qualquer `AI_CONF_*` individual sobrescreve o preset ativo.

## Banco de dados

Aplicar schema principal:

```powershell
.\scripts\apply_schema.ps1
.\.venv\Scripts\python.exe .\scripts\apply_schema.py
```

Aplicar a migracao isolada de clientes em ambientes ja existentes:

```powershell
.\scripts\apply_sql_file.ps1 -SqlFile sql/schema_clientes_upgrade.sql
```

Uso generico para qualquer upgrade SQL isolado:

```powershell
.\scripts\apply_sql_file.ps1 -SqlFile sql\schema_enterprise_upgrade.sql
```

O script usa `SUPABASE_DB_URL` ou monta a conexao a partir de `SUPABASE_PROJECT_REF` + `SUPABASE_DB_PASSWORD`.

Para ambientes antigos, se necessário recalcular saldos dos 5 caixas:

```powershell
.\.venv\Scripts\python.exe .\scripts\backfill_caixas.py
```

## Endpoints já implementados

Base local: `http://127.0.0.1:8000`

- `GET /health`
- `GET /menu`

Webhooks:

- `POST /webhook/whatsapp`
- `POST /webhook/twilio`

Relatórios:

- `GET /reports/daily-closure`
- `GET /reports/risk-alerts`
- `GET /reports/closure-range`
- `GET /reports/reconciliation-by-currency`
- `GET /reports/closure-csv`
- `GET /reports/top-divergences`
- `GET /reports/audit/operation/{operation_id}`

Multiagente:

- `POST /ai/multi-agent/analyze`
- `GET /ai/multi-agent/runs`

Operações:

- `POST /operations/{operation_id}/edit`

Painel web SaaS:

- `GET /saas`
- `GET /saas/dashboard`
- `GET /saas/market-snapshot`
- `GET /saas/market-news`
- `GET /saas/clientes`
- `GET /saas/clientes/{cliente_id}`
- `GET /saas/clientes/search`
- `POST /saas/login`
- `POST /saas/logout`
- `POST /saas/clientes`
- `POST /saas/lots/{lot_id}/monitor`
- `POST /saas/profile/pin`
- `POST /saas/console`
- `POST /saas/operations/quick`

## Acesso web SaaS

- O painel web agora usa sessão HTTP com login por operador, sem token na URL.
- Login: informe o telefone cadastrado em `usuarios` e o PIN web.
- Primeiro acesso apos aplicar a migracao: se `web_pin_hash` estiver vazio, o PIN temporario sao os ultimos 6 digitos do telefone.
- Apos entrar, troque o PIN no proprio painel em `Seguranca do Acesso`.
- O formulario rapido web aceita ate 4 pagamentos por operacao, com moedas `USD`, `EUR`, `SRD` e `BRL`.
- Ao concluir uma operacao pelo formulario rapido, o sistema abre um recibo detalhado com opcao de imprimir, exportar em PDF e compartilhar por WhatsApp.
- O cadastro de clientes e a conta por cliente dependem da execucao de `sql/schema_clientes_upgrade.sql`.
- O dashboard acompanha mercado com polling interno, exibindo deltas, percentual, setas, sparkline e feed de noticias para ouro e dolar.
- Cada lote aberto pode receber configuracao de monitoramento persistida em `gold_inventory_lots.metadata.monitor`.
- O alerta automatico de venda por WhatsApp so dispara quando `LOT_MONITOR_ENABLED=true` e as tres variaveis `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN` e `TWILIO_WHATSAPP_FROM` estiverem preenchidas.
- Sem credenciais Twilio, o monitor continua calculando sinais, mas nao envia mensagem outbound.

## Performance web

O painel SaaS agora entrega o CSS e o JavaScript principais como assets estaticos versionados em `app/static/`, com suporte a:

- `GZipMiddleware` para respostas HTML, JSON, CSS e JS acima de 1 KB.
- `Cache-Control: public, max-age=31536000, immutable` para `/static/*`.
- `Cache-Control: private, no-store` para HTML autenticado do painel.
- Versionamento por query string (`/static/arquivo.css?v=...`) com base no `mtime` do arquivo.
- Páginas sem foco operacional imediato (`perfil`, `clientes`, `extrato`) não montam o rail de mercado nem consultam snapshot externo no SSR.
- Quando `REDIS_URL` estiver configurada, o app compartilha cache de mercado e agregados críticos de banco entre processos/workers.
- Em produção, use CDN apenas para `/static/*`, respeite `Cache-Control` de origem e não publique cache de HTML autenticado de `/saas/*` no edge.
- `GET /saas/recibos/{operation_id}`
- `GET /saas/recibos/{operation_id}/pdf`

## Teste rápido de webhook

Com script PowerShell:

```powershell
.\invoke_whatsapp.ps1 -Remetente "+59711111111" -Mensagem "menu"
```

Exemplo de corpo JSON para `POST /webhook/whatsapp`:

```json
{
  "remetente": "+59711111111",
  "mensagem": "compra"
}
```

Header necessário:

- `X-Webhook-Token: <valor de WEBHOOK_TOKEN>`

## Comandos conversacionais principais

- `menu`
- `compra`
- `venda`
- `caixa`
- `extrato`
- `taxa ouro 70`
- `editar 123 preco 110`
- `cancelar 123`
- `voltar` (durante fluxo guiado)

## Scripts úteis

- Inicialização em background: `scripts/start_background.ps1`
- Status local (API + ngrok): `scripts/status_background.ps1`
- Parar serviços: `scripts/stop_background.ps1`
- Simular mensagem via Python: `scripts/simulate_whatsapp.py`
- Registrar tarefa de autostart no Windows: `scripts/register_autostart.ps1`

## Testes no repositório

Existem suites e smoke tests em `tests/`, incluindo:

- `test_comprehensive.py`
- `test_caixas.py` (suite `unittest` para inicialização e movimentação dos 5 caixas)
- `test_menu_options.ps1`
- `smoke_enterprise.ps1`
- `hundred_test.ps1`

Para rodar a cobertura focada de caixas:

```bash
.venv\Scripts\python.exe -m unittest tests.test_caixas -q
```

## Deploy

Compatível com Railway/Procfile usando:

```bash
uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

- O README foi recriado para refletir o estado atual real do código e scripts.

This backend processes WhatsApp messages and runs multi-currency cash operations (USD, EUR, SRD, BRL, XAU) using FastAPI + Supabase.

Key capabilities:

- Guided transaction flow (buy/sell)
- FX-aware pricing and settlement
- Multi-currency cashbox and per-currency subcashbox
- Operation receipt with unique ID
- Edit/cancel operations by natural commands
- User onboarding by phone number and name

### Main Webhooks

- `POST /webhook/whatsapp` (JSON integrations)
- `POST /webhook/twilio?token=YOUR_TOKEN` (direct Twilio WhatsApp)

Twilio endpoint returns TwiML XML responses.

### Environment Variables (Core)

- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY` (or `SUPABASE_KEY`)
- `GEMINI_API_KEY`
- `WEBHOOK_TOKEN`

Optional but recommended:

- `TZ_OFFSET_HOURS=-3`
- `GEMINI_MODEL=gemini-2.5-flash`
- `LOG_LEVEL=INFO`

Twilio debug controls:

- `TWILIO_REPLY_MODE=normal|silent_prefix|silent_all`
- `TWILIO_SILENT_PREFIX=debug:`

### Run Locally

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

### Useful WhatsApp Commands

- `menu`
- `caixa`, `caixa eur`, `caixa xau`
- `editar 123 preco 110`
- `cancelar 123`
- `voltar preco` (inside guided flow)

### Security Notes

- Keep `WEBHOOK_TOKEN` private
- Restrict admin-only actions (rate updates)
- Use HTTPS-only webhooks
- Rotate exposed credentials immediately

---

## Author and Ownership

Created and maintained by **Daniel Abreu**.

- Repository owner: `dan-abreu`
- Project: `caixa_whatsapp`

If you are using this project as a base, please keep proper attribution to the original author.

## Credits

- Architecture and implementation: Daniel Abreu
- AI assistant support (coding and iteration): GitHub Copilot (GPT-5.3-Codex)
- Platform services: Supabase, Twilio, Railway, Google Gemini

## License

This project currently does not include a formal open-source license file.

Recommendation:

- Add a `LICENSE` file (MIT is a common option for private/prototype projects that may be shared later).
- If no license is added, all rights are reserved by default.

## Copyright Notice

Copyright (c) 2026 Daniel Abreu. All rights reserved.

## Contact

For business, partnerships, or technical collaboration, contact the project owner through GitHub:

- <https://github.com/dan-abreu>
