from decimal import Decimal
from html import escape
from types import SimpleNamespace
from typing import Any, Callable, Dict, List


def build_runtime_saas_layout_helpers(*, money: Callable[[Decimal], Decimal]) -> SimpleNamespace:
    def build_nav_html(current_page: str) -> str:
        nav_items = [
            ("dashboard", "/saas/dashboard", "Dashboard", "<svg viewBox='0 0 24 24' aria-hidden='true'><path d='M4 13h7V4H4zm9 7h7V4h-7zm-9 0h7v-5H4z'/></svg>"),
            ("monitors", "/saas/monitores", "Monitores IA", "<svg viewBox='0 0 24 24' aria-hidden='true'><path d='M3 17h3V7H3zm5 4h3V3H8zm5-6h3V9h-3zm5 4h3V5h-3z'/></svg>"),
            ("news_hub", "/saas/noticias", "Noticias", "<svg viewBox='0 0 24 24' aria-hidden='true'><path d='M4 5h14v14H4zm3 3v2h8V8zm0 4v2h8v-2zm0 4v2h5v-2zm13-8h-1v9a2 2 0 0 1-2 2H7v1h10a3 3 0 0 0 3-3z'/></svg>"),
            ("operation", "/saas/operation", "Operacao", "<svg viewBox='0 0 24 24' aria-hidden='true'><path d='M19 11h-6V5h-2v6H5v2h6v6h2v-6h6z'/></svg>"),
            ("clients", "/saas/clientes", "Clientes", "<svg viewBox='0 0 24 24' aria-hidden='true'><path d='M16 11c1.66 0 2.99-1.57 2.99-3.5S17.66 4 16 4s-3 1.57-3 3.5S14.34 11 16 11m-8 0c1.66 0 2.99-1.57 2.99-3.5S9.66 4 8 4 5 5.57 5 7.5 6.34 11 8 11m0 2c-2.33 0-7 1.17-7 3.5V19h14v-2.5C15 14.17 10.33 13 8 13m8 0c-.29 0-.62.02-.97.05 1.16.84 1.97 1.94 1.97 3.45V19h6v-2.5c0-2.33-4.67-3.5-7-3.5'/></svg>"),
            ("suppliers", "/saas/fornecedores", "Fornecedores", "<svg viewBox='0 0 24 24' aria-hidden='true'><path d='M4 7h16v12H4zm2 2v8h12V9zm9-6h2v3h-2zM7 3h2v3H7z'/></svg>"),
            ("statement", "/saas/extrato", "Extrato", "<svg viewBox='0 0 24 24' aria-hidden='true'><path d='M6 2h9l5 5v15a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2m8 1.5V8h4.5zM8 12v2h8v-2zm0 4v2h6v-2z'/></svg>"),
            ("profile", "/saas/profile", "Perfil", "<svg viewBox='0 0 24 24' aria-hidden='true'><path d='M12 12a4 4 0 1 0-4-4 4 4 0 0 0 4 4m0 2c-3.33 0-6 1.79-6 4v2h12v-2c0-2.21-2.67-4-6-4'/></svg>"),
        ]
        return "".join(
            f"<a href='{href}' class='nav-link {'active' if current_page == key else ''}'><span class='nav-icon'>{icon}</span><span class='nav-label'>{label}</span></a>"
            for key, href, label, icon in nav_items
        )

    def build_statement_rows_html(
        statement_transactions: List[Dict[str, Any]],
        *,
        build_fechamento_status: Callable[[Dict[str, Any]], Dict[str, Any]],
    ) -> str:
        fallback = "<tr><td colspan='7'>Nenhuma operacao encontrada para o periodo.</td></tr>"
        statement_rows: List[str] = []
        for item in reversed(statement_transactions):
            source = str(item.get("source") or "transacoes")
            item_id = str(item.get("id") or "-")
            id_label = f"GT-{item_id}" if source == "gold_transactions" else f"T-{item_id}"
            fechamento_status = build_fechamento_status(item)
            fechamento_txt = "Total"
            if bool(fechamento_status["is_partial"]):
                fechamento_txt = (
                    f"Parcial: {Decimal(str(fechamento_status['fechado'])):,.3f} g fechados"
                    f" | {Decimal(str(fechamento_status['aberto'])):,.3f} g em aberto"
                )
            pagamentos = item.get("pagamentos") or []
            pagamentos_txt = ", ".join(
                f"{str(p.get('moeda') or 'USD').upper()} {money(Decimal(str(p.get('valor_moeda') or '0')))}"
                for p in pagamentos
            ) or "-"
            statement_rows.append(
                f"<tr><td>{escape(id_label)}</td><td>{escape(str(item.get('tipo_operacao') or '-').upper())}</td><td>{escape(str(item.get('pessoa') or '-'))}</td><td>{escape(str(item.get('peso') or '0'))} g</td><td>USD {escape(str(item.get('total_usd') or '0'))}</td><td>{escape(fechamento_txt)}</td><td>{escape(pagamentos_txt)}</td></tr>"
            )
        return "".join(statement_rows) or fallback

    def build_open_fechamentos_statement_html(open_fechamentos_statement: List[Dict[str, Any]]) -> str:
        fallback = "<tr><td colspan='5'>Nenhum fechamento parcial em aberto nesse periodo.</td></tr>"
        rows: List[str] = []
        for item in open_fechamentos_statement[:12]:
            source = str(item.get("source") or "gold_transactions")
            item_id = str(item.get("id") or "-")
            id_label = f"GT-{item_id}" if source == "gold_transactions" else f"T-{item_id}"
            status = item.get("fechamento_status") or {}
            rows.append(
                f"<tr><td>{escape(id_label)}</td><td>{escape(str(item.get('pessoa') or '-'))}</td><td>{escape(str(item.get('peso') or '0'))} g</td><td>{escape(str(status.get('fechado') or '0'))} g</td><td>{escape(str(status.get('aberto') or '0'))} g</td></tr>"
            )
        return "".join(rows) or fallback

    def build_shared_top_shell_html(*, user_name: str, nav_html: str, day_date: str, sidebar_inventory_grams: str, market_rail_html: str) -> str:
        sidebar_inventory_html = ""
        if sidebar_inventory_grams:
            sidebar_inventory_html = f"""
                <div class='sidebar-metric'>
                    <span>Estoque</span>
                    <strong>{escape(sidebar_inventory_grams)} g</strong>
                </div>
        """

        return f"""
    <aside class='app-sidebar panel'>
        <div class='sidebar-shell'>
            <div class='sidebar-brand'>
                <div class='sidebar-brand-mark'>CW</div>
                <div class='sidebar-brand-copy'>
                    <span class='sidebar-kicker'>Caixa de compra</span>
                    <strong>Caixa SaaS</strong>
                    <small>{user_name}</small>
                </div>
            </div>
            <nav class='sidebar-nav'>{nav_html}</nav>
            <div class='sidebar-footer'>
                <div class='sidebar-metric'>
                    <span>Data</span>
                    <strong>{escape(day_date)}</strong>
                </div>
                {sidebar_inventory_html}
                <div class='sidebar-actions'>
                    <a href='/reports/inventory-status' class='ghost-link mini-action sidebar-link' target='_blank'>JSON estoque</a>
                    <form method='post' action='/saas/logout'><button class='ghost-btn mini-action sidebar-link' type='submit'>Sair</button></form>
                </div>
            </div>
        </div>
    </aside>
    {market_rail_html}
    """

    def build_monitor_alerts_html(web_lot_ai_alerts: List[Dict[str, Any]]) -> str:
        fallback = "<tr><td colspan='4'>Nenhum gatilho ativo agora.</td></tr>"
        rows: List[str] = []
        for alert in web_lot_ai_alerts[:8]:
            rows.append(
                f"<tr><td>GT-{escape(str(alert.get('source_transaction_id') or '-'))}</td><td>{escape(str(alert.get('status_label') or '-'))}</td><td>{escape(str(alert.get('profit_pct') or '0'))}%</td><td>{escape(str(alert.get('reason') or '-'))}</td></tr>"
            )
        return "".join(rows) or fallback

    return SimpleNamespace(
        build_nav_html=build_nav_html,
        build_statement_rows_html=build_statement_rows_html,
        build_open_fechamentos_statement_html=build_open_fechamentos_statement_html,
        build_shared_top_shell_html=build_shared_top_shell_html,
        build_monitor_alerts_html=build_monitor_alerts_html,
    )