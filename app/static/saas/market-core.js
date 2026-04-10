(function (global) {
    const createMarketCore = (config) => {
        const { setClassPresenceIfChanged, setDatasetValueIfChanged, setTextIfChanged } = config;

        const resetMarketCardNeutralState = (card, changeEl, arrowEl, deltaEl) => {
            if (changeEl.className !== 'market-change neutral') changeEl.className = 'market-change neutral';
            setTextIfChanged(arrowEl, '•');
            setTextIfChanged(deltaEl, 'Coletando janela');
            setClassPresenceIfChanged(card, 'alert-positive', false);
            setClassPresenceIfChanged(card, 'alert-negative', false);
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

        const findWindowBaseline = (values, raw) => {
            if (!Array.isArray(values) || !values.length) return raw;
            for (let index = 0; index < values.length; index += 1) {
                const value = values[index];
                if (Number.isFinite(value)) return value;
            }
            return raw;
        };

        return { drawSparkline, findWindowBaseline, resetMarketCardNeutralState };
    };

    global.CaixaSaasMarketCore = { createMarketCore };
})(window);