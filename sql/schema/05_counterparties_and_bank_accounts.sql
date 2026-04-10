CREATE TABLE IF NOT EXISTS fornecedores (
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

CREATE TABLE IF NOT EXISTS fornecedor_movimentacoes (
    id BIGSERIAL PRIMARY KEY,
    fornecedor_id BIGINT NOT NULL REFERENCES fornecedores(id) ON DELETE CASCADE,
    moeda VARCHAR(10) NOT NULL,
    tipo_movimento VARCHAR(40) NOT NULL,
    valor NUMERIC(20, 8) NOT NULL,
    descricao TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    criado_em TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_fornecedor_mov_moeda CHECK (moeda IN ('XAU', 'USD', 'EUR', 'SRD', 'BRL'))
);

CREATE TABLE IF NOT EXISTS saved_bank_accounts (
    id BIGSERIAL PRIMARY KEY,
    owner_kind VARCHAR(20) NOT NULL,
    owner_id BIGINT,
    currency_code VARCHAR(10) NOT NULL,
    country_code VARCHAR(10) NOT NULL,
    label VARCHAR(120) NOT NULL,
    holder_name VARCHAR(150) NOT NULL,
    bank_name VARCHAR(150),
    branch_name VARCHAR(120),
    branch_code VARCHAR(40),
    account_number VARCHAR(80),
    pix_key VARCHAR(120),
    document_number VARCHAR(40),
    notes TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    is_default BOOLEAN NOT NULL DEFAULT FALSE,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_by_phone VARCHAR(30),
    criado_em TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    atualizado_em TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_saved_bank_accounts_owner_kind CHECK (owner_kind IN ('cliente', 'fornecedor', 'empresa')),
    CONSTRAINT chk_saved_bank_accounts_currency CHECK (currency_code IN ('USD', 'EUR', 'SRD', 'BRL')),
    CONSTRAINT chk_saved_bank_accounts_country CHECK (country_code IN ('SR', 'BR', 'OTHER'))
);

CREATE INDEX IF NOT EXISTS idx_fornecedores_atualizado_em
    ON fornecedores (atualizado_em DESC);

CREATE INDEX IF NOT EXISTS idx_fornecedor_movimentacoes_fornecedor
    ON fornecedor_movimentacoes (fornecedor_id, criado_em DESC);

CREATE INDEX IF NOT EXISTS idx_saved_bank_accounts_owner_currency
    ON saved_bank_accounts (owner_kind, owner_id, currency_code, active, atualizado_em DESC);
