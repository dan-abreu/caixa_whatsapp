(function (global) {
    const createWidgetRuntime = (config) => {
        const {
            dragHandle,
            dragPositionKey,
            dragSnapMargin,
            dragSnapThreshold,
            setStylePropertyIfChanged,
            supportsHover,
            widget,
        } = config;

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

        const initialize = (onBeforeDrag) => {
            setMinimized(supportsHover);
            window.addEventListener('resize', () => ensureWidgetInViewport());
            dragHandle.addEventListener('pointerdown', (event) => {
                if (event.button !== 0) return;
                if (typeof onBeforeDrag === 'function') onBeforeDrag();
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
        };

        return { ensureWidgetInViewport, initialize, restoreWidgetPosition, setMinimized };
    };

    global.CaixaSaasWidgetRuntime = { createWidgetRuntime };
})(window);