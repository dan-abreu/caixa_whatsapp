-- Enterprise upgrade (non-breaking) for multi-currency and commodities robustness
-- Safe to run after schema.sql

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- 1) Master table for ISO currencies and commodity-like codes (e.g. XAU)
CREATE TABLE IF NOT EXISTS currencies (
    code CHAR(3) PRIMARY KEY,
    name VARCHAR(80) NOT NULL,
    minor_units SMALLINT NOT NULL DEFAULT 2 CHECK (minor_units BETWEEN 0 AND 6),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO currencies (code, name, minor_units)
VALUES
    ('USD', 'US Dollar', 2),
    ('EUR', 'Euro', 2),
    ('SRD', 'Surinamese Dollar', 2),
    ('BRL', 'Brazilian Real', 2)
ON CONFLICT (code) DO UPDATE
SET name = EXCLUDED.name,
    minor_units = EXCLUDED.minor_units,
    is_active = TRUE;

-- 3) FX rates history with provenance for forensic audit
CREATE TABLE IF NOT EXISTS fx_rates (
    id BIGSERIAL PRIMARY KEY,
    base_currency CHAR(3) NOT NULL REFERENCES currencies(code),
    quote_currency CHAR(3) NOT NULL REFERENCES currencies(code),
    rate NUMERIC(20,10) NOT NULL CHECK (rate > 0),
    source VARCHAR(60) NOT NULL DEFAULT 'manual',
    captured_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_fx_base_quote_diff CHECK (base_currency <> quote_currency)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_fx_rates_point
    ON fx_rates (base_currency, quote_currency, source, captured_at);

CREATE INDEX IF NOT EXISTS idx_fx_rates_pair_latest
    ON fx_rates (base_currency, quote_currency, captured_at DESC);

-- 4) Immutable accounting journal (double-entry ready)
CREATE TABLE IF NOT EXISTS accounting_journal_entries (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    posted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    reference_table VARCHAR(60),
    reference_id BIGINT,
    description TEXT NOT NULL,
    source_message_id VARCHAR(120),
    created_by VARCHAR(100),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS accounting_accounts (
    code VARCHAR(40) PRIMARY KEY,
    name VARCHAR(120) NOT NULL,
    category VARCHAR(20) NOT NULL CHECK (category IN ('asset', 'liability', 'equity', 'revenue', 'expense')),
    normal_side VARCHAR(6) NOT NULL CHECK (normal_side IN ('debit', 'credit')),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO accounting_accounts (code, name, category, normal_side)
VALUES
    ('CASH_USD_EQUIV', 'Cash (USD Equivalent)', 'asset', 'debit'),
    ('INVENTORY_COMMODITIES', 'Gold Inventory', 'asset', 'debit'),
    ('FX_POSITION_ASSET', 'Foreign Currency Position', 'asset', 'debit'),
    ('RECEIVABLE_CLIENT_SETTLEMENT', 'Client Settlement Receivable', 'asset', 'debit'),
    ('PAYABLE_CLIENT_SETTLEMENT', 'Client Settlement Payable', 'liability', 'credit'),
    ('TRANSFER_CLEARING', 'Transfer Money Clearing', 'asset', 'debit'),
    ('TRANSFER_FEE_REVENUE', 'Transfer Money Fee Revenue', 'revenue', 'credit'),
    ('FX_GAIN_LOSS', 'Foreign Exchange Gain/Loss', 'revenue', 'credit')
ON CONFLICT (code) DO UPDATE
SET name = EXCLUDED.name,
    category = EXCLUDED.category,
    normal_side = EXCLUDED.normal_side,
    is_active = TRUE;

CREATE TABLE IF NOT EXISTS accounting_journal_lines (
    id BIGSERIAL PRIMARY KEY,
    journal_entry_id UUID NOT NULL REFERENCES accounting_journal_entries(id) ON DELETE RESTRICT,
    account_code VARCHAR(40) NOT NULL,
    currency_code CHAR(3) NOT NULL REFERENCES currencies(code),
    debit NUMERIC(20,10) NOT NULL DEFAULT 0 CHECK (debit >= 0),
    credit NUMERIC(20,10) NOT NULL DEFAULT 0 CHECK (credit >= 0),
    commodity_symbol VARCHAR(10),
    quantity NUMERIC(20,10),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_line_one_side_only CHECK (
        (debit = 0 AND credit > 0) OR
        (credit = 0 AND debit > 0)
    )
);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fk_accounting_journal_lines_account_code'
    ) THEN
        ALTER TABLE accounting_journal_lines
            ADD CONSTRAINT fk_accounting_journal_lines_account_code
            FOREIGN KEY (account_code) REFERENCES accounting_accounts(code) NOT VALID;
    END IF;
END$$;

-- 5) Core operation table for transfer money (scope-limited)
CREATE TABLE IF NOT EXISTS transfer_money_transactions (
    id BIGSERIAL PRIMARY KEY,
    data_hora TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    sender_nome VARCHAR(150),
    receiver_nome VARCHAR(150),
    origem_moeda CHAR(3) NOT NULL REFERENCES currencies(code),
    destino_moeda CHAR(3) NOT NULL REFERENCES currencies(code),
    valor_origem NUMERIC(20,10) NOT NULL CHECK (valor_origem > 0),
    cambio_origem_para_usd NUMERIC(20,10) NOT NULL CHECK (cambio_origem_para_usd > 0),
    cambio_destino_para_usd NUMERIC(20,10) NOT NULL CHECK (cambio_destino_para_usd > 0),
    taxa_servico_origem NUMERIC(20,10) NOT NULL DEFAULT 0 CHECK (taxa_servico_origem >= 0),
    valor_destino NUMERIC(20,10) NOT NULL CHECK (valor_destino > 0),
    valor_origem_usd NUMERIC(20,10) NOT NULL CHECK (valor_origem_usd > 0),
    valor_destino_usd NUMERIC(20,10) NOT NULL CHECK (valor_destino_usd > 0),
    operador_id VARCHAR(100) NOT NULL,
    source_message_id VARCHAR(120),
    status VARCHAR(30) NOT NULL DEFAULT 'registrada',
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    criado_em TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_transfer_money_source_message
    ON transfer_money_transactions (source_message_id)
    WHERE source_message_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_transfer_money_data_hora
    ON transfer_money_transactions (data_hora DESC);

CREATE INDEX IF NOT EXISTS idx_transfer_money_moedas
    ON transfer_money_transactions (origem_moeda, destino_moeda, data_hora DESC);

CREATE INDEX IF NOT EXISTS idx_journal_lines_entry
    ON accounting_journal_lines (journal_entry_id);

CREATE INDEX IF NOT EXISTS idx_journal_lines_account
    ON accounting_journal_lines (account_code, created_at DESC);

CREATE OR REPLACE VIEW vw_account_trial_balance AS
SELECT
    l.account_code,
    a.name AS account_name,
    a.category,
    COALESCE(SUM(l.debit), 0)::numeric(20,10) AS total_debit,
    COALESCE(SUM(l.credit), 0)::numeric(20,10) AS total_credit,
    COALESCE(SUM(l.debit - l.credit), 0)::numeric(20,10) AS net_balance
FROM accounting_journal_lines l
JOIN accounting_accounts a ON a.code = l.account_code
GROUP BY l.account_code, a.name, a.category
ORDER BY l.account_code;

CREATE OR REPLACE VIEW vw_account_daily_currency_exposure AS
SELECT
    DATE_TRUNC('day', e.posted_at) AS dia,
    l.currency_code,
    COALESCE(SUM(l.debit), 0)::numeric(20,10) AS total_debit,
    COALESCE(SUM(l.credit), 0)::numeric(20,10) AS total_credit,
    COALESCE(SUM(l.debit - l.credit), 0)::numeric(20,10) AS net_exposure
FROM accounting_journal_entries e
JOIN accounting_journal_lines l ON l.journal_entry_id = e.id
GROUP BY DATE_TRUNC('day', e.posted_at), l.currency_code
ORDER BY dia DESC, l.currency_code;

CREATE OR REPLACE VIEW vw_fx_pnl_daily AS
SELECT
    DATE_TRUNC('day', e.posted_at) AS dia,
    COALESCE(SUM(l.credit - l.debit), 0)::numeric(20,10) AS fx_pnl_usd
FROM accounting_journal_entries e
JOIN accounting_journal_lines l ON l.journal_entry_id = e.id
WHERE l.account_code = 'FX_GAIN_LOSS'
GROUP BY DATE_TRUNC('day', e.posted_at)
ORDER BY dia DESC;

CREATE OR REPLACE VIEW vw_core_ops_daily AS
SELECT
    DATE_TRUNC('day', data_hora) AS dia,
    'transacoes'::VARCHAR(30) AS operacao,
    COUNT(*) AS total_itens,
    COALESCE(SUM(valor_total), 0)::numeric(20,10) AS total_usd
FROM transacoes
GROUP BY DATE_TRUNC('day', data_hora)

UNION ALL

SELECT
    DATE_TRUNC('day', criado_em) AS dia,
    'gold_transactions'::VARCHAR(30) AS operacao,
    COUNT(*) AS total_itens,
    COALESCE(SUM(total_usd), 0)::numeric(20,10) AS total_usd
FROM gold_transactions
GROUP BY DATE_TRUNC('day', criado_em)

UNION ALL

SELECT
    DATE_TRUNC('day', data_hora) AS dia,
    'transfer_money'::VARCHAR(30) AS operacao,
    COUNT(*) AS total_itens,
    COALESCE(SUM(valor_origem_usd), 0)::numeric(20,10) AS total_usd
FROM transfer_money_transactions
GROUP BY DATE_TRUNC('day', data_hora);

CREATE OR REPLACE FUNCTION fn_validate_balanced_journal()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_entry UUID;
    v_debit NUMERIC(20,10);
    v_credit NUMERIC(20,10);
BEGIN
    v_entry := COALESCE(NEW.journal_entry_id, OLD.journal_entry_id);

    SELECT COALESCE(SUM(debit), 0), COALESCE(SUM(credit), 0)
      INTO v_debit, v_credit
      FROM accounting_journal_lines
     WHERE journal_entry_id = v_entry;

    IF ROUND(v_debit, 6) <> ROUND(v_credit, 6) THEN
        RAISE EXCEPTION 'Journal entry % is unbalanced (debit %, credit %)', v_entry, v_debit, v_credit;
    END IF;

    RETURN COALESCE(NEW, OLD);
END;
$$;

DROP TRIGGER IF EXISTS trg_validate_balanced_journal ON accounting_journal_lines;
CREATE CONSTRAINT TRIGGER trg_validate_balanced_journal
AFTER INSERT OR UPDATE OR DELETE ON accounting_journal_lines
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW
EXECUTE FUNCTION fn_validate_balanced_journal();

-- 6) Add currency foreign keys (NOT VALID to avoid breaking legacy rows)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fk_gold_payments_currency_code'
    ) THEN
        ALTER TABLE gold_payments
            ADD CONSTRAINT fk_gold_payments_currency_code
            FOREIGN KEY (moeda) REFERENCES currencies(code) NOT VALID;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fk_transacoes_moeda_liquidacao_code'
    ) THEN
        ALTER TABLE transacoes
            ADD CONSTRAINT fk_transacoes_moeda_liquidacao_code
            FOREIGN KEY (moeda_liquidacao) REFERENCES currencies(code) NOT VALID;
    END IF;
END$$;

-- 7) Targeted performance indexes for international reporting
CREATE INDEX IF NOT EXISTS idx_transacoes_data_moeda_ativo
    ON transacoes (data_hora DESC, moeda_liquidacao, ativo_id);

CREATE INDEX IF NOT EXISTS idx_gold_payments_currency_date
    ON gold_payments (moeda, criado_em DESC);

CREATE INDEX IF NOT EXISTS idx_gold_transactions_source_message
    ON gold_transactions (source_message_id)
    WHERE source_message_id IS NOT NULL;

-- 8) Data quality checks to run after cleaning legacy values
-- ALTER TABLE gold_payments VALIDATE CONSTRAINT fk_gold_payments_currency_code;
-- ALTER TABLE transacoes VALIDATE CONSTRAINT fk_transacoes_moeda_liquidacao_code;
