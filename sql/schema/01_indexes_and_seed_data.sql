CREATE TABLE IF NOT EXISTS multi_agent_runs (
    id BIGSERIAL PRIMARY KEY,
    objective TEXT NOT NULL,
    operation_id BIGINT,
    operation_kind VARCHAR(30),
    source_message_id VARCHAR(120),
    request_payload JSONB NOT NULL,
    response_payload JSONB NOT NULL,
    criado_em TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_taxas_diarias_ativo_data
    ON taxas_diarias (ativo_id, data_atualizacao DESC);

CREATE INDEX IF NOT EXISTS idx_transacoes_data_hora
    ON transacoes (data_hora DESC);

CREATE INDEX IF NOT EXISTS idx_usuarios_telefone
    ON usuarios (telefone);

CREATE INDEX IF NOT EXISTS idx_clientes_nome
    ON clientes (LOWER(nome));

CREATE INDEX IF NOT EXISTS idx_clientes_documento
    ON clientes (documento);

CREATE INDEX IF NOT EXISTS idx_clientes_telefone
    ON clientes (telefone);

CREATE INDEX IF NOT EXISTS idx_logs_data_hora
    ON logs (data_hora DESC);

CREATE INDEX IF NOT EXISTS idx_mensagens_processadas_criado_em
    ON mensagens_processadas (criado_em DESC);

CREATE INDEX IF NOT EXISTS idx_sessoes_conversa_atualizado_em
    ON sessoes_conversa (atualizado_em DESC);

CREATE INDEX IF NOT EXISTS idx_gold_transactions_criado_em
    ON gold_transactions (criado_em DESC);

CREATE INDEX IF NOT EXISTS idx_gold_transactions_operador
    ON gold_transactions (operador_id, criado_em DESC);

CREATE INDEX IF NOT EXISTS idx_gold_transactions_cliente
    ON gold_transactions (cliente_id, criado_em DESC);

CREATE INDEX IF NOT EXISTS idx_gold_payments_transaction
    ON gold_payments (gold_transaction_id);

CREATE INDEX IF NOT EXISTS idx_cliente_movimentacoes_cliente
    ON cliente_movimentacoes (cliente_id, criado_em DESC);

CREATE INDEX IF NOT EXISTS idx_cliente_movimentacoes_gold_tx
    ON cliente_movimentacoes (gold_transaction_id, criado_em DESC);

CREATE INDEX IF NOT EXISTS idx_caixas_moeda
    ON caixas (moeda);

CREATE INDEX IF NOT EXISTS idx_caixas_mov_moeda
    ON caixas_movimentacoes (caixa_moeda, criado_em DESC);

CREATE INDEX IF NOT EXISTS idx_caixas_mov_tx_id
    ON caixas_movimentacoes (gold_transaction_id, criado_em DESC);

CREATE INDEX IF NOT EXISTS idx_multi_agent_runs_criado_em
    ON multi_agent_runs (criado_em DESC);

CREATE INDEX IF NOT EXISTS idx_multi_agent_runs_operation_id
    ON multi_agent_runs (operation_id, criado_em DESC);

CREATE INDEX IF NOT EXISTS idx_multi_agent_runs_operation_kind
    ON multi_agent_runs (operation_kind, criado_em DESC);

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_name = 'transacoes' AND column_name = 'source_message_id'
    ) THEN
        CREATE UNIQUE INDEX IF NOT EXISTS uq_transacoes_source_message_id
            ON transacoes (source_message_id)
            WHERE source_message_id IS NOT NULL;
    END IF;
END$$;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_name = 'gold_transactions' AND column_name = 'cliente_id'
    ) THEN
        CREATE INDEX IF NOT EXISTS idx_gold_transactions_cliente_runtime
            ON gold_transactions (cliente_id, criado_em DESC);
    END IF;
END$$;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_name = 'gold_transactions' AND column_name = 'source_message_id'
    ) THEN
        CREATE UNIQUE INDEX IF NOT EXISTS uq_gold_transactions_source_message_id
            ON gold_transactions (source_message_id)
            WHERE source_message_id IS NOT NULL;
    END IF;
END$$;

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

INSERT INTO ativos (nome, tipo)
VALUES
    ('Ouro 24k', 'ouro'),
    ('USD', 'moeda'),
    ('EUR', 'moeda'),
    ('SRD', 'moeda'),
    ('BRL', 'moeda')
ON CONFLICT (nome) DO NOTHING;

INSERT INTO usuarios (nome, telefone, tipo_usuario)
VALUES
    ('Administrador', '+59700000000', 'admin'),
    ('Operador 1', '+59711111111', 'admin'),
    ('Operador Teste 2', '+5978145515', 'admin'),
    ('Operador Teste 3', '+5978967488', 'admin')
ON CONFLICT (telefone) DO NOTHING;