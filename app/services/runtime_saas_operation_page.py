from decimal import Decimal
from html import escape
from types import SimpleNamespace
from typing import Any, Callable, Dict, List


def build_runtime_saas_operation_page_helpers() -> SimpleNamespace:
    def render_operation_page_content(
        *,
        values: Dict[str, str],
        payment_rows_html: str,
        money_balances_html: str,
        pending_open_gold_total: Any,
        gold_caixa_metrics: Dict[str, Any],
        operation_lot_market_context: Dict[str, Any],
        operation_open_lots: List[Dict[str, Any]],
        operation_lot_teor_html: str,
        risk_lots_html: str,
        normalize_gold_type: Callable[[Any], str],
        format_caixa_movement: Callable[[str, Decimal], str],
    ) -> str:
        sale_source_mode = str(values.get("sale_source_mode") or "manual")
        sale_lot_rows: List[str] = []
        for lot in operation_open_lots[:18]:
            lot_id = int(lot.get("id") or 0)
            if lot_id <= 0:
                continue
            selected_key = f"sale_lot_{lot_id}_selected"
            grams_key = f"sale_lot_{lot_id}_grams"
            selected = str(values.get(selected_key) or "") == "1"
            grams_value = escape(values.get(grams_key, ""))
            remaining_grams = Decimal(str(lot.get("remaining_grams") or "0"))
            sale_lot_rows.append(
                f"<tr><td><input type='checkbox' name='{selected_key}' value='1' class='js-sale-lot-check' data-lot-id='{lot_id}' {'checked' if selected else ''} /></td><td>GT-{escape(str(lot.get('source_transaction_id') or '-'))}</td><td>{escape(str(lot.get('pessoa') or '-'))}</td><td>{escape(str(lot.get('teor') or '-'))}%</td><td>{escape(str(remaining_grams))} g</td><td>USD {escape(str(lot.get('unit_cost_usd') or '0'))}</td><td><input name='{grams_key}' value='{grams_value}' class='js-sale-lot-grams' data-lot-id='{lot_id}' data-lot-max='{escape(str(remaining_grams))}' inputmode='decimal' placeholder='g usados' /></td></tr>"
            )
        sale_lot_table_html = "".join(sale_lot_rows) or "<tr><td colspan='7'>Nenhum lote aberto disponivel para selecao.</td></tr>"
        return f"""
        <div class='operation-layout'>
            <div class='operation-main'>
                <section class='panel section'>
                    <div class='section-head'>
                        <div>
                            <h2>Registro de Operacao</h2>
                            <p class='hint'>Area central para registro operacional. O formulario grava diretamente no sistema, enquanto o assistente pode apoiar a montagem por linguagem natural.</p>
                        </div>
                    </div>
                    <div class='notice info is-hidden notice-action' id='operationFormNotice'>
                        <span id='operationFormNoticeText'></span>
                        <a id='operationFormReceiptLink' class='notice-link is-hidden' href='#' target='_blank' rel='noopener'>Abrir recibo</a>
                    </div>
                    <form method='post' action='/saas/operations/quick' id='quickOperationForm'>
                        <input type='hidden' name='page' value='operation' />
                        <div class='quick-mode-bar'>
                            <button type='button' class='ghost-link mini-action' id='toggleQuickOrderMode'>Modo de lancamento agil</button>
                            <span class='hint' id='quickModeHint'>Enter avanca campo a campo. Ctrl+Enter confirma o registro.</span>
                        </div>
                        <div class='fields-3'>
                            <label data-quick-optional='1'>Operador
                                <input name='operador_id' value='{escape(values['operador_id'])}' required />
                            </label>
                            <label>Tipo
                                <select name='tipo_operacao' id='opTipoOperacao'>
                                    <option value='compra' {'selected' if values['tipo_operacao']=='compra' else ''}>Compra</option>
                                    <option value='venda' {'selected' if values['tipo_operacao']=='venda' else ''}>Venda</option>
                                </select>
                            </label>
                            <label data-quick-optional='1'>Origem
                                <select name='origem'>
                                    <option value='balcao' {'selected' if values['origem']=='balcao' else ''}>Balcao</option>
                                    <option value='fora' {'selected' if values['origem']=='fora' else ''}>Fora</option>
                                </select>
                            </label>
                        </div>
                        <div class='fields-2'>
                            <label>Material do ouro
                                <select name='gold_type' id='opGoldType'>
                                    <option value='fundido' {'selected' if normalize_gold_type(values.get('gold_type'))=='fundido' else ''}>Fundido</option>
                                    <option value='queimado' {'selected' if normalize_gold_type(values.get('gold_type'))=='queimado' else ''}>Queimado</option>
                                </select>
                            </label>
                            <label id='opQuebraWrap' class='{'is-hidden' if not (values.get('tipo_operacao') == 'compra' and normalize_gold_type(values.get('gold_type')) == 'queimado') else ''}'>Quebra %
                                <input name='quebra' id='opQuebra' value='{escape(values.get('quebra', ''))}' placeholder='Obrigatorio se a compra for queimado' inputmode='decimal' />
                            </label>
                        </div>
                        <div class='fields-3'>
                            <label>Teor %
                                <input name='teor' id='opTeor' value='{escape(values['teor'])}' required inputmode='decimal' />
                            </label>
                            <label>Peso g
                                <input name='peso' id='opPeso' value='{escape(values['peso'])}' required inputmode='decimal' />
                            </label>
                            <label>Preco USD/g
                                <input name='preco_usd' id='opPrecoUsd' value='{escape(values['preco_usd'])}' required inputmode='decimal' />
                            </label>
                        </div>
                        <div class='tip-box sale-lot-box {'is-hidden' if values.get('tipo_operacao') != 'venda' else ''}' id='saleLotSelectorBox'>
                            <div class='section-head'>
                                <div>
                                    <h2>Fonte da Venda</h2>
                                    <p class='hint'>Use o modo manual quando a venda vier livre. Use selecao de ordens/lotes quando quiser escolher exatamente quais compras estao saindo para o comprador.</p>
                                </div>
                            </div>
                            <div class='fields-2'>
                                <label>Modo da venda
                                    <select name='sale_source_mode' id='saleSourceMode'>
                                        <option value='manual' {'selected' if sale_source_mode=='manual' else ''}>Manual</option>
                                        <option value='selected' {'selected' if sale_source_mode=='selected' else ''}>Selecionar ordens/lotes</option>
                                    </select>
                                </label>
                                <div class='mini-stat'><span>Total selecionado</span><strong id='saleLotSelectedTotal'>0 g</strong></div>
                            </div>
                            <div id='saleLotSelectionPanel' class='{'is-hidden' if sale_source_mode != 'selected' else ''}'>
                                <div class='quick-actions'>
                                    <button type='button' class='ghost-link mini-action' id='saleLotApplySelection'>Usar soma selecionada no peso</button>
                                </div>
                                <table>
                                    <thead><tr><th>Sel.</th><th>Ordem</th><th>Origem</th><th>Teor</th><th>Saldo</th><th>Custo</th><th>Gramas desta venda</th></tr></thead>
                                    <tbody>{sale_lot_table_html}</tbody>
                                </table>
                            </div>
                        </div>
                        <div class='operation-strip'>
                            <div class='mini-stat'><span>Ouro fino</span><strong id='opFineGold'>0.000 g</strong></div>
                            <div class='mini-stat'><span>Valor de referencia</span><strong id='opTotalUsd'>USD 0.00</strong></div>
                            <div class='mini-stat'><span>Base de fechamento</span><strong id='opTargetUsd'>USD 0.00</strong></div>
                            <div class='mini-stat'><span>Total liquidado</span><strong id='opPaidUsd'>USD 0.00</strong></div>
                            <div class='mini-stat'><span>Diferenca apurada</span><strong id='opDiffUsd'>USD 0.00</strong></div>
                        </div>
                        <div class='fields-2'>
                            <label>Fechamento g
                                <input name='fechamento_gramas' id='opFechamentoGramas' value='{escape(values['fechamento_gramas'])}' placeholder='vazio = total' inputmode='decimal' />
                            </label>
                            <label>Fechamento Tipo
                                <select name='fechamento_tipo' id='opFechamentoTipo'>
                                    <option value='total' {'selected' if values['fechamento_tipo']=='total' else ''}>Total</option>
                                    <option value='parcial' {'selected' if values['fechamento_tipo']=='parcial' else ''}>Parcial</option>
                                </select>
                            </label>
                        </div>
                        <p class='hint inline-hint' id='opFechamentoHint'>Selecione Total quando toda a quantidade ja estiver liquidada. Selecione Parcial quando houver saldo a regularizar posteriormente.</p>
                        <div class='quick-actions'>
                            <button type='button' class='ghost-link mini-action' id='opUsePesoTotal'>Aplicar peso integral no fechamento</button>
                            <button type='button' class='ghost-link mini-action' id='opUseTotalAsUsd'>Replicar base de fechamento no pagamento em USD</button>
                        </div>
                        <div class='fields-2'>
                            <label class='client-picker'>Cliente
                                <input type='hidden' name='cliente_id' id='opClienteId' value='{escape(values['cliente_id'])}' />
                                <input type='hidden' name='cliente_lookup_meta' id='opClienteLookupMeta' value='{escape(values['cliente_lookup_meta'])}' />
                                <input name='pessoa' id='opPessoa' value='{escape(values['pessoa'])}' placeholder='Digite nome, telefone ou documento' autocomplete='off' required />
                                <div class='client-autocomplete is-hidden' id='opClienteResults'></div>
                                <span class='hint' id='opClienteMeta'>{escape(values['cliente_lookup_meta'] or 'Selecione um cliente existente ou use o cadastro rapido abaixo.')}</span>
                            </label>
                            <label>Total liquidado em USD
                                <input name='total_pago_usd' id='opTotalPagoUsd' value='{escape(values['total_pago_usd'])}' placeholder='utilize apenas se nao informar as linhas de pagamento' inputmode='decimal' />
                            </label>
                        </div>
                        <div class='quick-actions'>
                            <button type='button' class='ghost-link mini-action' id='toggleInlineCliente'>Cadastro rapido de cliente</button>
                            <a href='/saas/clientes' class='ghost-link mini-action'>Abrir base de clientes</a>
                        </div>
                        <div class='tip-box inline-client-box {'is-hidden' if values.get('inline_cliente_mode', '0') != '1' else ''}' id='inlineClienteBox'>
                            <input type='hidden' name='inline_cliente_mode' id='inlineClienteMode' value='{escape(values.get('inline_cliente_mode', '0'))}' />
                            <strong>Cadastro rapido no proprio lancamento</strong>
                            <p class='hint'>Use este bloco quando o cliente ainda nao existir. O operador continua no fluxo, registra o cliente e segue com a operacao.</p>
                            <div class='fields-3'>
                                <label>Nome do cliente
                                    <input name='inline_cliente_nome' id='inlineClienteNome' value='{escape(values.get('inline_cliente_nome', ''))}' />
                                </label>
                                <label>Telefone
                                    <input name='inline_cliente_telefone' value='{escape(values.get('inline_cliente_telefone', ''))}' />
                                </label>
                                <label>Documento
                                    <input name='inline_cliente_documento' value='{escape(values.get('inline_cliente_documento', ''))}' />
                                </label>
                            </div>
                            <div class='fields-3'>
                                <label>Apelido / referencia
                                    <input name='inline_cliente_apelido' value='{escape(values.get('inline_cliente_apelido', ''))}' />
                                </label>
                                <label>Saldo inicial em ouro (g)
                                    <input name='inline_cliente_saldo_xau' value='{escape(values.get('inline_cliente_saldo_xau', ''))}' inputmode='decimal' />
                                </label>
                                <label>Observacoes
                                    <input name='inline_cliente_observacoes' value='{escape(values.get('inline_cliente_observacoes', ''))}' />
                                </label>
                            </div>
                            <div class='inline-client-actions'>
                                <button type='button' id='inlineClienteSave' class='ghost-link mini-action'>Confirmar cadastro do cliente</button>
                                <span class='hint inline-client-status' id='inlineClienteStatus'>Salve o cliente aqui para selecionar a conta antes de registrar a operacao.</span>
                            </div>
                        </div>
                        <div class='payment-stack'>
                            {payment_rows_html}
                        </div>
                        <div class='tip-box rateio-box'>
                            <strong>Rateio automatico de liquidacao</strong>
                            <p class='hint' id='opRateioHint'>Ao informar o percentual por moeda, o sistema distribui a base de fechamento entre os pagamentos e calcula automaticamente os respectivos valores.</p>
                        </div>
                        <div class='tip-box op-summary-box'>
                            <strong>Resumo operacional</strong>
                            <p class='hint' id='opSummaryText'>Preencha peso, preco e pagamentos para gerar uma sintese da operacao antes da confirmacao.</p>
                        </div>
                        <label data-quick-optional='1'>Observacoes
                            <textarea name='observacoes' placeholder='Detalhes adicionais'>{escape(values['observacoes'])}</textarea>
                        </label>
                        <label data-quick-optional='1'><input type='checkbox' name='risk_override' value='1' style='width:auto;margin-right:8px;' /> Autorizar risco se o operador informado for admin</label>
                        <button type='submit'>Registrar operacao</button>
                    </form>
                </section>
            </div>
            <aside class='operation-side'>
                <section class='panel section'>
                    <h2>Assistente Operacional</h2>
                    <p class='hint'>O assistente pode conduzir consultas, esclarecer duvidas e estruturar a operacao a partir de instrucoes em texto livre.</p>
                    <div class='tip-box ai-draft-box'>
                        <strong>Preparar pre-lancamento</strong>
                        <form id='aiDraftForm' class='ai-draft-form'>
                            <label>Descreva a operacao
                                <textarea id='aiDraftInput' placeholder='Ex.: comprei 12,4g teor 91,6 a 104 usd de Joao pago em 300 USD e 7600 SRD'></textarea>
                            </label>
                            <div class='quick-actions'>
                                <button type='submit'>Gerar pre-lancamento</button>
                            </div>
                            <p class='hint' id='aiDraftStatus'>A IA interpreta a descricao e preenche o formulario para conferencia antes do registro.</p>
                        </form>
                    </div>
                </section>
                <section class='panel section'>
                    <h2>Posicao Financeira e de Ouro</h2>
                    <p class='hint'>Consulta lateral com os saldos por moeda e a segregacao entre ouro fisico em caixa, ouro de terceiros pendente e posicao propria.</p>
                    <div class='operation-balance-grid'>{money_balances_html}</div>
                    <div class='cards cards-closure operation-side-cards compact-gold-cards'>
                        <div class='card'><small>Ouro de terceiros pendente</small><strong>{escape(str(pending_open_gold_total))} g</strong></div>
                        <div class='card'><small>Ouro fisico em caixa</small><strong>{escape(format_caixa_movement('XAU', gold_caixa_metrics['ouro_em_caixa']))}</strong></div>
                        <div class='card'><small>Posicao propria em ouro</small><strong>{escape(format_caixa_movement('XAU', gold_caixa_metrics['ouro_proprio']))}</strong></div>
                    </div>
                    <div class='cards cards-closure operation-side-cards compact-gold-cards'>
                        <div class='card'><small>Ouro fino aberto</small><strong>{escape(str(operation_lot_market_context.get('available_fine_grams', '0')))} g</strong></div>
                        <div class='card'><small>Mercado em aberto</small><strong>USD {escape(str(operation_lot_market_context.get('market_value_usd', '0')))}</strong></div>
                        <div class='card'><small>P/L em aberto</small><strong class='{'positive' if Decimal(str(operation_lot_market_context.get('unrealized_pnl_usd', '0'))) >= 0 else 'negative'}'>USD {escape(str(operation_lot_market_context.get('unrealized_pnl_usd', '0')))}</strong></div>
                    </div>
                    <p class='hint'>No caixa, os lotes seguem segregados por teor. Isso impede leitura misturada entre, por exemplo, 90 e 85, e mostra onde a posição aberta está concentrando lucro ou risco.</p>
                    <table style='margin-top:14px;'>
                        <thead><tr><th>Teor</th><th>Gramas</th><th>P/L</th></tr></thead>
                        <tbody>{operation_lot_teor_html}</tbody>
                    </table>
                    <table style='margin-top:14px;'>
                        <thead><tr><th>Lote</th><th>Teor</th><th>Saldo</th><th>P/L</th></tr></thead>
                        <tbody>{risk_lots_html}</tbody>
                    </table>
                </section>
            </aside>
        </div>
        """

    return SimpleNamespace(render_operation_page_content=render_operation_page_content)