(function (global) {
    const createMarketRuntime = (config) => {
        const {
            bootstrapSnapshot,
            formatDeltaValue,
            formatLocaleNumber,
            formatMarketValue,
            parseSnapshotNumber,
            playAlertTone,
            setClassPresenceIfChanged,
            setDatasetValueIfChanged,
            setInputValueIfChanged,
            setTextIfChanged,
        } = config;

        const marketPanels = Array.from(document.querySelectorAll('.market-panel-live'));
        const marketState = new Map();
        const marketHistory = new Map();
        const marketAlerts = new Map();
        const marketPanelBindings = new WeakMap();
        const marketStreamSources = new WeakMap();
        const marketPollTimers = new WeakMap();
        const marketHistoryStorageKey = `caixaMarketHistory:${new Date().toISOString().slice(0, 10)}`;
        const storedMarketHistoryCache = readStoredMarketHistory();
        let marketHistorySaveTimer = 0;
        const marketCore = global.CaixaSaasMarketCore && typeof global.CaixaSaasMarketCore.createMarketCore === 'function'
            ? global.CaixaSaasMarketCore.createMarketCore({ setClassPresenceIfChanged, setDatasetValueIfChanged, setTextIfChanged })
            : null;
        const drawSparkline = marketCore ? marketCore.drawSparkline : () => {};
        const findWindowBaseline = marketCore ? marketCore.findWindowBaseline : (_values, raw) => raw;
        const resetMarketCardNeutralState = marketCore ? marketCore.resetMarketCardNeutralState : () => {};

        function getMarketPanelBinding(panel) {
            const cached = marketPanelBindings.get(panel);
            if (cached) return cached;
            const binding = {
                cards: Array.from(panel.querySelectorAll('[data-market-field]')).map((card) => ({
                    card,
                    field: card.dataset.marketField,
                    prefix: card.dataset.prefix || '',
                    suffix: card.dataset.suffix || '',
                    decimals: Number.parseInt(card.dataset.decimals || '2', 10),
                    historyLength: -1,
                    label: card.querySelector('small')?.textContent || card.dataset.marketField || '',
                    polyline: card.querySelector('.market-sparkline-line'),
                    valueEl: card.querySelector('.market-value'),
                    metaEl: card.querySelector('[data-market-freshness]'),
                    windowEl: card.querySelector('[data-market-window]'),
                    changeEl: card.querySelector('.market-change'),
                    arrowEl: card.querySelector('.market-arrow'),
                    deltaEl: card.querySelector('.market-delta'),
                })),
                banner: panel.querySelector('[data-market-alert-banner]'),
                bannerText: panel.querySelector('[data-market-alert-text]'),
                statusEl: panel.querySelector('.market-status'),
                updatedEl: panel.querySelector('[data-market-updated]'),
            };
            marketPanelBindings.set(panel, binding);
            return binding;
        }

        function readStoredMarketHistory() {
            try {
                const parsed = JSON.parse(localStorage.getItem(marketHistoryStorageKey) || '{}');
                return parsed && typeof parsed === 'object' ? parsed : {};
            } catch (_error) {
                return {};
            }
        }

        function saveStoredMarketHistory() {
            marketHistorySaveTimer = 0;
            localStorage.setItem(marketHistoryStorageKey, JSON.stringify(storedMarketHistoryCache));
        }

        function scheduleStoredMarketHistorySave() {
            if (marketHistorySaveTimer) return;
            marketHistorySaveTimer = window.setTimeout(() => {
                saveStoredMarketHistory();
            }, 250);
        }

        function pushMarketHistory(endpointKey, panelHistory, field, value) {
            if (!Number.isFinite(value)) return false;
            const values = Array.isArray(panelHistory[field]) ? panelHistory[field] : [];
            if (values.length > 19) {
                values.splice(0, values.length - 19);
            }
            values.push(value);
            panelHistory[field] = values;
            storedMarketHistoryCache[endpointKey] = panelHistory;
            return true;
        }

        function maybeTriggerMarketAlert(panel, panelBinding, label, delta, pct, threshold) {
            if (!Number.isFinite(pct) || Math.abs(pct) < threshold) return false;
            const direction = pct > 0 ? 'alta' : 'queda';
            const signature = `${label}:${direction}`;
            const previousSignature = marketAlerts.get(panel);
            if (previousSignature === signature) return true;
            marketAlerts.set(panel, signature);
            const { banner, bannerText } = panelBinding || {};
            if (banner && bannerText) {
                setClassPresenceIfChanged(banner, 'is-hidden', false);
                setTextIfChanged(bannerText, `${label} em ${direction} de ${formatLocaleNumber(Math.abs(pct), 2)}% (` + formatDeltaValue(delta, 2) + ').');
            }
            playAlertTone();
            return true;
        }

        function bindMarketThresholdControl(panel) {
            const select = panel.querySelector('[data-market-threshold-select]');
            if (!select) return;
            const storageKey = `caixaMarketAlertThreshold:${panel.dataset.marketEndpoint || 'default'}`;
            const stored = localStorage.getItem(storageKey);
            if (stored) {
                setDatasetValueIfChanged(panel, 'marketAlertThreshold', stored);
                setInputValueIfChanged(select, stored);
            } else {
                setInputValueIfChanged(select, panel.dataset.marketAlertThreshold || '0.50');
            }
            select.addEventListener('change', () => {
                setDatasetValueIfChanged(panel, 'marketAlertThreshold', select.value);
                localStorage.setItem(storageKey, select.value);
            });
        }

        function updateMarketPanel(panel, snapshot) {
            if (!panel || !snapshot) return;
            const panelBinding = getMarketPanelBinding(panel);
            const endpointKey = panel.dataset.marketEndpoint || 'default';
            const panelHistory = marketHistory.get(panel) || {};
            if (!marketHistory.has(panel)) marketHistory.set(panel, panelHistory);
            const hadActiveAlert = marketAlerts.has(panel);
            const threshold = Number.parseFloat(panel.dataset.marketAlertThreshold || '0.5');
            const feedLabel = snapshot.updated_at_label ? `Feed ${snapshot.updated_at_label}` : 'Feed ativo';
            const sourceLabel = snapshot.xau_source_label ? ` · ${snapshot.xau_source_label}` : '';
            const updatedLabel = snapshot.updated_at_label ? `Atualizado ${snapshot.updated_at_label}${sourceLabel}` : `Atualizando${sourceLabel}`;
            let hasActiveAlert = false;
            let shouldPersistHistory = false;
            panelBinding.cards.forEach((binding) => {
                const { card, field, prefix, suffix, decimals, valueEl, metaEl, windowEl, changeEl, arrowEl, deltaEl, polyline, label } = binding;
                const raw = parseSnapshotNumber(snapshot[field]);
                shouldPersistHistory = pushMarketHistory(endpointKey, panelHistory, field, raw) || shouldPersistHistory;
                const historyValues = panelHistory[field] || [];
                const historyLength = historyValues.length;
                drawSparkline(polyline, historyValues);
                setTextIfChanged(valueEl, formatMarketValue(raw, prefix, suffix, decimals));
                setTextIfChanged(metaEl, feedLabel);
                if (binding.historyLength !== historyLength) {
                    binding.historyLength = historyLength;
                    setTextIfChanged(windowEl, `Janela ${Math.max(historyLength - 1, 1)}s`);
                }
                if (!changeEl || !arrowEl || !deltaEl || !Number.isFinite(raw)) return;
                if (historyLength < 2) {
                    resetMarketCardNeutralState(card, changeEl, arrowEl, deltaEl);
                    return;
                }
                const baselineRaw = Number(findWindowBaseline(historyValues, raw));
                if (!Number.isFinite(baselineRaw) || baselineRaw === 0) {
                    resetMarketCardNeutralState(card, changeEl, arrowEl, deltaEl);
                    return;
                }
                const delta = raw - baselineRaw;
                const pct = (delta / baselineRaw) * 100;
                const pctSign = pct > 0 ? '+' : '';
                let directionClass = 'neutral';
                let arrow = '•';
                if (delta > 0) {
                    directionClass = 'positive';
                    arrow = '▲';
                } else if (delta < 0) {
                    directionClass = 'negative';
                    arrow = '▼';
                }
                const changeClassName = `market-change ${directionClass}`;
                if (changeEl.className !== changeClassName) changeEl.className = changeClassName;
                setTextIfChanged(arrowEl, arrow);
                setTextIfChanged(deltaEl, Math.abs(delta) < 0.000001
                    ? 'Sem mudanca na janela'
                    : `${formatDeltaValue(delta, decimals)} ${pctSign}${formatLocaleNumber(pct, 2)}% na janela`);
                setClassPresenceIfChanged(card, 'alert-positive', pct >= threshold);
                setClassPresenceIfChanged(card, 'alert-negative', pct <= -threshold);
                const isAlertEnabled = card.dataset.alertEnabled === '1';
                hasActiveAlert = (isAlertEnabled ? maybeTriggerMarketAlert(panel, panelBinding, label || field, delta, pct, threshold) : false) || hasActiveAlert;
            });
            const { banner, bannerText, statusEl, updatedEl } = panelBinding;
            if (banner && bannerText && !hasActiveAlert) {
                const shouldResetBanner = hadActiveAlert
                    || !banner.classList.contains('is-hidden')
                    || bannerText.textContent !== 'Sem alertas relevantes.';
                if (shouldResetBanner) {
                    setClassPresenceIfChanged(banner, 'is-hidden', true);
                    setTextIfChanged(bannerText, 'Sem alertas relevantes.');
                }
                if (hadActiveAlert) marketAlerts.delete(panel);
            }
            if (statusEl && snapshot.status) setTextIfChanged(statusEl, snapshot.status);
            if (updatedEl) setTextIfChanged(updatedEl, updatedLabel);
            if (shouldPersistHistory) scheduleStoredMarketHistorySave();
            marketState.set(panel, snapshot);
        }

        async function pollMarketPanel(panel) {
            if (document.hidden) return;
            const endpoint = panel && panel.dataset.marketEndpoint;
            if (!endpoint) return;
            try {
                const response = await fetch(endpoint, { headers: { Accept: 'application/json' }, credentials: 'same-origin' });
                if (!response.ok) throw new Error('Falha ao atualizar o mercado.');
                const data = await response.json();
                if (!data || !data.ok || !data.snapshot) throw new Error('Snapshot de mercado invalido.');
                updateMarketPanel(panel, data.snapshot);
            } catch (_error) {
                const statusEl = panel.querySelector('.market-status');
                setTextIfChanged(statusEl, 'Falha ao atualizar em tempo real. Mantendo ultimo snapshot.');
            }
        }

        function connectMarketStream(panel) {
            if (document.hidden) return false;
            const streamEndpoint = panel && panel.dataset ? panel.dataset.marketStreamEndpoint || '' : '';
            if (!streamEndpoint || !('EventSource' in window)) return false;
            const existingSource = marketStreamSources.get(panel);
            if (existingSource) return true;
            try {
                const source = new EventSource(streamEndpoint);
                source.addEventListener('snapshot', (event) => {
                    try {
                        if (document.hidden) return;
                        const data = JSON.parse(event.data || '{}');
                        if (data && data.ok && data.snapshot) updateMarketPanel(panel, data.snapshot);
                    } catch (_error) {}
                });
                source.onerror = () => {
                    source.close();
                    if (marketStreamSources.get(panel) === source) marketStreamSources.delete(panel);
                    window.setTimeout(() => {
                        if (document.hidden) return;
                        if (!connectMarketStream(panel)) pollMarketPanel(panel);
                    }, 2000);
                };
                marketStreamSources.set(panel, source);
                return true;
            } catch (_error) {
                return false;
            }
        }

        function closeAllMarketStreams() {
            marketPanels.forEach((panel) => {
                const source = marketStreamSources.get(panel);
                if (!source) return;
                source.close();
                marketStreamSources.delete(panel);
            });
        }

        function stopMarketPolling(panel) {
            const timerId = marketPollTimers.get(panel);
            if (!timerId) return;
            window.clearInterval(timerId);
            marketPollTimers.delete(panel);
        }

        function ensureMarketPolling(panel) {
            if (marketPollTimers.has(panel)) return;
            const timerId = window.setInterval(() => {
                if (document.hidden) return;
                pollMarketPanel(panel);
            }, 5000);
            marketPollTimers.set(panel, timerId);
        }

        function initialize() {
            marketPanels.forEach((panel) => {
                const endpointKey = panel.dataset.marketEndpoint || 'default';
                if (storedMarketHistoryCache[endpointKey] && typeof storedMarketHistoryCache[endpointKey] === 'object') {
                    marketHistory.set(panel, storedMarketHistoryCache[endpointKey]);
                }
                bindMarketThresholdControl(panel);
                updateMarketPanel(panel, bootstrapSnapshot || {});
                if (connectMarketStream(panel)) {
                    stopMarketPolling(panel);
                } else {
                    ensureMarketPolling(panel);
                }
            });
        }

        function resumeVisibleRealtime() {
            if (document.hidden) return;
            marketPanels.forEach((panel) => {
                if (connectMarketStream(panel)) {
                    stopMarketPolling(panel);
                } else {
                    ensureMarketPolling(panel);
                    pollMarketPanel(panel);
                }
            });
        }

        function handleHidden() {
            closeAllMarketStreams();
        }

        function teardown() {
            if (marketHistorySaveTimer) {
                window.clearTimeout(marketHistorySaveTimer);
                saveStoredMarketHistory();
            }
            marketPanels.forEach((panel) => stopMarketPolling(panel));
            closeAllMarketStreams();
        }

        return { initialize, resumeVisibleRealtime, handleHidden, teardown };
    };

    global.CaixaSaasMarketRuntime = { createMarketRuntime };
})(window);