from .common import Any, Decimal, Dict, List, Optional, _safe_decimal_from_row, cast, datetime, logger, timezone


def _consume_selected_sale_lots(
    lots_state: List[Dict[str, Any]],
    selected_lots: List[Dict[str, Any]],
    sale_transaction_id: int,
    created_at_sale: str,
    consumption_rows: List[Dict[str, Any]],
) -> Decimal:
    remaining_sale = sum((_safe_decimal_from_row(cast(Dict[str, Any], item), "grams") for item in selected_lots), Decimal("0"))
    for item in selected_lots:
        source_transaction_id = int(item.get("source_transaction_id") or 0)
        selected_grams = _safe_decimal_from_row(cast(Dict[str, Any], item), "grams")
        if source_transaction_id <= 0 or selected_grams <= 0:
            continue
        lot = next((candidate for candidate in lots_state if int(candidate.get("source_transaction_id") or 0) == source_transaction_id), None)
        if not lot:
            continue
        lot_remaining = _safe_decimal_from_row(cast(Dict[str, Any], lot), "remaining_grams")
        if lot_remaining <= 0:
            continue
        consumed = min(lot_remaining, selected_grams)
        unit_cost = _safe_decimal_from_row(cast(Dict[str, Any], lot), "unit_cost_usd")
        consumption_rows.append({"sale_transaction_id": sale_transaction_id, "lot_source_transaction_id": source_transaction_id, "consumed_grams": str(consumed), "unit_cost_usd": str(unit_cost), "consumed_cost_usd": str(consumed * unit_cost), "created_at_sale": created_at_sale, "metadata": {"source": "selected", "sale_tx_id": sale_transaction_id}})
        lot["remaining_grams"] = lot_remaining - consumed
        remaining_sale -= consumed
    return remaining_sale if remaining_sale > 0 else Decimal("0")


class InventoryLedgerMixin:
    def get_gold_inventory_transactions(self, end_iso: Optional[str] = None) -> List[Dict[str, Any]]:
        try:
            query = self.client.table("gold_transactions").select("*").order("criado_em", desc=False)
            if end_iso:
                query = query.lt("criado_em", end_iso)
            response = query.execute()
            rows = cast(List[Dict[str, Any]], response.data or [])
            return [row for row in rows if str(row.get("status") or "registrada").lower() != "cancelada"]
        except Exception as exc:
            logger.warning("Falha ao carregar transacoes de inventario de ouro: %s", exc)
            return []

    def sync_gold_inventory_ledger(self) -> Dict[str, Any]:
        transactions = self.get_gold_inventory_transactions()
        lots_state: List[Dict[str, Any]] = []
        lot_rows: List[Dict[str, Any]] = []
        consumption_rows: List[Dict[str, Any]] = []
        for tx in transactions:
            try:
                tx_id = int(tx.get("id") or 0)
                tipo = str(tx.get("tipo_operacao") or "").lower()
                peso = _safe_decimal_from_row(tx, "peso")
                preco_usd = _safe_decimal_from_row(tx, "preco_usd")
                criado_em = str(tx.get("criado_em") or datetime.now(timezone.utc).isoformat())
            except (TypeError, ValueError) as exc:
                logger.warning("Falha ao interpretar transacao de inventario %s: %s", tx.get("id"), exc)
                continue
            if tx_id <= 0 or peso <= 0 or tipo not in {"compra", "venda"}:
                continue
            if tipo == "compra":
                lot_rows.append({"source_transaction_id": tx_id, "origem_tipo": tipo, "created_at_tx": criado_em, "initial_grams": str(peso), "remaining_grams": str(peso), "unit_cost_usd": str(preco_usd), "total_cost_usd": str(peso * preco_usd), "status": "open", "metadata": {"source": "sync", "tx_id": tx_id, "teor": str(tx.get("teor") or ""), "gold_type": str(tx.get("gold_type") or ""), "quebra": str(tx.get("quebra") or ""), "pessoa": str(tx.get("pessoa") or "")}})
                lots_state.append({"source_transaction_id": tx_id, "created_at_tx": criado_em, "remaining_grams": peso, "unit_cost_usd": preco_usd, "teor": str(tx.get("teor") or ""), "gold_type": str(tx.get("gold_type") or ""), "quebra": str(tx.get("quebra") or ""), "pessoa": str(tx.get("pessoa") or "")})
                continue
            remaining_sale = peso
            contexto_raw = tx.get("contexto") if isinstance(tx.get("contexto"), dict) else {}
            selected_sale_lots = cast(List[Dict[str, Any]], cast(Dict[str, Any], contexto_raw).get("selected_sale_lots") or [])
            if selected_sale_lots:
                remaining_sale = _consume_selected_sale_lots(lots_state, selected_sale_lots, tx_id, criado_em, consumption_rows)
            for lot in lots_state:
                if remaining_sale <= 0:
                    break
                lot_remaining = _safe_decimal_from_row(cast(Dict[str, Any], lot), "remaining_grams")
                if lot_remaining <= 0:
                    continue
                consumed = min(lot_remaining, remaining_sale)
                unit_cost = _safe_decimal_from_row(cast(Dict[str, Any], lot), "unit_cost_usd")
                consumption_rows.append({"sale_transaction_id": tx_id, "lot_source_transaction_id": int(lot.get("source_transaction_id") or 0), "consumed_grams": str(consumed), "unit_cost_usd": str(unit_cost), "consumed_cost_usd": str(consumed * unit_cost), "created_at_sale": criado_em, "metadata": {"source": "sync", "sale_tx_id": tx_id}})
                lot["remaining_grams"] = lot_remaining - consumed
                remaining_sale -= consumed
        try:
            self.client.table("gold_inventory_consumptions").delete().neq("id", 0).execute()
        except Exception as exc:
            logger.warning("Falha ao limpar consumos antigos do inventario de ouro: %s", exc)
        try:
            self.client.table("gold_inventory_lots").delete().neq("id", 0).execute()
        except Exception as exc:
            logger.warning("Falha ao limpar lotes antigos do inventario de ouro: %s", exc)
        persisted_lots: Dict[int, int] = {}
        for row in lot_rows:
            source_tx_id = int(row["source_transaction_id"])
            remaining = next((_safe_decimal_from_row(cast(Dict[str, Any], lot), "remaining_grams") for lot in lots_state if int(lot.get("source_transaction_id") or 0) == source_tx_id), Decimal("0"))
            row["remaining_grams"] = str(remaining)
            row["status"] = "open" if remaining > 0 else "consumed"
            try:
                resp = self.client.table("gold_inventory_lots").insert(row).execute()
                data = cast(List[Dict[str, Any]], resp.data or [])
                if data:
                    persisted_lots[source_tx_id] = int(data[0].get("id") or 0)
            except Exception as exc:
                logger.warning("Falha ao persistir lote de inventario para transacao %s: %s", source_tx_id, exc)
                continue
        for row in consumption_rows:
            lot_id = persisted_lots.get(int(row.pop("lot_source_transaction_id", 0) or 0))
            if lot_id:
                row["lot_id"] = lot_id
                try:
                    self.client.table("gold_inventory_consumptions").insert(row).execute()
                except Exception as exc:
                    logger.warning("Falha ao persistir consumo de inventario para lote %s: %s", lot_id, exc)
                    continue
        open_grams = sum((_safe_decimal_from_row(cast(Dict[str, Any], lot), "remaining_grams") for lot in lots_state), Decimal("0"))
        self._invalidate_runtime_cache("gold_inventory_overview", self._gold_inventory_status_cache_key(open_only=False), self._gold_inventory_status_cache_key(open_only=True), "gold_pending_closure_grams")
        return {"lots": len(lot_rows), "consumptions": len(consumption_rows), "open_grams": str(open_grams)}