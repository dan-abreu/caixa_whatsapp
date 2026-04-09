from decimal import Decimal
from html import escape
from typing import Any, Callable, Dict, List, Optional, cast

from app.database import DatabaseClient


def _build_saas_clients_context(
    db: DatabaseClient,
    selected_client_id: Optional[int] = None,
    search_term: Optional[str] = None,
) -> Dict[str, Any]:
    clients = db.list_clientes_with_balances(search=search_term, limit=40)
    selected_account = db.get_cliente_account_snapshot(selected_client_id) if selected_client_id else None
    return {
        "search_term": str(search_term or "").strip(),
        "clients": clients,
        "selected_account": selected_account,
    }


def _render_saas_clients_page(
    clients_context: Dict[str, Any],
    values: Dict[str, str],
    *,
    build_cliente_lookup_meta: Callable[[Dict[str, Any]], str],
    format_caixa_movement: Callable[[str, Decimal], str],
) -> str:
    selected_account = cast(Optional[Dict[str, Any]], clients_context.get("selected_account"))
    selected_cliente = cast(Optional[Dict[str, Any]], (selected_account or {}).get("cliente"))
    client_rows: List[str] = []
    for cliente in cast(List[Dict[str, Any]], clients_context.get("clients") or []):
        balances = cast(Dict[str, Any], cliente.get("balances") or {})
        gold_balance = Decimal(str(balances.get("XAU") or "0"))
        client_rows.append(
            f"<tr><td><a href='/saas/clientes/{int(cliente.get('id') or 0)}'>{escape(str(cliente.get('nome') or '-'))}</a><br><small>{escape(build_cliente_lookup_meta(cliente))}</small></td><td>{escape(format_caixa_movement('XAU', gold_balance))}</td><td>{escape(str(cliente.get('observacoes') or '-'))}</td></tr>"
        )
    clients_table_html = "".join(client_rows) or "<tr><td colspan='3'>Nenhum cliente cadastrado.</td></tr>"

    selected_summary_html = "<div class='empty-state'>Selecione um cliente para abrir a conta e acompanhar saldo, historico e operacoes recentes.</div>"
    if selected_cliente and selected_account:
        balances = cast(Dict[str, Any], selected_account.get("balances") or {})
        movements = cast(List[Dict[str, Any]], selected_account.get("movements") or [])
        recent_transactions = cast(List[Dict[str, Any]], selected_account.get("recent_transactions") or [])
        balance_cards = "".join(
            f"<div class='balance'><span>{currency}</span><strong>{escape(format_caixa_movement(currency, Decimal(str(balances.get(currency) or '0'))))}</strong></div>"
            for currency in ["XAU", "USD", "EUR", "SRD", "BRL"]
        )
        movement_rows = "".join(
            f"<tr><td>{escape(str(item.get('criado_em') or '')[:16].replace('T', ' '))}</td><td>{escape(str(item.get('tipo_movimento') or '-'))}</td><td>{escape(str(item.get('moeda') or '-'))}</td><td>{escape(str(item.get('valor') or '0'))}</td></tr>"
            for item in movements[:12]
        ) or "<tr><td colspan='4'>Sem movimentacoes de saldo.</td></tr>"
        transaction_rows = "".join(
            f"<tr><td>GT-{escape(str(item.get('id') or '-'))}</td><td>{escape(str(item.get('tipo_operacao') or '-').upper())}</td><td>{escape(str(item.get('peso') or '0'))} g</td><td>{escape(str(item.get('fechamento_gramas') or '0'))} g</td></tr>"
            for item in recent_transactions[:10]
        ) or "<tr><td colspan='4'>Sem operacoes vinculadas.</td></tr>"
        selected_summary_html = f"""
        <div class='stack'>
            <section class='panel section'>
                <div class='section-head'>
                    <div>
                        <h2>Conta do Cliente</h2>
                        <p class='hint'>{escape(build_cliente_lookup_meta(selected_cliente))}</p>
                    </div>
                    <a class='ghost-link mini-action' href='/saas/operation?client_id={int(selected_cliente.get("id") or 0)}'>Usar no lancamento</a>
                </div>
                <div class='balance-grid'>{balance_cards}</div>
            </section>
            <section class='panel section'>
                <h2>Movimentacoes de Saldo</h2>
                <table>
                    <thead><tr><th>Data</th><th>Tipo</th><th>Moeda</th><th>Valor</th></tr></thead>
                    <tbody>{movement_rows}</tbody>
                </table>
            </section>
            <section class='panel section'>
                <h2>Operacoes Recentes</h2>
                <table>
                    <thead><tr><th>ID</th><th>Tipo</th><th>Peso</th><th>Fechamento</th></tr></thead>
                    <tbody>{transaction_rows}</tbody>
                </table>
            </section>
        </div>
        """

    search_value = escape(str(clients_context.get("search_term") or ""))
    return f"""
    <div class='grid'>
        <div class='stack'>
            <section class='panel section'>
                <div class='section-head'>
                    <div>
                        <h2>Cadastro de Cliente</h2>
                        <p class='hint'>Cadastre pessoas com nome repetido sem perda de contexto. Telefone, documento e apelido ajudam a diferenciar clientes homonimos no lancamento.</p>
                    </div>
                </div>
                <form method='post' action='/saas/clientes'>
                    <input type='hidden' name='page' value='clients' />
                    <div class='fields-3'>
                        <label>Nome completo
                            <input name='client_nome' value='{escape(values.get('client_nome', ''))}' required />
                        </label>
                        <label>Telefone
                            <input name='client_telefone' value='{escape(values.get('client_telefone', ''))}' />
                        </label>
                        <label>Documento
                            <input name='client_documento' value='{escape(values.get('client_documento', ''))}' />
                        </label>
                    </div>
                    <div class='fields-2'>
                        <label>Apelido / referencia
                            <input name='client_apelido' value='{escape(values.get('client_apelido', ''))}' />
                        </label>
                        <label>Observacoes
                            <input name='client_observacoes' value='{escape(values.get('client_observacoes', ''))}' />
                        </label>
                    </div>
                    <div class='fields-3'>
                        <label>Saldo inicial em ouro (g)
                            <input name='client_opening_xau' value='{escape(values.get('client_opening_xau', ''))}' inputmode='decimal' />
                        </label>
                        <label>Saldo inicial USD
                            <input name='client_opening_usd' value='{escape(values.get('client_opening_usd', ''))}' inputmode='decimal' />
                        </label>
                        <label>Saldo inicial EUR
                            <input name='client_opening_eur' value='{escape(values.get('client_opening_eur', ''))}' inputmode='decimal' />
                        </label>
                    </div>
                    <div class='fields-2'>
                        <label>Saldo inicial SRD
                            <input name='client_opening_srd' value='{escape(values.get('client_opening_srd', ''))}' inputmode='decimal' />
                        </label>
                        <label>Saldo inicial BRL
                            <input name='client_opening_brl' value='{escape(values.get('client_opening_brl', ''))}' inputmode='decimal' />
                        </label>
                    </div>
                    <button type='submit'>Registrar cliente</button>
                </form>
            </section>
            <section class='panel section'>
                <div class='section-head'>
                    <div>
                        <h2>Base de Clientes</h2>
                        <p class='hint'>A lista abaixo mostra o saldo em ouro por conta do cliente e abre o detalhe completo ao selecionar um registro.</p>
                    </div>
                </div>
                <form method='get' action='/saas/clientes' class='filter-bar'>
                    <label>Buscar cliente
                        <input name='q' value='{search_value}' placeholder='nome, telefone ou documento' />
                    </label>
                    <button type='submit'>Buscar</button>
                    <a href='/saas/clientes' class='ghost-link'>Limpar</a>
                </form>
                <table>
                    <thead><tr><th>Cliente</th><th>Saldo Ouro</th><th>Observacoes</th></tr></thead>
                    <tbody>{clients_table_html}</tbody>
                </table>
            </section>
        </div>
        {selected_summary_html}
    </div>
    """