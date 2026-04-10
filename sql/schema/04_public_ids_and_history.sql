-- 11) IDs globais unicos (UUID) para auditoria e integracao externa
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

-- 8) Tabela de historico imutavel para auditoria forense de transacoes
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

-- 10) Bloqueio de exclusao fisica (append-only) para evitar perda de historico
CREATE OR REPLACE FUNCTION fn_block_delete_and_track()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    INSERT INTO transaction_history(tabela, registro_id, acao, old_data, new_data, origem)
    VALUES (TG_TABLE_NAME, OLD.id, 'DELETE_BLOCKED', to_jsonb(OLD), NULL, 'delete_guard');

    -- Retornar NULL em BEFORE DELETE cancela exclusao sem erro, preservando o registro.
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