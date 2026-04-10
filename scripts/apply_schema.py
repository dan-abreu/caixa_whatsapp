from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable

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


def _iter_schema_parts(schema_dir: Path) -> Iterable[Path]:
    return sorted(
        path
        for path in schema_dir.glob("*.sql")
        if path.is_file()
    )


def load_schema_sql(repo_root: Path) -> str:
    schema_dir = repo_root / "sql" / "schema"
    if schema_dir.exists():
        parts = _iter_schema_parts(schema_dir)
        bundled_sql = "\n\n".join(path.read_text(encoding="utf-8").strip() for path in parts)
        if bundled_sql.strip():
            return bundled_sql

    schema_path = repo_root / "sql" / "schema.sql"
    return schema_path.read_text(encoding="utf-8")


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    sql = load_schema_sql(repo_root)
    db_url = build_db_url()

    with psycopg.connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.commit()

    print("schema applied")


if __name__ == "__main__":
    main()
