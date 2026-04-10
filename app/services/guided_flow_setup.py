from decimal import Decimal
from types import SimpleNamespace
from typing import Any, Callable, Dict, Optional, Sequence


def build_guided_flow_setup_helpers() -> SimpleNamespace:
    def handle_setup_states(
        *,
        estado: str,
        db: Any,
        remetente: str,
        mensagem: str,
        contexto: Dict[str, Any],
        money: Callable[[Decimal], Decimal],
        parse_decimal_from_text: Callable[[str, str], Decimal],
        parse_origem_choice: Callable[[str], Optional[str]],
        parse_single_currency_choice: Callable[[str], Optional[str]],
        extract_moedas: Callable[[str], list[str]],
        save_session: Callable[[Any, str, str, Dict[str, Any]], None],
        navigation_hint: Callable[[], str],
        normalize_cambio_para_usd: Callable[[str, Decimal], Decimal],
        build_pair_cambio_prompt: Callable[[str, str], str],
        supported_currencies: Sequence[str],
    ) -> Optional[Dict[str, Any]]:
        if estado == "await_origem":
            origem = parse_origem_choice(mensagem)
            if origem is None:
                return {
                    "mensagem": (
                        "Origem inválida. Escolha uma opção:\n"
                        "1) balcão\n"
                        "2) fora"
                        f"{navigation_hint()}"
                    ),
                    "dados": {"etapa": estado},
                }
            contexto["origem"] = origem
            save_session(db, remetente, "await_teor", contexto)
            return {"mensagem": "Qual o teor do ouro em %? (0 a 99,99)", "dados": {"etapa": "await_teor"}}

        if estado == "await_teor":
            teor = parse_decimal_from_text(mensagem, "teor")
            if teor < 0 or teor > Decimal("99.99"):
                return {"mensagem": "O teor deve estar entre 0 e 99,99.", "dados": {"etapa": estado}}
            contexto["teor"] = str(money(teor))
            save_session(db, remetente, "await_peso", contexto)
            return {"mensagem": "Quantas gramas?", "dados": {"etapa": "await_peso"}}

        if estado == "await_peso":
            peso = parse_decimal_from_text(mensagem, "peso")
            if peso <= 0:
                return {"mensagem": "O peso deve ser maior que zero.", "dados": {"etapa": estado}}
            contexto["peso"] = str(peso)
            save_session(db, remetente, "await_preco_moeda", contexto)
            return {
                "mensagem": (
                    "Moeda base para precificação:\n"
                    "1) USD\n"
                    "2) EUR\n"
                    "3) SRD\n"
                    "4) BRL\n"
                    "Você também pode digitar: dólar, euro, srd ou real."
                    f"{navigation_hint()}"
                ),
                "dados": {"etapa": "await_preco_moeda"},
            }

        if estado == "await_preco_moeda":
            moeda_preco = parse_single_currency_choice(mensagem)
            if moeda_preco not in supported_currencies:
                return {
                    "mensagem": (
                        "Moeda inválida. Escolha uma opção:\n"
                        "1) USD\n"
                        "2) EUR\n"
                        "3) SRD\n"
                        "4) BRL\n"
                        "Você também pode digitar: dólar, euro, srd ou real."
                        f"{navigation_hint()}"
                    ),
                    "dados": {"etapa": estado},
                }
            contexto["preco_moeda"] = moeda_preco
            save_session(db, remetente, "await_preco_usd", contexto)
            return {
                "mensagem": f"Informe o preço por grama em {moeda_preco}.",
                "dados": {"etapa": "await_preco_usd"},
            }

        if estado == "await_preco_usd":
            preco = parse_decimal_from_text(mensagem, "preco_usd")
            if preco <= 0:
                return {"mensagem": "Preço deve ser maior que zero.", "dados": {"etapa": estado}}

            preco_moeda = str(contexto.get("preco_moeda", "USD")).upper()
            if preco_moeda != "USD":
                contexto["preco_moeda_valor"] = str(money(preco))
                peso = Decimal(str(contexto.get("peso")))
                total_moeda = money(peso * preco)
                contexto["total_moeda"] = str(total_moeda)
                save_session(db, remetente, "await_moedas", contexto)
                return {
                    "mensagem": (
                        f"Preco recebido: {money(preco)} {preco_moeda}/g.\n"
                        f"Total da operação: {total_moeda} {preco_moeda}.\n"
                        "Informe as moedas de pagamento: USD, EUR, SRD, BRL\n"
                        "(o câmbio será pedido na etapa de pagamento, se necessário)"
                    ),
                    "dados": {"etapa": "await_moedas"},
                }

            peso = Decimal(str(contexto.get("peso")))
            total = money(peso * preco)
            contexto["preco_usd"] = str(money(preco))
            contexto["total_usd"] = str(total)
            save_session(db, remetente, "await_moedas", contexto)
            return {
                "mensagem": (
                    f"{peso}g x {money(preco)} USD/g = {total} USD.\n"
                    "Informe as moedas de pagamento: USD, EUR, SRD, BRL"
                ),
                "dados": {"etapa": "await_moedas"},
            }

        if estado == "await_preco_cambio":
            cambio = parse_decimal_from_text(mensagem, "cambio_preco")
            if cambio <= 0:
                return {"mensagem": "Câmbio deve ser maior que zero.", "dados": {"etapa": estado}}

            preco_moeda = str(contexto.get("preco_moeda", "USD")).upper()
            cambio_normalizado = normalize_cambio_para_usd(preco_moeda, cambio)
            preco_moeda_valor = Decimal(str(contexto.get("preco_moeda_valor", "0")))
            preco_usd = money(preco_moeda_valor / cambio_normalizado)
            peso = Decimal(str(contexto.get("peso")))
            total = money(peso * preco_usd)

            contexto["preco_usd"] = str(preco_usd)
            contexto["cambio_preco_moeda"] = str(cambio_normalizado)
            contexto["total_usd"] = str(total)
            save_session(db, remetente, "await_moedas", contexto)
            return {
                "mensagem": (
                    f"Conversão feita: {preco_usd} USD/g.\n"
                    f"Total da operação: {total} USD.\n"
                    "Informe as moedas de pagamento: USD, EUR, SRD, BRL"
                ),
                "dados": {"etapa": "await_moedas"},
            }

        if estado == "await_moedas":
            moedas = extract_moedas(mensagem)
            if not moedas:
                return {"mensagem": "Não entendi as moedas. Exemplo: USD e SRD", "dados": {"etapa": estado}}
            contexto["moedas"] = moedas
            contexto["moeda_index"] = 0
            contexto["pagamentos"] = []
            contexto["moeda_atual"] = moedas[0]
            save_session(db, remetente, "await_valor_moeda", contexto)
            total_operacao = Decimal(str(contexto.get("total_usd", "0")))
            preco_moeda = str(contexto.get("preco_moeda", "USD")).upper()
            total_moeda = Decimal(str(contexto.get("total_moeda", "0")))

            if total_operacao > 0:
                total_txt = f"Total da operação: {money(total_operacao)} USD."
            elif preco_moeda != "USD" and total_moeda > 0:
                total_txt = f"Total da operação: {money(total_moeda)} {preco_moeda}."
            else:
                total_txt = "Total da operação definido."

            primeira_moeda = str(moedas[0]).upper()
            if primeira_moeda != preco_moeda:
                save_session(db, remetente, "await_cambio_moeda_pre_valor", contexto)
                cambio_prompt = build_pair_cambio_prompt(preco_moeda, primeira_moeda)
                return {
                    "mensagem": (
                        f"{total_txt}\n"
                        f"Câmbio {preco_moeda}/{primeira_moeda}: {cambio_prompt}"
                    ),
                    "dados": {"etapa": "await_cambio_moeda_pre_valor"},
                }

            return {
                "mensagem": (
                    f"{total_txt}\n"
                    f"Quanto será pago em {moedas[0]}?"
                ),
                "dados": {"etapa": "await_valor_moeda"},
            }

        return None

    return SimpleNamespace(handle_setup_states=handle_setup_states)