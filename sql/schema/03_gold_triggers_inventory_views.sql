-- 3) Trigger para validar consistencia transacional em gold_transactions
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
        RAISE EXCEPTION 'gold_type fundido nao pode ter quebra diferente de 0';
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

-- 4) Trigger para validar consistencia em gold_payments
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

-- 6) Auditoria robusta para operacoes de ouro
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

CREATE TABLE IF NOT EXISTS gold_inventory_lots (
    id BIGSERIAL PRIMARY KEY,
    source_transaction_id BIGINT NOT NULL REFERENCES gold_transactions(id) ON DELETE CASCADE,
    origem_tipo VARCHAR(20) NOT NULL,
    created_at_tx TIMESTAMPTZ NOT NULL,
    initial_grams NUMERIC(18, 6) NOT NULL,
    remaining_grams NUMERIC(18, 6) NOT NULL,
    unit_cost_usd NUMERIC(18, 6) NOT NULL,
    total_cost_usd NUMERIC(18, 6) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'open',
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    criado_em TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_gold_inventory_lots_positive
        CHECK (initial_grams > 0 AND remaining_grams >= 0 AND unit_cost_usd >= 0 AND total_cost_usd >= 0),
    CONSTRAINT chk_gold_inventory_lots_status
        CHECK (status IN ('open', 'consumed'))
);

CREATE TABLE IF NOT EXISTS gold_inventory_consumptions (
    id BIGSERIAL PRIMARY KEY,
    sale_transaction_id BIGINT NOT NULL REFERENCES gold_transactions(id) ON DELETE CASCADE,
    lot_id BIGINT NOT NULL REFERENCES gold_inventory_lots(id) ON DELETE CASCADE,
    consumed_grams NUMERIC(18, 6) NOT NULL,
    unit_cost_usd NUMERIC(18, 6) NOT NULL,
    consumed_cost_usd NUMERIC(18, 6) NOT NULL,
    created_at_sale TIMESTAMPTZ NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    criado_em TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_gold_inventory_consumptions_positive
        CHECK (consumed_grams > 0 AND unit_cost_usd >= 0 AND consumed_cost_usd >= 0)
);

CREATE INDEX IF NOT EXISTS idx_gold_inventory_lots_open
    ON gold_inventory_lots (status, created_at_tx ASC);

CREATE INDEX IF NOT EXISTS idx_gold_inventory_lots_source
    ON gold_inventory_lots (source_transaction_id);

CREATE INDEX IF NOT EXISTS idx_gold_inventory_consumptions_sale
    ON gold_inventory_consumptions (sale_transaction_id, criado_em DESC);

CREATE INDEX IF NOT EXISTS idx_gold_inventory_consumptions_lot
    ON gold_inventory_consumptions (lot_id, criado_em DESC);

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

-- 7) Views operacionais para fechamento e reconciliacao
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