from decimal import Decimal
from html import escape
from typing import Any, Callable, Dict, List, Optional, cast

from app.database import DatabaseClient


def _build_saas_suppliers_context(
    db: DatabaseClient,
    selected_supplier_id: Optional[int] = None,
    search_term: Optional[str] = None,
) -> Dict[str, Any]:
    suppliers = db.list_fornecedores_with_balances(search=search_term, limit=40)
    selected_account = db.get_fornecedor_account_snapshot(selected_supplier_id) if selected_supplier_id else None
    selected_bank_accounts = db.list_fornecedor_bank_accounts(selected_supplier_id) if selected_supplier_id else []
    return {
        "search_term": str(search_term or "").strip(),
        "suppliers": suppliers,
        "selected_account": selected_account,
        "selected_bank_accounts": selected_bank_accounts,
    }


def _render_saas_suppliers_page(
    suppliers_context: Dict[str, Any],
    values: Dict[str, str],
    *,
    build_fornecedor_lookup_meta: Callable[[Dict[str, Any]], str],
    format_caixa_movement: Callable[[str, Decimal], str],
    render_bank_account_section: Callable[..., str],
) -> str:
    selected_account = cast(Optional[Dict[str, Any]], suppliers_context.get("selected_account"))
    selected_fornecedor = cast(Optional[Dict[str, Any]], (selected_account or {}).get("fornecedor"))
    supplier_rows: List[str] = []
    for fornecedor in cast(List[Dict[str, Any]], suppliers_context.get("suppliers") or []):
        balances = cast(Dict[str, Any], fornecedor.get("balances") or {})
        supplier_rows.append(
            f"<tr><td><a href='/saas/fornecedores/{int(fornecedor.get('id') or 0)}'>{escape(str(fornecedor.get('nome') or '-'))}</a><br><small>{escape(build_fornecedor_lookup_meta(fornecedor))}</small></td><td>{escape(format_caixa_movement('USD', Decimal(str(balances.get('USD') or '0'))))}</td><td>{escape(format_caixa_movement('XAU', Decimal(str(balances.get('XAU') or '0'))))}</td></tr>"
        )
    suppliers_table_html = "".join(supplier_rows) or "<tr><td colspan='3'>Nenhum fornecedor cadastrado.</td></tr>"

    selected_summary_html = "<div class='empty-state'>Selecione um fornecedor para acompanhar adiantamentos, dividas e contas bancarias salvas.</div>"
    if selected_fornecedor and selected_account:
        balances = cast(Dict[str, Any], selected_account.get("balances") or {})
        movements = cast(List[Dict[str, Any]], selected_account.get("movements") or [])
        movement_rows = "".join(
            f"<tr><td>{escape(str(item.get('criado_em') or '')[:16].replace('T', ' '))}</td><td>{escape(str(item.get('tipo_movimento') or '-'))}</td><td>{escape(str(item.get('moeda') or '-'))}</td><td>{escape(str(item.get('valor') or '0'))}</td><td>{escape(str(item.get('descricao') or '-'))}</td></tr>"
            for item in movements[:12]
        ) or "<tr><td colspan='5'>Sem movimentacoes registradas.</td></tr>"
        balance_cards = "".join(
            f"<div class='balance'><span>{currency}</span><strong>{escape(format_caixa_movement(currency, Decimal(str(balances.get(currency) or '0'))))}</strong></div>"
            for currency in ["XAU", "USD", "EUR", "SRD", "BRL"]
        )
        bank_accounts_html = render_bank_account_section(
            title="Contas Bancarias do Fornecedor",
            hint="Cada fornecedor pode ter varias contas por moeda para recebimentos e liquidacoes futuras.",
            action=f"/saas/fornecedores/{int(selected_fornecedor.get('id') or 0)}/bank-accounts",
            page="suppliers",
            accounts=cast(List[Dict[str, Any]], suppliers_context.get("selected_bank_accounts") or []),
            empty_message="Nenhuma conta bancaria salva para este fornecedor.",
            submit_label="Salvar conta do fornecedor",
            allow_management=True,
        )
        selected_summary_html = f"""
        <div class='stack'>
            <section class='panel section'>
                <div class='section-head'>
                    <div>
                        <h2>Conta do Fornecedor</h2>
                        <p class='hint'>{escape(build_fornecedor_lookup_meta(selected_fornecedor))}</p>
                    </div>
                </div>
                <div class='balance-grid'>{balance_cards}</div>
            </section>
            <section class='panel section'>
                <h2>Lancar adiantamento ou divida</h2>
                <p class='hint'>Adiantamento credita a conta do fornecedor. Divida debita a conta e deixa o saldo visivel para cobranca ou compensacao.</p>
                <form method='post' action='/saas/fornecedores/{int(selected_fornecedor.get("id") or 0)}/movimentos'>
                    <input type='hidden' name='page' value='suppliers' />
                    <div class='fields-3'>
                        <label>Tipo
                            <select name='supplier_movement_type'>
                                <option value='adiantamento'>Adiantamento</option>
                                <option value='divida'>Divida</option>
                                <option value='ajuste_credito'>Ajuste credito</option>
                                <option value='ajuste_debito'>Ajuste debito</option>
                            </select>
                        </label>
                        <label>Moeda
                            <select name='supplier_movement_currency'>
                                <option value='USD'>USD</option>
                                <option value='EUR'>EUR</option>
                                <option value='SRD'>SRD</option>
                                <option value='BRL'>BRL</option>
                                <option value='XAU'>XAU</option>
                            </select>
                        </label>
                        <label>Valor
                            <input name='supplier_movement_amount' inputmode='decimal' required />
                        </label>
                    </div>
                    <label>Descricao
                        <input name='supplier_movement_description' />
                    </label>
                    <button type='submit'>Registrar movimento do fornecedor</button>
                </form>
            </section>
            <section class='panel section'>
                <h2>Movimentacoes do Fornecedor</h2>
                <table>
                    <thead><tr><th>Data</th><th>Tipo</th><th>Moeda</th><th>Valor</th><th>Descricao</th></tr></thead>
                    <tbody>{movement_rows}</tbody>
                </table>
            </section>
            {bank_accounts_html}
        </div>
        """

    search_value = escape(str(suppliers_context.get("search_term") or ""))
    return f"""
    <div class='grid'>
        <div class='stack'>
            <section class='panel section'>
                <div class='section-head'>
                    <div>
                        <h2>Cadastro de Fornecedor</h2>
                        <p class='hint'>Fornecedor fica separado do cliente para preservar a leitura de adiantamentos, dividas e historico de contas de recebimento.</p>
                    </div>
                </div>
                <form method='post' action='/saas/fornecedores'>
                    <input type='hidden' name='page' value='suppliers' />
                    <div class='fields-3'>
                        <label>Nome completo
                            <input name='supplier_nome' value='{escape(values.get('supplier_nome', ''))}' required />
                        </label>
                        <label>Telefone
                            <input name='supplier_telefone' value='{escape(values.get('supplier_telefone', ''))}' />
                        </label>
                        <label>Documento
                            <input name='supplier_documento' value='{escape(values.get('supplier_documento', ''))}' />
                        </label>
                    </div>
                    <div class='fields-2'>
                        <label>Apelido / referencia
                            <input name='supplier_apelido' value='{escape(values.get('supplier_apelido', ''))}' />
                        </label>
                        <label>Observacoes
                            <input name='supplier_observacoes' value='{escape(values.get('supplier_observacoes', ''))}' />
                        </label>
                    </div>
                    <div class='fields-3'>
                        <label>Saldo inicial USD
                            <input name='supplier_opening_usd' value='{escape(values.get('supplier_opening_usd', ''))}' inputmode='decimal' />
                        </label>
                        <label>Saldo inicial SRD
                            <input name='supplier_opening_srd' value='{escape(values.get('supplier_opening_srd', ''))}' inputmode='decimal' />
                        </label>
                        <label>Saldo inicial BRL
                            <input name='supplier_opening_brl' value='{escape(values.get('supplier_opening_brl', ''))}' inputmode='decimal' />
                        </label>
                    </div>
                    <button type='submit'>Registrar fornecedor</button>
                </form>
            </section>
            <section class='panel section'>
                <div class='section-head'>
                    <div>
                        <h2>Base de Fornecedores</h2>
                        <p class='hint'>A lista destaca o saldo financeiro e o saldo em ouro para facilitar negociacao de repasse, compensacao e cobranca.</p>
                    </div>
                </div>
                <form method='get' action='/saas/fornecedores' class='filter-bar'>
                    <label>Buscar fornecedor
                        <input name='q' value='{search_value}' placeholder='nome, telefone ou documento' />
                    </label>
                    <button type='submit'>Buscar</button>
                    <a href='/saas/fornecedores' class='ghost-link'>Limpar</a>
                </form>
                <table>
                    <thead><tr><th>Fornecedor</th><th>Saldo USD</th><th>Saldo Ouro</th></tr></thead>
                    <tbody>{suppliers_table_html}</tbody>
                </table>
            </section>
        </div>
        {selected_summary_html}
    </div>
    """
