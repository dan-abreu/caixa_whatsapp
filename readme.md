# Caixa Inteligente via WhatsApp

Backend em Python + FastAPI para operar caixa multimoeda (USD, EUR, SRD, BRL, XAU) via WhatsApp, com persistência em Supabase e extração de intenção por IA.

## Resumo

O sistema recebe mensagens de WhatsApp, interpreta intenção e executa regras de negócio no backend (com Decimal), incluindo:

- Registro de operação com fluxo guiado
- Taxas e câmbio manual com validação
- Caixa multimoeda e subcaixa por moeda
- Recibo com ID único
- Edição/cancelamento de operação por comando
- Onboarding por nome e menu numerado

## Stack

- Python 3.11-3.13 (recomendado 3.12 no Windows)
- FastAPI
- Supabase (PostgreSQL)
- Google Gemini API
- Twilio WhatsApp (integração direta)

## Estrutura do Projeto

- `main.py`: API e regras de negócio
- `database.py`: acesso a dados (supabase-py)
- `ai_service.py`: extração de dados via IA
- `schema.sql`: DDL + seed inicial
- `requirements.txt`: dependências
- `setup.ps1`, `run.ps1`: automação local no Windows
- `simulate_whatsapp.py`, `invoke_whatsapp.ps1`: testes locais
- `.env.example`: variáveis de ambiente

## Configuração Local

### 1) Ambiente virtual

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
```

Opcional:

```powershell
.\setup.ps1 -RecreateVenv
```

### 2) Dependências

```powershell
pip install -r requirements.txt
```

### 3) Variáveis de ambiente

Copie `.env.example` para `.env` e preencha.

Obrigatórias:

- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY` (ou `SUPABASE_KEY`)
- `GEMINI_API_KEY`
- `WEBHOOK_TOKEN`

Importantes:

- `GEMINI_MODEL` (default: `gemini-2.5-flash`)
- `TZ_OFFSET_HOURS` (default: `-3`)
- `LOG_LEVEL` (default: `INFO`)
- `MULTI_AGENT_AUTO_ENABLED` (default: `true`)

Twilio debug control:

- `TWILIO_REPLY_MODE=normal|silent_prefix|silent_all`
- `TWILIO_SILENT_PREFIX=debug:`

## Banco de Dados

Execute `schema.sql` no Supabase.

Opcional (se configurado):

```powershell
.\apply_schema.ps1
```

Tabelas principais:

- `usuarios`, `ativos`, `taxas_diarias`, `transacoes`, `logs`
- `sessoes_conversa`, `mensagens_processadas`
- `gold_transactions`, `gold_payments`

## Execução da API

```powershell
uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

Ou:

```powershell
.\run.ps1
```

Health check:

- `GET /health`

## Webhooks

### JSON webhook (integrações/API)

- `POST /webhook/whatsapp`
- Token via `X-Webhook-Token` ou `?token=`

Body exemplo:

```json
{
  "remetente": "+5598991438754",
  "mensagem": "comprei 10 gramas de ouro"
}
```

### Twilio direto (produção WhatsApp)

- `POST /webhook/twilio?token=SEU_TOKEN`
- Espera payload `application/x-www-form-urlencoded`
- Responde TwiML XML

URL recomendada no Twilio Sandbox:

`https://SEU-APP.railway.app/webhook/twilio?token=SEU_TOKEN`

## Fluxo Guiado de Operação

Exemplo de jornada completa:

1. Tipo (`compra`/`venda`)
2. Origem (`balcao`/`fora`)
3. Teor (`0` a `99.99`)
4. Peso (gramas)
5. Moeda base de preço (`USD`, `EUR`, `SRD`, `BRL`)
6. Preço por grama na moeda escolhida
7. Câmbio para USD (se não for USD)
8. Moeda(s) de pagamento e valores
9. Fechamento, pessoa, forma de pagamento, observações
10. Confirmação e gravação

Durante o fluxo, o bot mostra cálculos parciais (total, parcial pago, restante e diferença).

## Correção sem Cancelar

Durante uma operação ativa, você pode corrigir etapa específica:

- `voltar peso`
- `voltar preco`
- `voltar teor`
- `voltar moedas`
- `voltar pagamento`
- `voltar fechamento`
- `voltar` (uma etapa anterior)

## Menu no WhatsApp

Comandos como `menu`, `ajuda`, `comandos` mostram checklist numerado.

Principais opções:

1. Registrar operação
2. Consultar caixa/extrato
3. Atualizar taxa (admin)
4. Editar operação
5. Cancelar operação

## Comandos Naturais de Gestão

Editar operação:

- `editar 123 preco 110`
- `editar 123 quantidade 2.5`
- `editar OP-20260403-00123 moeda EUR`

Cancelar operação:

- `cancelar 123`
- `cancelar OP-20260403-00123`

Permissão: admin ou operador dono da operação.

## Caixa e Subcaixa

Visão geral:

- `caixa`

Subcaixa por moeda:

- `caixa usd`
- `caixa eur`
- `caixa srd`
- `caixa brl`
- `caixa xau` (ou `caixa ouro`)

Para `XAU`, o sistema mostra saldo em gramas e referência em USD com base na última cotação.

## Testes Locais Rápidos

PowerShell:

```powershell
.\invoke_whatsapp.ps1 -Remetente "+5598991438754" -Mensagem "menu"
```

Curl (JSON webhook):

```bash
curl -X POST "http://127.0.0.1:8000/webhook/whatsapp?token=SEU_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"remetente\":\"+5598991438754\",\"mensagem\":\"caixa eur\"}"
```

## Segurança e Observabilidade

- Token obrigatório em webhook
- Controle de autorização por telefone ativo
- Restrição de atualização de taxa para admin
- Logs de entrada/saída e erros em banco
- Idempotência por `provider_message_id`

## Roadmap Recomendado

- Estorno formal (ao invés de edição direta) para trilha contábil
- Fechamento diário por moeda (abertura/entradas/saídas/fechamento)
- Assinatura HMAC para webhook Twilio
- Testes automatizados (unitário + integração)
- Observabilidade estruturada (métricas, tracing)

---

## English Quick Guide

### What This Project Does

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
uvicorn main:app --reload --host 127.0.0.1 --port 8000
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
