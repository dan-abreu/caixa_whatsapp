from html import escape
from types import SimpleNamespace
from typing import Any, Dict, List


def build_bank_accounts_ui_helpers() -> SimpleNamespace:
    def format_bank_account_summary(account: Dict[str, Any]) -> str:
        currency = str(account.get("currency_code") or "-").upper()
        country = str(account.get("country_code") or "OTHER").upper()
        label = str(account.get("label") or "Conta salva")
        holder = str(account.get("holder_name") or "-")
        bank = str(account.get("bank_name") or "Banco nao informado")
        reference = str(account.get("pix_key") or account.get("account_number") or account.get("branch_code") or "Sem referencia")
        return f"{label} | {currency}/{country} | {holder} | {bank} | {reference}"

    def render_bank_account_section(
        *,
        title: str,
        hint: str,
        action: str,
        page: str,
        accounts: List[Dict[str, Any]],
        empty_message: str,
        submit_label: str,
        allow_management: bool,
    ) -> str:
        account_rows = "".join(
            f"<tr><td>{escape(str(item.get('label') or '-'))}</td><td>{escape(str(item.get('currency_code') or '-'))}</td><td>{escape(str(item.get('country_code') or '-'))}</td><td>{escape(str(item.get('holder_name') or '-'))}</td><td>{escape(str(item.get('bank_name') or '-'))}</td><td>{escape(str(item.get('pix_key') or item.get('account_number') or '-'))}</td></tr>"
            for item in accounts
        ) or f"<tr><td colspan='6'>{escape(empty_message)}</td></tr>"
        management_html = "<p class='hint'>Somente administradores podem adicionar novas contas corporativas.</p>"
        if allow_management:
            management_html = f"""
            <form method='post' action='{escape(action)}'>
                <input type='hidden' name='page' value='{escape(page)}' />
                <div class='fields-3'>
                    <label>Apelido da conta
                        <input name='bank_label' required />
                    </label>
                    <label>Moeda
                        <select name='bank_currency_code'>
                            <option value='USD'>USD</option>
                            <option value='EUR'>EUR</option>
                            <option value='SRD'>SRD</option>
                            <option value='BRL'>BRL</option>
                        </select>
                    </label>
                    <label>Pais / padrao bancario
                        <select name='bank_country_code'>
                            <option value=''>Auto pela moeda</option>
                            <option value='SR'>Suriname</option>
                            <option value='BR'>Brasil</option>
                            <option value='OTHER'>Outro</option>
                        </select>
                    </label>
                </div>
                <div class='fields-3'>
                    <label>Titular
                        <input name='bank_holder_name' required />
                    </label>
                    <label>Banco
                        <input name='bank_bank_name' />
                    </label>
                    <label>Numero da conta
                        <input name='bank_account_number' />
                    </label>
                </div>
                <div class='fields-3'>
                    <label>Agencia / branch code
                        <input name='bank_branch_code' />
                    </label>
                    <label>Branch / local
                        <input name='bank_branch_name' />
                    </label>
                    <label>Chave PIX
                        <input name='bank_pix_key' />
                    </label>
                </div>
                <div class='fields-2'>
                    <label>Documento do titular
                        <input name='bank_document_number' />
                    </label>
                    <label>Observacoes
                        <input name='bank_notes' />
                    </label>
                </div>
                <label data-quick-optional='1'><input type='checkbox' name='bank_is_default' value='1' style='width:auto;margin-right:8px;' /> Marcar como conta padrao dessa moeda</label>
                <button type='submit'>{escape(submit_label)}</button>
            </form>
            """
        return f"""
        <section class='panel section'>
            <div class='section-head'>
                <div>
                    <h2>{escape(title)}</h2>
                    <p class='hint'>{escape(hint)}</p>
                </div>
            </div>
            <table>
                <thead><tr><th>Conta</th><th>Moeda</th><th>Pais</th><th>Titular</th><th>Banco</th><th>Referencia</th></tr></thead>
                <tbody>{account_rows}</tbody>
            </table>
            {management_html}
        </section>
        """

    return SimpleNamespace(
        format_bank_account_summary=format_bank_account_summary,
        render_bank_account_section=render_bank_account_section,
    )
