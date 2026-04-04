# Matriz de Governanca - Fluxo de Negocio (Ouro + Cambio)

Objetivo: mapear o fluxo operacional real, definir pontos de decisao humana e transformar regras de negocio em controles claros para o sistema.

Como usar:
1. Preencha em ordem, do fluxo mais frequente para os casos de excecao.
2. Registre exemplos reais (ultimas 2-4 semanas) para cada etapa critica.
3. Marque o que ja esta implementado no sistema e o que ainda depende de processo humano.

---

## 1) Escopo operacional

- Unidade/loja: [PREENCHER]
- Responsavel operacional: [PREENCHER]
- Responsavel financeiro: [PREENCHER]
- Responsavel compliance: [PREENCHER]
- Canais de entrada (WhatsApp/balcao/outros): WhatsApp (implementado), balcao (processo operacional)
- Horario de operacao: [PREENCHER]

Produtos/operacoes principais:
- Compra de ouro: Implementado no fluxo guiado
- Venda de ouro: Implementado no fluxo guiado
- Cambio fiat-fiat: Implementado como tipo de operacao
- Cambio ligado a ouro: Parcial (depende de regra operacional detalhada)

---

## 2) Fluxo ponta a ponta (estado atual)

Preencha um fluxo por operacao.

### 2.1 Compra de ouro (cliente vende para a casa)

1. Gatilho de entrada: mensagem "compra" no WhatsApp
2. Dados coletados no atendimento: tipo_operacao, origem, teor, peso, preco, moedas/pagamentos, pessoa, forma_pagamento, observacoes
3. Formacao de preco: preco_usd e/ou preco por moeda no fluxo guiado
4. Validacoes antes de confirmar: campos obrigatorios, peso > 0, preco valido, pagamentos consistentes
5. Forma de pagamento ao cliente: dinheiro/transferencia/cheque/misto
6. Registro em sistema: gold_transaction + transacao + log de confirmacao
7. Conferencia de caixa/moeda: reconciliacao por moeda e caixa XAU com analise multiagente
8. Fechamento/arquivamento: comprovante com ID e trilha em logs/runs multiagente

Riscos observados nesse fluxo:
- Diferenca entre total informado e soma de pagamentos
- Caixa projetado negativo (XAU ou moeda)
- Preco/peso invalido

### 2.2 Venda de ouro (cliente compra da casa)

1. Gatilho de entrada: mensagem "venda" no WhatsApp
2. Dados coletados no atendimento: mesmos campos do fluxo de compra
3. Formacao de preco: preco_usd e/ou preco em moeda-base
4. Validacoes antes de confirmar: consistencia de pagamento, risco de diferenca, validacao de caixa
5. Forma de recebimento do cliente: dinheiro/transferencia/cheque/misto
6. Registro em sistema: persistencia de operacao e logs
7. Conferencia de caixa/moeda: impacto em XAU e moeda(s) de recebimento
8. Fechamento/arquivamento: comprovante + trilha de auditoria

Riscos observados nesse fluxo:
- Venda com estoque XAU insuficiente
- Diferenca relativa elevada na reconciliacao
- Alertas de anomalia por historico

### 2.3 Cambio

1. Gatilho de entrada: mensagem de cambio/troca ou tipo_operacao="cambio"
2. Dados coletados no atendimento: moedas envolvidas, valores, taxa aplicada, forma de pagamento
3. Definicao da taxa: [PREENCHER REGRA DE NEGOCIO]
4. Validacoes antes de confirmar: consistencia de valores/taxa e saldo por moeda
5. Liquidação (entrada/saida de moeda): movimentacao de duas moedas no caixa
6. Registro em sistema: operacao persistida + logs
7. Conferencia de caixa por moeda: reconciliacao por moeda com impacto separado
8. Fechamento/arquivamento: comprovante + rastreio da taxa utilizada

Riscos observados nesse fluxo:
- Taxa fora da politica operacional
- Falta de saldo na moeda de entrega
- Erro de conversao entre moedas

---

## 3) Pontos de decisao humana (maker-checker)

Regra: o que pode ser automatico e o que precisa de aprovacao.

| Etapa | Pode automatizar? | Exige operador? | Exige supervisor? | Motivo |
|---|---|---|---|---|
| Cadastro da operacao | Parcial | Sim | Nao | Fluxo guiado exige dados informados pelo operador/atendente |
| Confirmacao final | Nao | Sim | [PREENCHER] | Confirmacao transacional atual e' manual |
| Diferenca acima do limite | Nao | Sim | Recomendado | Sistema alerta, supervisor ainda depende de processo |
| Valor alto (ticket alto) | [PREENCHER] | Sim | Recomendado | Falta threshold formal de aprovacao |
| Cambio fora da banda | [PREENCHER] | Sim | Recomendado | Falta banda formal parametrizada |
| Operacao com alerta de fraude | Nao | Sim | Recomendado | Anomalia detectada, aprovacao formal ainda nao bloqueia |

---

## 4) Matriz de risco por tipo de operacao

Definir limites e resposta operacional.

| Tipo de operacao | Limite normal | Limite de alerta | Limite de bloqueio | Acao esperada |
|---|---|---|---|---|
| Compra ouro |  |  |  |  |
| Venda ouro |  |  |  |  |
| Cambio |  |  |  |  |
| Cambio + ouro |  |  |  |  |

Observacoes:
- Diferenca absoluta (USD): ja existe limite global no sistema (parametrizavel)
- Diferenca relativa (%): sistema marca alerta para diferenca relativa alta
- Limite por operador: existe limite dinamico por historico (precisa governanca formal)
- Limite por cliente: [PREENCHER]

---

## 5) Compliance minimo (AML/KYC operacional)

| Controle | Regra de negocio | Dono da regra | Evidencia obrigatoria |
|---|---|---|---|
| Identificacao do cliente |  |  |  |
| Limite diario por cliente |  |  |  |
| Fracionamento de operacoes |  |  |  |
| Origem de fundos |  |  |  |
| Lista restritiva/observacao |  |  |  |

---

## 6) Excecoes e tratativas

| Excecao | Sinal de deteccao | Quem decide | Prazo maximo | Como registrar |
|---|---|---|---|---|
| Caixa negativo projetado |  |  |  |  |
| Falha de conciliacao |  |  |  |  |
| Divergencia de taxa/preco |  |  |  |  |
| Falha de sistema |  |  |  |  |

---

## 7) KPIs de operacao e risco

KPIs diarios:
- Numero de operacoes:
- Volume por moeda:
- Diferenca acumulada (abs e %):
- Taxa de alertas de risco:
- Taxa de operacoes em revisao:
- Tempo medio de aprovacao supervisor:

KPIs semanais:
- Tendencia de risco por operador:
- Tendencia de risco por tipo de operacao:
- Reincidencia de excecoes:

---

## 8) Priorizacao de implementacao (30-60-90 dias)

### 0-30 dias (ganho rapido)
- Formalizar politica de taxa de cambio e thresholds de ticket alto
- Definir matriz de aprovacao supervisor (quando obrigatoria)
- Congelar valores padrao de risco para operacao diaria

### 31-60 dias (controle transacional)
- Gate de aprovacao antes da gravacao final em operacoes de risco
- Estado transacional de pendencia/aprovacao/rejeicao
- Alertas ativos para excecoes criticas

### 61-90 dias (governanca avancada)
- Camada AML/KYC operacional minima por cliente
- Score de risco por cliente/operador/tipo de operacao
- Trilha de auditoria reforcada com evidencias obrigatorias

---

## 9) Mapa regra -> sistema

Use esta secao para transformar processo em backlog tecnico.

| Regra de negocio | Endpoint/fluxo impactado | Campo(s) | Comportamento esperado | Prioridade |
|---|---|---|---|---|
| Diferenca acima de limite exige aprovacao | /webhook/whatsapp fluxo guiado | diferenca_usd, total_pago_usd | Nao liquidar sem aprovacao supervisor | Alta |
| Cambio fora da banda exige revisao | fluxo cambio | taxa, moeda_origem, moeda_destino | Bloquear confirmacao ate revisao | Alta |
| Ticket alto exige maker-checker | fluxo compra/venda/cambio | total_usd | Marcar pendente e exigir aprovacao | Media |

---

## 10) Checklist de aprovacao final

- [ ] Fluxos principais mapeados
- [ ] Limites de risco aprovados
- [ ] Pontos de aprovacao humana definidos
- [ ] Excecoes e SLA definidos
- [ ] KPIs acordados
- [ ] Backlog tecnico priorizado

Responsavel pela aprovacao:
Data:
Versao:
