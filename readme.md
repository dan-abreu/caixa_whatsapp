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
- Scripts de setup, execução, simulação e schema.

## Estrutura atual

```text
caixa_whatsapp/
|- app/
|  |- __init__.py
|  |- ai_intents_lexicon.json
|  |- ai_service.py
|  |- database.py
|  |- main.py
|  |- multi_agent_system.py
|- scripts/
|  |- apply_schema.ps1
|  |- apply_schema.py
|  |- backfill_caixas.py
|  |- register_autostart.ps1
|  |- simulate_whatsapp.py
|  |- start_background.ps1
|  |- status_background.ps1
|  |- stop_background.ps1
|- sql/
|  |- schema.sql
|  |- schema_caixas.sql
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
- `AI_LEXICON_PATH` (override opcional do léxico)

Notas de governança do confidence score:

- `AI_CONF_PROFILE` define defaults operacionais para o score (pesos, metas e faixas).
- `AI_CONF_PROFILE=auto` aplica roteamento por maturidade historica:
  - seed (`< 30` amostras): `aggressive`
  - learning/stable (`30-299` amostras): `balanced`
  - advanced (`>= 300` amostras): `conservative`
- Qualquer variável `AI_CONF_*` individual definida no ambiente sobrescreve o preset ativo.

## Banco de dados

Aplicar schema principal:

```powershell
.\scripts\apply_schema.ps1
```

ou via Python:

```powershell
.\.venv\Scripts\python.exe .\scripts\apply_schema.py
```

Para ambientes antigos, se necessário recalcular saldos dos 5 caixas:

```powershell
.\.venv\Scripts\python.exe .\scripts\backfill_caixas.py
```

## Endpoints já implementados

Base local: `http://127.0.0.1:8000`

Saúde e menu:

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
- `test_caixas.py`
- `test_menu_options.ps1`
- `smoke_enterprise.ps1`
- `hundred_test.ps1`

## Deploy

Compatível com Railway/Procfile usando:

```bash
uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

## Observações finais

- O projeto já está reorganizado e funcional com código principal em `app/`.
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
