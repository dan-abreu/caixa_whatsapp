from .bank_accounts import BankAccountsMixin
from .base import DatabaseClientBase
from .caixas_rebuild import CaixasRebuildMixin
from .caixas_runtime import CaixasRuntimeMixin
from .client_accounts import ClientAccountsMixin
from .common import (
    DatabaseError,
    _aggregate_cliente_movements,
    _aggregate_cliente_movements_by_client,
    _hash_web_pin,
    _verify_web_pin,
)
from .gold_transactions import GoldTransactionsMixin
from .inventory_ledger import InventoryLedgerMixin
from .inventory_status import InventoryStatusMixin
from .legacy_transactions import LegacyTransactionsMixin
from .lookups import LookupMixin
from .multi_agent import MultiAgentMixin
from .reporting import ReportingMixin
from .supplier_accounts import SupplierAccountsMixin
from .transfer_money import TransferMoneyMixin


class DatabaseClient(
    DatabaseClientBase,
    LookupMixin,
    ClientAccountsMixin,
    SupplierAccountsMixin,
    BankAccountsMixin,
    InventoryLedgerMixin,
    InventoryStatusMixin,
    LegacyTransactionsMixin,
    GoldTransactionsMixin,
    TransferMoneyMixin,
    ReportingMixin,
    CaixasRuntimeMixin,
    CaixasRebuildMixin,
    MultiAgentMixin,
):
    pass


__all__ = [
    "DatabaseClient",
    "DatabaseError",
    "_aggregate_cliente_movements",
    "_aggregate_cliente_movements_by_client",
    "_hash_web_pin",
    "_verify_web_pin",
]