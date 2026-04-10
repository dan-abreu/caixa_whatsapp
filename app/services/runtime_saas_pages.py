from html import escape
from types import SimpleNamespace
from typing import Any, Dict, List


def build_runtime_saas_page_helpers() -> SimpleNamespace:
    def render_dashboard_page_content(balances_html: str) -> str:
        return f"""
        <section class='panel section'>
            <div data-fragment-url='/saas/fragments/dashboard-summary' data-fragment-priority='eager'>
                <div class='empty-state'>Carregando resumo executivo...</div>
            </div>
        </section>
        <div class='dashboard-shell'>
            <div class='dashboard-main'>
                <section class='panel section'>
                    <h2>Evolucao Operacional</h2>
                    <p class='hint'>As barras mostram as gramas brutas giradas por dia. A linha acompanha o ouro fino equivalente movimentado, refletindo melhor a qualidade real do metal no caixa.</p>
                    <div data-fragment-url='/saas/fragments/dashboard-trend' data-fragment-priority='eager'>
                        <div class='empty-state'>Carregando evolucao operacional...</div>
                    </div>
                </section>
                <section class='panel section'>
                    <h2>Posicao Consolidada dos Caixas</h2>
                    <div class='balance-grid'>{balances_html}</div>
                </section>
                <section class='panel section'>
                    <h2>Estoque FIFO em Aberto</h2>
                    <div data-fragment-url='/saas/fragments/dashboard-inventory' data-fragment-priority='viewport'>
                        <div class='empty-state'>Carregando estoque FIFO...</div>
                    </div>
                </section>
                <section class='panel section'>
                    <div class='section-head'>
                        <div>
                            <h2>Monitores IA Selecionados</h2>
                            <p class='hint'>O dashboard exibe apenas os lotes com monitor 24h habilitado. A pagina dedicada concentra todos os lotes abertos, inclusive os ainda nao monitorados.</p>
                        </div>
                        <a href='/saas/monitores' class='ghost-link mini-action'>Abrir pagina completa</a>
                    </div>
                    <div class='lot-monitor-explainer'>
                        <p class='hint'><strong>Como funciona:</strong> a IA da web revisa cada lote aberto em ciclos. Ela compara o preco atual com sua meta em USD/g e com a tendencia recente do ouro.</p>
                        <p class='hint'><span class='legend-chip positive'>Verde</span> indica oportunidade de venda ou meta atingida. <span class='legend-chip negative'>Vermelho</span> indica enfraquecimento e sugestao de proteger lucro. <span class='legend-chip neutral'>Cinza</span> indica que o lote ainda deve aguardar.</p>
                        <p class='hint'>Campos usados no gatilho: <strong>Meta USD/g</strong>, <strong>Lucro minimo %</strong> e <strong>Ativar monitor 24h</strong>. O card atualiza sozinho e a IA da web repete o aviso no banner e no chat quando surgir novo gatilho.</p>
                    </div>
                    <div class='lot-monitor-grid' data-fragment-url='/saas/fragments/dashboard-monitors' data-fragment-priority='viewport'>
                        <div class='empty-state'>Carregando monitores selecionados...</div>
                    </div>
                </section>
            </div>
            <div class='dashboard-side'>
                <section class='panel section'>
                    <div class='section-head'>
                        <div>
                            <h2>Radar de Noticias</h2>
                            <p class='hint'>Somente as 3 manchetes mais recentes entram no dashboard. A central de noticias preserva o fluxo completo.</p>
                        </div>
                        <a href='/saas/noticias' class='ghost-link mini-action'>Ver noticias</a>
                    </div>
                    <div data-fragment-url='/saas/fragments/dashboard-news' data-fragment-priority='idle'>
                        <div class='empty-state'>Carregando noticias...</div>
                    </div>
                </section>
                <section class='panel section'>
                    <h2>Fechamentos Pendentes</h2>
                    <div data-fragment-url='/saas/fragments/dashboard-pending-closings' data-fragment-priority='idle'>
                        <div class='empty-state'>Carregando fechamentos pendentes...</div>
                    </div>
                </section>
                <section class='panel section'>
                    <h2>Ultimas Operacoes</h2>
                    <div data-fragment-url='/saas/fragments/dashboard-recent-operations' data-fragment-priority='idle'>
                        <div class='empty-state'>Carregando ultimas operacoes...</div>
                    </div>
                </section>
            </div>
        </div>
        """

    def render_monitors_page_content(
        lot_monitor_entries: List[Dict[str, Any]],
        enabled_lot_monitor_entries: List[Dict[str, Any]],
        web_lot_ai_alerts: List[Dict[str, Any]],
        full_lot_monitor_html: str,
        monitor_alerts_html: str,
    ) -> str:
        return f"""
        <div class='grid'>
            <div class='stack'>
                <section class='panel section'>
                    <div class='section-head'>
                        <div>
                            <h2>Monitores IA dos Lotes</h2>
                            <p class='hint'>Esta pagina organiza a rotina de acompanhamento dos lotes abertos. O operador escolhe o que merece vigilancia 24h e a IA destaca lotes em janela de saida, meta batida ou perda de forca.</p>
                        </div>
                    </div>
                    <div class='cards'>
                        <div class='card'><small>Lotes Abertos</small><strong>{escape(str(len(lot_monitor_entries)))}</strong></div>
                        <div class='card'><small>Monitores 24h Ativos</small><strong>{escape(str(len(enabled_lot_monitor_entries)))}</strong></div>
                        <div class='card'><small>Gatilhos Ativos</small><strong>{escape(str(len(web_lot_ai_alerts)))}</strong></div>
                    </div>
                </section>
                <section class='panel section'>
                    <div class='section-head'>
                        <div>
                            <h2>Painel Completo de Lotes</h2>
                            <p class='hint'>Use esta area para ligar ou desligar monitores, calibrar meta por grama e exigir folga minima antes de vender.</p>
                        </div>
                    </div>
                    <div class='lot-monitor-grid'>{full_lot_monitor_html}</div>
                </section>
            </div>
            <div class='stack'>
                <section class='panel section'>
                    <h2>Fila de Gatilhos</h2>
                    <p class='hint'>Priorize primeiro meta batida, depois janela favoravel e por fim protecao de lucro. Isso preserva disciplina de caixa e evita operar lote fora do plano.</p>
                    <table>
                        <thead><tr><th>Lote</th><th>Status</th><th>P/L %</th><th>Leitura</th></tr></thead>
                        <tbody>{monitor_alerts_html}</tbody>
                    </table>
                </section>
            </div>
        </div>
        """

    def render_news_page_content(market_news_items: List[Dict[str, Any]], news_gold_count: int, news_fx_count: int, news_hub_html: str, recent_html: str) -> str:
        return f"""
        <div class='grid'>
            <div class='stack'>
                <section class='panel section'>
                    <div class='section-head'>
                        <div>
                            <h2>Central de Noticias</h2>
                            <p class='hint'>O dashboard ficou enxuto. Aqui ficam todas as noticias recentes usadas para leitura de contexto macro antes de ajustar preco, segurar lote ou travar lucro.</p>
                        </div>
                    </div>
                    <div class='cards'>
                        <div class='card'><small>Noticias Carregadas</small><strong>{escape(str(len(market_news_items)))}</strong></div>
                        <div class='card'><small>Radar Ouro</small><strong>{escape(str(news_gold_count))}</strong></div>
                        <div class='card'><small>Radar Cambio</small><strong>{escape(str(news_fx_count))}</strong></div>
                    </div>
                </section>
                <section class='panel section'>
                    <h2>Feed Completo</h2>
                    <p class='hint'>Leitura dedicada para ouro, dolar e referencias externas que podem deslocar preco, spread e urgencia de saida.</p>
                    {news_hub_html}
                </section>
            </div>
            <div class='stack'>
                <section class='panel section'>
                    <h2>Ultimas Operacoes</h2>
                    <p class='hint'>Cruze a noticia com o fluxo do balcao: manchete sem reflexo nas compras e vendas recentes raramente exige ajuste imediato.</p>
                    <table>
                        <thead><tr><th>ID</th><th>Tipo</th><th>Pessoa</th><th>Peso</th><th>Total</th></tr></thead>
                        <tbody>{recent_html}</tbody>
                    </table>
                </section>
            </div>
        </div>
        """

    def render_profile_page_content(session_user: Dict[str, Any], user_name: str, user_phone: str, user_role: str, balances_html: str) -> str:
        bootstrap_flag = "Sim" if session_user.get("web_pin_bootstrap_required") else "Nao"
        return f"""
        <div class='grid'>
            <div class='stack'>
                <section class='panel section'>
                    <h2>Perfil do Usuario</h2>
                    <p class='hint'>Esta area concentra as informacoes cadastrais, credenciais e parametros vinculados ao acesso atual do painel.</p>
                    <div class='cards'>
                        <div class='card'><small>Nome</small><strong>{user_name}</strong></div>
                        <div class='card'><small>Telefone</small><strong>{user_phone}</strong></div>
                        <div class='card'><small>Perfil</small><strong>{user_role}</strong></div>
                    </div>
                </section>
                <section class='panel section'>
                    <h2>Seguranca de Acesso</h2>
                    <p class='hint'>Atualize o PIN sempre que houver bootstrap ou renovacao de credencial. Em caso de PIN temporario, recomenda-se a troca imediata.</p>
                    <form method='post' action='/saas/profile/pin'>
                        <input type='hidden' name='page' value='profile' />
                        <div class='fields-3'>
                            <label>PIN atual
                                <input type='password' name='current_pin' inputmode='numeric' required />
                            </label>
                            <label>Novo PIN
                                <input type='password' name='new_pin' inputmode='numeric' required />
                            </label>
                            <label>Confirmar novo PIN
                                <input type='password' name='confirm_pin' inputmode='numeric' required />
                            </label>
                        </div>
                        <button type='submit'>Atualizar PIN de acesso</button>
                    </form>
                </section>
            </div>
            <div class='stack'>
                <section class='panel section'>
                    <h2>Conta de Acesso</h2>
                    <div class='cards'>
                        <div class='card'><small>Login ativo</small><strong>{user_phone}</strong></div>
                        <div class='card'><small>PIN temporario</small><strong>{escape(bootstrap_flag)}</strong></div>
                        <div class='card'><small>Status da sessao</small><strong>Ativa</strong></div>
                    </div>
                    <div class='tip-box'>
                        <strong>Escopo desta area</strong>
                        <p class='hint'>Esta aba concentra configuracoes pessoais e informacoes da conta, preservando a separacao entre gestao de acesso e rotina operacional.</p>
                    </div>
                </section>
                <section class='panel section'>
                    <h2>Saldos de Referencia</h2>
                    <p class='hint'>Os saldos permanecem visiveis nesta area como consulta rapida, sem necessidade de retornar ao painel inicial.</p>
                    <div class='balance-grid'>{balances_html}</div>
                </section>
                {session_user.get('company_bank_accounts_html') or ''}
            </div>
        </div>
        """

    def render_statement_page_content(statement: Dict[str, Any], statement_rows_html: str, open_fechamentos_statement_html: str) -> str:
        return f"""
        <section class='panel section'>
            <div class='section-head'>
                <div>
                    <h2>Extrato Operacional</h2>
                    <p class='hint'>Por padrao, a consulta apresenta o movimento do dia e permite filtragem por intervalo fechado de datas.</p>
                </div>
            </div>
            <form method='get' action='/saas/extrato' class='filter-bar'>
                <label>Data inicial
                    <input type='date' name='start_date' value='{escape(str(statement.get('start_date') or ''))}' />
                </label>
                <label>Data final
                    <input type='date' name='end_date' value='{escape(str(statement.get('end_date') or ''))}' />
                </label>
                <button type='submit'>Aplicar filtro</button>
                <a href='/saas/extrato' class='ghost-link'>Hoje</a>
            </form>
            <div class='cards'>
                <div class='card'><small>Periodo</small><strong>{escape(str(statement.get('label') or '-'))}</strong></div>
                <div class='card'><small>Operacoes no periodo</small><strong>{escape(str(statement.get('summary', {}).get('total_operacoes', 0)))}</strong></div>
                <div class='card'><small>Volume em USD</small><strong>USD {escape(str(statement.get('summary', {}).get('total_usd', '0')))}</strong></div>
            </div>
        </section>
        <div class='grid'>
            <div class='stack'>
                <section class='panel section'>
                    <h2>Movimentacoes</h2>
                    <table>
                        <thead><tr><th>ID</th><th>Tipo</th><th>Pessoa</th><th>Peso</th><th>Total</th><th>Fechamento</th><th>Pagamentos</th></tr></thead>
                        <tbody>{statement_rows_html}</tbody>
                    </table>
                </section>
            </div>
            <div class='stack'>
                <section class='panel section'>
                    <h2>Posicoes com Fechamento Pendente</h2>
                    <p class='hint'>Este quadro apresenta as operacoes do periodo filtrado que permaneceram com fechamento parcial e ainda possuem gramas em aberto.</p>
                    <table>
                        <thead><tr><th>ID</th><th>Pessoa</th><th>Peso</th><th>Fechado</th><th>Em aberto</th></tr></thead>
                        <tbody>{open_fechamentos_statement_html}</tbody>
                    </table>
                </section>
                <section class='panel section'>
                    <h2>Resumo Textual</h2>
                    <pre>{escape(str(statement.get('statement_text') or ''))}</pre>
                </section>
            </div>
        </div>
        """

    return SimpleNamespace(
        render_dashboard_page_content=render_dashboard_page_content,
        render_monitors_page_content=render_monitors_page_content,
        render_news_page_content=render_news_page_content,
        render_profile_page_content=render_profile_page_content,
        render_statement_page_content=render_statement_page_content,
    )