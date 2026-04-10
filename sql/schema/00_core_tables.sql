-- PostgreSQL DDL para o MVP de Caixa Inteligente via WhatsApp

CREATE EXTENSION IF NOT EXISTS pgcrypto;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'tipo_ativo') THEN
        CREATE TYPE tipo_ativo AS ENUM ('ouro', 'moeda');
    END IF;
END$$;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'tipo_operacao') THEN
        CREATE TYPE tipo_operacao AS ENUM ('compra', 'venda', 'cambio');
    END IF;
END$$;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'tipo_usuario') THEN
        CREATE TYPE tipo_usuario AS ENUM ('admin', 'operador');
    END IF;
END$$;

CREATE TABLE IF NOT EXISTS ativos (
    id BIGSERIAL PRIMARY KEY,
    nome VARCHAR(100) NOT NULL UNIQUE,
    tipo tipo_ativo NOT NULL,
    criado_em TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS usuarios (
    id BIGSERIAL PRIMARY KEY,
    nome VARCHAR(120) NOT NULL,
    telefone VARCHAR(30) NOT NULL UNIQUE,
    tipo_usuario tipo_usuario NOT NULL,
    web_pin_hash VARCHAR(255),
    web_pin_updated_em TIMESTAMPTZ,
    ativo BOOLEAN NOT NULL DEFAULT TRUE,
    criado_em TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS clientes (
    id BIGSERIAL PRIMARY KEY,
    nome VARCHAR(150) NOT NULL,
    apelido VARCHAR(120),
    telefone VARCHAR(30),
    documento VARCHAR(40),
    observacoes TEXT,
    ativo BOOLEAN NOT NULL DEFAULT TRUE,
    criado_em TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    atualizado_em TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS taxas_diarias (
    id BIGSERIAL PRIMARY KEY,
    ativo_id BIGINT NOT NULL REFERENCES ativos(id),
    preco_compra NUMERIC(18, 6) NOT NULL CHECK (preco_compra >= 0),
    preco_venda NUMERIC(18, 6) NOT NULL CHECK (preco_venda >= 0),
    admin_id VARCHAR(100) NOT NULL,
    data_atualizacao TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS transacoes (
    id BIGSERIAL PRIMARY KEY,
    data_hora TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    tipo_operacao tipo_operacao NOT NULL,
    ativo_id BIGINT NOT NULL REFERENCES ativos(id),
    quantidade NUMERIC(18, 6) NOT NULL CHECK (quantidade > 0),
    cotacao_usada NUMERIC(18, 6) NOT NULL CHECK (cotacao_usada >= 0),
    valor_total NUMERIC(18, 6) NOT NULL CHECK (valor_total >= 0),
    operador_id VARCHAR(100) NOT NULL,
    source_message_id VARCHAR(120),
    status VARCHAR(50) NOT NULL
);

CREATE TABLE IF NOT EXISTS logs (
    id BIGSERIAL PRIMARY KEY,
    data_hora TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    remetente VARCHAR(30),
    mensagem_recebida TEXT,
    resposta_enviada TEXT,
    nivel VARCHAR(20) NOT NULL,
    contexto JSONB,
    erro TEXT
);

CREATE TABLE IF NOT EXISTS mensagens_processadas (
    id BIGSERIAL PRIMARY KEY,
    provider_message_id VARCHAR(120) NOT NULL UNIQUE,
    remetente VARCHAR(30) NOT NULL,
    mensagem_recebida TEXT NOT NULL,
    resposta_payload JSONB NOT NULL,
    status_code INTEGER NOT NULL,
    criado_em TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS sessoes_conversa (
    id BIGSERIAL PRIMARY KEY,
    remetente VARCHAR(30) NOT NULL UNIQUE,
    estado VARCHAR(60) NOT NULL,
    contexto JSONB NOT NULL DEFAULT '{}'::jsonb,
    atualizado_em TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS gold_transactions (
    id BIGSERIAL PRIMARY KEY,
    cliente_id BIGINT REFERENCES clientes(id),
    tipo_operacao VARCHAR(20) NOT NULL,
    origem VARCHAR(20) NOT NULL,
    gold_type VARCHAR(20) NOT NULL,
    quebra NUMERIC(10, 4),
    teor NUMERIC(10, 4) NOT NULL,
    peso NUMERIC(18, 6) NOT NULL,
    preco_usd NUMERIC(18, 6) NOT NULL,
    total_usd NUMERIC(18, 6) NOT NULL,
    total_pago_usd NUMERIC(18, 6) NOT NULL,
    diferenca_usd NUMERIC(18, 6) NOT NULL,
    fechamento_gramas NUMERIC(18, 6) NOT NULL,
    fechamento_tipo VARCHAR(20) NOT NULL,
    pessoa VARCHAR(150) NOT NULL,
    forma_pagamento VARCHAR(30) NOT NULL,
    observacoes TEXT,
    operador_id VARCHAR(100) NOT NULL,
    source_message_id VARCHAR(120),
    status VARCHAR(20) NOT NULL DEFAULT 'registrada',
    contexto JSONB NOT NULL DEFAULT '{}'::jsonb,
    criado_em TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS cliente_movimentacoes (
    id BIGSERIAL PRIMARY KEY,
    cliente_id BIGINT NOT NULL REFERENCES clientes(id) ON DELETE CASCADE,
    gold_transaction_id BIGINT REFERENCES gold_transactions(id) ON DELETE SET NULL,
    moeda VARCHAR(10) NOT NULL,
    tipo_movimento VARCHAR(40) NOT NULL,
    valor NUMERIC(20, 8) NOT NULL,
    descricao TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    criado_em TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_cliente_mov_moeda CHECK (moeda IN ('XAU', 'USD', 'EUR', 'SRD', 'BRL'))
);

CREATE TABLE IF NOT EXISTS gold_payments (
    id BIGSERIAL PRIMARY KEY,
    gold_transaction_id BIGINT NOT NULL REFERENCES gold_transactions(id) ON DELETE CASCADE,
    moeda VARCHAR(10) NOT NULL,
    valor_moeda NUMERIC(18, 6) NOT NULL,
    cambio_para_usd NUMERIC(18, 6) NOT NULL,
    valor_usd NUMERIC(18, 6) NOT NULL,
    forma_pagamento VARCHAR(30) NOT NULL,
    criado_em TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Caixa segregado por moeda/commodity (sem referencia unica em USD)
CREATE TABLE IF NOT EXISTS caixas (
    id BIGSERIAL PRIMARY KEY,
    moeda VARCHAR(10) NOT NULL UNIQUE,
    saldo NUMERIC(20, 8) NOT NULL DEFAULT 0,
    atualizado_em TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    criado_em TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_caixas_moeda CHECK (moeda IN ('XAU', 'USD', 'EUR', 'SRD', 'BRL'))
);

INSERT INTO caixas (moeda, saldo)
VALUES
    ('XAU', 0),
    ('USD', 0),
    ('EUR', 0),
    ('SRD', 0),
    ('BRL', 0)
ON CONFLICT (moeda) DO NOTHING;

CREATE TABLE IF NOT EXISTS caixas_movimentacoes (
    id BIGSERIAL PRIMARY KEY,
    caixa_moeda VARCHAR(10) NOT NULL,
    tipo_operacao VARCHAR(30) NOT NULL,
    gold_transaction_id BIGINT REFERENCES gold_transactions(id),
    valor NUMERIC(20, 8) NOT NULL,
    saldo_anterior NUMERIC(20, 8) NOT NULL,
    saldo_posterior NUMERIC(20, 8) NOT NULL,
    descricao TEXT,
    pessoa TEXT,
    criado_em TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT fk_caixas_movimentacoes_caixa_moeda
        FOREIGN KEY (caixa_moeda) REFERENCES caixas(moeda),
    CONSTRAINT chk_caixas_mov_tipo_operacao
        CHECK (tipo_operacao IN ('compra', 'venda', 'ajuste', 'adiantamento', 'devolucao'))
);