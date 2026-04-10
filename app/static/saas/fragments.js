(function (global) {
    const createFragmentsRuntime = (config) => {
        const { escapeHtml } = config;

        const loadDeferredFragment = (container) => {
            if (!(container instanceof HTMLElement)) return;
            const url = String(container.dataset.fragmentUrl || '').trim();
            if (!url || container.dataset.fragmentLoaded === '1' || container.dataset.fragmentLoading === '1') return;
            container.dataset.fragmentLoading = '1';
            fetch(url, {
                headers: { 'X-Requested-With': 'XMLHttpRequest', Accept: 'text/html' },
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

        const initialize = () => {
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

        return { initialize };
    };

    global.CaixaSaasFragments = { createFragmentsRuntime };
})(window);