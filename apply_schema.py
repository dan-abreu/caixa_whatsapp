from __future__ import annotations

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
        raise RuntimeError("SUPABASE_DB_PASSWORD ou SUPABASE_DB_URL não configurado.")

    return f"postgresql://postgres:{password}@db.{project_ref}.supabase.co:5432/postgres?sslmode=require"


def main() -> None:
    schema_path = Path(__file__).with_name("schema.sql")
    sql = schema_path.read_text(encoding="utf-8")
    db_url = build_db_url()

    with psycopg.connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.commit()

    print("schema applied")


if __name__ == "__main__":
    main()
