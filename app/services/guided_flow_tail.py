from decimal import Decimal
from types import SimpleNamespace
from typing import Any, Callable, Dict, Optional

from fastapi import HTTPException


def build_guided_flow_tail_helpers() -> SimpleNamespace:
    def handle_tail_states(
        *,
        estado: str,
        db: Any,
        remetente: str,
        mensagem: str,
        contexto: Dict[str, Any],
        parse_decimal_from_text: Callable[[str, str], Decimal],
        parse_single_currency_choice: Callable[[str], Optional[str]],
        money: Callable[[Decimal], Decimal],
        save_session: Callable[[Any, str, str, Dict[str, Any]], None],
        clear_session: Callable[[Any, str], None],
        extract_caixa_currency: Callable[[str], Optional[str]],
        build_day_range: Callable[[Optional[str]], Dict[str, str]],
        build_week_range: Callable[[], Dict[str, str]],
        build_caixa_detail_response: Callable[[Any, str, str, str, str], Dict[str, Any]],
        build_extrato_response: Callable[[Any, str, str, str], Dict[str, Any]],
        parse_date_user_input: Callable[[str], Optional[str]],
        finish_transacao_simples: Callable[[Any, str, str, Dict[str, Any]], Dict[str, Any]],
        normalize_text: Callable[[str], str],
        navigation_hint: Callable[[], str],
    ) -> Optional[Dict[str, Any]]:
        if estado == "await_preco_simples":
            cotacao = parse_decimal_from_text(mensagem, "preco_usd")
            if cotacao <= 0:
                return {"mensagem": "Preço inválido. Exemplo: 65.50", "dados": {"etapa": estado}}

            quantidade = Decimal(str(contexto["quantidade"]))
            total_usd = money(quantidade * cotacao)
            contexto["cotacao_usd"] = str(cotacao)
            contexto["total_usd"] = str(total_usd)
            save_session(db, remetente, "await_moeda_simples", contexto)
            return {
                "mensagem": "Em qual moeda foi pago?\nUSD / EUR / SRD / BRL",
                "dados": {"etapa": "await_moeda_simples"},
            }

        if estado == "await_moeda_simples":
            moeda = parse_single_currency_choice(mensagem)
            moedas_validas = {"USD", "EUR", "SRD", "BRL"}
            if moeda not in moedas_validas:
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
            contexto["moeda_liquidacao"] = moeda
            if moeda == "USD":
                contexto["cambio_para_usd"] = "1.0"
                return finish_transacao_simples(db, remetente, mensagem, contexto)

            save_session(db, remetente, "await_cambio_simples", contexto)
            return {
                "mensagem": f"Qual o câmbio?\n(1 USD = quantos {moeda})",
                "dados": {"etapa": "await_cambio_simples"},
            }

        if estado == "await_cambio_simples":
            cambio = parse_decimal_from_text(mensagem, "cambio_para_usd")
            if cambio <= 0:
                return {
                    "mensagem": "Câmbio inválido. Exemplo: 38",
                    "dados": {"etapa": estado},
                }
            contexto["cambio_para_usd"] = str(cambio)
            return finish_transacao_simples(db, remetente, mensagem, contexto)

        if estado == "await_caixa_detalhe":
            requested_currency = extract_caixa_currency(mensagem)
            if not requested_currency:
                return {
                    "mensagem": (
                        "Escolha um caixa para detalhar:\n"
                        "1 (ouro) | 2 (euro) | 3 (dolar) | 4 (surinames) | 5 (real)"
                    ),
                    "dados": {"etapa": "await_caixa_detalhe"},
                }
            day = build_day_range(None)
            clear_session(db, remetente)
            return build_caixa_detail_response(db, requested_currency, day["start"], day["end"], f"Hoje ({day['date']})")

        if estado == "await_extrato_periodo":
            escolha = normalize_text(mensagem)
            if escolha in {"1", "hoje", "dia", "hoje (1)", "1)"}:
                day = build_day_range(None)
                clear_session(db, remetente)
                return build_extrato_response(db, day["start"], day["end"], f"Hoje ({day['date']})")
            if escolha in {"2", "semana", "esta semana", "week", "2)"}:
                week = build_week_range()
                clear_session(db, remetente)
                return build_extrato_response(db, week["start"], week["end"], week["label"])
            if escolha in {"3", "data", "datas", "informar", "informar datas", "outro", "3)"}:
                save_session(db, remetente, "await_extrato_data_inicio", {})
                return {
                    "mensagem": (
                        "Informe a data inicial:\n"
                        "Ex: 01/04/2026 ou 2026-04-01"
                    ),
                    "dados": {"etapa": "await_extrato_data_inicio"},
                }
            return {
                "mensagem": "Escolha inválida. Digite 1, 2 ou 3.",
                "dados": {"etapa": "await_extrato_periodo"},
            }

        if estado == "await_extrato_data_inicio":
            parsed = parse_date_user_input(mensagem.strip())
            if not parsed:
                return {
                    "mensagem": "Data inválida. Use o formato DD/MM/AAAA ou AAAA-MM-DD.",
                    "dados": {"etapa": estado},
                }
            save_session(db, remetente, "await_extrato_data_fim", {"data_inicio": parsed})
            return {
                "mensagem": (
                    f"Data inicial: {parsed}\n"
                    "Informe a data final:\n"
                    "Ex: 04/04/2026 ou 2026-04-04"
                ),
                "dados": {"etapa": "await_extrato_data_fim"},
            }

        if estado == "await_extrato_data_fim":
            parsed = parse_date_user_input(mensagem.strip())
            if not parsed:
                return {
                    "mensagem": "Data inválida. Use o formato DD/MM/AAAA ou AAAA-MM-DD.",
                    "dados": {"etapa": estado},
                }
            data_inicio = str(contexto.get("data_inicio", ""))
            if not data_inicio:
                clear_session(db, remetente)
                return {"mensagem": "Erro interno. Tente novamente: extrato", "dados": {"etapa": "reiniciar"}}
            try:
                start_day = build_day_range(data_inicio)
                end_day = build_day_range(parsed)
            except HTTPException:
                return {
                    "mensagem": "Datas inválidas. Use o formato AAAA-MM-DD.",
                    "dados": {"etapa": estado},
                }
            if end_day["start"] < start_day["start"]:
                return {
                    "mensagem": "A data final deve ser maior ou igual à data inicial.",
                    "dados": {"etapa": estado},
                }
            label = f"{data_inicio} a {parsed}"
            clear_session(db, remetente)
            return build_extrato_response(db, start_day["start"], end_day["end"], label)

        return None

    return SimpleNamespace(handle_tail_states=handle_tail_states)