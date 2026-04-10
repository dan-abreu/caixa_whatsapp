import json
from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional, Tuple

from fastapi import FastAPI, HTTPException, Request, Response


def _parse_sale_lot_selections(
    db: Any,
    form: Dict[str, Any],
    peso: Decimal,
    *,
    parse_decimal_web_field: Callable[[str, str], Decimal],
) -> Tuple[str, List[Dict[str, Any]]]:
    mode = str(form.get("sale_source_mode") or "manual").strip().lower()
    if mode not in {"manual", "selected"}:
        mode = "manual"
    if mode != "selected":
        return mode, []
    inventory = db.get_gold_inventory_status(open_only=True)
    open_lots = {int(item.get("id") or 0): item for item in inventory.get("open_lots") or [] if int(item.get("id") or 0) > 0}
    selections: List[Dict[str, Any]] = []
    selected_total = Decimal("0")
    for key in form.keys():
        key_text = str(key)
        if not key_text.startswith("sale_lot_") or not key_text.endswith("_selected"):
            continue
        if str(form.get(key) or "") != "1":
            continue
        lot_id_raw = key_text[len("sale_lot_") : -len("_selected")]
        if not lot_id_raw.isdigit():
            raise HTTPException(status_code=400, detail="Lote selecionado inválido")
        lot_id = int(lot_id_raw)
        lot = open_lots.get(lot_id)
        if not lot:
            raise HTTPException(status_code=400, detail=f"Lote {lot_id} não está mais disponível")
        remaining_grams = Decimal(str(lot.get("remaining_grams") or "0"))
        grams_raw = str(form.get(f"sale_lot_{lot_id}_grams") or "").strip()
        grams = remaining_grams if not grams_raw else parse_decimal_web_field(grams_raw, f"sale_lot_{lot_id}_grams")
        if grams <= 0 or grams > remaining_grams:
            raise HTTPException(status_code=400, detail=f"Gramas inválidas para o lote GT-{lot.get('source_transaction_id')}")
        selections.append({
            "lot_id": lot_id,
            "source_transaction_id": int(lot.get("source_transaction_id") or 0),
            "grams": str(grams),
            "remaining_grams": str(remaining_grams),
            "pessoa": str(lot.get("pessoa") or ""),
            "teor": str(lot.get("teor") or ""),
        })
        selected_total += grams
    if not selections:
        raise HTTPException(status_code=400, detail="Selecione ao menos um lote para a venda por ordens")
    if selected_total != peso:
        raise HTTPException(status_code=400, detail="A soma das gramas selecionadas deve ser igual ao peso da venda")
    return mode, selections


def register_saas_operation_routes(
    app: FastAPI,
    *,
    get_db: Callable[[], Any],
    get_saas_authenticated_user: Callable[[Request, Any], Optional[Dict[str, Any]]],
    request_form_dict: Callable[[Request], Any],
    render_saas_login_html: Callable[..., str],
    clear_saas_session: Callable[[Response], None],
    normalize_saas_page: Callable[[Optional[str]], str],
    normalize_user_phone: Callable[[str], str],
    normalize_text: Callable[[str], str],
    parse_gold_trade_profile: Callable[[str, Any, Any], Tuple[str, Optional[Decimal]]],
    parse_decimal_web_field: Callable[[str, str], Decimal],
    parse_web_payments_from_form: Callable[[Any, Dict[str, str]], List[Dict[str, Any]]],
    derive_forma_pagamento_summary: Callable[[List[Dict[str, Any]]], str],
    build_cliente_lookup_meta: Callable[[Dict[str, Any]], str],
    attach_sale_profit_reference: Callable[[Any, Dict[str, Any]], None],
    project_caixa_balances: Callable[[Dict[str, Any], str, Decimal, List[Dict[str, Any]]], Dict[str, Decimal]],
    find_negative_caixa_balances: Callable[[Dict[str, Decimal]], List[Tuple[str, Decimal]]],
    format_negative_caixa_lines: Callable[[List[Tuple[str, Decimal]]], List[str]],
    persist_gold_operation_from_context: Callable[[Any, str, Dict[str, Any], bool], Dict[str, Any]],
    render_saas_dashboard_html: Callable[..., str],
    build_gold_receipt_context: Callable[[Any, int], Dict[str, Any]],
    render_saas_receipt_html: Callable[[Dict[str, Any], str, str], str],
    money: Callable[[Decimal], Decimal],
    friendly_errors: Dict[int, str],
) -> None:
    @app.post("/saas/operations/quick")
    async def saas_quick_operation(request: Request) -> Response:
        form = await request_form_dict(request)
        is_ajax = request.headers.get("x-requested-with", "").lower() == "xmlhttprequest"
        db = get_db()
        session_user = get_saas_authenticated_user(request, db)
        if not session_user:
            if is_ajax:
                return Response(
                    content=json.dumps({"ok": False, "notice": "Faça login para continuar."}, ensure_ascii=False),
                    media_type="application/json",
                    status_code=401,
                )
            response = Response(content=render_saas_login_html("Faça login para continuar."), media_type="text/html", status_code=401)
            clear_saas_session(response)
            return response

        current_page = normalize_saas_page(form.get("page"))
        values = {k: str(v) for k, v in form.items()}
        try:
            operador_id = normalize_user_phone(str(form.get("operador_id") or session_user.get("telefone") or ""))
            tipo_operacao = normalize_text(str(form.get("tipo_operacao") or "compra"))
            origem = normalize_text(str(form.get("origem") or "balcao"))
            gold_type, quebra = parse_gold_trade_profile(tipo_operacao, form.get("gold_type"), form.get("quebra"))
            teor = parse_decimal_web_field(str(form.get("teor") or "0"), "teor")
            peso = parse_decimal_web_field(str(form.get("peso") or "0"), "peso")
            preco_usd = parse_decimal_web_field(str(form.get("preco_usd") or "0"), "preco_usd")
            pessoa = str(form.get("pessoa") or "").strip()
            cliente_id_raw = str(form.get("cliente_id") or "").strip()
            observacoes = str(form.get("observacoes") or "").strip()

            if tipo_operacao not in {"compra", "venda"}:
                raise HTTPException(status_code=400, detail="Tipo de operação inválido")
            if origem not in {"balcao", "fora"}:
                raise HTTPException(status_code=400, detail="Origem inválida")
            if teor < 0 or teor > Decimal("99.99"):
                raise HTTPException(status_code=400, detail="Teor inválido")
            if peso <= 0 or preco_usd <= 0:
                raise HTTPException(status_code=400, detail="Peso e preço devem ser maiores que zero")

            cliente: Optional[Dict[str, Any]] = None
            if cliente_id_raw:
                if not cliente_id_raw.isdigit():
                    raise HTTPException(status_code=400, detail="Cliente inválido")
                cliente = db.get_cliente_by_id(int(cliente_id_raw))
                if not cliente:
                    raise HTTPException(status_code=404, detail="Cliente não encontrado")
            else:
                inline_mode = str(form.get("inline_cliente_mode") or "0") == "1"
                inline_nome = str(form.get("inline_cliente_nome") or pessoa).strip()
                inline_phone = str(form.get("inline_cliente_telefone") or "").strip()
                inline_document = str(form.get("inline_cliente_documento") or "").strip()
                inline_apelido = str(form.get("inline_cliente_apelido") or "").strip()
                inline_observacoes = str(form.get("inline_cliente_observacoes") or "").strip()
                opening_balances: Dict[str, Decimal] = {}
                inline_saldo_xau = str(form.get("inline_cliente_saldo_xau") or "").strip()
                if inline_saldo_xau:
                    opening_balances["XAU"] = parse_decimal_web_field(inline_saldo_xau, "inline_cliente_saldo_xau")
                if inline_mode or inline_nome or inline_phone or inline_document:
                    if not inline_nome:
                        raise HTTPException(status_code=400, detail="Nome do cliente é obrigatório no cadastro rápido")
                    cliente = db.create_cliente(
                        nome=inline_nome,
                        telefone=inline_phone or None,
                        documento=inline_document or None,
                        apelido=inline_apelido or None,
                        observacoes=inline_observacoes or None,
                        opening_balances=opening_balances,
                    )
                    if not cliente:
                        raise HTTPException(status_code=409, detail="Cadastro de clientes indisponível. Aplique a migração do banco antes de usar esta rotina.")
                    values["cliente_id"] = str(cliente.get("id") or "")
                    values["inline_cliente_mode"] = "0"
                else:
                    raise HTTPException(status_code=400, detail="Selecione ou cadastre o cliente da operação")

            pessoa = str((cliente or {}).get("nome") or pessoa).strip()
            if not pessoa:
                raise HTTPException(status_code=400, detail="Cliente da operação é obrigatório")

            cliente_id = int((cliente or {}).get("id") or 0)
            values["cliente_id"] = str(cliente_id)
            values["pessoa"] = pessoa
            values["cliente_lookup_meta"] = build_cliente_lookup_meta(cliente or {"id": cliente_id, "nome": pessoa})

            session_phone = str(session_user.get("telefone") or "")
            is_admin = str(session_user.get("tipo_usuario", "")).lower() == "admin"
            if not operador_id:
                operador_id = session_phone
            if not is_admin and operador_id != session_phone:
                raise HTTPException(status_code=403, detail="Operador web só pode lançar em seu próprio usuário")

            usuario = db.get_usuario_by_telefone(operador_id)
            if not usuario:
                raise HTTPException(status_code=403, detail="Operador não autorizado")

            total_usd = money(peso * preco_usd)
            pagamentos = parse_web_payments_from_form(db, values)
            total_pago_usd = sum((Decimal(str(item.get("valor_usd") or "0")) for item in pagamentos), Decimal("0"))
            forma_pagamento = derive_forma_pagamento_summary(pagamentos)

            fechamento_raw = str(form.get("fechamento_gramas") or "").strip()
            fechamento_gramas = peso if not fechamento_raw else parse_decimal_web_field(fechamento_raw, "fechamento_gramas")
            fechamento_tipo = normalize_text(str(form.get("fechamento_tipo") or "total"))
            if fechamento_tipo not in {"total", "parcial"}:
                raise HTTPException(status_code=400, detail="Fechamento inválido")
            if fechamento_gramas < 0 or fechamento_gramas > peso:
                raise HTTPException(status_code=400, detail="Fechamento em gramas inválido")

            contexto: Dict[str, Any] = {
                "tipo_operacao": tipo_operacao,
                "origem": origem,
                "gold_type": gold_type,
                "quebra": str(quebra) if quebra is not None else None,
                "teor": str(money(teor)),
                "peso": str(peso),
                "preco_moeda": "USD",
                "preco_usd": str(money(preco_usd)),
                "total_usd": str(total_usd),
                "total_pago_usd": str(money(total_pago_usd)),
                "fechamento_gramas": str(money(fechamento_gramas)),
                "fechamento_tipo": fechamento_tipo,
                "cliente_id": cliente_id,
                "pessoa": pessoa,
                "forma_pagamento": forma_pagamento,
                "observacoes": observacoes,
                "source_message_id": None,
                "pagamentos": pagamentos,
            }
            if tipo_operacao == "venda":
                sale_source_mode, selected_sale_lots = _parse_sale_lot_selections(db, form, peso, parse_decimal_web_field=parse_decimal_web_field)
                contexto["sale_source_mode"] = sale_source_mode
                if selected_sale_lots:
                    contexto["selected_sale_lots"] = selected_sale_lots
                attach_sale_profit_reference(db, contexto)

            projected = project_caixa_balances(db.get_saldo_caixa(), tipo_operacao, peso, pagamentos)
            negative_balances = find_negative_caixa_balances(projected)
            fifo_shortfall = Decimal(str(contexto.get("fifo_shortfall_grams", "0")))
            risk_lines: List[str] = []
            if negative_balances:
                risk_lines.append("Saldos projetados negativos:")
                risk_lines.extend(format_negative_caixa_lines(negative_balances))
            if fifo_shortfall > 0:
                risk_lines.append(f"- Estoque FIFO insuficiente: faltam {fifo_shortfall} g")

            wants_override = str(form.get("risk_override") or "") == "1"
            if risk_lines and not (is_admin and wants_override):
                notice = "⛔ " + " | ".join(risk_lines)
                if is_ajax:
                    return Response(
                        content=json.dumps({"ok": False, "notice": notice}, ensure_ascii=False),
                        media_type="application/json",
                        status_code=400,
                    )
                html = render_saas_dashboard_html(db, session_user, notice=notice, notice_kind="error", form_values=values, current_page=current_page)
                return Response(content=html, media_type="text/html", status_code=400)

            result = persist_gold_operation_from_context(db, operador_id, contexto, False)
            gt_id_raw = result.get("dados", {}).get("gold_transaction_id")
            if not gt_id_raw:
                ok_msg = "Operação web salva com sucesso."
                if is_ajax:
                    return Response(
                        content=json.dumps({"ok": True, "notice": ok_msg, "receipt_url": None}, ensure_ascii=False),
                        media_type="application/json",
                    )
                html = render_saas_dashboard_html(db, session_user, notice=ok_msg, notice_kind="info", assistant_result=result, form_values=values, current_page=current_page)
                return Response(content=html, media_type="text/html")

            gt_id = int(gt_id_raw)
            receipt_url = str(request.url_for("saas_receipt_view", operation_id=gt_id))
            if is_ajax:
                return Response(
                    content=json.dumps(
                        {
                            "ok": True,
                            "notice": f"Operacao web salva com sucesso. Recibo GT-{gt_id} aberto em outra pagina.",
                            "receipt_url": receipt_url,
                            "operation_id": gt_id,
                        },
                        ensure_ascii=False,
                    ),
                    media_type="application/json",
                )

            receipt = build_gold_receipt_context(db, gt_id)
            pdf_url = str(request.url_for("saas_receipt_pdf", operation_id=gt_id))
            html = render_saas_receipt_html(receipt, pdf_url=pdf_url, back_url="/saas?page=operations")
            return Response(content=html, media_type="text/html")
        except HTTPException as exc:
            notice = friendly_errors.get(exc.status_code, str(exc.detail))
            if is_ajax:
                return Response(
                    content=json.dumps({"ok": False, "notice": notice}, ensure_ascii=False),
                    media_type="application/json",
                    status_code=exc.status_code,
                )
            html = render_saas_dashboard_html(db, session_user, notice=notice, notice_kind="error", form_values=values, current_page=current_page)
            return Response(content=html, media_type="text/html", status_code=exc.status_code)