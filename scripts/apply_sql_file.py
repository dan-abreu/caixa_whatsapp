from __future__ import annotations

import argparse
import os
from pathlib import Path

import psycopg


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
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    sql_path = (repo_root / args.sql_file).resolve() if not Path(args.sql_file).is_absolute() else Path(args.sql_file)
    if not sql_path.exists():
        raise FileNotFoundError(f"Arquivo SQL nao encontrado: {sql_path}")

    sql = sql_path.read_text(encoding="utf-8")
    db_url = build_db_url()

    with psycopg.connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.commit()

    print(f"sql applied: {sql_path}")


if __name__ == "__main__":
    main()