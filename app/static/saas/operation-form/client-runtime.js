(function (global) {
    const createOperationClientRuntime = (config) => {
        const {
            clienteIdInput,
            clienteLookupMeta,
            clienteMeta,
            clienteResults,
            escapeHtml,
            inlineClienteBox,
            inlineClienteMode,
            inlineClienteNome,
            inlineClienteSave,
            inlineClienteStatus,
            opForm,
            pessoaInput,
            setClassPresenceIfChanged,
            setInputValueIfChanged,
            setPropertyIfChanged,
            setTextIfChanged,
            toggleInlineCliente,
        } = config;

        let clienteSearchTimer = 0;
        let clienteSearchController = null;
        let clienteSearchSequence = 0;
        let clienteResultsTerm = '';
        let clienteResultsSignature = '';
        const clienteSearchCache = new Map();

        const setInlineClienteMode = (enabled) => {
            if (!inlineClienteBox || !inlineClienteMode) return;
            setClassPresenceIfChanged(inlineClienteBox, 'is-hidden', !enabled);
            setInputValueIfChanged(inlineClienteMode, enabled ? '1' : '0');
            if (toggleInlineCliente) {
                setTextIfChanged(toggleInlineCliente, enabled ? 'Fechar cadastro rapido' : 'Cadastro rapido de cliente');
            }
            if (enabled && inlineClienteNome && !String(inlineClienteNome.value || '').trim() && pessoaInput) {
                setInputValueIfChanged(inlineClienteNome, String(pessoaInput.value || '').trim());
            }
            setTextIfChanged(inlineClienteStatus, enabled
                ? 'Preencha os dados e use o botao de confirmacao para salvar o cliente.'
                : 'Salve o cliente aqui para selecionar a conta antes de registrar a operacao.');
        };

        const closeClienteResults = () => {
            if (!clienteResults) return;
            clienteResults.innerHTML = '';
            setClassPresenceIfChanged(clienteResults, 'is-hidden', true);
            clienteResultsTerm = '';
            clienteResultsSignature = '';
        };

        const setSelectedCliente = (cliente) => {
            if (!cliente) return;
            setInputValueIfChanged(clienteIdInput, String(cliente.id || ''));
            setInputValueIfChanged(pessoaInput, String(cliente.nome || ''));
            const metaText = String(cliente.meta || '');
            setInputValueIfChanged(clienteLookupMeta, metaText);
            setTextIfChanged(clienteMeta, metaText || 'Cliente selecionado.');
            setInlineClienteMode(false);
            closeClienteResults();
            document.dispatchEvent(new CustomEvent('caixa:cliente-selected', { detail: { id: String(cliente.id || ''), nome: String(cliente.nome || ''), meta: metaText } }));
        };

        const clearSelectedCliente = () => {
            setInputValueIfChanged(clienteIdInput, '');
            setInputValueIfChanged(clienteLookupMeta, '');
            setTextIfChanged(clienteMeta, 'Selecione um cliente existente ou use o cadastro rapido abaixo.');
            document.dispatchEvent(new CustomEvent('caixa:cliente-cleared'));
        };

        const buildClienteResultsSignature = (items) => {
            if (!Array.isArray(items) || !items.length) return '__empty__';
            return items.map((cliente) => [
                String(cliente && cliente.id ? cliente.id : ''),
                String(cliente && cliente.nome ? cliente.nome : ''),
                String(cliente && cliente.meta ? cliente.meta : ''),
            ].join('|')).join('||');
        };

        const renderClienteResults = (items) => {
            if (!clienteResults) return;
            const resultsSignature = buildClienteResultsSignature(items);
            if (clienteResultsSignature === resultsSignature) {
                setClassPresenceIfChanged(clienteResults, 'is-hidden', false);
                return;
            }
            if (!Array.isArray(items) || !items.length) {
                clienteResults.innerHTML = '<div class="hint">Nenhum cliente encontrado. Use o cadastro rapido para registrar um novo.</div>';
                setClassPresenceIfChanged(clienteResults, 'is-hidden', false);
                clienteResultsSignature = resultsSignature;
                return;
            }
            clienteResults.innerHTML = items.map((cliente) => `
                <button type="button" class="client-option" data-client-id="${String(cliente.id || '')}" data-client-name="${escapeHtml(cliente.nome || '')}" data-client-meta="${escapeHtml(cliente.meta || '')}">
                    ${escapeHtml(cliente.nome || '')}
                    <small>${escapeHtml(cliente.meta || '')}</small>
                </button>
            `).join('');
            setClassPresenceIfChanged(clienteResults, 'is-hidden', false);
            clienteResultsSignature = resultsSignature;
        };

        const fetchClientes = async (term, signal) => {
            const normalizedTerm = String(term || '').trim();
            if (clienteSearchCache.has(normalizedTerm)) {
                return clienteSearchCache.get(normalizedTerm);
            }
            const response = await fetch(`/saas/clientes/search?q=${encodeURIComponent(term)}`, {
                headers: { Accept: 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
                credentials: 'same-origin',
                signal,
            });
            const data = await response.json();
            if (!response.ok || !data.ok) {
                throw new Error(data.notice || 'Nao foi possivel consultar clientes.');
            }
            const items = Array.isArray(data.items) ? data.items : [];
            clienteSearchCache.set(normalizedTerm, items);
            return items;
        };

        const queueClienteSearch = (term, delay, onError) => {
            const normalizedTerm = String(term || '').trim();
            window.clearTimeout(clienteSearchTimer);
            if (normalizedTerm.length < 2) {
                if (clienteSearchController) {
                    clienteSearchController.abort();
                    clienteSearchController = null;
                }
                closeClienteResults();
                return;
            }
            if (!clienteResults?.classList.contains('is-hidden') && clienteResultsTerm === normalizedTerm) {
                return;
            }
            clienteSearchTimer = window.setTimeout(async () => {
                const sequence = clienteSearchSequence + 1;
                clienteSearchSequence = sequence;
                if (clienteSearchController) clienteSearchController.abort();
                clienteSearchController = typeof AbortController === 'function' ? new AbortController() : null;
                try {
                    const items = await fetchClientes(normalizedTerm, clienteSearchController ? clienteSearchController.signal : undefined);
                    if (sequence !== clienteSearchSequence) return;
                    renderClienteResults(items);
                    clienteResultsTerm = normalizedTerm;
                } catch (error) {
                    if (error && error.name === 'AbortError') return;
                    if (typeof onError === 'function') onError(error);
                }
            }, delay);
        };

        const saveInlineCliente = async () => {
            const nome = String(opForm.elements.namedItem('inline_cliente_nome')?.value || '').trim();
            if (!nome) {
                throw new Error('Informe o nome do cliente para concluir o cadastro rapido.');
            }
            const payload = new URLSearchParams();
            payload.set('page', 'operation');
            payload.set('client_nome', nome);
            payload.set('client_telefone', String(opForm.elements.namedItem('inline_cliente_telefone')?.value || '').trim());
            payload.set('client_documento', String(opForm.elements.namedItem('inline_cliente_documento')?.value || '').trim());
            payload.set('client_apelido', String(opForm.elements.namedItem('inline_cliente_apelido')?.value || '').trim());
            payload.set('client_observacoes', String(opForm.elements.namedItem('inline_cliente_observacoes')?.value || '').trim());
            payload.set('client_opening_xau', String(opForm.elements.namedItem('inline_cliente_saldo_xau')?.value || '').trim());

            const response = await fetch('/saas/clientes', {
                method: 'POST',
                headers: { 'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8', 'X-Requested-With': 'XMLHttpRequest', Accept: 'application/json' },
                body: payload.toString(),
                credentials: 'same-origin',
            });
            const data = await response.json();
            if (!response.ok || !data.ok) {
                throw new Error(data.notice || 'Nao foi possivel cadastrar o cliente agora.');
            }
            const item = data.item || {};
            clienteSearchCache.clear();
            clienteResultsTerm = '';
            setSelectedCliente({ id: item.id, nome: item.nome, meta: item.meta });
            setTextIfChanged(inlineClienteStatus, 'Cliente cadastrado e selecionado com sucesso.');
            ['inline_cliente_nome', 'inline_cliente_telefone', 'inline_cliente_documento', 'inline_cliente_apelido', 'inline_cliente_observacoes', 'inline_cliente_saldo_xau'].forEach((fieldName) => {
                const field = opForm.elements.namedItem(fieldName);
                setInputValueIfChanged(field, '');
            });
        };

        const syncLookupMeta = () => {
            if (clienteMeta) {
                setTextIfChanged(clienteMeta, String(opForm.elements.namedItem('cliente_lookup_meta')?.value || '').trim() || 'Selecione um cliente existente ou use o cadastro rapido abaixo.');
            }
        };

        const resetAfterSuccess = () => {
            clearSelectedCliente();
            closeClienteResults();
            setInlineClienteMode(false);
            setTextIfChanged(inlineClienteStatus, 'Salve o cliente aqui para selecionar a conta antes de registrar a operacao.');
        };

        const initialize = () => {
            if (clienteResults) {
                clienteResults.addEventListener('click', (event) => {
                    const button = event.target instanceof Element ? event.target.closest('.client-option') : null;
                    if (!button) return;
                    setSelectedCliente({
                        id: button.dataset.clientId,
                        nome: button.dataset.clientName,
                        meta: button.dataset.clientMeta,
                    });
                });
            }
            if (toggleInlineCliente) {
                toggleInlineCliente.addEventListener('click', () => setInlineClienteMode(inlineClienteMode && inlineClienteMode.value !== '1'));
            }
            if (inlineClienteSave) {
                inlineClienteSave.addEventListener('click', async () => {
                    setTextIfChanged(inlineClienteStatus, 'Salvando cliente...');
                    setPropertyIfChanged(inlineClienteSave, 'disabled', true);
                    try {
                        await saveInlineCliente();
                    } catch (error) {
                        setTextIfChanged(inlineClienteStatus, (error && error.message) || 'Falha ao cadastrar o cliente.');
                    } finally {
                        setPropertyIfChanged(inlineClienteSave, 'disabled', false);
                    }
                });
            }
            if (pessoaInput) {
                pessoaInput.addEventListener('input', () => {
                    clearSelectedCliente();
                    const term = String(pessoaInput.value || '').trim();
                    if (inlineClienteMode && inlineClienteMode.value === '1' && inlineClienteNome && !String(inlineClienteNome.value || '').trim()) {
                        setInputValueIfChanged(inlineClienteNome, term);
                    }
                    queueClienteSearch(term, 180, (error) => {
                        setTextIfChanged(clienteMeta, (error && error.message) || 'Falha ao consultar clientes.');
                    });
                });

                pessoaInput.addEventListener('focus', () => {
                    const term = String(pessoaInput.value || '').trim();
                    queueClienteSearch(term, 120, () => {});
                });
            }
            document.addEventListener('click', (event) => {
                if (!clienteResults || !pessoaInput) return;
                if (clienteResults.contains(event.target) || pessoaInput.contains(event.target)) return;
                closeClienteResults();
            });
            setInlineClienteMode(inlineClienteMode && inlineClienteMode.value === '1');
            if (clienteLookupMeta && clienteLookupMeta.value) {
                setTextIfChanged(clienteMeta, clienteLookupMeta.value);
            }
        };

        return { initialize, resetAfterSuccess, syncLookupMeta };
    };

    global.CaixaSaasOperationClientRuntime = { createOperationClientRuntime };
})(window);