# Caixa Inteligente via WhatsApp (MVP)

Backend em Python + FastAPI para registrar operações de caixa (ouro e câmbio) a partir de mensagens recebidas por webhook.

## Objetivo

O sistema recebe mensagens de texto (simulando WhatsApp), usa IA para extrair dados estruturados e executa as regras de negócio no backend:

- Atualização de taxa por admin
- Registro de operação por funcionário

A IA não realiza cálculo financeiro. Todos os cálculos são feitos no backend com Decimal.

## Stack

- Python
- FastAPI
- Supabase (PostgreSQL)
- Google Gemini API

## Estrutura

- schema.sql: DDL do banco + seed de ativos e usuários padrão
- database.py: acesso a dados via supabase-py
- ai_service.py: integração com Gemini + extração de JSON
- main.py: API FastAPI e regras de negócio
- simulate_whatsapp.py: script de simulação local do webhook
- setup.ps1: setup automatizado do ambiente no Windows
- run.ps1: execução automatizada da API no Windows
- invoke_whatsapp.ps1: envio de mensagem de teste via PowerShell
- .env.example: exemplo de configuração
- requirements.txt: dependências do projeto

## Pré-requisitos

- Python 3.11 a 3.13
- Projeto Supabase com acesso ao banco
- Chave de API do Gemini

Observação para Windows:

- Evite Python 3.14 neste projeto por enquanto, porque algumas dependências ainda podem cair em build nativo sem wheel pronta.
- Se a instalação falhar com erro de compilação no Windows, recrie a virtualenv com Python 3.12, que é a opção mais estável hoje para este stack.

## Configuração

1. Criar e ativar ambiente virtual

Windows PowerShell:

py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1

Se você já estiver com uma `.venv` quebrada, remova a pasta e recrie antes de instalar as dependências.

Alternativa genérica:

python -m venv .venv
.\.venv\Scripts\Activate.ps1

Atalho recomendado no Windows:

```powershell
.\setup.ps1 -RecreateVenv
```

1. Instalar dependências

pip install -r requirements.txt

1. Criar arquivo de ambiente

Copie o arquivo .env.example para .env e preencha os valores.

Variáveis obrigatórias:

- SUPABASE_URL
- SUPABASE_SERVICE_ROLE_KEY (ou SUPABASE_KEY)
- GEMINI_API_KEY
- WEBHOOK_TOKEN

Opcional:

- GEMINI_MODEL (padrão: gemini-1.5-flash)
- APP_HOST (padrão recomendado: 127.0.0.1)
- APP_PORT (padrão recomendado: 8000)
- LOG_LEVEL (padrão recomendado: INFO)
- SUPABASE_DB_URL (para aplicar schema via psql)
- SUPABASE_PROJECT_REF (alternativa para montar a URL SQL)
- SUPABASE_DB_PASSWORD (alternativa para montar a URL SQL)
- MULTI_AGENT_AUTO_ENABLED (padrão: true)
- MULTI_AGENT_AUTO_MIN_USD (padrão: 500)
- MULTI_AGENT_AUTO_MIN_WEIGHT_GRAMS (padrão: 10)

## Banco de dados

Execute o conteúdo de schema.sql no PostgreSQL do Supabase.

Se você tiver acesso SQL ao banco, também pode aplicar com o atalho:

```powershell
.\apply_schema.ps1
```

Esse script usa `SUPABASE_DB_URL` ou monta a URL a partir de `SUPABASE_PROJECT_REF` + `SUPABASE_DB_PASSWORD`.

O script cria:

- tipo_ativo (ouro, moeda)
- tipo_operacao (compra, venda, cambio)
- tipo_usuario (admin, operador)
- tabelas: ativos, usuarios, taxas_diarias, transacoes, logs
- tabelas enterprise adicionais: mensagens_processadas, sessoes_conversa, gold_transactions, gold_payments, multi_agent_runs, gold_audit_log, transaction_history
- índices de consulta
- seed idempotente de ativos: Ouro 24k, USD, EUR, SRD
- seed idempotente de usuários: Administrador e Operador 1

## Executando a API

uvicorn main:app --reload --host 127.0.0.1 --port 8000

Atalho recomendado no Windows:

```powershell
.\run.ps1
```

Health check:

GET /health

Análise multiagente manual:

POST /ai/multi-agent/analyze

Histórico recente de análises multiagente:

GET /ai/multi-agent/runs?limit=10

Webhook principal:

POST /webhook/whatsapp

Autenticação aceita:

- Header X-Webhook-Token com valor igual ao WEBHOOK_TOKEN
- Ou query string ?token=<WEBHOOK_TOKEN>

Observação de produção:

- Em Railway + Pipedream, mantenha o mesmo valor de WEBHOOK_TOKEN configurado na Railway e usado na URL ou header do passo HTTP do Pipedream.
- Não grave o token real em arquivos versionados do repositório.

Body JSON:

{
  "remetente": "+59700000000",
  "mensagem": "Taxa ouro 68.50"
}

## Fluxos

Fluxo A - Atualizar taxa (admin)

- Remetente deve existir em usuarios e ter tipo_usuario=admin
- Exemplo de mensagem: Taxa ouro 68.50
- IA extrai intenção e variáveis
- Backend grava em taxas_diarias

Exemplo de retorno:

{
  "mensagem": "Taxa do ouro atualizada para 68.50",
  "dados": {
    "intencao": "atualizar_taxa",
    "ativo": "Ouro 24k",
    "taxa": "68.50"
  }
}

Fluxo B - Registrar operação (funcionário)

- Remetente deve existir em usuarios e estar ativo
- Exemplo de mensagem: Comprei 10g de ouro
- IA extrai intenção, ativo e quantidade
- Backend pergunta o preço por grama em USD
- Backend pergunta a moeda da liquidação (USD, EUR, SRD ou BRL)
- Se a moeda não for USD, backend pergunta o câmbio manual no formato: 1 USD = X moeda
- Backend calcula total com Decimal e grava a operação com moeda_liquidacao, valor_moeda e cambio_para_usd
- Operações com risco, peso alto, valor alto, venda ou câmbio podem disparar análise multiagente automática
- Quando disparada, a resposta inclui dados.analise_multiagente com resumo, decisões, riscos e recomendações

Exemplo do fluxo rápido no WhatsApp:

- Usuário: comprei 5g
- Bot: Qual o preço por grama em USD para essa compra de 5g?
- Usuário: 65
- Bot: Em qual moeda foi liquidado? USD / EUR / SRD / BRL
- Usuário: SRD
- Bot: Qual o câmbio? (1 USD = quantos SRD)
- Usuário: 38
- Bot: confirma a operação com total USD e total em SRD

## Simulação local de WhatsApp

Exemplo com script Python:

python simulate_whatsapp.py --remetente +59711111111 --mensagem "Comprei 10g de ouro" --token seu-token

Exemplo com PowerShell:

```powershell
.\invoke_whatsapp.ps1 -Remetente "+59711111111" -Mensagem "Comprei 10g de ouro"
```

Exemplo com curl:

```bash
curl -X POST "http://127.0.0.1:8000/webhook/whatsapp" \
  -H "Content-Type: application/json" \
  -H "X-Webhook-Token: seu-token" \
  -d "{\"remetente\":\"+59711111111\",\"mensagem\":\"Comprei 10g de ouro\"}"
```

Exemplo em produção com Railway:

```bash
curl -X POST "https://SEU-APP.railway.app/webhook/whatsapp?token=seu-token" \
  -H "Content-Type: application/json" \
  -d "{\"remetente\":\"+59711111111\",\"mensagem\":\"extrato\"}"
```

Integração recomendada Twilio + Pipedream + Railway:

- Twilio recebe a mensagem WhatsApp
- Twilio chama o webhook do Pipedream
- Pipedream envia POST para Railway em /webhook/whatsapp
- Railway responde com JSON contendo campo mensagem
- Pipedream envia esse texto de volta ao WhatsApp usando a API da Twilio

Exemplo de retorno:

{
  "mensagem": "Compra registrada. 10 x 68.50 = 685.00.",
  "dados": {
    "intencao": "registrar_operacao",
    "tipo_operacao": "compra",
    "ativo": "Ouro 24k",
    "quantidade": "10",
    "cotacao_usada": "68.50",
    "valor_total": "685.00"
  }
}

## Segurança mínima atual

- Token no header X-Webhook-Token
- Restrição de atualização de taxa ao perfil admin
- Lista de remetentes autorizados em usuarios

## Auditoria e erros

- Todas as entradas/saídas relevantes são registradas em logs
- Erros de IA e de validação de contrato também são auditados
- Execuções multiagente ficam disponíveis em /ai/multi-agent/runs e usam fallback em logs caso a tabela multi_agent_runs ainda não tenha sido migrada

## Próximos passos recomendados

- Adicionar testes automatizados (unitários e integração)
- Registrar logs estruturados e observabilidade
- Adicionar rate limit no webhook
- Implementar autenticação mais robusta (assinatura HMAC)
