(function (global) {
    const createOperationCalculatorRuntime = (config) => {
        const {
            closureClosed, closureClosedSide, closureOpen, closureOpenSide, closurePeso, closurePesoSide,
            diffUsd, fechamentoHint, fechamentoHintSide, fechamentoInput, fechamentoTipo, fineGold,
            goldTypeInput, grams, money, opForm, operationClientRuntime, paymentRows, precoInput,
            pesoInput, quebraInput, quebraWrap, rateioHint, recentFx, scheduleFrame, setClassPresenceIfChanged, setDatasetValueIfChanged,
            setInputValueIfChanged, setPropertyIfChanged, setStylePropertyIfChanged, setTextIfChanged,
            summaryText, targetUsd, paidUsd, totalPagoInput, teorInput, tipoOperacao, totalUsd,
            usePesoTotal, useTotalAsUsd,
        } = config;

        const paymentRowBindings = new WeakMap();
        let calculatorFrame = 0;

        const parseNumber = (value) => {
            const normalized = String(value || '').trim().replace(/,/g, '.');
            const parsed = Number(normalized);
            return Number.isFinite(parsed) ? parsed : 0;
        };
        const paymentFxLabel = (currency) => {
            const moeda = String(currency || '').toUpperCase();
            if (moeda === 'EUR') return '1 EUR = quantos USD?';
            if (moeda === 'SRD' || moeda === 'BRL') return `1 USD = quantos ${moeda}?`;
            return 'Cambio para USD';
        };
        const paymentUsdFromInput = (currency, amount, rate) => {
            const moeda = String(currency || '').toUpperCase();
            if (!moeda || amount <= 0) return 0;
            if (moeda === 'USD') return amount;
            if (rate <= 0) return 0;
            return moeda === 'EUR' ? amount * rate : amount / rate;
        };
        const paymentAmountFromUsdTarget = (currency, usdAmount, rate) => {
            const moeda = String(currency || '').toUpperCase();
            if (!moeda || usdAmount <= 0) return 0;
            if (moeda === 'USD') return usdAmount;
            if (rate <= 0) return 0;
            return moeda === 'EUR' ? usdAmount / rate : usdAmount * rate;
        };
        const formatInputNumber = (value, places = 2) => Number(value || 0).toFixed(places).replace(/\.00$/, '').replace(/(\.\d*[1-9])0+$/, '$1');
        const getPaymentRowBinding = (row) => {
            const cached = paymentRowBindings.get(row);
            if (cached) return cached;
            const binding = {
                moeda: row.querySelector('.js-payment-moeda'),
                valor: row.querySelector('.js-payment-valor'),
                percent: row.querySelector('.js-payment-percent'),
                cambio: row.querySelector('.js-payment-cambio'),
                preview: row.querySelector('.js-payment-preview'),
                label: row.querySelector('.js-payment-cambio-label'),
            };
            paymentRowBindings.set(row, binding);
            return binding;
        };
        const refreshPaymentRowUi = (row) => {
            const { moeda, label } = getPaymentRowBinding(row);
            if (!moeda || !label) return;
            setTextIfChanged(label, paymentFxLabel(moeda.value));
        };
        const updateGoldTypeUi = () => {
            const isCompra = !tipoOperacao || tipoOperacao.value === 'compra';
            const isQueimado = !!goldTypeInput && String(goldTypeInput.value || '').toLowerCase() === 'queimado';
            const mustShowQuebra = isCompra && isQueimado;
            setClassPresenceIfChanged(quebraWrap, 'is-hidden', !mustShowQuebra);
            if (!quebraInput) return;
            setPropertyIfChanged(quebraInput, 'disabled', !mustShowQuebra);
            setPropertyIfChanged(quebraInput, 'required', mustShowQuebra);
            if (!mustShowQuebra) setInputValueIfChanged(quebraInput, '');
        };
        const applyAutoFx = (row, force = false) => {
            const { moeda, cambio } = getPaymentRowBinding(row);
            if (!moeda || !cambio) return;
            refreshPaymentRowUi(row);
            const moedaValue = String(moeda.value || '').toUpperCase();
            const suggested = moedaValue === 'USD' ? '1' : String(recentFx[moedaValue] || '');
            if (!suggested) return;
            if (force || !String(cambio.value || '').trim() || cambio.dataset.autofilled === '1') {
                setInputValueIfChanged(cambio, suggested);
                setDatasetValueIfChanged(cambio, 'autofilled', '1');
            }
        };
        const buildPaymentRowState = (row) => {
            const binding = getPaymentRowBinding(row);
            return { row, ...binding, moedaValue: binding.moeda ? String(binding.moeda.value || '').toUpperCase() : '', valorNumber: parseNumber(binding.valor && binding.valor.value), percentNumber: parseNumber(binding.percent && binding.percent.value), cambioNumber: parseNumber(binding.cambio && binding.cambio.value) };
        };
        const clearDraftFields = () => {
            for (let index = 1; index <= 4; index += 1) {
                const moeda = opForm.elements.namedItem(`payment_${index}_moeda`);
                const valor = opForm.elements.namedItem(`payment_${index}_valor`);
                const percent = opForm.elements.namedItem(`payment_${index}_percent`);
                const cambio = opForm.elements.namedItem(`payment_${index}_cambio`);
                const forma = opForm.elements.namedItem(`payment_${index}_forma`);
                setInputValueIfChanged(moeda, index === 1 ? 'USD' : '');
                setInputValueIfChanged(valor, '');
                setInputValueIfChanged(percent, '');
                if (cambio) {
                    setInputValueIfChanged(cambio, index === 1 ? '1' : '');
                    setDatasetValueIfChanged(cambio, 'autofilled', index === 1 ? '1' : '0');
                }
                setInputValueIfChanged(forma, 'dinheiro');
            }
        };
        const updateCalculator = () => {
            updateGoldTypeUi();
            const peso = parseNumber(pesoInput && pesoInput.value);
            const teor = parseNumber(teorInput && teorInput.value);
            const preco = parseNumber(precoInput && precoInput.value);
            const fechamentoAtual = parseNumber(fechamentoInput && fechamentoInput.value);
            const isTotal = fechamentoTipo && fechamentoTipo.value === 'total';
            if (isTotal && fechamentoInput && peso > 0) setInputValueIfChanged(fechamentoInput, peso.toFixed(3).replace(/\.000$/, ''));
            const fechamento = isTotal ? peso : fechamentoAtual;
            const fechamentoAplicado = Math.max(0, Math.min(fechamento, peso || 0));
            const abertoDepois = Math.max(0, (peso || 0) - fechamentoAplicado);
            const ouroFino = peso * (teor / 100);
            const totalRef = peso * preco;
            const targetPaymentUsd = peso > 0 ? (totalRef * (fechamentoAplicado / peso)) : totalRef;
            const paymentRowStates = paymentRows.map(buildPaymentRowState);
            let totalPercent = 0;
            paymentRowStates.forEach((state) => { totalPercent += state.percentNumber; });
            if (totalPercent > 0 && targetPaymentUsd > 0) {
                paymentRowStates.forEach((state) => {
                    const { moedaValue, valor, percentNumber, cambioNumber } = state;
                    if (!valor) return;
                    if (percentNumber <= 0 || !moedaValue) {
                        const wasPercentAutofilled = valor.dataset.percentAutofill === '1';
                        if (wasPercentAutofilled && valor.value !== '') setInputValueIfChanged(valor, '');
                        setDatasetValueIfChanged(valor, 'percentAutofill', '0');
                        if (wasPercentAutofilled) state.valorNumber = 0;
                        return;
                    }
                    const moedaAmount = paymentAmountFromUsdTarget(moedaValue, targetPaymentUsd * (percentNumber / 100), cambioNumber);
                    if (moedaAmount <= 0) return;
                    setInputValueIfChanged(valor, formatInputNumber(moedaAmount, 2));
                    setDatasetValueIfChanged(valor, 'percentAutofill', '1');
                    state.valorNumber = moedaAmount;
                });
            }
            let pagamentosUsd = 0;
            paymentRowStates.forEach((state) => {
                const { row, preview, moedaValue, percentNumber } = state;
                applyAutoFx(row);
                state.cambioNumber = parseNumber(state.cambio && state.cambio.value);
                state.valorNumber = parseNumber(state.valor && state.valor.value);
                const rowUsd = paymentUsdFromInput(moedaValue, state.valorNumber, state.cambioNumber);
                pagamentosUsd += rowUsd;
                if (!preview) return;
                setTextIfChanged(preview, rowUsd > 0 ? (percentNumber > 0 ? `${money(rowUsd)} · ${percentNumber.toFixed(2).replace(/\.00$/, '')}%` : money(rowUsd)) : (moedaValue && (state.valorNumber > 0 || percentNumber > 0) ? 'Informe cambio' : 'USD 0.00'));
            });
            const fallbackPago = parseNumber(totalPagoInput && totalPagoInput.value);
            const totalPago = pagamentosUsd > 0 ? pagamentosUsd : fallbackPago;
            if (totalPagoInput && pagamentosUsd <= 0 && targetPaymentUsd > 0 && (!String(totalPagoInput.value || '').trim() || totalPagoInput.dataset.autofilled === '1')) {
                setInputValueIfChanged(totalPagoInput, targetPaymentUsd.toFixed(2));
                setDatasetValueIfChanged(totalPagoInput, 'autofilled', '1');
            }
            if (pagamentosUsd > 0 && totalPagoInput) {
                setInputValueIfChanged(totalPagoInput, pagamentosUsd.toFixed(2));
                setDatasetValueIfChanged(totalPagoInput, 'autofilled', '1');
            }
            const diferenca = targetPaymentUsd - totalPago;
            const closurePesoText = grams(peso);
            const closureClosedText = grams(fechamentoAplicado);
            const closureOpenText = grams(abertoDepois);
            setTextIfChanged(fineGold, grams(ouroFino));
            setTextIfChanged(totalUsd, money(totalRef));
            setTextIfChanged(targetUsd, money(targetPaymentUsd));
            setTextIfChanged(paidUsd, money(totalPago));
            setTextIfChanged(diffUsd, money(Math.abs(diferenca)));
            setStylePropertyIfChanged(diffUsd, 'color', Math.abs(diferenca) < 0.005 ? 'var(--green)' : 'var(--danger)');
            setTextIfChanged(closurePeso, closurePesoText); setTextIfChanged(closurePesoSide, closurePesoText);
            setTextIfChanged(closureClosed, closureClosedText); setTextIfChanged(closureClosedSide, closureClosedText);
            setTextIfChanged(closureOpen, closureOpenText); setTextIfChanged(closureOpenSide, closureOpenText);
            const fechamentoMensagem = peso <= 0 ? 'Informe o peso para o painel mostrar quanto fica fechado agora e quanto sobra pendente.' : (isTotal ? `Fechamento total: os ${grams(peso)} da operacao ficam fechados agora, sem saldo pendente para depois.` : `Fechamento parcial: ${grams(fechamentoAplicado)} ficam fechados agora e ${grams(abertoDepois)} continuam em aberto para fechamento futuro. Esse saldo passa a aparecer nos quadros de fechamentos pendentes.`);
            setTextIfChanged(fechamentoHint, fechamentoMensagem); setTextIfChanged(fechamentoHintSide, fechamentoMensagem);
            if (rateioHint) {
                let rateioHintText = '';
                if (totalPercent <= 0) rateioHintText = `O alvo atual do pagamento e ${money(targetPaymentUsd)}. Se voce preencher o % por moeda, o sistema calcula automaticamente quanto pagar em cada uma.`;
                else {
                    const totalPercentLabel = totalPercent.toFixed(2).replace(/\.00$/, '');
                    const remainingPercent = Math.max(0, 100 - totalPercent);
                    rateioHintText = `${Math.abs(totalPercent - 100) < 0.005 ? 'Rateio completo em 100%.' : `Rateio preenchido em ${totalPercentLabel}%.`} O painel calcula cada moeda sobre ${money(targetPaymentUsd)} com base no percentual digitado.${remainingPercent > 0.005 ? ` Ainda faltam ${remainingPercent.toFixed(2).replace(/\.00$/, '')}% para completar o pagamento.` : ''}`;
                }
                setTextIfChanged(rateioHint, rateioHintText);
            }
            if (summaryText) {
                const fechamentoLabel = fechamento > 0 ? grams(fechamento) : 'aguardando fechamento';
                const diffLabel = diferenca > 0.005 ? `faltam ${money(Math.abs(diferenca))}` : (diferenca < -0.005 ? `sobram ${money(Math.abs(diferenca))}` : 'pagamento fechado sem diferenca');
                setTextIfChanged(summaryText, (peso <= 0 || preco <= 0) ? 'Preencha peso e preco para o sistema calcular total, fechamento sugerido e diferenca automaticamente.' : `${tipoOperacao && tipoOperacao.value === 'venda' ? 'Entrada prevista' : 'Saida prevista'} ${money(totalRef)} para ${closurePesoText} (${grams(ouroFino)} de ouro fino). Alvo do fechamento: ${money(targetPaymentUsd)} para ${fechamentoLabel}. Pagamentos conferidos: ${money(totalPago)} e ${diffLabel}.`);
            }
        };
        const scheduleUpdateCalculator = () => {
            if (calculatorFrame) return;
            calculatorFrame = (typeof scheduleFrame === 'function' ? scheduleFrame : window.requestAnimationFrame)(() => {
                calculatorFrame = 0;
                updateCalculator();
            });
        };
        const refreshAfterReset = () => {
            setDatasetValueIfChanged(totalPagoInput, 'autofilled', '1');
            paymentRows.forEach((row) => {
                const { valor, cambio } = getPaymentRowBinding(row);
                setDatasetValueIfChanged(valor, 'percentAutofill', '0');
                setDatasetValueIfChanged(cambio, 'autofilled', String(cambio && cambio.value || '').trim() ? '1' : '0');
                applyAutoFx(row, true);
            });
            updateGoldTypeUi();
            updateCalculator();
        };
        const applyDraft = (draft) => {
            clearDraftFields();
            Object.entries(draft || {}).forEach(([name, value]) => {
                const field = opForm.elements.namedItem(name);
                if (field) setInputValueIfChanged(field, value == null ? '' : String(value));
            });
            operationClientRuntime.syncLookupMeta();
            updateGoldTypeUi();
            paymentRows.forEach((row) => applyAutoFx(row));
            scheduleUpdateCalculator();
        };
        const initialize = () => {
            if (usePesoTotal) usePesoTotal.addEventListener('click', () => { setInputValueIfChanged(fechamentoTipo, 'total'); scheduleUpdateCalculator(); });
            if (useTotalAsUsd) useTotalAsUsd.addEventListener('click', () => {
                const peso = parseNumber(pesoInput && pesoInput.value);
                const preco = parseNumber(precoInput && precoInput.value);
                const fechamentoAtual = parseNumber(fechamentoInput && fechamentoInput.value);
                const fechamentoAplicado = fechamentoTipo && fechamentoTipo.value === 'total' ? peso : Math.max(0, Math.min(fechamentoAtual, peso || 0));
                const targetPaymentUsd = peso > 0 ? ((peso * preco) * (fechamentoAplicado / peso)) : (peso * preco);
                if (totalPagoInput && targetPaymentUsd > 0) { setInputValueIfChanged(totalPagoInput, targetPaymentUsd.toFixed(2)); setDatasetValueIfChanged(totalPagoInput, 'autofilled', '1'); }
                scheduleUpdateCalculator();
            });
            paymentRows.forEach((row) => {
                const { moeda, valor, percent, cambio } = getPaymentRowBinding(row);
                if (moeda) moeda.addEventListener('change', () => { setDatasetValueIfChanged(cambio, 'autofilled', '1'); applyAutoFx(row, true); scheduleUpdateCalculator(); });
                if (valor) valor.addEventListener('input', () => { setDatasetValueIfChanged(valor, 'percentAutofill', '0'); });
                if (percent) percent.addEventListener('input', scheduleUpdateCalculator);
                if (cambio) cambio.addEventListener('input', () => { setDatasetValueIfChanged(cambio, 'autofilled', '0'); });
                refreshPaymentRowUi(row);
            });
            if (goldTypeInput) goldTypeInput.addEventListener('change', scheduleUpdateCalculator);
            if (tipoOperacao) tipoOperacao.addEventListener('change', scheduleUpdateCalculator);
            if (totalPagoInput) totalPagoInput.addEventListener('input', () => { setDatasetValueIfChanged(totalPagoInput, 'autofilled', '0'); });
            opForm.querySelectorAll('input, select, textarea').forEach((field) => { field.addEventListener('input', scheduleUpdateCalculator); field.addEventListener('change', scheduleUpdateCalculator); });
            paymentRows.forEach((row) => applyAutoFx(row));
            updateCalculator();
        };
        return { applyDraft, initialize, refreshAfterReset, scheduleUpdateCalculator, updateCalculator };
    };

    global.CaixaSaasOperationCalculatorRuntime = { createOperationCalculatorRuntime };
})(window);