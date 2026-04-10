from typing import cast


class _FakeResponse:
    def __init__(self, data):
        self.data = data


class _FakeTable:
    def __init__(self, store, name):
        self.store = store
        self.name = name
        self._filters = []
        self._range_filters = []
        self._null_filters = []
        self._negate_next = False
        self._ilike_filters = []
        self._in_filters = []
        self._order_by = None
        self._limit = None
        self._pending_insert = None
        self._delete_mode = False
        self._selected_fields = None

    def select(self, _fields):
        self._selected_fields = _fields
        return self

    def order(self, field, desc=False):
        self._order_by = (field, desc)
        return self

    def eq(self, field, value):
        self._filters.append((field, value))
        return self

    def gte(self, field, value):
        self._range_filters.append((field, value, "gte"))
        return self

    def lt(self, field, value):
        self._range_filters.append((field, value, "lt"))
        return self

    def ilike(self, field, value):
        self._ilike_filters.append((field, str(value)))
        return self

    def limit(self, value):
        self._limit = value
        return self

    def in_(self, field, values):
        self._in_filters.append((field, set(values)))
        return self

    def neq(self, _field, _value):
        return self

    @property
    def not_(self):
        self._negate_next = True
        return self

    def is_(self, field, value):
        self._null_filters.append((field, value, self._negate_next))
        self._negate_next = False
        return self

    def delete(self):
        self._delete_mode = True
        return self

    def insert(self, payload):
        self._pending_insert = [dict(item) for item in payload] if isinstance(payload, list) else dict(payload)
        return self

    def update(self, payload):
        self._pending_insert = {"__update__": dict(payload)}
        return self

    def execute(self):
        if self._delete_mode:
            self.store[self.name] = []
            self._delete_mode = False
            return _FakeResponse([])
        if self._pending_insert is not None:
            pending = self._pending_insert
            self._pending_insert = None
            if isinstance(pending, dict) and "__update__" in pending:
                changes = cast(dict, pending["__update__"])
                updated_rows = []
                for row in self.store[self.name]:
                    if any(row.get(field) != value for field, value in self._filters):
                        continue
                    row.update(changes)
                    updated_rows.append(dict(row))
                self._filters = []
                return _FakeResponse(updated_rows)
            if isinstance(pending, list):
                rows = []
                for item in pending:
                    row = dict(item)
                    row["id"] = len(self.store[self.name]) + 1
                    self.store[self.name].append(row)
                    rows.append(row)
                return _FakeResponse(rows)
            row = dict(pending)
            row["id"] = len(self.store[self.name]) + 1
            self.store[self.name].append(row)
            return _FakeResponse([row])
        rows = [dict(row) for row in self.store[self.name]]
        for field, value in self._filters:
            rows = [row for row in rows if row.get(field) == value]
        for field, value, operator in self._range_filters:
            if operator == "gte":
                rows = [row for row in rows if row.get(field) is not None and row.get(field) >= value]
            elif operator == "lt":
                rows = [row for row in rows if row.get(field) is not None and row.get(field) < value]
        for field, value, negate in self._null_filters:
            if str(value).lower() == "null":
                rows = [row for row in rows if (row.get(field) is not None) == negate]
        if self._ilike_filters:
            self.store["_cliente_search_exec_count"] = int(self.store.get("_cliente_search_exec_count", 0)) + 1
        for field, pattern in self._ilike_filters:
            needle = pattern.replace("%", "").lower()
            rows = [row for row in rows if needle in str(row.get(field) or "").lower()]
        for field, values in self._in_filters:
            rows = [row for row in rows if row.get(field) in values]
        if self._order_by:
            field, desc = self._order_by
            rows = sorted(rows, key=lambda row: row.get(field), reverse=desc)
        if self._limit is not None:
            rows = rows[: self._limit]
        self._filters = []
        self._range_filters = []
        self._null_filters = []
        self._negate_next = False
        self._ilike_filters = []
        self._in_filters = []
        self._limit = None
        return _FakeResponse(rows)


class _FakeSupabaseClient:
    def __init__(self):
        self.store = {
            "clientes": [],
            "gold_transactions": [],
            "gold_inventory_lots": [],
            "gold_inventory_consumptions": [],
            "cliente_movimentacoes": [],
        }

    def table(self, name):
        return _FakeTable(self.store, name)


class _FakeMissingWebPinTable(_FakeTable):
    def execute(self):
        if self.name == "usuarios":
            self.store["_base_select_attempts"] = self.store.get("_base_select_attempts", 0) + 1
            if isinstance(self._pending_insert, dict) and "__update__" in self._pending_insert:
                changes = cast(dict, self._pending_insert["__update__"])
                if "web_pin_hash" in changes or "web_pin_updated_em" in changes:
                    self.store["_web_pin_update_attempts"] = self.store.get("_web_pin_update_attempts", 0) + 1
                    raise Exception("{'message': 'column usuarios.web_pin_hash does not exist', 'code': '42703'}")
        return super().execute()


class _FakeMissingWebPinSupabaseClient(_FakeSupabaseClient):
    def __init__(self):
        super().__init__()
        self.store["_base_select_attempts"] = 0
        self.store["_web_pin_update_attempts"] = 0
        self.store["usuarios"] = [{"id": 8, "nome": "Daniel", "telefone": "+5598991438754", "tipo_usuario": "admin", "ativo": True}]

    def table(self, name):
        return _FakeMissingWebPinTable(self.store, name)
