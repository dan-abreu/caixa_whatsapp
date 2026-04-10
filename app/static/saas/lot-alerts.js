(function (global) {
    const createLotAlertRuntime = (config) => {
        const {
            bootstrapData,
            lotAlertBootstrap,
            lotMonitorBootstrap,
            appendAssistantAlert,
            maybeNotifyBrowser,
            playAlertTone,
            setDatasetValueIfChanged,
            setPropertyIfChanged,
            setTextIfChanged,
        } = config;

        const lotAlertBanner = document.getElementById('webAiAlertBanner');
        const lotAlertText = document.getElementById('webAiAlertText');
        const lotAlertButton = document.getElementById('webAiNotificationButton');
        const lotAlertEndpoint = lotAlertBanner ? lotAlertBanner.dataset.lotAlertEndpoint : '';
        const lotAlertStreamEndpoint = lotAlertBanner ? lotAlertBanner.dataset.lotAlertStreamEndpoint || '' : '';
        const lotAlertSeenKey = 'caixaLotAiAlertsSeen';
        const lotAlertSeen = new Set();
        const lotMonitorCardBindings = new WeakMap();
        const hasLotAlertFeatures = Boolean(
            lotAlertBanner
            || lotAlertButton
            || lotAlertStreamEndpoint
            || lotAlertEndpoint
            || lotAlertBootstrap.length
            || lotMonitorBootstrap.length
            || document.querySelector('[data-lot-monitor-card]')
        );
        let lotAlertStream = null;
        let lotAlertPollTimer = null;

        const getLotMonitorCards = () => Array.from(document.querySelectorAll('[data-lot-monitor-card]'));

        const readLotAlertSeen = () => {
            try {
                const parsed = JSON.parse(localStorage.getItem(lotAlertSeenKey) || '[]');
                if (Array.isArray(parsed)) parsed.forEach((item) => lotAlertSeen.add(String(item || '')));
            } catch (_error) {}
        };

        const saveLotAlertSeen = () => localStorage.setItem(lotAlertSeenKey, JSON.stringify(Array.from(lotAlertSeen).slice(-40)));

        const renderLotAlertBanner = (payload) => {
            if (!lotAlertBanner || !lotAlertText) return;
            const summary = payload && payload.summary ? payload.summary : 'Nenhum monitor de lote com gatilho ativo agora.';
            const alerts = Array.isArray(payload && payload.alerts) ? payload.alerts : [];
            const topStatusClass = String(alerts[0] && alerts[0].status_class ? alerts[0].status_class : 'neutral');
            setTextIfChanged(lotAlertText, summary);
            const previousStatusClass = lotAlertBanner.dataset.statusClass || '';
            if (previousStatusClass !== topStatusClass) {
                if (previousStatusClass) {
                    lotAlertBanner.classList.remove(previousStatusClass);
                } else {
                    lotAlertBanner.classList.remove('positive', 'negative', 'neutral');
                }
                lotAlertBanner.classList.add(topStatusClass);
                setDatasetValueIfChanged(lotAlertBanner, 'statusClass', topStatusClass);
            }
            const shouldHide = alerts.length === 0;
            if (lotAlertBanner.classList.contains('is-hidden') !== shouldHide) {
                lotAlertBanner.classList.toggle('is-hidden', shouldHide);
            }
        };

        const getLotMonitorCardBinding = (card) => {
            const cached = lotMonitorCardBindings.get(card);
            if (cached) return cached;
            const binding = {
                statusPill: card.querySelector('[data-lot-status-pill]'),
                sourceId: card.querySelector('[data-lot-source-id]'),
                remaining: card.querySelector('[data-lot-remaining-grams]'),
                teor: card.querySelector('[data-lot-teor]'),
                trendLabel: card.querySelector('[data-lot-trend-label]'),
                marketUnit: card.querySelector('[data-lot-market-unit]'),
                entryUnit: card.querySelector('[data-lot-entry-unit]'),
                targetUnit: card.querySelector('[data-lot-target-unit]'),
                targetGap: card.querySelector('[data-lot-target-gap]'),
                targetProgress: card.querySelector('[data-lot-target-progress]'),
                targetProgressBar: card.querySelector('[data-lot-target-progress-bar]'),
                unrealized: card.querySelector('[data-lot-unrealized-pnl]'),
                profitPct: card.querySelector('[data-lot-profit-pct]'),
                minProfitGap: card.querySelector('[data-lot-min-profit-gap]'),
                holdDays: card.querySelector('[data-lot-hold-days]'),
                trendBias: card.querySelector('[data-lot-trend-bias]'),
                monitorMode: card.querySelector('[data-lot-monitor-mode]'),
                reason: card.querySelector('[data-lot-reason]'),
                targetPrice: card.querySelector('[data-lot-target-price]'),
                minProfit: card.querySelector('[data-lot-min-profit]'),
                notifyPhone: card.querySelector('[data-lot-notify-phone]'),
                enabled: card.querySelector('[data-lot-enabled]'),
            };
            lotMonitorCardBindings.set(card, binding);
            return binding;
        };

        const renderLotMonitorCards = (lots) => {
            const lotMonitorCards = getLotMonitorCards();
            if (!Array.isArray(lots) || !lotMonitorCards.length) return;
            const byId = new Map(lots.map((item) => [String(item && item.id ? item.id : ''), item]));
            lotMonitorCards.forEach((card) => {
                const item = byId.get(String(card.dataset.lotId || ''));
                if (!item) return;
                const statusClass = String(item.status_class || 'neutral');
                const binding = getLotMonitorCardBinding(card);
                const setInputValue = (node, value) => {
                    if (!node || document.activeElement === node) return;
                    if (node.value !== value) node.value = value;
                };
                setTextIfChanged(binding.sourceId, `GT-${item.source_transaction_id || ''}`);
                setTextIfChanged(binding.remaining, String(item.remaining_grams || '0'));
                setTextIfChanged(binding.teor, String(item.teor || '-'));
                setTextIfChanged(binding.trendLabel, String(item.trend_label || 'Lateral'));
                if (binding.statusPill) {
                    setTextIfChanged(binding.statusPill, String(item.action_label || item.status_label || 'Aguardar'));
                    const pillClassName = `lot-monitor-pill ${statusClass}`;
                    if (binding.statusPill.className !== pillClassName) binding.statusPill.className = pillClassName;
                }
                const previousStatusClass = card.dataset.statusClass || '';
                if (previousStatusClass !== statusClass) {
                    if (previousStatusClass) {
                        card.classList.remove(previousStatusClass);
                    } else {
                        card.classList.remove('positive', 'negative', 'neutral');
                    }
                    card.classList.add(statusClass);
                    setDatasetValueIfChanged(card, 'statusClass', statusClass);
                }
                setTextIfChanged(binding.entryUnit, `USD ${item.entry_unit_usd || '0'}`);
                setTextIfChanged(binding.marketUnit, `USD ${item.market_unit_usd || '0'}`);
                setTextIfChanged(binding.targetUnit, item.target_unit_usd ? `USD ${item.target_unit_usd}` : '-');
                if (binding.targetGap) {
                    setTextIfChanged(binding.targetGap, `USD ${item.target_gap_usd || '0'}`);
                    if (binding.targetGap.className !== statusClass) binding.targetGap.className = statusClass;
                }
                setTextIfChanged(binding.targetProgress, `${item.target_progress_pct || '0'}%`);
                if (binding.targetProgressBar) {
                    const width = `${item.target_progress_pct || '0'}%`;
                    if (binding.targetProgressBar.style.width !== width) binding.targetProgressBar.style.width = width;
                }
                if (binding.unrealized) {
                    setTextIfChanged(binding.unrealized, `USD ${item.unrealized_pnl_usd || '0'}`);
                    if (binding.unrealized.className !== statusClass) binding.unrealized.className = statusClass;
                }
                if (binding.profitPct) {
                    setTextIfChanged(binding.profitPct, `${item.profit_pct || '0'}%`);
                    if (binding.profitPct.className !== statusClass) binding.profitPct.className = statusClass;
                }
                if (binding.minProfitGap) {
                    setTextIfChanged(binding.minProfitGap, `${item.min_profit_gap_pct || '0'} p.p.`);
                    if (binding.minProfitGap.className !== statusClass) binding.minProfitGap.className = statusClass;
                }
                setTextIfChanged(binding.holdDays, String(item.hold_days || '0'));
                setTextIfChanged(binding.trendBias, String(item.trend_label || 'Lateral'));
                setTextIfChanged(binding.monitorMode, item.enabled ? 'Monitor 24h ativo' : 'Monitor desligado');
                setTextIfChanged(binding.reason, String(item.reason || '-'));
                setInputValue(binding.targetPrice, String(item.target_price_usd || ''));
                setInputValue(binding.minProfit, String(item.min_profit_pct || '4'));
                setInputValue(binding.notifyPhone, String(item.notify_phone || ''));
                if (binding.enabled && binding.enabled.checked !== Boolean(item.enabled)) binding.enabled.checked = Boolean(item.enabled);
            });
        };

        const ingestLotAlerts = (payload, options = { markSeenOnly: false }) => {
            const alerts = Array.isArray(payload && payload.alerts) ? payload.alerts : [];
            const lots = Array.isArray(payload && payload.lots) ? payload.lots : [];
            renderLotAlertBanner(payload);
            renderLotMonitorCards(lots);
            alerts.forEach((alert) => {
                const signature = String(alert && alert.signature ? alert.signature : '');
                if (!signature || lotAlertSeen.has(signature)) return;
                lotAlertSeen.add(signature);
                if (!options.markSeenOnly) {
                    appendAssistantAlert(String(alert.message || payload.summary || 'A IA web detectou um novo gatilho de lote.'));
                    playAlertTone();
                    maybeNotifyBrowser('IA da web', String(alert.message || payload.summary || 'Novo alerta de lote.'));
                }
            });
            saveLotAlertSeen();
        };

        const pollLotAlerts = async () => {
            if (document.hidden || !lotAlertEndpoint) return;
            try {
                const response = await fetch(lotAlertEndpoint, { headers: { Accept: 'application/json' }, credentials: 'same-origin' });
                if (!response.ok) throw new Error('Falha ao atualizar os monitores IA.');
                const data = await response.json();
                if (!data || !data.ok) throw new Error('Snapshot de monitor IA invalido.');
                ingestLotAlerts(data);
            } catch (_error) {}
        };

        const connectLotAlertStream = () => {
            if (document.hidden || !lotAlertStreamEndpoint || !('EventSource' in window)) return false;
            try {
                if (lotAlertStream) return true;
                lotAlertStream = new EventSource(lotAlertStreamEndpoint);
                lotAlertStream.addEventListener('snapshot', (event) => {
                    try {
                        if (document.hidden) return;
                        const data = JSON.parse(event.data || '{}');
                        if (data && data.ok) ingestLotAlerts(data);
                    } catch (_error) {}
                });
                lotAlertStream.onerror = () => {
                    if (lotAlertStream) {
                        lotAlertStream.close();
                        lotAlertStream = null;
                    }
                    window.setTimeout(() => {
                        if (document.hidden) return;
                        if (!connectLotAlertStream()) pollLotAlerts();
                    }, 2000);
                };
                return true;
            } catch (_error) {
                lotAlertStream = null;
                return false;
            }
        };

        const closeLotAlertStream = () => {
            if (!lotAlertStream) return;
            lotAlertStream.close();
            lotAlertStream = null;
        };

        const stopLotAlertPolling = () => {
            if (!lotAlertPollTimer) return;
            window.clearInterval(lotAlertPollTimer);
            lotAlertPollTimer = null;
        };

        const ensureLotAlertPolling = () => {
            if (lotAlertPollTimer || !lotAlertEndpoint) return;
            lotAlertPollTimer = window.setInterval(() => {
                if (document.hidden) return;
                pollLotAlerts();
            }, 5000);
        };

        const setupNotificationButton = () => {
            if (!lotAlertButton) return;
            if (!('Notification' in window)) {
                lotAlertButton.classList.add('is-hidden');
            } else if (Notification.permission === 'granted') {
                setTextIfChanged(lotAlertButton, 'Avisos do navegador ativos');
                setPropertyIfChanged(lotAlertButton, 'disabled', true);
            } else {
                lotAlertButton.addEventListener('click', async () => {
                    try {
                        const result = await Notification.requestPermission();
                        if (result === 'granted') {
                            setTextIfChanged(lotAlertButton, 'Avisos do navegador ativos');
                            setPropertyIfChanged(lotAlertButton, 'disabled', true);
                        }
                    } catch (_error) {}
                }, { once: true });
            }
        };

        const initialize = () => {
            setupNotificationButton();
            if (!hasLotAlertFeatures) return;
            readLotAlertSeen();
            ingestLotAlerts({ alerts: lotAlertBootstrap, lots: lotMonitorBootstrap, summary: String(bootstrapData.lotSummary || '') }, { markSeenOnly: true });
            if (!lotAlertStreamEndpoint && !lotAlertEndpoint) return;
            if (connectLotAlertStream()) {
                stopLotAlertPolling();
            } else if (lotAlertEndpoint) {
                ensureLotAlertPolling();
            }
        };

        const resumeVisibleRealtime = () => {
            if (document.hidden || !hasLotAlertFeatures || (!lotAlertStreamEndpoint && !lotAlertEndpoint)) return;
            if (connectLotAlertStream()) {
                stopLotAlertPolling();
            } else {
                ensureLotAlertPolling();
                pollLotAlerts();
            }
        };

        const handleHidden = () => {
            closeLotAlertStream();
        };

        const teardown = () => {
            closeLotAlertStream();
            stopLotAlertPolling();
        };

        return { initialize, resumeVisibleRealtime, handleHidden, teardown };
    };

    global.CaixaSaasLotAlerts = { createLotAlertRuntime };
})(window);