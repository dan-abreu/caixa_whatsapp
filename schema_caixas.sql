-- Tabela de caixas separados por moeda/commodity
-- Cada caixa tem seu saldo independente, sem conversão para USD

CREATE TABLE IF NOT EXISTS caixas (
  id BIGSERIAL PRIMARY KEY,
  moeda TEXT NOT NULL UNIQUE,
  -- moeda: 'XAU' (gramas), 'EUR', 'USD', 'SRD', 'BRL'
  saldo DECIMAL(20, 8) NOT NULL DEFAULT 0,
  -- saldo: quantidade de gramas (XAU) ou saldo em moeda (EUR, USD, SRD, BRL)
  atualizado_em TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
  criado_em TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

-- Insert inicial para os 5 caixas
INSERT INTO caixas (moeda, saldo)
VALUES
  ('XAU', 0),
  ('EUR', 0),
  ('USD', 0),
  ('SRD', 0),
  ('BRL', 0)
ON CONFLICT (moeda) DO NOTHING;

-- Índice para melhor performance
CREATE INDEX IF NOT EXISTS idx_caixas_moeda ON caixas(moeda);

-- Tabela de auditoria: histórico de movimentações de caixa
-- Rastreia cada débito/crédito em cada caixa
CREATE TABLE IF NOT EXISTS caixas_movimentacoes (
  id BIGSERIAL PRIMARY KEY,
  caixa_moeda TEXT NOT NULL REFERENCES caixas(moeda),
  tipo_operacao TEXT NOT NULL,
  -- tipo_operacao: 'compra' | 'venda' | 'ajuste' | 'adiantamento' | 'devolucao'
  gold_transaction_id BIGINT REFERENCES gold_transactions(id),
  -- valor é positivo (crédito/entrada) ou negativo (débito/saída)
  valor DECIMAL(20, 8) NOT NULL,
  saldo_anterior DECIMAL(20, 8) NOT NULL,
  saldo_posterior DECIMAL(20, 8) NOT NULL,
  descricao TEXT,
  pessoa TEXT,
  criado_em TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_caixas_mov_moeda ON caixas_movimentacoes(caixa_moeda);
CREATE INDEX IF NOT EXISTS idx_caixas_mov_tx_id ON caixas_movimentacoes(gold_transaction_id);
