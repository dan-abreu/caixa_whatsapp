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
    const sharedRuntime = window.CaixaSaasSharedRuntime && typeof window.CaixaSaasSharedRuntime.createSharedRuntime === 'function'
        ? window.CaixaSaasSharedRuntime.createSharedRuntime({ supportsHover })
        : null;
    const escapeHtml = sharedRuntime ? sharedRuntime.escapeHtml : (value) => String(value || '');
    const formatDeltaValue = sharedRuntime ? sharedRuntime.formatDeltaValue : (value) => String(value || '');
    const formatLocaleNumber = sharedRuntime ? sharedRuntime.formatLocaleNumber : (value) => String(value || '');
    const formatMarketValue = sharedRuntime ? sharedRuntime.formatMarketValue : (value) => String(value || '');
    const maybeNotifyBrowser = sharedRuntime ? sharedRuntime.maybeNotifyBrowser : () => {};
    const parseSnapshotNumber = sharedRuntime ? sharedRuntime.parseSnapshotNumber : (value) => Number(value || 0);
    const playAlertTone = sharedRuntime ? sharedRuntime.playAlertTone : () => {};
    const setClassPresenceIfChanged = sharedRuntime ? sharedRuntime.setClassPresenceIfChanged : () => {};
    const setDatasetValueIfChanged = sharedRuntime ? sharedRuntime.setDatasetValueIfChanged : () => {};
    const setInputValueIfChanged = sharedRuntime ? sharedRuntime.setInputValueIfChanged : () => {};
    const setPropertyIfChanged = sharedRuntime ? sharedRuntime.setPropertyIfChanged : () => {};
    const setStylePropertyIfChanged = sharedRuntime ? sharedRuntime.setStylePropertyIfChanged : () => {};
    const setTextIfChanged = sharedRuntime ? sharedRuntime.setTextIfChanged : () => {};

    const widgetRuntime = window.CaixaSaasWidgetRuntime && typeof window.CaixaSaasWidgetRuntime.createWidgetRuntime === 'function'
        ? window.CaixaSaasWidgetRuntime.createWidgetRuntime({
            dragHandle,
            dragPositionKey,
            dragSnapMargin,
            dragSnapThreshold,
            setStylePropertyIfChanged,
            supportsHover,
            widget,
        })
        : { ensureWidgetInViewport() {}, initialize() {}, restoreWidgetPosition() {}, setMinimized() {} };

    const marketRail = document.getElementById('marketRail');
    if (sharedRuntime) sharedRuntime.bindMarketRail(marketRail);

    const chatRuntime = window.CaixaSaasChatRuntime && typeof window.CaixaSaasChatRuntime.createChatRuntime === 'function'
        ? window.CaixaSaasChatRuntime.createChatRuntime({
            bootstrapHistory: bootstrap,
            currentPage,
            escapeHtml,
            form,
            historyKey,
            input,
            remetente,
            restoreWidgetPosition: widgetRuntime.restoreWidgetPosition,
            send,
            setInputValueIfChanged,
            setMinimized: widgetRuntime.setMinimized,
            setPropertyIfChanged,
            shouldHydrateChatEagerly,
            supportsHover,
            thread,
            widget,
        })
        : { appendAssistantAlert() {}, hydrate() {}, initialize() {} };

    const lotAlertRuntime = window.CaixaSaasLotAlerts && typeof window.CaixaSaasLotAlerts.createLotAlertRuntime === 'function'
        ? window.CaixaSaasLotAlerts.createLotAlertRuntime({
            bootstrapData,
            lotAlertBootstrap,
            lotMonitorBootstrap,
            appendAssistantAlert: chatRuntime.appendAssistantAlert,
            maybeNotifyBrowser,
            playAlertTone,
            setDatasetValueIfChanged,
            setPropertyIfChanged,
            setTextIfChanged,
        })
        : { initialize() {}, resumeVisibleRealtime() {}, handleHidden() {}, teardown() {} };

    const fragmentsRuntime = window.CaixaSaasFragments && typeof window.CaixaSaasFragments.createFragmentsRuntime === 'function'
        ? window.CaixaSaasFragments.createFragmentsRuntime({ escapeHtml })
        : { initialize() {} };

    const marketRuntime = window.CaixaSaasMarketRuntime && typeof window.CaixaSaasMarketRuntime.createMarketRuntime === 'function'
        ? window.CaixaSaasMarketRuntime.createMarketRuntime({
            bootstrapSnapshot: bootstrapData.marketSnapshot || {},
            formatDeltaValue,
            formatLocaleNumber,
            formatMarketValue,
            parseSnapshotNumber,
            playAlertTone,
            setClassPresenceIfChanged,
            setDatasetValueIfChanged,
            setInputValueIfChanged,
            setTextIfChanged,
        })
        : { initialize() {}, resumeVisibleRealtime() {}, handleHidden() {}, teardown() {} };

    const resumeVisibleRealtime = () => {
        if (document.hidden) return;
        marketRuntime.resumeVisibleRealtime();
        lotAlertRuntime.resumeVisibleRealtime();
    };

    marketRuntime.initialize();
    fragmentsRuntime.initialize();
    lotAlertRuntime.initialize();

    document.addEventListener('visibilitychange', () => {
        if (document.hidden) {
            marketRuntime.handleHidden();
            lotAlertRuntime.handleHidden();
            return;
        }
        resumeVisibleRealtime();
    });

    window.addEventListener('beforeunload', () => {
        marketRuntime.teardown();
        lotAlertRuntime.teardown();
    });

    chatRuntime.initialize();

    widgetRuntime.initialize(() => chatRuntime.hydrate());
    const money = (value) => `USD ${value.toFixed(2)}`;
    const grams = (value) => `${value.toFixed(3)} g`;
    const operationFormRuntime = window.CaixaSaasOperationFormRuntime && typeof window.CaixaSaasOperationFormRuntime.createOperationFormRuntime === 'function'
        ? window.CaixaSaasOperationFormRuntime.createOperationFormRuntime({
            escapeHtml,
            grams,
            money,
            recentFx,
            setDatasetValueIfChanged,
            setInputValueIfChanged,
            setPropertyIfChanged,
            setStylePropertyIfChanged,
            setTextIfChanged,
        })
        : { initialize() {} };
    operationFormRuntime.initialize();
})();