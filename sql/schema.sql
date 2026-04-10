-- Entrypoint para psql.
-- Para execucao via Python, os scripts montam automaticamente o bundle a partir de sql/schema/*.sql.

\ir schema/00_core_tables.sql
\ir schema/01_indexes_and_seed_data.sql
\ir schema/02_hardening_and_constraints.sql
\ir schema/03_gold_triggers_inventory_views.sql
\ir schema/04_public_ids_and_history.sql
