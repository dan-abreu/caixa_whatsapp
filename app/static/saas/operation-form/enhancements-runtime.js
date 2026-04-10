(function (global) {
    const createOperationEnhancementsRuntime = (config) => {
        const {
            clienteIdInput, escapeHtml, fechamentoInput, grams, paymentRows, pesoInput, saleLotApplySelection,
            saleLotChecks, saleLotGrams, saleLotSelectedTotal, saleLotSelectionPanel, saleLotSelectorBox,
            saleSourceMode, setClassPresenceIfChanged, setInputValueIfChanged, setTextIfChanged, tipoOperacao,
        } = config;

        let clientBankAccounts = [];

        const emitFieldUpdate = (field, type = 'input') => {
            if (!field || typeof field.dispatchEvent !== 'function') return;
            field.dispatchEvent(new Event(type, { bubbles: true }));
        };

        const buildBankAccountOptions = (items, selectedValue) => {
            const options = [`<option value=''>Selecionar conta salva</option>`];
            items.forEach((item) => {
                const id = String(item && item.id ? item.id : '');
                const summary = String(item && item.summary ? item.summary : 'Conta salva');
                const currency = String(item && item.currency_code ? item.currency_code : '').toUpperCase();
                const country = String(item && item.country_code ? item.country_code : '').toUpperCase();
                options.push(`<option value='${escapeHtml(id)}' data-bank-currency='${escapeHtml(currency)}' data-bank-country='${escapeHtml(country)}' data-bank-summary='${escapeHtml(summary)}' ${selectedValue === id ? 'selected' : ''}>${escapeHtml(summary)}</option>`);
            });
            return options.join('');
        };

        const syncBankOptionsForSelect = (select, currency) => {
            if (!select) return;
            Array.from(select.options || []).forEach((option) => {
                if (!option.value) {
                    option.hidden = false;
                    option.disabled = false;
                    return;
                }
                const optionCurrency = String(option.dataset.bankCurrency || '').toUpperCase();
                const visible = !currency || !optionCurrency || optionCurrency === currency;
                option.hidden = !visible;
                option.disabled = !visible;
            });
            const currentOption = select.selectedOptions && select.selectedOptions[0];
            if (currentOption && currentOption.value && currentOption.disabled) {
                select.value = '';
            }
        };

        const updateTransferBox = (row) => {
            if (!(row instanceof Element)) return;
            const methodSelect = row.querySelector('.js-payment-forma');
            const currencySelect = row.querySelector('.js-payment-moeda');
            const transferBox = row.querySelector('.js-payment-transfer-box');
            const clientSelect = row.querySelector('.js-client-bank-account-select');
            const companySelect = row.querySelector('.js-company-bank-account-select');
            const summary = row.querySelector('.js-payment-transfer-summary');
            const method = String(methodSelect && methodSelect.value ? methodSelect.value : '').toLowerCase();
            const currency = String(currencySelect && currencySelect.value ? currencySelect.value : '').toUpperCase();
            if (transferBox) {
                setClassPresenceIfChanged(transferBox, 'is-hidden', method !== 'transferencia');
            }
            syncBankOptionsForSelect(clientSelect, currency);
            syncBankOptionsForSelect(companySelect, currency);
            const selectedTexts = [clientSelect, companySelect]
                .map((select) => select && select.selectedOptions && select.selectedOptions[0] ? String(select.selectedOptions[0].dataset.bankSummary || '') : '')
                .filter(Boolean);
            if (summary) {
                setTextIfChanged(summary, selectedTexts.length
                    ? selectedTexts.join(' | ')
                    : 'Quando a linha for transferencia, selecione as contas salvas para SRD, BRL ou demais moedas operadas.');
            }
        };

        const refreshAllTransferBoxes = () => {
            paymentRows.forEach((row) => updateTransferBox(row));
        };

        const refreshClientBankAccountOptions = (selectedValue = '') => {
            paymentRows.forEach((row) => {
                const clientSelect = row.querySelector('.js-client-bank-account-select');
                if (!clientSelect) return;
                const currentValue = selectedValue || String(clientSelect.value || '');
                clientSelect.innerHTML = buildBankAccountOptions(clientBankAccounts, currentValue);
            });
            refreshAllTransferBoxes();
        };

        const fetchClientBankAccounts = async (clientId) => {
            const normalizedClientId = String(clientId || '').trim();
            if (!normalizedClientId) {
                clientBankAccounts = [];
                refreshClientBankAccountOptions('');
                return;
            }
            const response = await fetch(`/saas/clientes/${encodeURIComponent(normalizedClientId)}/bank-accounts`, {
                headers: { Accept: 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
                credentials: 'same-origin',
            });
            const data = await response.json();
            if (!response.ok || !data.ok) {
                throw new Error(data.notice || 'Nao foi possivel carregar as contas bancarias do cliente.');
            }
            clientBankAccounts = Array.isArray(data.items) ? data.items : [];
            refreshClientBankAccountOptions('');
        };

        const updateSaleLotUi = () => {
            const isSale = String(tipoOperacao && tipoOperacao.value ? tipoOperacao.value : '').toLowerCase() === 'venda';
            const isSelectionMode = String(saleSourceMode && saleSourceMode.value ? saleSourceMode.value : 'manual').toLowerCase() === 'selected';
            if (saleLotSelectorBox) setClassPresenceIfChanged(saleLotSelectorBox, 'is-hidden', !isSale);
            if (saleLotSelectionPanel) setClassPresenceIfChanged(saleLotSelectionPanel, 'is-hidden', !isSale || !isSelectionMode);
            let total = 0;
            saleLotChecks.forEach((checkbox) => {
                const lotId = String(checkbox.dataset.lotId || '');
                const gramsInput = saleLotGrams.find((input) => String(input.dataset.lotId || '') === lotId);
                if (!checkbox.checked) return;
                const gramsValue = Number.parseFloat(String(gramsInput && gramsInput.value ? gramsInput.value : '0').replace(',', '.'));
                if (Number.isFinite(gramsValue) && gramsValue > 0) total += gramsValue;
            });
            if (saleLotSelectedTotal) setTextIfChanged(saleLotSelectedTotal, `${grams(total)} g`);
        };

        const bindSaleLotSelection = () => {
            saleLotChecks.forEach((checkbox) => {
                checkbox.addEventListener('change', () => {
                    const lotId = String(checkbox.dataset.lotId || '');
                    const gramsInput = saleLotGrams.find((input) => String(input.dataset.lotId || '') === lotId);
                    if (checkbox.checked && gramsInput && !String(gramsInput.value || '').trim()) {
                        setInputValueIfChanged(gramsInput, String(gramsInput.dataset.lotMax || ''));
                    }
                    updateSaleLotUi();
                });
            });
            saleLotGrams.forEach((input) => input.addEventListener('input', updateSaleLotUi));
            if (saleSourceMode) saleSourceMode.addEventListener('change', updateSaleLotUi);
            if (tipoOperacao) tipoOperacao.addEventListener('change', updateSaleLotUi);
            if (saleLotApplySelection) {
                saleLotApplySelection.addEventListener('click', () => {
                    let total = 0;
                    saleLotChecks.forEach((checkbox) => {
                        if (!checkbox.checked) return;
                        const lotId = String(checkbox.dataset.lotId || '');
                        const gramsInput = saleLotGrams.find((input) => String(input.dataset.lotId || '') === lotId);
                        const gramsValue = Number.parseFloat(String(gramsInput && gramsInput.value ? gramsInput.value : '0').replace(',', '.'));
                        if (Number.isFinite(gramsValue) && gramsValue > 0) total += gramsValue;
                    });
                    if (pesoInput) {
                        setInputValueIfChanged(pesoInput, total > 0 ? String(total) : '');
                        emitFieldUpdate(pesoInput);
                    }
                    if (fechamentoInput && !String(fechamentoInput.value || '').trim() && total > 0) {
                        setInputValueIfChanged(fechamentoInput, String(total));
                        emitFieldUpdate(fechamentoInput);
                    }
                    updateSaleLotUi();
                });
            }
            updateSaleLotUi();
        };

        const bindTransferBoxes = () => {
            paymentRows.forEach((row) => {
                ['.js-payment-forma', '.js-payment-moeda', '.js-client-bank-account-select', '.js-company-bank-account-select'].forEach((selector) => {
                    const field = row.querySelector(selector);
                    if (!field) return;
                    field.addEventListener('change', () => updateTransferBox(row));
                });
            });
            document.addEventListener('caixa:cliente-selected', async (event) => {
                try {
                    await fetchClientBankAccounts(event && event.detail ? event.detail.id : '');
                } catch (_error) {
                    clientBankAccounts = [];
                    refreshClientBankAccountOptions('');
                }
            });
            document.addEventListener('caixa:cliente-cleared', () => {
                clientBankAccounts = [];
                refreshClientBankAccountOptions('');
            });
            const initialClientId = clienteIdInput ? String(clienteIdInput.value || '').trim() : '';
            if (initialClientId) {
                fetchClientBankAccounts(initialClientId).catch(() => {
                    clientBankAccounts = [];
                    refreshClientBankAccountOptions('');
                });
            } else {
                refreshAllTransferBoxes();
            }
        };

        const refreshAfterReset = () => {
            updateSaleLotUi();
            refreshAllTransferBoxes();
        };

        const initialize = () => {
            bindSaleLotSelection();
            bindTransferBoxes();
        };

        return { initialize, refreshAfterReset };
    };

    global.CaixaSaasOperationEnhancementsRuntime = { createOperationEnhancementsRuntime };
})(window);
