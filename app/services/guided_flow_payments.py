from decimal import Decimal
from types import SimpleNamespace
from typing import Any, Callable, Dict, Mapping, Optional


def build_guided_flow_payment_helpers() -> SimpleNamespace:
    def handle_payment_states(
        *,
        estado: str,
        db: Any,
        remetente: str,
        mensagem: str,
        contexto: Dict[str, Any],
        money: Callable[[Decimal], Decimal],
        parse_decimal_from_text: Callable[[str, str], Decimal],
        save_session: Callable[[Any, str, str, Dict[str, Any]], None],
        clear_session: Callable[[Any, str], None],
        normalize_cambio_para_usd: Callable[[str, Decimal], Decimal],
        try_set_total_usd_from_base_rate: Callable[[Dict[str, Any], Decimal], bool],
        pair_rate_to_payment_per_usd: Callable[[str, str, Decimal, Any], tuple[Optional[Decimal], Decimal, Optional[Decimal]]],
        moeda_strength: Mapping[str, int],
        fx_rate: Callable[[Decimal], Decimal],
        advance_after_payment_exchange: Callable[[Any, str, Dict[str, Any], list[Dict[str, Any]]], Dict[str, Any]],
        build_cambio_prompt: Callable[[str], str],
    ) -> Optional[Dict[str, Any]]:
        if estado == "await_cambio_moeda_pre_valor":
            cambio = parse_decimal_from_text(mensagem, "cambio_pre_valor")
            if cambio <= 0:
                return {"mensagem": "Câmbio deve ser maior que zero.", "dados": {"etapa": estado}}

            moeda_atual = str(contexto.get("moeda_atual", "USD")).upper()
            preco_moeda = str(contexto.get("preco_moeda", "USD")).upper()

            if moeda_atual == "USD" and preco_moeda == "USD":
                save_session(db, remetente, "await_valor_moeda", contexto)
                return {"mensagem": "Quanto será pago em USD?", "dados": {"etapa": "await_valor_moeda"}}

            if moeda_atual == "USD" and preco_moeda != "USD":
                cambio_normalizado = normalize_cambio_para_usd(preco_moeda, cambio)
                try_set_total_usd_from_base_rate(contexto, cambio_normalizado)
                total_usd = Decimal(str(contexto.get("total_usd", "0")))
                total_moeda = Decimal(str(contexto.get("total_moeda", "0")))
                lines = [f"Câmbio: 1 {preco_moeda} = {money(cambio)} USD."]
                if total_usd > 0:
                    lines.append(f"Total equivalente: ~{money(total_usd)} USD.")
                elif total_moeda > 0:
                    lines.append(f"Total da operação: {money(total_moeda)} {preco_moeda}.")
                lines.append("Quanto será pago em USD?")
                save_session(db, remetente, "await_valor_moeda", contexto)
                return {"mensagem": "\n".join(lines), "dados": {"etapa": "await_valor_moeda"}}

            if preco_moeda != "USD" and moeda_atual != "USD":
                pay_per_usd, pair_p_per_b, cambio_base = pair_rate_to_payment_per_usd(preco_moeda, moeda_atual, cambio, db)
                total_moeda_base = Decimal(str(contexto.get("total_moeda", "0")))
                total_em_pagamento = money(total_moeda_base * pair_p_per_b) if total_moeda_base > 0 else None
                if moeda_strength.get(preco_moeda, 5) <= moeda_strength.get(moeda_atual, 5):
                    rate_echo = f"1 {preco_moeda} = {money(pair_p_per_b)} {moeda_atual}"
                else:
                    inv = fx_rate(Decimal("1") / pair_p_per_b) if pair_p_per_b > 0 else Decimal("0")
                    rate_echo = f"1 {moeda_atual} = {money(inv)} {preco_moeda}"
                lines = [f"Câmbio: {rate_echo}."]
                if total_em_pagamento and total_em_pagamento > 0:
                    lines.append(f"Total estimado: {money(total_em_pagamento)} {moeda_atual}.")
                lines.append(f"Quanto será pago em {moeda_atual}?")
                if pay_per_usd is not None:
                    contexto["cambio_moeda_atual_pre"] = str(pay_per_usd)
                    contexto["fx_auto_assumido"] = True
                else:
                    contexto.pop("cambio_moeda_atual_pre", None)
                    contexto["fx_auto_assumido"] = True
                if cambio_base is not None:
                    try_set_total_usd_from_base_rate(contexto, cambio_base)
                save_session(db, remetente, "await_valor_moeda", contexto)
                return {"mensagem": "\n".join(lines), "dados": {"etapa": "await_valor_moeda"}}

            cambio_normalizado = normalize_cambio_para_usd(moeda_atual, cambio)
            contexto["cambio_moeda_atual_pre"] = str(cambio_normalizado)
            save_session(db, remetente, "await_valor_moeda", contexto)
            return {
                "mensagem": f"Câmbio registrado. Quanto será pago em {moeda_atual}?",
                "dados": {"etapa": "await_valor_moeda"},
            }

        if estado == "await_valor_moeda":
            moeda_atual = str(contexto.get("moeda_atual"))
            valor_moeda = parse_decimal_from_text(mensagem, "valor_moeda")
            if valor_moeda < 0:
                return {"mensagem": "Valor da moeda não pode ser negativo.", "dados": {"etapa": estado}}

            pagamento: Dict[str, Any] = {
                "moeda": moeda_atual,
                "valor_moeda": str(money(valor_moeda)),
                "cambio_para_usd": "1",
                "valor_usd": str(money(valor_moeda)),
                "forma_pagamento": None,
            }
            pagamentos = list(contexto.get("pagamentos", []))
            pagamentos.append(pagamento)
            contexto["pagamentos"] = pagamentos

            if moeda_atual == "USD":
                contexto.pop("cambio_moeda_atual_pre", None)
                return advance_after_payment_exchange(db, remetente, contexto, pagamentos)

            cambio_pre = contexto.get("cambio_moeda_atual_pre")
            if cambio_pre:
                cambio_pre_dec = Decimal(str(cambio_pre))
                valor_usd_pre = money(valor_moeda / cambio_pre_dec)
                pagamentos[-1]["cambio_para_usd"] = str(cambio_pre_dec)
                pagamentos[-1]["valor_usd"] = str(valor_usd_pre)
                contexto["pagamentos"] = pagamentos
                contexto.pop("cambio_moeda_atual_pre", None)

                preco_moeda = str(contexto.get("preco_moeda", "USD")).upper()
                if preco_moeda != "USD" and str(moeda_atual).upper() == preco_moeda:
                    try_set_total_usd_from_base_rate(contexto, cambio_pre_dec)

                return advance_after_payment_exchange(db, remetente, contexto, pagamentos)

            preco_moeda = str(contexto.get("preco_moeda", "USD")).upper()
            if preco_moeda != "USD" and str(moeda_atual).upper() == preco_moeda:
                cambio_auto = db.get_last_cambio_para_usd(preco_moeda)
                cambio_auto_dec = Decimal(str(cambio_auto)) if (cambio_auto and cambio_auto > 0) else Decimal("1")
                contexto["fx_auto_assumido"] = False
                valor_usd_auto = money(valor_moeda / cambio_auto_dec)
                pagamentos[-1]["cambio_para_usd"] = str(cambio_auto_dec)
                pagamentos[-1]["valor_usd"] = str(valor_usd_auto)
                contexto["pagamentos"] = pagamentos
                try_set_total_usd_from_base_rate(contexto, cambio_auto_dec)
                return advance_after_payment_exchange(db, remetente, contexto, pagamentos)

            total_operacao = Decimal(str(contexto.get("total_usd", "0")))
            save_session(db, remetente, "await_cambio_moeda", contexto)
            total_linha = f"Total da operação: {money(total_operacao)} USD.\n" if total_operacao > 0 else ""
            return {
                "mensagem": (
                    f"{moeda_atual}: {money(valor_moeda)} registrado.\n"
                    f"{total_linha}"
                    f"Câmbio do {moeda_atual}: {build_cambio_prompt(moeda_atual)}"
                ),
                "dados": {"etapa": "await_cambio_moeda"},
            }

        if estado == "await_cambio_moeda":
            cambio = parse_decimal_from_text(mensagem, "cambio")
            if cambio <= 0:
                return {"mensagem": "Câmbio deve ser maior que zero.", "dados": {"etapa": estado}}

            pagamentos = list(contexto.get("pagamentos", []))
            if not pagamentos:
                save_session(db, remetente, "await_moedas", contexto)
                return {
                    "mensagem": "Pagamentos reiniciados. Informe as moedas novamente.",
                    "dados": {"etapa": "await_moedas"},
                }

            ultimo = dict(pagamentos[-1])
            moeda_ult = str(ultimo.get("moeda", "USD")).upper()
            cambio_normalizado = normalize_cambio_para_usd(moeda_ult, cambio)
            valor_moeda_ult = Decimal(str(ultimo["valor_moeda"]))
            valor_usd = money(valor_moeda_ult / cambio_normalizado)
            ultimo["cambio_para_usd"] = str(cambio_normalizado)
            ultimo["valor_usd"] = str(valor_usd)
            pagamentos[-1] = ultimo
            contexto["pagamentos"] = pagamentos

            preco_moeda = str(contexto.get("preco_moeda", "USD")).upper()
            if preco_moeda != "USD" and moeda_ult == preco_moeda:
                try_set_total_usd_from_base_rate(contexto, cambio_normalizado)

            return advance_after_payment_exchange(db, remetente, contexto, pagamentos)

        if estado == "await_cambio_base_para_total":
            cambio = parse_decimal_from_text(mensagem, "cambio_base_total")
            if cambio <= 0:
                return {"mensagem": "Câmbio deve ser maior que zero.", "dados": {"etapa": estado}}

            preco_moeda = str(contexto.get("preco_moeda", "USD")).upper()
            cambio_normalizado = normalize_cambio_para_usd(preco_moeda, cambio)
            if not try_set_total_usd_from_base_rate(contexto, cambio_normalizado):
                clear_session(db, remetente)
                return {
                    "mensagem": "Não consegui retomar os dados da operação. Envie compra ou venda para reiniciar.",
                    "dados": {"acao": "reiniciar"},
                }

            pagamentos = list(contexto.get("pagamentos", []))
            return advance_after_payment_exchange(db, remetente, contexto, pagamentos)

        return None

    return SimpleNamespace(handle_payment_states=handle_payment_states)