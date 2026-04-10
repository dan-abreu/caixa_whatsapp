from .common import Any, Decimal, Dict, List, Optional, _safe_decimal, _safe_decimal_from_row, cast, logger


def _safe_int(value: Any, default: int = 0, *, context: str = "valor") -> int:
    try:
        return int(str(default if value is None else value))
    except (TypeError, ValueError) as exc:
        logger.warning("Falha ao converter inteiro em %s: %s", context, exc)
        return default


class InventoryStatusMixin:
    def preview_gold_inventory_selection(self, peso_venda: Decimal, selected_lots: List[Dict[str, Any]]) -> Dict[str, Any]:
        remaining_sale = Decimal(str(peso_venda or "0"))
        consumed_cost = Decimal("0")
        consumed_grams = Decimal("0")
        breakdown: List[Dict[str, Any]] = []
        if remaining_sale <= 0:
            return {"consumed_grams": Decimal("0"), "consumed_cost_usd": Decimal("0"), "shortfall_grams": Decimal("0"), "breakdown": []}
        lots_by_source = {
            _safe_int(item.get("source_transaction_id"), context="inventory_selection.source_transaction_id"): item
            for item in self.get_gold_inventory_status(open_only=True).get("open_lots") or []
            if _safe_int(item.get("source_transaction_id"), context="inventory_selection.source_transaction_id") > 0
        }
        for item in selected_lots:
            if remaining_sale <= 0:
                break
            source_transaction_id = _safe_int(item.get("source_transaction_id"), context="selected_sale_lots.source_transaction_id")
            lot = lots_by_source.get(source_transaction_id)
            if not lot:
                continue
            lot_remaining = _safe_decimal(lot.get("remaining_grams"), context=f"open_lots.{source_transaction_id}.remaining_grams")
            selected_grams = _safe_decimal(item.get("grams"), context=f"selected_sale_lots.{source_transaction_id}.grams")
            if lot_remaining <= 0 or selected_grams <= 0:
                continue
            consumed = min(remaining_sale, lot_remaining, selected_grams)
            unit_cost = _safe_decimal(lot.get("unit_cost_usd"), context=f"open_lots.{source_transaction_id}.unit_cost_usd")
            cost_usd = consumed * unit_cost
            breakdown.append({"source_id": source_transaction_id, "grams": str(consumed), "unit_cost_usd": str(unit_cost), "cost_usd": str(cost_usd)})
            consumed_cost += cost_usd
            consumed_grams += consumed
            remaining_sale -= consumed
        return {"consumed_grams": consumed_grams, "consumed_cost_usd": consumed_cost, "shortfall_grams": remaining_sale if remaining_sale > 0 else Decimal("0"), "breakdown": breakdown}

    def get_gold_inventory_status(self, inventory_transactions: Optional[List[Dict[str, Any]]] = None, *, open_only: bool = False) -> Dict[str, Any]:
        cache_key = self._gold_inventory_status_cache_key(open_only)
        if inventory_transactions is None:
            cached = self._get_runtime_cache(cache_key)
            if cached is not None:
                return cast(Dict[str, Any], cached)
        try:
            lots_query = self.client.table("gold_inventory_lots").select("id,source_transaction_id,created_at_tx,initial_grams,remaining_grams,unit_cost_usd,total_cost_usd,status,metadata").order("created_at_tx", desc=False)
            if open_only:
                lots_query = lots_query.eq("status", "open")
            lots = cast(List[Dict[str, Any]], lots_query.execute().data or [])
            has_any_lots = bool(lots)
            if open_only and not has_any_lots:
                any_lot_resp = self.client.table("gold_inventory_lots").select("id").limit(1).execute()
                has_any_lots = bool(cast(List[Dict[str, Any]], any_lot_resp.data or []))
            needs_transaction_fallback = False
            for lot in lots:
                if str(lot.get("status") or "") == "open":
                    metadata = cast(Dict[str, Any], lot.get("metadata") or {})
                    if any(field not in metadata for field in ("teor", "gold_type", "quebra", "pessoa")):
                        needs_transaction_fallback = True
                        break
            tx_lookup: Dict[int, Dict[str, Any]] = {}
            if needs_transaction_fallback:
                tx_rows = inventory_transactions if inventory_transactions is not None else self.get_gold_inventory_transactions()
                for tx in tx_rows:
                    tx_id = _safe_int(tx.get("id"), context="gold_inventory_transactions.id")
                    if tx_id > 0:
                        tx_lookup[tx_id] = tx
            open_lots: List[Dict[str, Any]] = []
            for lot in lots:
                if str(lot.get("status") or "") != "open":
                    continue
                metadata = cast(Dict[str, Any], lot.get("metadata") or {})
                source_tx = tx_lookup.get(_safe_int(lot.get("source_transaction_id"), context="gold_inventory_lots.source_transaction_id")) or {}
                open_lots.append({**lot, "teor": metadata.get("teor") or source_tx.get("teor"), "gold_type": metadata.get("gold_type") or source_tx.get("gold_type"), "quebra": metadata.get("quebra") or source_tx.get("quebra"), "pessoa": metadata.get("pessoa") or source_tx.get("pessoa")})
            available_grams = sum((_safe_decimal_from_row(lot, "remaining_grams") for lot in open_lots), Decimal("0"))
            open_cost = sum((_safe_decimal_from_row(lot, "remaining_grams") * _safe_decimal_from_row(lot, "unit_cost_usd") for lot in open_lots), Decimal("0"))
            avg_cost = (open_cost / available_grams) if available_grams > 0 else Decimal("0")
            result = {"lots": lots, "open_lots": open_lots, "available_grams": str(available_grams), "inventory_cost_usd": str(open_cost.quantize(Decimal("0.01"))), "avg_cost_usd_per_gram": str(avg_cost.quantize(Decimal("0.01"))), "has_any_lots": has_any_lots}
            return cast(Dict[str, Any], self._set_runtime_cache(cache_key, result) if inventory_transactions is None else result)
        except Exception as exc:
            logger.warning("Falha ao montar status de inventario de ouro: %s", exc)
            return {"lots": [], "open_lots": [], "available_grams": "0", "inventory_cost_usd": "0.00", "avg_cost_usd_per_gram": "0.00", "has_any_lots": False}

    def get_gold_pending_closure_grams(self) -> Decimal:
        cached = self._get_runtime_cache("gold_pending_closure_grams")
        if cached is not None:
            return Decimal(str(cached))
        try:
            select_fields = "peso,fechamento_gramas,fechamento_tipo,status"
            if type(self)._GOLD_PENDING_CLOSURE_SCHEMA_READY is False:
                select_fields = "peso,fechamento_gramas"
            try:
                response = self.client.table("gold_transactions").select(select_fields).execute()
                type(self)._GOLD_PENDING_CLOSURE_SCHEMA_READY = select_fields != "peso,fechamento_gramas"
            except Exception as exc:
                logger.warning("Falha ao consultar gold_transactions com schema estendido; tentando fallback reduzido: %s", exc)
                response = self.client.table("gold_transactions").select("peso,fechamento_gramas").execute()
                type(self)._GOLD_PENDING_CLOSURE_SCHEMA_READY = False
            rows = cast(List[Dict[str, Any]], response.data or [])
            pending_total = Decimal("0")
            for row in rows:
                if str(row.get("status") or "registrada").lower() == "cancelada":
                    continue
                peso = _safe_decimal_from_row(row, "peso")
                fechamento = _safe_decimal_from_row(row, "fechamento_gramas", str(peso or Decimal("0")))
                if peso <= 0:
                    continue
                if fechamento <= 0:
                    fechamento = peso
                aberto = max(Decimal("0"), peso - min(fechamento, peso))
                fechamento_tipo = str(row.get("fechamento_tipo") or "total").lower()
                if (fechamento_tipo == "parcial" or aberto > 0) and aberto > 0:
                    pending_total += aberto
            self._set_runtime_cache("gold_pending_closure_grams", str(pending_total))
            return pending_total
        except Exception as exc:
            logger.warning("Falha ao calcular fechamento pendente em gramas: %s", exc)
            self._set_runtime_cache("gold_pending_closure_grams", "0")
            return Decimal("0")

    def get_gold_inventory_overview(self) -> Dict[str, Any]:
        cached = self._get_runtime_cache("gold_inventory_overview")
        if cached is not None:
            return cast(Dict[str, Any], cached)
        try:
            lots_resp = self.client.table("gold_inventory_lots").select("remaining_grams,unit_cost_usd,status").eq("status", "open").execute()
            open_lots = cast(List[Dict[str, Any]], lots_resp.data or [])
            available_grams = sum((_safe_decimal_from_row(lot, "remaining_grams") for lot in open_lots), Decimal("0"))
            open_cost = sum((_safe_decimal_from_row(lot, "remaining_grams") * _safe_decimal_from_row(lot, "unit_cost_usd") for lot in open_lots), Decimal("0"))
            avg_cost = (open_cost / available_grams) if available_grams > 0 else Decimal("0")
            return self._set_runtime_cache("gold_inventory_overview", {"lots": open_lots, "open_lots": open_lots, "available_grams": str(available_grams), "inventory_cost_usd": str(open_cost.quantize(Decimal("0.01"))), "avg_cost_usd_per_gram": str(avg_cost.quantize(Decimal("0.01")))})
        except Exception as exc:
            logger.warning("Falha ao montar overview de inventario de ouro: %s", exc)
            return {"lots": [], "open_lots": [], "available_grams": "0", "inventory_cost_usd": "0.00", "avg_cost_usd_per_gram": "0.00"}

    def update_gold_inventory_lot_monitor(self, lot_id: int, monitor_payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        try:
            current_resp = self.client.table("gold_inventory_lots").select("id,metadata").eq("id", lot_id).limit(1).execute()
            current_rows = cast(List[Dict[str, Any]], current_resp.data or [])
            if not current_rows:
                return None
            row = current_rows[0]
            metadata = cast(Dict[str, Any], row.get("metadata") or {})
            metadata["monitor"] = monitor_payload
            update_resp = self.client.table("gold_inventory_lots").update({"metadata": metadata}).eq("id", lot_id).execute()
            data = cast(List[Dict[str, Any]], update_resp.data or [])
            self._invalidate_runtime_cache(self._gold_inventory_status_cache_key(open_only=False), self._gold_inventory_status_cache_key(open_only=True))
            return data[0] if data else row
        except Exception as exc:
            logger.warning("Falha ao atualizar monitor do lote %s: %s", lot_id, exc)
            return None