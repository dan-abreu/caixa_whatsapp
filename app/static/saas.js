(() => {
    const widget = document.getElementById('aiChatWidget');
    const dragHandle = document.getElementById('aiChatHandle');
    const thread = document.getElementById('aiChatThread');
    const form = document.getElementById('aiChatForm');
    const input = document.getElementById('aiChatInput');
    const send = document.getElementById('aiChatSend');
    const remetente = document.getElementById('aiChatRemetente');
    if (!widget || !dragHandle || !thread || !form || !input || !send || !remetente) return;

    const historyKey = 'caixaSaasAiHistory';
    const dragPositionKey = 'caixaSaasAiPosition';
    const dragSnapMargin = 12;
    const dragSnapThreshold = 42;
    const bootstrapNode = document.getElementById('saasDashboardBootstrap');
    if (!bootstrapNode) return;

    let bootstrapData = {};
    try {
        bootstrapData = JSON.parse(bootstrapNode.textContent || '{}') || {};
    } catch (_error) {}

    const bootstrap = Array.isArray(bootstrapData.chatHistory) ? bootstrapData.chatHistory : [];
    const lotAlertBootstrap = Array.isArray(bootstrapData.lotAlerts) ? bootstrapData.lotAlerts : [];
    const lotMonitorBootstrap = Array.isArray(bootstrapData.lotMonitorEntries) ? bootstrapData.lotMonitorEntries : [];
    const recentFx = bootstrapData.recentFx && typeof bootstrapData.recentFx === 'object' ? bootstrapData.recentFx : { USD: '1' };
    const currentPage = String(bootstrapData.currentPage || 'dashboard');
    const supportsHover = window.matchMedia('(hover: hover) and (pointer: fine)').matches;
    const shouldHydrateChatEagerly = !supportsHover || currentPage === 'dashboard';

    const escapeHtml = (value) => String(value || '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');

    const nowStamp = () => new Date().toISOString();
    const localeNumberFormatters = new Map();
    const chatTimeFormatter = new Intl.DateTimeFormat('pt-BR', { hour: '2-digit', minute: '2-digit' });

    const formatLocaleNumber = (value, decimals) => {
        if (!Number.isFinite(value)) return 'Indisponivel';
        const normalizedDecimals = Number.isFinite(decimals) ? decimals : 2;
        let formatter = localeNumberFormatters.get(normalizedDecimals);
        if (!formatter) {
            formatter = new Intl.NumberFormat('pt-BR', {
                minimumFractionDigits: normalizedDecimals,
                maximumFractionDigits: normalizedDecimals,
            });
            localeNumberFormatters.set(normalizedDecimals, formatter);
        }
        return formatter.format(value);
    };

    const formatMarketValue = (value, prefix = '', suffix = '', decimals = 2) => {
        if (!Number.isFinite(value)) return 'Indisponivel';
        return `${prefix}${formatLocaleNumber(value, decimals)}${suffix}`;
    };

    const formatDeltaValue = (value, decimals = 2) => {
        if (!Number.isFinite(value)) return 'Indisponivel';
        const sign = value > 0 ? '+' : '';
        return `${sign}${formatLocaleNumber(value, decimals)}`;
    };

    const parseSnapshotNumber = (value) => {
        if (typeof value === 'number') return value;
        return Number.parseFloat(String(value || '').replace(',', '.'));
    };

    const formatStamp = (value) => {
        const parsed = value ? new Date(value) : new Date();
        if (Number.isNaN(parsed.getTime())) return '';
        return chatTimeFormatter.format(parsed);
    };

    const normalizeEntry = (item) => ({
        role: item && item.role ? item.role : 'assistant',
        content: item && item.content ? item.content : '',
        ts: item && item.ts ? item.ts : nowStamp(),
    });

    const readHistory = () => {
        try {
            const parsed = JSON.parse(localStorage.getItem(historyKey) || '[]');
            if (Array.isArray(parsed) && parsed.length) return parsed.map(normalizeEntry);
        } catch (_error) {}
        return bootstrap.map(normalizeEntry);
    };

    const saveHistory = (history) => localStorage.setItem(historyKey, JSON.stringify(history.slice(-24)));

    const renderChatEntry = (item) => {
        const role = item && item.role === 'user' ? 'user' : (item && item.role === 'typing' ? 'typing' : 'assistant');
        const meta = role === 'user' ? 'Voce' : (role === 'typing' ? 'Digitando' : 'IA Operacional');
        const stamp = role === 'typing' ? '' : formatStamp(item.ts);
        const body = role === 'typing'
            ? '<span class="typing-dots" aria-label="Digitando"><span></span><span></span><span></span></span>'
            : escapeHtml(item.content || '');
        return `<div class="chat-row ${role}"><div class="chat-bubble"><span class="chat-meta"><span>${meta}</span><span class="chat-time">${stamp}</span></span>${body}</div></div>`;
    };

    const getChatEntrySignature = (item) => JSON.stringify([item.role, item.ts, item.content]);

    let renderedHistorySignatures = [];

    const renderHistory = (history) => {
        const normalizedHistory = history.map(normalizeEntry);
        const nextSignatures = normalizedHistory.map(getChatEntrySignature);
        const commonPrefixLength = (() => {
            const limit = Math.min(renderedHistorySignatures.length, nextSignatures.length);
            let index = 0;
            while (index < limit && renderedHistorySignatures[index] === nextSignatures[index]) index += 1;
            return index;
        })();
        if (commonPrefixLength === renderedHistorySignatures.length && nextSignatures.length > renderedHistorySignatures.length) {
            const suffixHtml = normalizedHistory.slice(commonPrefixLength).map(renderChatEntry).join('');
            if (suffixHtml) thread.insertAdjacentHTML('beforeend', suffixHtml);
        } else if (
            nextSignatures.length === renderedHistorySignatures.length
            && nextSignatures.length > 0
            && commonPrefixLength === nextSignatures.length - 1
            && thread.lastElementChild
        ) {
            thread.lastElementChild.outerHTML = renderChatEntry(normalizedHistory[normalizedHistory.length - 1]);
        } else if (commonPrefixLength !== nextSignatures.length || nextSignatures.length !== renderedHistorySignatures.length) {
            thread.innerHTML = normalizedHistory.map(renderChatEntry).join('');
        }
        renderedHistorySignatures = nextSignatures;
        thread.scrollTop = thread.scrollHeight;
    };

    const clampWidgetPosition = (left, top) => {
        const rect = widget.getBoundingClientRect();
        const maxLeft = Math.max(dragSnapMargin, window.innerWidth - rect.width - dragSnapMargin);
        const maxTop = Math.max(dragSnapMargin, window.innerHeight - rect.height - dragSnapMargin);
        return {
            left: Math.min(Math.max(dragSnapMargin, left), maxLeft),
            top: Math.min(Math.max(dragSnapMargin, top), maxTop),
        };
    };

    const applyWidgetPosition = (left, top) => {
        const clamped = clampWidgetPosition(left, top);
        setStylePropertyIfChanged(widget, 'left', `${clamped.left}px`);
        setStylePropertyIfChanged(widget, 'top', `${clamped.top}px`);
        setStylePropertyIfChanged(widget, 'right', 'auto');
        setStylePropertyIfChanged(widget, 'bottom', 'auto');
    };

    const snapWidgetPosition = (left, top) => {
        const rect = widget.getBoundingClientRect();
        const maxLeft = Math.max(dragSnapMargin, window.innerWidth - rect.width - dragSnapMargin);
        const maxTop = Math.max(dragSnapMargin, window.innerHeight - rect.height - dragSnapMargin);
        let snappedLeft = left;
        let snappedTop = top;
        if (left <= dragSnapMargin + dragSnapThreshold) snappedLeft = dragSnapMargin;
        else if (left >= maxLeft - dragSnapThreshold) snappedLeft = maxLeft;
        if (top <= dragSnapMargin + dragSnapThreshold) snappedTop = dragSnapMargin;
        else if (top >= maxTop - dragSnapThreshold) snappedTop = maxTop;
        return clampWidgetPosition(snappedLeft, snappedTop);
    };

    const persistWidgetPosition = () => {
        const rect = widget.getBoundingClientRect();
        localStorage.setItem(dragPositionKey, JSON.stringify({ left: Math.round(rect.left), top: Math.round(rect.top) }));
    };

    const ensureWidgetInViewport = () => {
        if (!widget.style.left && !widget.style.top) return;
        const rect = widget.getBoundingClientRect();
        applyWidgetPosition(rect.left, rect.top);
        persistWidgetPosition();
    };

    const setMinimized = (state) => {
        widget.classList.toggle('minimized', state);
        widget.setAttribute('data-expanded', state ? 'false' : 'true');
        window.requestAnimationFrame(() => ensureWidgetInViewport());
    };

    const restoreWidgetPosition = () => {
        try {
            const parsed = JSON.parse(localStorage.getItem(dragPositionKey) || '{}');
            const left = Number(parsed && parsed.left);
            const top = Number(parsed && parsed.top);
            if (!Number.isFinite(left) || !Number.isFinite(top)) return;
            applyWidgetPosition(left, top);
        } catch (_error) {}
    };

    setMinimized(supportsHover);
    let history = [];
    let chatWidgetHydrated = false;
    let chatHydrationScheduled = false;

    const hydrateChatWidget = () => {
        if (chatWidgetHydrated) return;
        chatWidgetHydrated = true;
        restoreWidgetPosition();
        history = readHistory();
        renderHistory(history);
    };

    const scheduleChatHydration = () => {
        if (chatWidgetHydrated || chatHydrationScheduled) return;
        chatHydrationScheduled = true;
        const hydrate = () => {
            chatHydrationScheduled = false;
            hydrateChatWidget();
        };
        if (typeof window.requestIdleCallback === 'function') {
            window.requestIdleCallback(hydrate, { timeout: 1500 });
            return;
        }
        window.setTimeout(hydrate, 300);
    };

    if (shouldHydrateChatEagerly) {
        hydrateChatWidget();
    } else {
        scheduleChatHydration();
    }

    const marketPanels = Array.from(document.querySelectorAll('.market-panel-live'));
    const marketState = new Map();
    const marketHistory = new Map();
    const marketAlerts = new Map();
    const marketPanelBindings = new WeakMap();
    const marketStreamSources = new WeakMap();
    const marketPollTimers = new WeakMap();
    const marketHistoryStorageKey = `caixaMarketHistory:${new Date().toISOString().slice(0, 10)}`;
    const lotMonitorCardBindings = new WeakMap();
    const getLotMonitorCards = () => Array.from(document.querySelectorAll('[data-lot-monitor-card]'));
    const lotAlertBanner = document.getElementById('webAiAlertBanner');
    const lotAlertText = document.getElementById('webAiAlertText');
    const lotAlertEndpoint = lotAlertBanner ? lotAlertBanner.dataset.lotAlertEndpoint : '';
    const lotAlertStreamEndpoint = lotAlertBanner ? lotAlertBanner.dataset.lotAlertStreamEndpoint || '' : '';
    const lotAlertSeenKey = 'caixaLotAiAlertsSeen';
    const lotAlertSeen = new Set();
    const lotAlertButton = document.getElementById('webAiNotificationButton');
    const marketRail = document.getElementById('marketRail');
    let lotAlertStream = null;
    let lotAlertPollTimer = null;
    const hasLotAlertFeatures = Boolean(
        lotAlertBanner
        || lotAlertButton
        || lotAlertStreamEndpoint
        || lotAlertEndpoint
        || lotAlertBootstrap.length
        || lotMonitorBootstrap.length
        || document.querySelector('[data-lot-monitor-card]')
    );

    const setMarketRailExpanded = (expanded) => {
        if (!marketRail) return;
        marketRail.classList.toggle('is-expanded', expanded);
        marketRail.classList.toggle('is-minimized', !expanded);
    };

    if (marketRail) {
        setMarketRailExpanded(!supportsHover);
        if (supportsHover) {
            marketRail.addEventListener('mouseenter', () => setMarketRailExpanded(true));
            marketRail.addEventListener('mouseleave', () => {
                if (!marketRail.contains(document.activeElement)) setMarketRailExpanded(false);
            });
            marketRail.addEventListener('focusin', () => setMarketRailExpanded(true));
            marketRail.addEventListener('focusout', () => {
                window.setTimeout(() => {
                    if (!marketRail.contains(document.activeElement)) setMarketRailExpanded(false);
                }, 0);
            });
        }
    }

    const readLotAlertSeen = () => {
        try {
            const parsed = JSON.parse(localStorage.getItem(lotAlertSeenKey) || '[]');
            if (Array.isArray(parsed)) parsed.forEach((item) => lotAlertSeen.add(String(item || '')));
        } catch (_error) {}
    };

    const saveLotAlertSeen = () => localStorage.setItem(lotAlertSeenKey, JSON.stringify(Array.from(lotAlertSeen).slice(-40)));

    const maybeNotifyBrowser = (title, body) => {
        if (!('Notification' in window) || Notification.permission !== 'granted') return;
        try {
            new Notification(title, { body });
        } catch (_error) {}
    };

    const appendAssistantAlert = (content) => {
        hydrateChatWidget();
        history = history.concat([{ role: 'assistant', content, ts: nowStamp() }]);
        saveHistory(history);
        renderHistory(history);
        setMinimized(false);
    };

    const loadDeferredFragment = (container) => {
        if (!(container instanceof HTMLElement)) return;
        const url = String(container.dataset.fragmentUrl || '').trim();
        if (!url || container.dataset.fragmentLoaded === '1' || container.dataset.fragmentLoading === '1') return;
        container.dataset.fragmentLoading = '1';
        fetch(url, {
            headers: { 'X-Requested-With': 'XMLHttpRequest', 'Accept': 'text/html' },
            credentials: 'same-origin',
        })
            .then(async (response) => {
                const html = await response.text();
                if (!response.ok) {
                    throw new Error(response.status === 401 ? 'Sessao expirada. Recarregue a pagina.' : 'Nao foi possivel carregar este painel.');
                }
                container.innerHTML = html;
                container.dataset.fragmentLoaded = '1';
                delete container.dataset.fragmentLoading;
            })
            .catch((error) => {
                container.innerHTML = `<div class="empty-state">${escapeHtml((error && error.message) || 'Nao foi possivel carregar este painel.')}</div>`;
                delete container.dataset.fragmentLoading;
            });
    };

    const loadDeferredFragments = () => {
        const containers = Array.from(document.querySelectorAll('[data-fragment-url]'));
        if (!containers.length) return;

        const eagerContainers = [];
        const viewportContainers = [];
        const idleContainers = [];

        containers.forEach((container) => {
            const priority = String(container.dataset.fragmentPriority || 'eager').toLowerCase();
            if (priority === 'idle') {
                idleContainers.push(container);
            } else if (priority === 'viewport') {
                viewportContainers.push(container);
            } else {
                eagerContainers.push(container);
            }
        });

        eagerContainers.forEach((container) => loadDeferredFragment(container));

        if (viewportContainers.length) {
            if ('IntersectionObserver' in window) {
                const observer = new IntersectionObserver((entries) => {
                    entries.forEach((entry) => {
                        if (!entry.isIntersecting) return;
                        observer.unobserve(entry.target);
                        loadDeferredFragment(entry.target);
                    });
                }, { rootMargin: '240px 0px' });
                viewportContainers.forEach((container) => observer.observe(container));
            } else {
                viewportContainers.forEach((container) => loadDeferredFragment(container));
            }
        }

        if (idleContainers.length) {
            const loadIdleContainers = () => idleContainers.forEach((container) => loadDeferredFragment(container));
            if (typeof window.requestIdleCallback === 'function') {
                window.requestIdleCallback(loadIdleContainers, { timeout: 1200 });
            } else {
                window.setTimeout(loadIdleContainers, 300);
            }
        }
    };

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
            const setNodeText = (node, value) => {
                if (node && node.textContent !== value) node.textContent = value;
            };
            const setInputValue = (node, value) => {
                if (!node || document.activeElement === node) return;
                if (node.value !== value) node.value = value;
            };
            setNodeText(binding.sourceId, `GT-${item.source_transaction_id || ''}`);
            setNodeText(binding.remaining, String(item.remaining_grams || '0'));
            setNodeText(binding.teor, String(item.teor || '-'));
            setNodeText(binding.trendLabel, String(item.trend_label || 'Lateral'));
            if (binding.statusPill) {
                setNodeText(binding.statusPill, String(item.action_label || item.status_label || 'Aguardar'));
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
            setNodeText(binding.entryUnit, `USD ${item.entry_unit_usd || '0'}`);
            setNodeText(binding.marketUnit, `USD ${item.market_unit_usd || '0'}`);
            setNodeText(binding.targetUnit, item.target_unit_usd ? `USD ${item.target_unit_usd}` : '-');
            if (binding.targetGap) {
                setNodeText(binding.targetGap, `USD ${item.target_gap_usd || '0'}`);
                if (binding.targetGap.className !== statusClass) binding.targetGap.className = statusClass;
            }
            setNodeText(binding.targetProgress, `${item.target_progress_pct || '0'}%`);
            if (binding.targetProgressBar) {
                const width = `${item.target_progress_pct || '0'}%`;
                if (binding.targetProgressBar.style.width !== width) binding.targetProgressBar.style.width = width;
            }
            if (binding.unrealized) {
                setNodeText(binding.unrealized, `USD ${item.unrealized_pnl_usd || '0'}`);
                if (binding.unrealized.className !== statusClass) binding.unrealized.className = statusClass;
            }
            if (binding.profitPct) {
                setNodeText(binding.profitPct, `${item.profit_pct || '0'}%`);
                if (binding.profitPct.className !== statusClass) binding.profitPct.className = statusClass;
            }
            if (binding.minProfitGap) {
                setNodeText(binding.minProfitGap, `${item.min_profit_gap_pct || '0'} p.p.`);
                if (binding.minProfitGap.className !== statusClass) binding.minProfitGap.className = statusClass;
            }
            setNodeText(binding.holdDays, String(item.hold_days || '0'));
            setNodeText(binding.trendBias, String(item.trend_label || 'Lateral'));
            setNodeText(binding.monitorMode, item.enabled ? 'Monitor 24h ativo' : 'Monitor desligado');
            setNodeText(binding.reason, String(item.reason || '-'));
            setInputValue(binding.targetPrice, String(item.target_price_usd || ''));
            setInputValue(binding.minProfit, String(item.min_profit_pct || '4'));
            setInputValue(binding.notifyPhone, String(item.notify_phone || ''));
            if (binding.enabled) {
                const enabled = Boolean(item.enabled);
                if (binding.enabled.checked !== enabled) binding.enabled.checked = enabled;
            }
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
        if (document.hidden) return;
        if (!lotAlertEndpoint) return;
        try {
            const response = await fetch(lotAlertEndpoint, { headers: { 'Accept': 'application/json' }, credentials: 'same-origin' });
            if (!response.ok) throw new Error('Falha ao atualizar os monitores IA.');
            const data = await response.json();
            if (!data || !data.ok) throw new Error('Snapshot de monitor IA invalido.');
            ingestLotAlerts(data);
        } catch (_error) {}
    };

    const connectLotAlertStream = () => {
        if (document.hidden) return false;
        if (!lotAlertStreamEndpoint || !('EventSource' in window)) return false;
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

    const readStoredMarketHistory = () => {
        try {
            const parsed = JSON.parse(localStorage.getItem(marketHistoryStorageKey) || '{}');
            return parsed && typeof parsed === 'object' ? parsed : {};
        } catch (_error) {
            return {};
        }
    };

    const storedMarketHistoryCache = readStoredMarketHistory();
    let marketHistorySaveTimer = 0;

    const saveStoredMarketHistory = () => {
        marketHistorySaveTimer = 0;
        localStorage.setItem(marketHistoryStorageKey, JSON.stringify(storedMarketHistoryCache));
    };

    const scheduleStoredMarketHistorySave = () => {
        if (marketHistorySaveTimer) return;
        marketHistorySaveTimer = window.setTimeout(() => {
            saveStoredMarketHistory();
        }, 250);
    };

    const pushMarketHistory = (endpointKey, panelHistory, field, value) => {
        if (!Number.isFinite(value)) return false;
        const values = Array.isArray(panelHistory[field]) ? panelHistory[field] : [];
        if (values.length > 19) {
            values.splice(0, values.length - 19);
        }
        values.push(value);
        panelHistory[field] = values;
        storedMarketHistoryCache[endpointKey] = panelHistory;
        return true;
    };

    const findWindowBaseline = (values, raw) => {
        if (!Array.isArray(values) || !values.length) return raw;
        for (let index = 0; index < values.length; index += 1) {
            const value = values[index];
            if (Number.isFinite(value)) return value;
        }
        return raw;
    };

    const setTextIfChanged = (node, value) => {
        if (node && node.textContent !== value) node.textContent = value;
    };

    const setClassPresenceIfChanged = (node, className, shouldHaveClass) => {
        if (!node) return;
        if (node.classList.contains(className) !== shouldHaveClass) {
            node.classList.toggle(className, shouldHaveClass);
        }
    };

    const setStylePropertyIfChanged = (node, propertyName, value) => {
        if (!node) return;
        if (node.style[propertyName] !== value) {
            node.style[propertyName] = value;
        }
    };

    const setInputValueIfChanged = (node, value) => {
        if (!node) return;
        if (node.value !== value) {
            node.value = value;
        }
    };

    const setPropertyIfChanged = (node, propertyName, value) => {
        if (!node) return;
        if (node[propertyName] !== value) {
            node[propertyName] = value;
        }
    };

    const setDatasetValueIfChanged = (node, key, value) => {
        if (!node) return;
        if (node.dataset[key] !== value) {
            node.dataset[key] = value;
        }
    };

    const resetMarketCardNeutralState = (card, changeEl, arrowEl, deltaEl) => {
        if (changeEl.className !== 'market-change neutral') changeEl.className = 'market-change neutral';
        setTextIfChanged(arrowEl, '•');
        setTextIfChanged(deltaEl, 'Coletando janela');
        setClassPresenceIfChanged(card, 'alert-positive', false);
        setClassPresenceIfChanged(card, 'alert-negative', false);
    };

    const getMarketPanelBinding = (panel) => {
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
    };

    const drawSparkline = (polyline, values) => {
        if (!polyline) return;
        if (!Array.isArray(values) || values.length < 2) {
            if (polyline.dataset.sparklinePoints !== '') {
                polyline.setAttribute('points', '');
                setDatasetValueIfChanged(polyline, 'sparklinePoints', '');
            }
            return;
        }
        const width = 120;
        const height = 36;
        const min = Math.min(...values);
        const max = Math.max(...values);
        const range = max - min || 1;
        const points = values.map((value, index) => {
            const x = (index / Math.max(values.length - 1, 1)) * width;
            const y = height - (((value - min) / range) * (height - 4) + 2);
            return `${x.toFixed(2)},${y.toFixed(2)}`;
        }).join(' ');
        if (polyline.dataset.sparklinePoints !== points) {
            polyline.setAttribute('points', points);
            setDatasetValueIfChanged(polyline, 'sparklinePoints', points);
        }
    };

    const playAlertTone = () => {
        const AudioContextClass = window.AudioContext || window.webkitAudioContext;
        if (!AudioContextClass) return;
        try {
            const audioCtx = new AudioContextClass();
            const oscillator = audioCtx.createOscillator();
            const gain = audioCtx.createGain();
            oscillator.type = 'sine';
            oscillator.frequency.value = 880;
            gain.gain.value = 0.02;
            oscillator.connect(gain);
            gain.connect(audioCtx.destination);
            oscillator.start();
            gain.gain.exponentialRampToValueAtTime(0.0001, audioCtx.currentTime + 0.18);
            oscillator.stop(audioCtx.currentTime + 0.18);
        } catch (_error) {}
    };

    const maybeTriggerMarketAlert = (panel, panelBinding, label, delta, pct, threshold) => {
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
    };

    const bindMarketThresholdControl = (panel) => {
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
    };

    const updateMarketPanel = (panel, snapshot) => {
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
        if (updatedEl) {
            setTextIfChanged(updatedEl, updatedLabel);
        }
        if (shouldPersistHistory) scheduleStoredMarketHistorySave();
        marketState.set(panel, snapshot);
    };

    const pollMarketPanel = async (panel) => {
        if (document.hidden) return;
        const endpoint = panel && panel.dataset.marketEndpoint;
        if (!endpoint) return;
        try {
            const response = await fetch(endpoint, { headers: { 'Accept': 'application/json' }, credentials: 'same-origin' });
            if (!response.ok) throw new Error('Falha ao atualizar o mercado.');
            const data = await response.json();
            if (!data || !data.ok || !data.snapshot) throw new Error('Snapshot de mercado invalido.');
            updateMarketPanel(panel, data.snapshot);
        } catch (_error) {
            const statusEl = panel.querySelector('.market-status');
            setTextIfChanged(statusEl, 'Falha ao atualizar em tempo real. Mantendo ultimo snapshot.');
        }
    };

    const connectMarketStream = (panel) => {
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
                if (marketStreamSources.get(panel) === source) {
                    marketStreamSources.delete(panel);
                }
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
    };

    const closeAllMarketStreams = () => {
        marketPanels.forEach((panel) => {
            const source = marketStreamSources.get(panel);
            if (!source) return;
            source.close();
            marketStreamSources.delete(panel);
        });
    };

    const stopMarketPolling = (panel) => {
        const timerId = marketPollTimers.get(panel);
        if (!timerId) return;
        window.clearInterval(timerId);
        marketPollTimers.delete(panel);
    };

    const ensureMarketPolling = (panel) => {
        if (marketPollTimers.has(panel)) return;
        const timerId = window.setInterval(() => {
            if (document.hidden) return;
            pollMarketPanel(panel);
        }, 5000);
        marketPollTimers.set(panel, timerId);
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

    const resumeVisibleRealtime = () => {
        if (document.hidden) return;
        marketPanels.forEach((panel) => {
            if (connectMarketStream(panel)) {
                stopMarketPolling(panel);
            } else {
                ensureMarketPolling(panel);
                pollMarketPanel(panel);
            }
        });
        if (hasLotAlertFeatures && (lotAlertStreamEndpoint || lotAlertEndpoint)) {
            if (connectLotAlertStream()) {
                stopLotAlertPolling();
            } else {
                ensureLotAlertPolling();
                pollLotAlerts();
            }
        }
    };

    marketPanels.forEach((panel) => {
        const endpointKey = panel.dataset.marketEndpoint || 'default';
        if (storedMarketHistoryCache[endpointKey] && typeof storedMarketHistoryCache[endpointKey] === 'object') {
            marketHistory.set(panel, storedMarketHistoryCache[endpointKey]);
        }
        bindMarketThresholdControl(panel);
        updateMarketPanel(panel, bootstrapData.marketSnapshot || {});
        if (connectMarketStream(panel)) {
            stopMarketPolling(panel);
        } else {
            ensureMarketPolling(panel);
        }
    });

    loadDeferredFragments();

    if (hasLotAlertFeatures) {
        readLotAlertSeen();
        ingestLotAlerts({ alerts: lotAlertBootstrap, lots: lotMonitorBootstrap, summary: String(bootstrapData.lotSummary || '') }, { markSeenOnly: true });
        if (lotAlertStreamEndpoint || lotAlertEndpoint) {
            if (connectLotAlertStream()) {
                stopLotAlertPolling();
            } else if (lotAlertEndpoint) {
                ensureLotAlertPolling();
            }
        }
    }

    document.addEventListener('visibilitychange', () => {
        if (document.hidden) {
            closeAllMarketStreams();
            closeLotAlertStream();
            return;
        }
        resumeVisibleRealtime();
    });

    window.addEventListener('beforeunload', () => {
        if (marketHistorySaveTimer) {
            window.clearTimeout(marketHistorySaveTimer);
            saveStoredMarketHistory();
        }
        closeAllMarketStreams();
        closeLotAlertStream();
    });

    if (lotAlertButton) {
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
            });
        }
    }

    if (supportsHover) {
        widget.addEventListener('mouseenter', () => {
            hydrateChatWidget();
            setMinimized(false);
        });
        widget.addEventListener('mouseleave', () => {
            if (!widget.classList.contains('dragging') && !widget.contains(document.activeElement)) setMinimized(true);
        });
        widget.addEventListener('focusin', () => {
            hydrateChatWidget();
            setMinimized(false);
        });
        widget.addEventListener('focusout', () => {
            window.setTimeout(() => {
                if (!widget.classList.contains('dragging') && !widget.contains(document.activeElement)) setMinimized(true);
            }, 0);
        });
    } else {
        hydrateChatWidget();
        setMinimized(false);
    }

    window.addEventListener('resize', () => ensureWidgetInViewport());

    dragHandle.addEventListener('pointerdown', (event) => {
        if (event.button !== 0) return;
        hydrateChatWidget();
        const rect = widget.getBoundingClientRect();
        const offsetX = event.clientX - rect.left;
        const offsetY = event.clientY - rect.top;
        widget.classList.add('dragging');
        setMinimized(false);
        dragHandle.setPointerCapture(event.pointerId);

        const move = (moveEvent) => {
            const maxLeft = Math.max(dragSnapMargin, window.innerWidth - rect.width - dragSnapMargin);
            const maxTop = Math.max(dragSnapMargin, window.innerHeight - rect.height - dragSnapMargin);
            const left = Math.min(Math.max(dragSnapMargin, moveEvent.clientX - offsetX), maxLeft);
            const top = Math.min(Math.max(dragSnapMargin, moveEvent.clientY - offsetY), maxTop);
            applyWidgetPosition(left, top);
        };

        const stop = () => {
            widget.classList.remove('dragging');
            const snapped = snapWidgetPosition(widget.getBoundingClientRect().left, widget.getBoundingClientRect().top);
            applyWidgetPosition(snapped.left, snapped.top);
            persistWidgetPosition();
            dragHandle.removeEventListener('pointermove', move);
            dragHandle.removeEventListener('pointerup', stop);
            dragHandle.removeEventListener('pointercancel', stop);
            if (supportsHover && !widget.matches(':hover') && !widget.contains(document.activeElement)) setMinimized(true);
        };

        dragHandle.addEventListener('pointermove', move);
        dragHandle.addEventListener('pointerup', stop);
        dragHandle.addEventListener('pointercancel', stop);
    });

    input.addEventListener('keydown', (event) => {
        if (event.key === 'Enter' && !event.shiftKey) {
            event.preventDefault();
            form.requestSubmit();
        }
    });

    form.addEventListener('submit', async (event) => {
        event.preventDefault();
        hydrateChatWidget();
        const message = input.value.trim();
        const sender = remetente.value.trim();
        if (!message || !sender) return;

        const sentAt = nowStamp();
        const optimisticHistory = history.concat([
            { role: 'user', content: message, ts: sentAt },
            { role: 'typing', content: '', ts: nowStamp() },
        ]);
        renderHistory(optimisticHistory);
        setInputValueIfChanged(input, '');
        setPropertyIfChanged(send, 'disabled', true);
        setPropertyIfChanged(input, 'disabled', true);

        try {
            const payload = new URLSearchParams();
            payload.set('page', widget.dataset.page || currentPage || 'dashboard');
            payload.set('console_remetente', sender);
            payload.set('console_mensagem', message);
            const response = await fetch('/saas/console', {
                method: 'POST',
                headers: { 'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8', 'X-Requested-With': 'XMLHttpRequest', 'Accept': 'application/json' },
                body: payload.toString(),
                credentials: 'same-origin',
            });
            const data = await response.json();
            if (!response.ok || !data.ok) {
                throw new Error(data.notice || 'Nao foi possivel enviar a mensagem agora.');
            }
            history = history.concat([
                { role: 'user', content: message, ts: sentAt },
                { role: 'assistant', content: data.message || 'Sem resposta.', ts: nowStamp() },
            ]);
            saveHistory(history);
            renderHistory(history);
            setMinimized(false);
        } catch (error) {
            history = history.concat([
                { role: 'user', content: message, ts: sentAt },
                { role: 'assistant', content: (error && error.message) || 'Falha ao falar com a IA.', ts: nowStamp() },
            ]);
            saveHistory(history);
            renderHistory(history);
        } finally {
            setPropertyIfChanged(send, 'disabled', false);
            setPropertyIfChanged(input, 'disabled', false);
            input.focus();
        }
    });

    const opForm = document.getElementById('quickOperationForm');
    if (opForm) {
        const pesoInput = document.getElementById('opPeso');
        const teorInput = document.getElementById('opTeor');
        const precoInput = document.getElementById('opPrecoUsd');
        const fechamentoInput = document.getElementById('opFechamentoGramas');
        const fechamentoTipo = document.getElementById('opFechamentoTipo');
        const goldTypeInput = document.getElementById('opGoldType');
        const quebraInput = document.getElementById('opQuebra');
        const quebraWrap = document.getElementById('opQuebraWrap');
        const totalPagoInput = document.getElementById('opTotalPagoUsd');
        const tipoOperacao = document.getElementById('opTipoOperacao');
        const fineGold = document.getElementById('opFineGold');
        const totalUsd = document.getElementById('opTotalUsd');
        const targetUsd = document.getElementById('opTargetUsd');
        const paidUsd = document.getElementById('opPaidUsd');
        const diffUsd = document.getElementById('opDiffUsd');
        const summaryText = document.getElementById('opSummaryText');
        const rateioHint = document.getElementById('opRateioHint');
        const fechamentoHint = document.getElementById('opFechamentoHint');
        const fechamentoHintSide = document.getElementById('opFechamentoHintSide');
        const closurePeso = document.getElementById('opClosurePeso');
        const closurePesoSide = document.getElementById('opClosurePesoSide');
        const closureClosed = document.getElementById('opClosureClosed');
        const closureClosedSide = document.getElementById('opClosureClosedSide');
        const closureOpen = document.getElementById('opClosureOpen');
        const closureOpenSide = document.getElementById('opClosureOpenSide');
        const usePesoTotal = document.getElementById('opUsePesoTotal');
        const useTotalAsUsd = document.getElementById('opUseTotalAsUsd');
        const toggleQuickOrderMode = document.getElementById('toggleQuickOrderMode');
        const quickModeHint = document.getElementById('quickModeHint');
        const aiDraftForm = document.getElementById('aiDraftForm');
        const aiDraftInput = document.getElementById('aiDraftInput');
        const aiDraftStatus = document.getElementById('aiDraftStatus');
        const pessoaInput = document.getElementById('opPessoa');
        const clienteIdInput = document.getElementById('opClienteId');
        const clienteLookupMeta = document.getElementById('opClienteLookupMeta');
        const clienteMeta = document.getElementById('opClienteMeta');
        const clienteResults = document.getElementById('opClienteResults');
        const toggleInlineCliente = document.getElementById('toggleInlineCliente');
        const inlineClienteBox = document.getElementById('inlineClienteBox');
        const inlineClienteMode = document.getElementById('inlineClienteMode');
        const inlineClienteNome = document.getElementById('inlineClienteNome');
        const inlineClienteSave = document.getElementById('inlineClienteSave');
        const inlineClienteStatus = document.getElementById('inlineClienteStatus');
        const operationFormNotice = document.getElementById('operationFormNotice');
        const operationFormNoticeText = document.getElementById('operationFormNoticeText');
        const operationFormReceiptLink = document.getElementById('operationFormReceiptLink');
        const paymentRows = Array.from(document.querySelectorAll('.js-payment-row'));
        const paymentRowBindings = new WeakMap();
        const quickModeKey = 'caixaQuickOrderMode';
        let clienteSearchTimer = 0;
        let clienteSearchController = null;
        let clienteSearchSequence = 0;
        let clienteResultsTerm = '';
        let clienteResultsSignature = '';
        const clienteSearchCache = new Map();
        let isSubmittingOperation = false;
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
            if (moeda === 'EUR') return amount * rate;
            return amount / rate;
        };

        const paymentAmountFromUsdTarget = (currency, usdAmount, rate) => {
            const moeda = String(currency || '').toUpperCase();
            if (!moeda || usdAmount <= 0) return 0;
            if (moeda === 'USD') return usdAmount;
            if (rate <= 0) return 0;
            if (moeda === 'EUR') return usdAmount / rate;
            return usdAmount * rate;
        };

        const formatInputNumber = (value, places = 2) => {
            const fixed = Number(value || 0).toFixed(places);
            return fixed.replace(/\.00$/, '').replace(/(\.\d*[1-9])0+$/, '$1');
        };

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

        const money = (value) => `USD ${value.toFixed(2)}`;
        const grams = (value) => `${value.toFixed(3)} g`;

        const showOperationNotice = (message, kind = 'info', receiptUrl = '') => {
            if (!operationFormNotice || !operationFormNoticeText) return;
            setTextIfChanged(operationFormNoticeText, String(message || ''));
            operationFormNotice.classList.remove('is-hidden', 'info', 'error');
            operationFormNotice.classList.add(kind === 'error' ? 'error' : 'info');
            if (operationFormReceiptLink) {
                const resolvedUrl = String(receiptUrl || '').trim();
                if (resolvedUrl) {
                    operationFormReceiptLink.href = resolvedUrl;
                    operationFormReceiptLink.classList.remove('is-hidden');
                } else {
                    operationFormReceiptLink.href = '#';
                    operationFormReceiptLink.classList.add('is-hidden');
                }
            }
        };

        const clearOperationNotice = () => {
            if (!operationFormNotice || !operationFormNoticeText) return;
            setTextIfChanged(operationFormNoticeText, '');
            operationFormNotice.classList.add('is-hidden');
            operationFormNotice.classList.remove('info', 'error');
            if (operationFormReceiptLink) {
                operationFormReceiptLink.href = '#';
                operationFormReceiptLink.classList.add('is-hidden');
            }
        };

        const scheduleUpdateCalculator = () => {
            if (calculatorFrame) return;
            calculatorFrame = window.requestAnimationFrame(() => {
                calculatorFrame = 0;
                updateCalculator();
            });
        };

        const resetOperationFormAfterSuccess = () => {
            opForm.reset();
            clearSelectedCliente();
            closeClienteResults();
            setInlineClienteMode(false);
            setTextIfChanged(inlineClienteStatus, 'Salve o cliente aqui para selecionar a conta antes de registrar a operacao.');
            setDatasetValueIfChanged(totalPagoInput, 'autofilled', '1');
            paymentRows.forEach((row) => {
                const { valor, cambio } = getPaymentRowBinding(row);
                setDatasetValueIfChanged(valor, 'percentAutofill', '0');
                setDatasetValueIfChanged(cambio, 'autofilled', String(cambio && cambio.value || '').trim() ? '1' : '0');
                applyAutoFx(row, true);
            });
            updateGoldTypeUi();
            updateCalculator();
            if (pessoaInput) pessoaInput.focus();
        };

        const setQuickMode = (enabled) => {
            opForm.classList.toggle('quick-order-mode', enabled);
            setTextIfChanged(toggleQuickOrderMode, enabled ? 'Desativar modo agil' : 'Modo de lancamento agil');
            setTextIfChanged(quickModeHint, enabled ? 'Enter avanca, Ctrl+Enter confirma o registro e os campos opcionais permanecem ocultos.' : 'Enter avanca campo a campo. Ctrl+Enter confirma o registro.');
            localStorage.setItem(quickModeKey, enabled ? '1' : '0');
        };

        const setInlineClienteMode = (enabled) => {
            if (!inlineClienteBox || !inlineClienteMode) return;
            inlineClienteBox.classList.toggle('is-hidden', !enabled);
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
            clienteResults.classList.add('is-hidden');
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
        };

        const clearSelectedCliente = () => {
            setInputValueIfChanged(clienteIdInput, '');
            setInputValueIfChanged(clienteLookupMeta, '');
            setTextIfChanged(clienteMeta, 'Selecione um cliente existente ou use o cadastro rapido abaixo.');
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
                clienteResults.classList.remove('is-hidden');
                return;
            }
            if (!Array.isArray(items) || !items.length) {
                clienteResults.innerHTML = '<div class="hint">Nenhum cliente encontrado. Use o cadastro rapido para registrar um novo.</div>';
                clienteResults.classList.remove('is-hidden');
                clienteResultsSignature = resultsSignature;
                return;
            }
            clienteResults.innerHTML = items.map((cliente) => `
                <button type="button" class="client-option" data-client-id="${String(cliente.id || '')}" data-client-name="${escapeHtml(cliente.nome || '')}" data-client-meta="${escapeHtml(cliente.meta || '')}">
                    ${escapeHtml(cliente.nome || '')}
                    <small>${escapeHtml(cliente.meta || '')}</small>
                </button>
            `).join('');
            clienteResults.classList.remove('is-hidden');
            clienteResultsSignature = resultsSignature;
        };

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

        const fetchClientes = async (term, signal) => {
            const normalizedTerm = String(term || '').trim();
            if (clienteSearchCache.has(normalizedTerm)) {
                return clienteSearchCache.get(normalizedTerm);
            }
            const response = await fetch(`/saas/clientes/search?q=${encodeURIComponent(term)}`, {
                headers: { 'Accept': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
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
                if (clienteSearchController) {
                    clienteSearchController.abort();
                }
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
                headers: { 'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8', 'X-Requested-With': 'XMLHttpRequest', 'Accept': 'application/json' },
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
            setSelectedCliente({
                id: item.id,
                nome: item.nome,
                meta: item.meta,
            });
            setTextIfChanged(inlineClienteStatus, 'Cliente cadastrado e selecionado com sucesso.');
            ['inline_cliente_nome', 'inline_cliente_telefone', 'inline_cliente_documento', 'inline_cliente_apelido', 'inline_cliente_observacoes', 'inline_cliente_saldo_xau'].forEach((fieldName) => {
                const field = opForm.elements.namedItem(fieldName);
                setInputValueIfChanged(field, '');
            });
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
            return {
                row,
                ...binding,
                moedaValue: binding.moeda ? String(binding.moeda.value || '').toUpperCase() : '',
                valorNumber: parseNumber(binding.valor && binding.valor.value),
                percentNumber: parseNumber(binding.percent && binding.percent.value),
                cambioNumber: parseNumber(binding.cambio && binding.cambio.value),
            };
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

        const applyDraft = (draft) => {
            clearDraftFields();
            Object.entries(draft || {}).forEach(([name, value]) => {
                const field = opForm.elements.namedItem(name);
                if (!field) return;
                setInputValueIfChanged(field, value == null ? '' : String(value));
            });
            if (clienteMeta) {
                setTextIfChanged(clienteMeta, String(opForm.elements.namedItem('cliente_lookup_meta')?.value || '').trim() || 'Selecione um cliente existente ou use o cadastro rapido abaixo.');
            }
            updateGoldTypeUi();
            paymentRows.forEach((row) => applyAutoFx(row));
            scheduleUpdateCalculator();
        };

        const focusableFields = () => Array.from(opForm.querySelectorAll('input, select, textarea, button[type="submit"]')).filter((field) => !field.disabled && field.offsetParent !== null);

        const updateGoldTypeUi = () => {
            const isCompra = !tipoOperacao || tipoOperacao.value === 'compra';
            const isQueimado = !!goldTypeInput && String(goldTypeInput.value || '').toLowerCase() === 'queimado';
            const mustShowQuebra = isCompra && isQueimado;
            if (quebraWrap) quebraWrap.classList.toggle('is-hidden', !mustShowQuebra);
            if (quebraInput) {
                setPropertyIfChanged(quebraInput, 'disabled', !mustShowQuebra);
                setPropertyIfChanged(quebraInput, 'required', mustShowQuebra);
                if (!mustShowQuebra) setInputValueIfChanged(quebraInput, '');
            }
        };

        const updateCalculator = () => {
            updateGoldTypeUi();
            const peso = parseNumber(pesoInput && pesoInput.value);
            const teor = parseNumber(teorInput && teorInput.value);
            const preco = parseNumber(precoInput && precoInput.value);
            const fechamentoAtual = parseNumber(fechamentoInput && fechamentoInput.value);
            const isTotal = fechamentoTipo && fechamentoTipo.value === 'total';

            if (isTotal && fechamentoInput && peso > 0) {
                setInputValueIfChanged(fechamentoInput, peso.toFixed(3).replace(/\.000$/, ''));
            }

            const fechamento = isTotal ? peso : fechamentoAtual;
            const fechamentoAplicado = Math.max(0, Math.min(fechamento, peso || 0));
            const abertoDepois = Math.max(0, (peso || 0) - fechamentoAplicado);
            const ouroFino = peso * (teor / 100);
            const totalRef = peso * preco;
            const targetPaymentUsd = peso > 0 ? (totalRef * (fechamentoAplicado / peso)) : totalRef;
            const paymentRowStates = paymentRows.map(buildPaymentRowState);

            let totalPercent = 0;
            paymentRowStates.forEach((state) => {
                totalPercent += state.percentNumber;
            });

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
                    const usdShare = targetPaymentUsd * (percentNumber / 100);
                    const moedaAmount = paymentAmountFromUsdTarget(moedaValue, usdShare, cambioNumber);
                    if (moedaAmount > 0) {
                        const formattedAmount = formatInputNumber(moedaAmount, 2);
                        setInputValueIfChanged(valor, formattedAmount);
                        setDatasetValueIfChanged(valor, 'percentAutofill', '1');
                        state.valorNumber = moedaAmount;
                    }
                });
            }

            let pagamentosUsd = 0;
            paymentRowStates.forEach((state) => {
                const { row, preview } = state;
                applyAutoFx(row);
                state.cambioNumber = parseNumber(state.cambio && state.cambio.value);
                state.valorNumber = parseNumber(state.valor && state.valor.value);
                const { moedaValue, valorNumber, percentNumber, cambioNumber } = state;
                const rowUsd = paymentUsdFromInput(moedaValue, valorNumber, cambioNumber);
                pagamentosUsd += rowUsd;
                if (preview) {
                    if (rowUsd > 0) {
                        setTextIfChanged(preview, percentNumber > 0 ? `${money(rowUsd)} · ${percentNumber.toFixed(2).replace(/\.00$/, '')}%` : money(rowUsd));
                    } else {
                        setTextIfChanged(preview, moedaValue && (valorNumber > 0 || percentNumber > 0) ? 'Informe cambio' : 'USD 0.00');
                    }
                }
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
            const direcao = tipoOperacao && tipoOperacao.value === 'venda' ? 'Entrada prevista' : 'Saida prevista';
            const fineGoldText = grams(ouroFino);
            const totalUsdText = money(totalRef);
            const targetUsdText = money(targetPaymentUsd);
            const paidUsdText = money(totalPago);
            const diffUsdText = money(Math.abs(diferenca));
            const diffColor = Math.abs(diferenca) < 0.005 ? 'var(--green)' : 'var(--danger)';
            const closurePesoText = grams(peso);
            const closureClosedText = grams(fechamentoAplicado);
            const closureOpenText = grams(abertoDepois);

            setTextIfChanged(fineGold, fineGoldText);
            setTextIfChanged(totalUsd, totalUsdText);
            setTextIfChanged(targetUsd, targetUsdText);
            setTextIfChanged(paidUsd, paidUsdText);
            setTextIfChanged(diffUsd, diffUsdText);
            setStylePropertyIfChanged(diffUsd, 'color', diffColor);
            setTextIfChanged(closurePeso, closurePesoText);
            setTextIfChanged(closurePesoSide, closurePesoText);
            setTextIfChanged(closureClosed, closureClosedText);
            setTextIfChanged(closureClosedSide, closureClosedText);
            setTextIfChanged(closureOpen, closureOpenText);
            setTextIfChanged(closureOpenSide, closureOpenText);
            const fechamentoMensagem = peso <= 0
                ? 'Informe o peso para o painel mostrar quanto fica fechado agora e quanto sobra pendente.'
                : (isTotal
                    ? `Fechamento total: os ${grams(peso)} da operacao ficam fechados agora, sem saldo pendente para depois.`
                    : `Fechamento parcial: ${grams(fechamentoAplicado)} ficam fechados agora e ${grams(abertoDepois)} continuam em aberto para fechamento futuro. Esse saldo passa a aparecer nos quadros de fechamentos pendentes.`);
            setTextIfChanged(fechamentoHint, fechamentoMensagem);
            setTextIfChanged(fechamentoHintSide, fechamentoMensagem);
            if (rateioHint) {
                let rateioHintText = '';
                if (totalPercent <= 0) {
                    rateioHintText = `O alvo atual do pagamento e ${targetUsdText}. Se voce preencher o % por moeda, o sistema calcula automaticamente quanto pagar em cada uma.`;
                } else {
                    const totalPercentLabel = totalPercent.toFixed(2).replace(/\.00$/, '');
                    const remainingPercent = Math.max(0, 100 - totalPercent);
                    const rateioStatus = Math.abs(totalPercent - 100) < 0.005 ? 'Rateio completo em 100%.' : `Rateio preenchido em ${totalPercentLabel}%.`;
                    const remainingLabel = remainingPercent > 0.005 ? ` Ainda faltam ${remainingPercent.toFixed(2).replace(/\.00$/, '')}% para completar o pagamento.` : '';
                    rateioHintText = `${rateioStatus} O painel calcula cada moeda sobre ${targetUsdText} com base no percentual digitado.${remainingLabel}`;
                }
                setTextIfChanged(rateioHint, rateioHintText);
            }

            if (summaryText) {
                const fechamentoLabel = fechamento > 0 ? grams(fechamento) : 'aguardando fechamento';
                let summaryLabel = '';
                if (peso <= 0 || preco <= 0) {
                    summaryLabel = 'Preencha peso e preco para o sistema calcular total, fechamento sugerido e diferenca automaticamente.';
                } else {
                    const diffLabel = diferenca > 0.005 ? `faltam ${diffUsdText}` : (diferenca < -0.005 ? `sobram ${diffUsdText}` : 'pagamento fechado sem diferenca');
                    summaryLabel = `${direcao} ${totalUsdText} para ${closurePesoText} (${fineGoldText} de ouro fino). Alvo do fechamento: ${targetUsdText} para ${fechamentoLabel}. Pagamentos conferidos: ${paidUsdText} e ${diffLabel}.`;
                }
                setTextIfChanged(summaryText, summaryLabel);
            }
        };

        if (usePesoTotal) {
            usePesoTotal.addEventListener('click', () => {
                setInputValueIfChanged(fechamentoTipo, 'total');
                scheduleUpdateCalculator();
            });
        }

        if (useTotalAsUsd) {
            useTotalAsUsd.addEventListener('click', () => {
                const peso = parseNumber(pesoInput && pesoInput.value);
                const preco = parseNumber(precoInput && precoInput.value);
                const fechamentoAtual = parseNumber(fechamentoInput && fechamentoInput.value);
                const fechamentoTipoAtual = fechamentoTipo && fechamentoTipo.value === 'total';
                const fechamentoAplicado = fechamentoTipoAtual ? peso : Math.max(0, Math.min(fechamentoAtual, peso || 0));
                const totalRef = peso * preco;
                const targetPaymentUsd = peso > 0 ? (totalRef * (fechamentoAplicado / peso)) : totalRef;
                if (totalPagoInput && targetPaymentUsd > 0) {
                    setInputValueIfChanged(totalPagoInput, targetPaymentUsd.toFixed(2));
                    setDatasetValueIfChanged(totalPagoInput, 'autofilled', '1');
                }
                scheduleUpdateCalculator();
            });
        }

        if (toggleQuickOrderMode) {
            toggleQuickOrderMode.addEventListener('click', () => setQuickMode(!opForm.classList.contains('quick-order-mode')));
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

        opForm.addEventListener('keydown', (event) => {
            if (event.key === 'Enter' && event.ctrlKey) {
                event.preventDefault();
                opForm.requestSubmit();
                return;
            }
            if (!opForm.classList.contains('quick-order-mode')) return;
            if (event.key !== 'Enter' || event.shiftKey) return;
            const target = event.target;
            if (target instanceof HTMLTextAreaElement) return;
            event.preventDefault();
            const fields = focusableFields();
            const currentIndex = fields.indexOf(target);
            if (currentIndex >= 0 && currentIndex < fields.length - 1) {
                fields[currentIndex + 1].focus();
                if (typeof fields[currentIndex + 1].select === 'function') fields[currentIndex + 1].select();
            } else {
                opForm.requestSubmit();
            }
        });

        opForm.addEventListener('submit', async (event) => {
            event.preventDefault();
            if (isSubmittingOperation) return;
            if (typeof opForm.reportValidity === 'function' && !opForm.reportValidity()) return;

            let receiptWindow = null;
            try {
                receiptWindow = window.open('', '_blank');
                if (receiptWindow && receiptWindow.document) {
                    receiptWindow.document.title = 'Gerando recibo';
                    receiptWindow.document.body.innerHTML = '<div style="font-family:Segoe UI,sans-serif;padding:24px;color:#184f3f">Gerando recibo da operacao...</div>';
                }
            } catch (_error) {
                receiptWindow = null;
            }

            isSubmittingOperation = true;
            clearOperationNotice();
            const submitButton = opForm.querySelector('button[type="submit"]');
            setPropertyIfChanged(submitButton, 'disabled', true);

            try {
                const formData = new FormData(opForm);
                const payload = new URLSearchParams();
                formData.forEach((value, key) => payload.append(key, String(value)));

                const response = await fetch('/saas/operations/quick', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8', 'X-Requested-With': 'XMLHttpRequest', 'Accept': 'application/json' },
                    body: payload.toString(),
                    credentials: 'same-origin',
                });

                const data = await response.json();
                if (!response.ok || !data.ok) {
                    throw new Error(data.notice || 'Nao foi possivel concluir a operacao.');
                }

                const receiptUrl = String(data.receipt_url || '').trim();
                if (receiptUrl) {
                    if (receiptWindow && !receiptWindow.closed) {
                        receiptWindow.location.replace(receiptUrl);
                    } else {
                        window.open(receiptUrl, '_blank', 'noopener');
                    }
                }
                resetOperationFormAfterSuccess();
                showOperationNotice(data.notice || 'Operacao registrada com sucesso. O recibo foi aberto em outra pagina.', 'info', receiptUrl);
            } catch (error) {
                if (receiptWindow && !receiptWindow.closed) {
                    receiptWindow.close();
                }
                showOperationNotice((error && error.message) || 'Falha ao concluir a operacao.', 'error');
            } finally {
                isSubmittingOperation = false;
                setPropertyIfChanged(submitButton, 'disabled', false);
            }
        });

        paymentRows.forEach((row) => {
            const { moeda, valor, percent, cambio } = getPaymentRowBinding(row);
            if (moeda) moeda.addEventListener('change', () => {
                setDatasetValueIfChanged(cambio, 'autofilled', '1');
                applyAutoFx(row, true);
                scheduleUpdateCalculator();
            });
            if (valor) valor.addEventListener('input', () => {
                setDatasetValueIfChanged(valor, 'percentAutofill', '0');
            });
            if (percent) percent.addEventListener('input', scheduleUpdateCalculator);
            if (cambio) cambio.addEventListener('input', () => {
                setDatasetValueIfChanged(cambio, 'autofilled', '0');
            });
            refreshPaymentRowUi(row);
        });

        if (goldTypeInput) goldTypeInput.addEventListener('change', scheduleUpdateCalculator);
        if (tipoOperacao) tipoOperacao.addEventListener('change', scheduleUpdateCalculator);

        if (totalPagoInput) {
            totalPagoInput.addEventListener('input', () => {
                setDatasetValueIfChanged(totalPagoInput, 'autofilled', '0');
            });
        }

        if (aiDraftForm && aiDraftInput && aiDraftStatus) {
            aiDraftForm.addEventListener('submit', async (event) => {
                event.preventDefault();
                const draftMessage = aiDraftInput.value.trim();
                if (!draftMessage) return;
                setTextIfChanged(aiDraftStatus, 'Montando rascunho da ordem...');
                try {
                    const payload = new URLSearchParams();
                    payload.set('draft_message', draftMessage);
                    const response = await fetch('/saas/operations/draft', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8', 'X-Requested-With': 'XMLHttpRequest', 'Accept': 'application/json' },
                        body: payload.toString(),
                        credentials: 'same-origin',
                    });
                    const data = await response.json();
                    if (!response.ok || !data.ok) {
                        throw new Error(data.notice || 'Nao foi possivel gerar o rascunho.');
                    }
                    applyDraft(data.draft || {});
                    setQuickMode(true);
                    let draftStatusText = data.summary || 'Rascunho aplicado no formulario.';
                    if (Array.isArray(data.missing_fields) && data.missing_fields.length) {
                        const missingFieldLabels = {
                            peso: 'peso',
                            preco_usd: 'preco',
                            cliente: 'cliente',
                            pessoa: 'cliente',
                        };
                        const missingFieldsText = data.missing_fields.map((field) => missingFieldLabels[field] || field).join(', ');
                        draftStatusText += ` Campos para revisar: ${missingFieldsText}.`;
                    }
                    setTextIfChanged(aiDraftStatus, draftStatusText);
                } catch (error) {
                    setTextIfChanged(aiDraftStatus, (error && error.message) || 'Falha ao montar o rascunho.');
                }
            });
        }

        opForm.querySelectorAll('input, select, textarea').forEach((field) => {
            field.addEventListener('input', scheduleUpdateCalculator);
            field.addEventListener('change', scheduleUpdateCalculator);
        });
        setInlineClienteMode(inlineClienteMode && inlineClienteMode.value === '1');
        if (clienteLookupMeta && clienteMeta && clienteLookupMeta.value) {
            setTextIfChanged(clienteMeta, clienteLookupMeta.value);
        }
        setQuickMode(localStorage.getItem(quickModeKey) === '1');
        paymentRows.forEach((row) => applyAutoFx(row));
        updateCalculator();
    }
})();
