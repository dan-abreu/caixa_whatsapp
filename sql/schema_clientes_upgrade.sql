-- Customer registry upgrade (non-breaking)
-- Safe to run after schema.sql
-- Adds customer master data, customer ledger, and links gold transactions to customers.

BEGIN;

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

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.tables
        WHERE table_name = 'gold_transactions'
    ) THEN
        ALTER TABLE gold_transactions
            ADD COLUMN IF NOT EXISTS cliente_id BIGINT REFERENCES clientes(id);
    END IF;
END$$;

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

CREATE INDEX IF NOT EXISTS idx_clientes_nome
    ON clientes (LOWER(nome));

CREATE INDEX IF NOT EXISTS idx_clientes_documento
    ON clientes (documento);

CREATE INDEX IF NOT EXISTS idx_clientes_telefone
    ON clientes (telefone);

CREATE INDEX IF NOT EXISTS idx_clientes_ativo_nome
    ON clientes (ativo, LOWER(nome));

CREATE INDEX IF NOT EXISTS idx_gold_transactions_cliente
    ON gold_transactions (cliente_id, criado_em DESC);

CREATE INDEX IF NOT EXISTS idx_cliente_movimentacoes_cliente
    ON cliente_movimentacoes (cliente_id, criado_em DESC);

CREATE INDEX IF NOT EXISTS idx_cliente_movimentacoes_gold_tx
    ON cliente_movimentacoes (gold_transaction_id, criado_em DESC);

COMMIT;
