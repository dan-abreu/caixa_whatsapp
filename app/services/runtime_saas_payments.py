from decimal import Decimal
from html import escape
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional

from fastapi import HTTPException

from app.database.common import _safe_int


def build_runtime_saas_payment_helpers(
    *,
    normalize_text: Callable[[str], str],
    payment_fx_prompt_label: Callable[[str], str],
    parse_decimal: Callable[[Any, str], Decimal],
    normalize_cambio_para_usd: Callable[[str, Decimal], Decimal],
    money: Callable[[Decimal], Decimal],
    fx_rate: Callable[[Decimal], Decimal],
) -> SimpleNamespace:
    def _bank_account_option_label(account: Dict[str, Any]) -> str:
        reference = str(account.get("pix_key") or account.get("account_number") or account.get("branch_code") or "Sem referencia")
        return " | ".join(
            bit
            for bit in [
                str(account.get("label") or "Conta salva"),
                str(account.get("currency_code") or "").upper(),
                str(account.get("holder_name") or ""),
                str(account.get("bank_name") or ""),
                reference,
            ]
            if bit
        )

    def _bank_account_option_html(accounts: List[Dict[str, Any]], selected_id: str) -> str:
        option_rows = ["<option value=''>Selecionar conta salva</option>"]
        for item in accounts:
            account_id = str(item.get("id") or "")
            option_rows.append(
                f"<option value='{escape(account_id)}' data-bank-currency='{escape(str(item.get('currency_code') or '').upper())}' data-bank-country='{escape(str(item.get('country_code') or '').upper())}' data-bank-summary='{escape(_bank_account_option_label(item))}' {'selected' if selected_id and selected_id == account_id else ''}>{escape(_bank_account_option_label(item))}</option>"
            )
        return "".join(option_rows)

    def _serialize_bank_account_reference(account: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "id": account.get("id"),
            "owner_kind": str(account.get("owner_kind") or ""),
            "currency_code": str(account.get("currency_code") or "").upper(),
            "country_code": str(account.get("country_code") or "").upper(),
            "label": str(account.get("label") or ""),
            "holder_name": str(account.get("holder_name") or ""),
            "bank_name": str(account.get("bank_name") or ""),
            "branch_name": str(account.get("branch_name") or ""),
            "branch_code": str(account.get("branch_code") or ""),
            "account_number": str(account.get("account_number") or ""),
            "pix_key": str(account.get("pix_key") or ""),
            "document_number": str(account.get("document_number") or ""),
        }

    def derive_forma_pagamento_summary(pagamentos: List[Dict[str, Any]]) -> str:
        if not pagamentos:
            return "dinheiro"
        methods = {str(item.get("forma_pagamento") or "dinheiro") for item in pagamentos}
        if len(methods) == 1:
            method = next(iter(methods))
            if method in {"dinheiro", "transferencia", "cheque"}:
                return method
        return "misto"

    def build_web_payment_rows_html(
        values: Dict[str, str],
        *,
        client_bank_accounts: Optional[List[Dict[str, Any]]] = None,
        company_bank_accounts: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        rows: List[str] = []
        client_accounts = list(client_bank_accounts or [])
        company_accounts = list(company_bank_accounts or [])
        for index in range(1, 5):
            currency_key = f"payment_{index}_moeda"
            amount_key = f"payment_{index}_valor"
            fx_key = f"payment_{index}_cambio"
            percent_key = f"payment_{index}_percent"
            method_key = f"payment_{index}_forma"
            moeda = values.get(currency_key, "USD" if index == 1 else "")
            valor = values.get(amount_key, "")
            cambio = values.get(fx_key, "1" if moeda == "USD" and index == 1 else "")
            percent = values.get(percent_key, "")
            forma = values.get(method_key, "dinheiro")
            client_bank_key = f"payment_{index}_client_bank_account_id"
            company_bank_key = f"payment_{index}_company_bank_account_id"
            selected_client_bank_id = values.get(client_bank_key, "")
            selected_company_bank_id = values.get(company_bank_key, "")
            transfer_box_class = "" if forma == "transferencia" else "is-hidden"
            rows.append(
                f"""
            <div class='payment-row js-payment-row'>
                <label>Moeda #{index}
                    <select name='{currency_key}' class='js-payment-moeda'>
                        <option value='' {'selected' if not moeda else ''}>-</option>
                        <option value='USD' {'selected' if moeda=='USD' else ''}>USD</option>
                        <option value='EUR' {'selected' if moeda=='EUR' else ''}>EUR</option>
                        <option value='SRD' {'selected' if moeda=='SRD' else ''}>SRD</option>
                        <option value='BRL' {'selected' if moeda=='BRL' else ''}>BRL</option>
                    </select>
                </label>
                <label>Valor na moeda
                    <input name='{amount_key}' value='{escape(valor)}' placeholder='ex.: 380' class='js-payment-valor' inputmode='decimal' />
                </label>
                <label>% do pagamento
                    <input name='{percent_key}' value='{escape(percent)}' placeholder='ex.: 40' class='js-payment-percent' inputmode='decimal' />
                </label>
                <label><span class='js-payment-cambio-label'>{escape(payment_fx_prompt_label(moeda))}</span>
                    <input name='{fx_key}' value='{escape(cambio)}' placeholder='vazio = último câmbio' class='js-payment-cambio' inputmode='decimal' />
                </label>
                <label>Forma
                    <select name='{method_key}' class='js-payment-forma'>
                        <option value='dinheiro' {'selected' if forma=='dinheiro' else ''}>Dinheiro</option>
                        <option value='transferencia' {'selected' if forma=='transferencia' else ''}>Transferência</option>
                        <option value='cheque' {'selected' if forma=='cheque' else ''}>Cheque</option>
                    </select>
                </label>
                <div class='payment-preview js-payment-preview'>USD 0.00</div>
                <div class='tip-box payment-transfer-box js-payment-transfer-box {transfer_box_class}'>
                    <div class='fields-2'>
                        <label>Conta do cliente
                            <select name='{client_bank_key}' class='js-client-bank-account-select'>
                                {_bank_account_option_html(client_accounts, selected_client_bank_id)}
                            </select>
                        </label>
                        <label>Conta da empresa
                            <select name='{company_bank_key}' class='js-company-bank-account-select'>
                                {_bank_account_option_html(company_accounts, selected_company_bank_id)}
                            </select>
                        </label>
                    </div>
                    <p class='hint js-payment-transfer-summary'>Quando a linha for transferencia, selecione as contas salvas para SRD, BRL ou demais moedas operadas.</p>
                </div>
            </div>
            """
            )
        return "".join(rows)

    def parse_decimal_web_field(raw: str, field_name: str) -> Decimal:
        return parse_decimal(str(raw or "0").strip().replace(",", "."), field_name)

    def parse_web_payments_from_form(db: Any, form: Dict[str, str]) -> List[Dict[str, Any]]:
        pagamentos: List[Dict[str, Any]] = []
        for index in range(1, 5):
            currency_key = f"payment_{index}_moeda"
            amount_key = f"payment_{index}_valor"
            fx_key = f"payment_{index}_cambio"
            method_key = f"payment_{index}_forma"
            moeda_raw = str(form.get(currency_key) or "").strip().upper()
            valor_raw = str(form.get(amount_key) or "").strip()
            cambio_raw = str(form.get(fx_key) or "").strip()
            forma = normalize_text(str(form.get(method_key) or "dinheiro"))

            if not any([moeda_raw, valor_raw, cambio_raw]):
                continue
            if not moeda_raw or not valor_raw:
                raise HTTPException(status_code=400, detail=f"Pagamento #{index} incompleto")
            if moeda_raw not in {"USD", "EUR", "SRD", "BRL"}:
                raise HTTPException(status_code=400, detail=f"Moeda inválida no pagamento #{index}")
            if forma not in {"dinheiro", "transferencia", "cheque"}:
                raise HTTPException(status_code=400, detail=f"Forma inválida no pagamento #{index}")

            valor_moeda = parse_decimal_web_field(valor_raw, amount_key)
            if valor_moeda <= 0:
                raise HTTPException(status_code=400, detail=f"Valor do pagamento #{index} deve ser maior que zero")

            if moeda_raw == "USD":
                cambio_para_usd = Decimal("1")
            elif cambio_raw:
                cambio_para_usd = normalize_cambio_para_usd(moeda_raw, parse_decimal_web_field(cambio_raw, fx_key))
            else:
                last_cambio = db.get_last_cambio_para_usd(moeda_raw)
                if not last_cambio or Decimal(str(last_cambio)) <= 0:
                    raise HTTPException(status_code=400, detail=f"Sem câmbio disponível para {moeda_raw} no pagamento #{index}")
                cambio_para_usd = fx_rate(Decimal(str(last_cambio)))

            if cambio_para_usd <= 0:
                raise HTTPException(status_code=400, detail=f"Câmbio inválido no pagamento #{index}")

            transfer_details: Dict[str, Any] = {}
            if forma == "transferencia":
                client_bank_id = _safe_int(form.get(f"payment_{index}_client_bank_account_id"), context=f"payment_{index}.client_bank_account_id")
                company_bank_id = _safe_int(form.get(f"payment_{index}_company_bank_account_id"), context=f"payment_{index}.company_bank_account_id")
                if client_bank_id > 0:
                    client_account = db.get_saved_bank_account_by_id(client_bank_id)
                    if not client_account or str(client_account.get("owner_kind") or "") != "cliente" or str(client_account.get("currency_code") or "").upper() != moeda_raw:
                        raise HTTPException(status_code=400, detail=f"Conta do cliente inválida no pagamento #{index}")
                    transfer_details["client_bank_account"] = _serialize_bank_account_reference(client_account)
                if company_bank_id > 0:
                    company_account = db.get_saved_bank_account_by_id(company_bank_id)
                    if not company_account or str(company_account.get("owner_kind") or "") != "empresa" or str(company_account.get("currency_code") or "").upper() != moeda_raw:
                        raise HTTPException(status_code=400, detail=f"Conta da empresa inválida no pagamento #{index}")
                    transfer_details["company_bank_account"] = _serialize_bank_account_reference(company_account)

            pagamentos.append(
                {
                    "moeda": moeda_raw,
                    "valor_moeda": str(money(valor_moeda)),
                    "cambio_para_usd": str(cambio_para_usd),
                    "valor_usd": str(money(valor_moeda / cambio_para_usd)),
                    "forma_pagamento": forma,
                    "transfer_details": transfer_details,
                }
            )

        if pagamentos:
            return pagamentos

        total_pago_raw = str(form.get("total_pago_usd") or "").strip()
        forma_pagamento = normalize_text(str(form.get("forma_pagamento") or "dinheiro"))
        if total_pago_raw:
            total_pago = parse_decimal_web_field(total_pago_raw, "total_pago_usd")
            if total_pago <= 0:
                raise HTTPException(status_code=400, detail="Total pago deve ser maior que zero")
            return [
                {
                    "moeda": "USD",
                    "valor_moeda": str(money(total_pago)),
                    "cambio_para_usd": "1",
                    "valor_usd": str(money(total_pago)),
                    "forma_pagamento": forma_pagamento if forma_pagamento in {"dinheiro", "transferencia", "cheque"} else "dinheiro",
                }
            ]

        raise HTTPException(status_code=400, detail="Informe ao menos um pagamento")

    return SimpleNamespace(
        derive_forma_pagamento_summary=derive_forma_pagamento_summary,
        build_web_payment_rows_html=build_web_payment_rows_html,
        parse_decimal_web_field=parse_decimal_web_field,
        parse_web_payments_from_form=parse_web_payments_from_form,
    )