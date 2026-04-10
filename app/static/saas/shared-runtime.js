(function (global) {
    const createSharedRuntime = (config) => {
        const { supportsHover } = config;
        const localeNumberFormatters = new Map();

        const escapeHtml = (value) => String(value || '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
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
            return `${value > 0 ? '+' : ''}${formatLocaleNumber(value, decimals)}`;
        };
        const parseSnapshotNumber = (value) => {
            if (typeof value === 'number') return value;
            return Number.parseFloat(String(value || '').replace(',', '.'));
        };
        const setTextIfChanged = (node, value) => {
            if (node && node.textContent !== value) node.textContent = value;
        };
        const setClassPresenceIfChanged = (node, className, shouldHaveClass) => {
            if (!node) return;
            if (node.classList.contains(className) !== shouldHaveClass) node.classList.toggle(className, shouldHaveClass);
        };
        const setStylePropertyIfChanged = (node, propertyName, value) => {
            if (node && node.style[propertyName] !== value) node.style[propertyName] = value;
        };
        const setInputValueIfChanged = (node, value) => {
            if (node && node.value !== value) node.value = value;
        };
        const setPropertyIfChanged = (node, propertyName, value) => {
            if (node && node[propertyName] !== value) node[propertyName] = value;
        };
        const setDatasetValueIfChanged = (node, key, value) => {
            if (node && node.dataset[key] !== value) node.dataset[key] = value;
        };
        const maybeNotifyBrowser = (title, body) => {
            if (!('Notification' in window) || Notification.permission !== 'granted') return;
            try {
                new Notification(title, { body });
            } catch (_error) {}
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
        const bindMarketRail = (marketRail) => {
            if (!marketRail) return;
            const setMarketRailExpanded = (expanded) => {
                marketRail.classList.toggle('is-expanded', expanded);
                marketRail.classList.toggle('is-minimized', !expanded);
            };
            setMarketRailExpanded(!supportsHover);
            if (!supportsHover) return;
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
        };

        return {
            bindMarketRail,
            escapeHtml,
            formatDeltaValue,
            formatLocaleNumber,
            formatMarketValue,
            maybeNotifyBrowser,
            parseSnapshotNumber,
            playAlertTone,
            setClassPresenceIfChanged,
            setDatasetValueIfChanged,
            setInputValueIfChanged,
            setPropertyIfChanged,
            setStylePropertyIfChanged,
            setTextIfChanged,
        };
    };

    global.CaixaSaasSharedRuntime = { createSharedRuntime };
})(window);