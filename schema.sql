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
    ativo BOOLEAN NOT NULL DEFAULT TRUE,
    criado_em TIMESTAMPTZ NOT NULL DEFAULT NOW()
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
    contexto JSONB NOT NULL DEFAULT '{}'::jsonb,
    criado_em TIMESTAMPTZ NOT NULL DEFAULT NOW()
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

CREATE INDEX IF NOT EXISTS idx_gold_payments_transaction
    ON gold_payments (gold_transaction_id);

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
        WHERE table_name = 'gold_transactions' AND column_name = 'source_message_id'
    ) THEN
        CREATE UNIQUE INDEX IF NOT EXISTS uq_gold_transactions_source_message_id
            ON gold_transactions (source_message_id)
            WHERE source_message_id IS NOT NULL;
    END IF;
END$$;

INSERT INTO ativos (nome, tipo)
VALUES
    ('Ouro 24k', 'ouro'),
    ('USD', 'moeda'),
    ('EUR', 'moeda'),
    ('SRD', 'moeda')
ON CONFLICT (nome) DO NOTHING;

INSERT INTO usuarios (nome, telefone, tipo_usuario)
VALUES
    ('Administrador', '+59700000000', 'admin'),
    ('Operador 1', '+59711111111', 'operador')
ON CONFLICT (telefone) DO NOTHING;

-- =============================
-- HARDENING / ENTERPRISE RULES
-- =============================

-- ======================================
-- MIGRAÇÃO: moedas e câmbio em transacoes
-- ======================================
-- Convenção cambio_para_usd: "1 USD = X moeda"
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

-- 1) Constraints adicionais para consistência de dados
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

-- 2) Índices de performance para consultas operacionais
CREATE INDEX IF NOT EXISTS idx_gold_transactions_operador_criado_em
    ON gold_transactions (operador_id, criado_em DESC);

CREATE INDEX IF NOT EXISTS idx_gold_transactions_tipo_criado_em
    ON gold_transactions (tipo_operacao, criado_em DESC);

CREATE INDEX IF NOT EXISTS idx_gold_payments_moeda_criado_em
    ON gold_payments (moeda, criado_em DESC);

CREATE INDEX IF NOT EXISTS idx_logs_warning_data_hora
    ON logs (data_hora DESC)
    WHERE nivel = 'warning';

-- 3) Trigger para validar consistência transacional em gold_transactions
CREATE OR REPLACE FUNCTION fn_validate_gold_transaction()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    expected_total NUMERIC(18,6);
BEGIN
    expected_total := ROUND((NEW.peso * NEW.preco_usd)::numeric, 6);

    IF ROUND(NEW.total_usd::numeric, 6) <> expected_total THEN
        RAISE EXCEPTION 'total_usd inconsistente: esperado %, recebido %', expected_total, NEW.total_usd;
    END IF;

    IF NEW.gold_type = 'fundido' AND NEW.quebra IS NOT NULL AND NEW.quebra <> 0 THEN
        RAISE EXCEPTION 'gold_type fundido não pode ter quebra diferente de 0';
    END IF;

    IF NEW.gold_type = 'queimado' AND NEW.quebra IS NULL THEN
        RAISE EXCEPTION 'gold_type queimado exige quebra';
    END IF;

    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_validate_gold_transaction ON gold_transactions;
CREATE TRIGGER trg_validate_gold_transaction
BEFORE INSERT OR UPDATE ON gold_transactions
FOR EACH ROW
EXECUTE FUNCTION fn_validate_gold_transaction();

-- 4) Trigger para validar consistência em gold_payments
CREATE OR REPLACE FUNCTION fn_validate_gold_payment()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    expected_usd NUMERIC(18,6);
BEGIN
    IF NEW.moeda = 'USD' THEN
        expected_usd := ROUND(NEW.valor_moeda::numeric, 6);
        IF ROUND(NEW.valor_usd::numeric, 6) <> expected_usd THEN
            RAISE EXCEPTION 'Pagamento USD inconsistente: valor_usd deve ser igual a valor_moeda';
        END IF;
    ELSE
        expected_usd := ROUND((NEW.valor_moeda / NEW.cambio_para_usd)::numeric, 6);
        IF ROUND(NEW.valor_usd::numeric, 6) <> expected_usd THEN
            RAISE EXCEPTION 'Pagamento inconsistente: valor_usd esperado %, recebido %', expected_usd, NEW.valor_usd;
        END IF;
    END IF;

    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_validate_gold_payment ON gold_payments;
CREATE TRIGGER trg_validate_gold_payment
BEFORE INSERT OR UPDATE ON gold_payments
FOR EACH ROW
EXECUTE FUNCTION fn_validate_gold_payment();

-- 5) Trigger para recalcular total_pago_usd e diferenca_usd automaticamente
CREATE OR REPLACE FUNCTION fn_recalculate_gold_transaction_totals()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_transaction_id BIGINT;
    v_total_pago NUMERIC(18,6);
    v_total_operacao NUMERIC(18,6);
BEGIN
    v_transaction_id := COALESCE(NEW.gold_transaction_id, OLD.gold_transaction_id);

    SELECT COALESCE(SUM(valor_usd), 0)::numeric(18,6)
    INTO v_total_pago
    FROM gold_payments
    WHERE gold_transaction_id = v_transaction_id;

    SELECT total_usd
    INTO v_total_operacao
    FROM gold_transactions
    WHERE id = v_transaction_id;

    UPDATE gold_transactions
    SET total_pago_usd = v_total_pago,
        diferenca_usd = ROUND((v_total_operacao - v_total_pago)::numeric, 6)
    WHERE id = v_transaction_id;

    RETURN COALESCE(NEW, OLD);
END;
$$;

DROP TRIGGER IF EXISTS trg_recalculate_gold_transaction_totals ON gold_payments;
CREATE TRIGGER trg_recalculate_gold_transaction_totals
AFTER INSERT OR UPDATE OR DELETE ON gold_payments
FOR EACH ROW
EXECUTE FUNCTION fn_recalculate_gold_transaction_totals();

-- 6) Auditoria robusta para operações de ouro
CREATE TABLE IF NOT EXISTS gold_audit_log (
    id BIGSERIAL PRIMARY KEY,
    tabela VARCHAR(50) NOT NULL,
    registro_id BIGINT NOT NULL,
    acao VARCHAR(10) NOT NULL,
    old_data JSONB,
    new_data JSONB,
    criado_em TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_gold_audit_log_tabela_registro
    ON gold_audit_log (tabela, registro_id, criado_em DESC);

CREATE OR REPLACE FUNCTION fn_audit_gold_changes()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        INSERT INTO gold_audit_log(tabela, registro_id, acao, old_data, new_data)
        VALUES (TG_TABLE_NAME, NEW.id, TG_OP, NULL, to_jsonb(NEW));
        RETURN NEW;
    ELSIF TG_OP = 'UPDATE' THEN
        INSERT INTO gold_audit_log(tabela, registro_id, acao, old_data, new_data)
        VALUES (TG_TABLE_NAME, NEW.id, TG_OP, to_jsonb(OLD), to_jsonb(NEW));
        RETURN NEW;
    ELSE
        INSERT INTO gold_audit_log(tabela, registro_id, acao, old_data, new_data)
        VALUES (TG_TABLE_NAME, OLD.id, TG_OP, to_jsonb(OLD), NULL);
        RETURN OLD;
    END IF;
END;
$$;

DROP TRIGGER IF EXISTS trg_audit_gold_transactions ON gold_transactions;
CREATE TRIGGER trg_audit_gold_transactions
AFTER INSERT OR UPDATE OR DELETE ON gold_transactions
FOR EACH ROW
EXECUTE FUNCTION fn_audit_gold_changes();

DROP TRIGGER IF EXISTS trg_audit_gold_payments ON gold_payments;
CREATE TRIGGER trg_audit_gold_payments
AFTER INSERT OR UPDATE OR DELETE ON gold_payments
FOR EACH ROW
EXECUTE FUNCTION fn_audit_gold_changes();

-- 7) Views operacionais para fechamento e reconciliação
CREATE OR REPLACE VIEW vw_gold_daily_closure AS
SELECT
    DATE_TRUNC('day', criado_em) AS dia,
    COUNT(*) AS total_operacoes,
    COALESCE(SUM(total_usd), 0)::numeric(18,6) AS total_usd,
    COALESCE(SUM(total_pago_usd), 0)::numeric(18,6) AS total_pago_usd,
    COALESCE(SUM(diferenca_usd), 0)::numeric(18,6) AS total_diferenca_usd
FROM gold_transactions
GROUP BY DATE_TRUNC('day', criado_em)
ORDER BY dia DESC;

CREATE OR REPLACE VIEW vw_gold_daily_by_operator AS
SELECT
    DATE_TRUNC('day', criado_em) AS dia,
    operador_id,
    COUNT(*) AS total_operacoes,
    COALESCE(SUM(total_usd), 0)::numeric(18,6) AS total_usd,
    COALESCE(SUM(total_pago_usd), 0)::numeric(18,6) AS total_pago_usd,
    COALESCE(SUM(diferenca_usd), 0)::numeric(18,6) AS total_diferenca_usd
FROM gold_transactions
GROUP BY DATE_TRUNC('day', criado_em), operador_id
ORDER BY dia DESC, operador_id;

CREATE OR REPLACE VIEW vw_gold_daily_by_currency AS
SELECT
    DATE_TRUNC('day', p.criado_em) AS dia,
    p.moeda,
    COUNT(*) AS total_pagamentos,
    COALESCE(SUM(p.valor_moeda), 0)::numeric(18,6) AS total_valor_moeda,
    COALESCE(SUM(p.valor_usd), 0)::numeric(18,6) AS total_valor_usd
FROM gold_payments p
GROUP BY DATE_TRUNC('day', p.criado_em), p.moeda
ORDER BY dia DESC, p.moeda;

-- 11) IDs globais únicos (UUID) para auditoria e integração externa
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'usuarios' AND column_name = 'public_id'
    ) THEN
        ALTER TABLE usuarios ADD COLUMN public_id UUID DEFAULT gen_random_uuid();
        UPDATE usuarios SET public_id = gen_random_uuid() WHERE public_id IS NULL;
        ALTER TABLE usuarios ALTER COLUMN public_id SET NOT NULL;
    END IF;
END$$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'ativos' AND column_name = 'public_id'
    ) THEN
        ALTER TABLE ativos ADD COLUMN public_id UUID DEFAULT gen_random_uuid();
        UPDATE ativos SET public_id = gen_random_uuid() WHERE public_id IS NULL;
        ALTER TABLE ativos ALTER COLUMN public_id SET NOT NULL;
    END IF;
END$$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'transacoes' AND column_name = 'public_id'
    ) THEN
        ALTER TABLE transacoes ADD COLUMN public_id UUID DEFAULT gen_random_uuid();
        UPDATE transacoes SET public_id = gen_random_uuid() WHERE public_id IS NULL;
        ALTER TABLE transacoes ALTER COLUMN public_id SET NOT NULL;
    END IF;
END$$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'gold_transactions' AND column_name = 'public_id'
    ) THEN
        ALTER TABLE gold_transactions ADD COLUMN public_id UUID DEFAULT gen_random_uuid();
        UPDATE gold_transactions SET public_id = gen_random_uuid() WHERE public_id IS NULL;
        ALTER TABLE gold_transactions ALTER COLUMN public_id SET NOT NULL;
    END IF;
END$$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'gold_payments' AND column_name = 'public_id'
    ) THEN
        ALTER TABLE gold_payments ADD COLUMN public_id UUID DEFAULT gen_random_uuid();
        UPDATE gold_payments SET public_id = gen_random_uuid() WHERE public_id IS NULL;
        ALTER TABLE gold_payments ALTER COLUMN public_id SET NOT NULL;
    END IF;
END$$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'mensagens_processadas' AND column_name = 'public_id'
    ) THEN
        ALTER TABLE mensagens_processadas ADD COLUMN public_id UUID DEFAULT gen_random_uuid();
        UPDATE mensagens_processadas SET public_id = gen_random_uuid() WHERE public_id IS NULL;
        ALTER TABLE mensagens_processadas ALTER COLUMN public_id SET NOT NULL;
    END IF;
END$$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'sessoes_conversa' AND column_name = 'public_id'
    ) THEN
        ALTER TABLE sessoes_conversa ADD COLUMN public_id UUID DEFAULT gen_random_uuid();
        UPDATE sessoes_conversa SET public_id = gen_random_uuid() WHERE public_id IS NULL;
        ALTER TABLE sessoes_conversa ALTER COLUMN public_id SET NOT NULL;
    END IF;
END$$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'logs' AND column_name = 'public_id'
    ) THEN
        ALTER TABLE logs ADD COLUMN public_id UUID DEFAULT gen_random_uuid();
        UPDATE logs SET public_id = gen_random_uuid() WHERE public_id IS NULL;
        ALTER TABLE logs ALTER COLUMN public_id SET NOT NULL;
    END IF;
END$$;

CREATE UNIQUE INDEX IF NOT EXISTS uq_usuarios_public_id ON usuarios (public_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_ativos_public_id ON ativos (public_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_transacoes_public_id ON transacoes (public_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_gold_transactions_public_id ON gold_transactions (public_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_gold_payments_public_id ON gold_payments (public_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_mensagens_processadas_public_id ON mensagens_processadas (public_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_sessoes_conversa_public_id ON sessoes_conversa (public_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_logs_public_id ON logs (public_id);

-- 8) Tabela de histórico imutável para auditoria forense de transações
CREATE TABLE IF NOT EXISTS transaction_history (
    id BIGSERIAL PRIMARY KEY,
    tabela VARCHAR(60) NOT NULL,
    registro_id BIGINT,
    acao VARCHAR(20) NOT NULL,
    old_data JSONB,
    new_data JSONB,
    origem VARCHAR(60) NOT NULL DEFAULT 'trigger',
    criado_em TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_transaction_history_lookup
    ON transaction_history (tabela, registro_id, criado_em DESC);

-- 9) Auditoria detalhada para tabela legada transacoes
CREATE OR REPLACE FUNCTION fn_audit_transacoes_changes()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        INSERT INTO transaction_history(tabela, registro_id, acao, old_data, new_data)
        VALUES ('transacoes', NEW.id, 'INSERT', NULL, to_jsonb(NEW));
        RETURN NEW;
    ELSIF TG_OP = 'UPDATE' THEN
        INSERT INTO transaction_history(tabela, registro_id, acao, old_data, new_data)
        VALUES ('transacoes', NEW.id, 'UPDATE', to_jsonb(OLD), to_jsonb(NEW));
        RETURN NEW;
    ELSE
        RETURN OLD;
    END IF;
END;
$$;

DROP TRIGGER IF EXISTS trg_audit_transacoes_changes ON transacoes;
CREATE TRIGGER trg_audit_transacoes_changes
AFTER INSERT OR UPDATE ON transacoes
FOR EACH ROW
EXECUTE FUNCTION fn_audit_transacoes_changes();

-- 10) Bloqueio de exclusão física (append-only) para evitar perda de histórico
CREATE OR REPLACE FUNCTION fn_block_delete_and_track()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    INSERT INTO transaction_history(tabela, registro_id, acao, old_data, new_data, origem)
    VALUES (TG_TABLE_NAME, OLD.id, 'DELETE_BLOCKED', to_jsonb(OLD), NULL, 'delete_guard');

    -- Retornar NULL em BEFORE DELETE cancela exclusão sem erro, preservando o registro.
    RETURN NULL;
END;
$$;

DROP TRIGGER IF EXISTS trg_block_delete_transacoes ON transacoes;
CREATE TRIGGER trg_block_delete_transacoes
BEFORE DELETE ON transacoes
FOR EACH ROW
EXECUTE FUNCTION fn_block_delete_and_track();

DROP TRIGGER IF EXISTS trg_block_delete_gold_transactions ON gold_transactions;
CREATE TRIGGER trg_block_delete_gold_transactions
BEFORE DELETE ON gold_transactions
FOR EACH ROW
EXECUTE FUNCTION fn_block_delete_and_track();

DROP TRIGGER IF EXISTS trg_block_delete_gold_payments ON gold_payments;
CREATE TRIGGER trg_block_delete_gold_payments
BEFORE DELETE ON gold_payments
FOR EACH ROW
EXECUTE FUNCTION fn_block_delete_and_track();
