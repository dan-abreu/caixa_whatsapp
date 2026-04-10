from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path
from typing import Any, cast

import psycopg
from psycopg import sql as psycopg_sql


logger = logging.getLogger("caixa_whatsapp")


def build_db_url() -> str:
    explicit = os.getenv("SUPABASE_DB_URL")
    if explicit:
        return explicit

    project_ref = os.getenv("SUPABASE_PROJECT_REF") or "wqmmtgncgtakxrifoqmn"
    password = os.getenv("SUPABASE_DB_PASSWORD")
    if not password:
        raise RuntimeError("SUPABASE_DB_PASSWORD ou SUPABASE_DB_URL nao configurado.")

    return f"postgresql://postgres:{password}@db.{project_ref}.supabase.co:5432/postgres?sslmode=require"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Aplica um arquivo SQL isolado no banco do Supabase.")
    parser.add_argument(
        "sql_file",
        nargs="?",
        default="sql/schema_clientes_upgrade.sql",
        help="Caminho do arquivo SQL relativo a raiz do repositorio. Default: sql/schema_clientes_upgrade.sql",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Valida o arquivo e a conexao, mas nao executa o SQL.",
    )
    parser.add_argument(
        "--statement-timeout-ms",
        type=int,
        default=0,
        help="Statement timeout em milissegundos. Use 0 para desabilitar.",
    )
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    sql_path = (repo_root / args.sql_file).resolve() if not Path(args.sql_file).is_absolute() else Path(args.sql_file)
    if not sql_path.exists():
        raise FileNotFoundError(f"Arquivo SQL nao encontrado: {sql_path}")
    if sql_path.suffix.lower() != ".sql":
        raise ValueError(f"Arquivo invalido para execucao SQL: {sql_path}")

    sql = sql_path.read_text(encoding="utf-8")
    if not sql.strip():
        raise ValueError(f"Arquivo SQL vazio: {sql_path}")

    if args.dry_run:
        print(f"sql validated (dry-run): {sql_path}")
        return

    db_url = build_db_url()

    try:
        with psycopg.connect(db_url) as conn:
            with conn.cursor() as cur:
                if args.statement_timeout_ms > 0:
                    cur.execute(
                        psycopg_sql.SQL("SET statement_timeout = {}")
                        .format(psycopg_sql.Literal(int(args.statement_timeout_ms)))
                    )
                cur.execute(cast(Any, sql))
            conn.commit()
    except psycopg.Error as exc:
        logger.error("Falha ao aplicar SQL %s: %s", sql_path, exc)
        raise RuntimeError(f"Falha ao aplicar SQL {sql_path.name}: {exc}") from exc

    print(f"sql applied: {sql_path}")


if __name__ == "__main__":
    main()