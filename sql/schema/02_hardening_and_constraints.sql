-- =============================
-- HARDENING / ENTERPRISE RULES
-- =============================

-- ======================================
-- MIGRACAO: moedas e cambio em transacoes
-- ======================================
-- Convencao cambio_para_usd: "1 USD = X moeda"
-- Exemplos: USD=1.0, SRD=38, BRL=5.20, EUR=0.877

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'transacoes' AND column_name = 'moeda_liquidacao'
    ) THEN
        ALTER TABLE transacoes
            ADD COLUMN moeda_liquidacao VARCHAR(10) NOT NULL DEFAULT 'USD',
            ADD COLUMN valor_moeda      NUMERIC(18,6),
            ADD COLUMN cambio_para_usd  NUMERIC(18,6) NOT NULL DEFAULT 1.0;
    END IF;
END$$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'chk_transacoes_moeda_liquidacao'
    ) THEN
        ALTER TABLE transacoes
            ADD CONSTRAINT chk_transacoes_moeda_liquidacao
            CHECK (moeda_liquidacao IN ('USD', 'EUR', 'SRD', 'BRL'));
    END IF;
END$$;

CREATE INDEX IF NOT EXISTS idx_transacoes_moeda_liquidacao
    ON transacoes (moeda_liquidacao, data_hora DESC);

-- 1) Constraints adicionais para consistencia de dados
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_name = 'multi_agent_runs' AND column_name = 'operation_kind'
    ) THEN
        ALTER TABLE multi_agent_runs ADD COLUMN operation_kind VARCHAR(30);
    END IF;
END$$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'chk_usuarios_telefone_e164'
    ) THEN
        ALTER TABLE usuarios
            ADD CONSTRAINT chk_usuarios_telefone_e164
            CHECK (telefone ~ '^\+[1-9][0-9]{7,14}$');
    END IF;
END$$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'usuarios' AND column_name = 'web_pin_hash'
    ) THEN
        ALTER TABLE usuarios ADD COLUMN web_pin_hash VARCHAR(255);
    END IF;
END$$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'usuarios' AND column_name = 'web_pin_updated_em'
    ) THEN
        ALTER TABLE usuarios ADD COLUMN web_pin_updated_em TIMESTAMPTZ;
    END IF;
END$$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'chk_logs_nivel'
    ) THEN
        ALTER TABLE logs
            ADD CONSTRAINT chk_logs_nivel
            CHECK (nivel IN ('debug', 'info', 'warning', 'error', 'critical'));
    END IF;
END$$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'chk_mensagens_status_code'
    ) THEN
        ALTER TABLE mensagens_processadas
            ADD CONSTRAINT chk_mensagens_status_code
            CHECK (status_code BETWEEN 100 AND 599);
    END IF;
END$$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_name = 'gold_transactions' AND column_name = 'status'
    ) THEN
        ALTER TABLE gold_transactions ADD COLUMN status VARCHAR(20) NOT NULL DEFAULT 'registrada';
    END IF;

    UPDATE gold_transactions
    SET status = 'registrada'
    WHERE status IS NULL;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'chk_gold_transactions_tipo_operacao'
    ) THEN
        ALTER TABLE gold_transactions
            ADD CONSTRAINT chk_gold_transactions_tipo_operacao
            CHECK (tipo_operacao IN ('compra', 'venda', 'cambio'));
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'chk_gold_transactions_origem'
    ) THEN
        ALTER TABLE gold_transactions
            ADD CONSTRAINT chk_gold_transactions_origem
            CHECK (origem IN ('balcao', 'fora'));
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'chk_gold_transactions_gold_type'
    ) THEN
        ALTER TABLE gold_transactions
            ADD CONSTRAINT chk_gold_transactions_gold_type
            CHECK (gold_type IN ('fundido', 'queimado'));
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'chk_gold_transactions_fechamento_tipo'
    ) THEN
        ALTER TABLE gold_transactions
            ADD CONSTRAINT chk_gold_transactions_fechamento_tipo
            CHECK (fechamento_tipo IN ('total', 'parcial'));
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'chk_gold_transactions_forma_pagamento'
    ) THEN
        ALTER TABLE gold_transactions
            ADD CONSTRAINT chk_gold_transactions_forma_pagamento
            CHECK (forma_pagamento IN ('dinheiro', 'transferencia', 'cheque', 'misto'));
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'chk_gold_transactions_teor'
    ) THEN
        ALTER TABLE gold_transactions
            ADD CONSTRAINT chk_gold_transactions_teor
            CHECK (teor >= 0 AND teor <= 99.99);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'chk_gold_transactions_quebra'
    ) THEN
        ALTER TABLE gold_transactions
            ADD CONSTRAINT chk_gold_transactions_quebra
            CHECK (quebra IS NULL OR (quebra >= 0 AND quebra <= 100));
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'chk_gold_transactions_positive_values'
    ) THEN
        ALTER TABLE gold_transactions
            ADD CONSTRAINT chk_gold_transactions_positive_values
            CHECK (
                peso > 0
                AND preco_usd > 0
                AND total_usd >= 0
                AND total_pago_usd >= 0
                AND fechamento_gramas >= 0
            );
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'chk_gold_transactions_fechamento_le_peso'
    ) THEN
        ALTER TABLE gold_transactions
            ADD CONSTRAINT chk_gold_transactions_fechamento_le_peso
            CHECK (fechamento_gramas <= peso);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'chk_gold_transactions_status'
    ) THEN
        ALTER TABLE gold_transactions
            ADD CONSTRAINT chk_gold_transactions_status
            CHECK (status IN ('registrada', 'cancelada'));
    END IF;
END$$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'chk_gold_payments_moeda'
    ) THEN
        ALTER TABLE gold_payments
            ADD CONSTRAINT chk_gold_payments_moeda
            CHECK (moeda IN ('USD', 'SRD', 'EUR', 'BRL'));
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'chk_gold_payments_forma_pagamento'
    ) THEN
        ALTER TABLE gold_payments
            ADD CONSTRAINT chk_gold_payments_forma_pagamento
            CHECK (forma_pagamento IN ('dinheiro', 'transferencia', 'cheque', 'misto'));
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'chk_gold_payments_positive_values'
    ) THEN
        ALTER TABLE gold_payments
            ADD CONSTRAINT chk_gold_payments_positive_values
            CHECK (valor_moeda >= 0 AND cambio_para_usd > 0 AND valor_usd >= 0);
    END IF;
END$$;

-- 2) Indices de performance para consultas operacionais
CREATE INDEX IF NOT EXISTS idx_gold_transactions_operador_criado_em
    ON gold_transactions (operador_id, criado_em DESC);

CREATE INDEX IF NOT EXISTS idx_gold_transactions_tipo_criado_em
    ON gold_transactions (tipo_operacao, criado_em DESC);

CREATE INDEX IF NOT EXISTS idx_gold_payments_moeda_criado_em
    ON gold_payments (moeda, criado_em DESC);

CREATE INDEX IF NOT EXISTS idx_logs_warning_data_hora
    ON logs (data_hora DESC)
    WHERE nivel = 'warning';