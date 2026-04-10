(function (global) {
    const createChatRuntime = (config) => {
        const {
            bootstrapHistory,
            currentPage,
            escapeHtml,
            form,
            historyKey,
            input,
            remetente,
            restoreWidgetPosition,
            send,
            setInputValueIfChanged,
            setMinimized,
            setPropertyIfChanged,
            shouldHydrateChatEagerly,
            supportsHover,
            thread,
            widget,
        } = config;

        const nowStamp = () => new Date().toISOString();
        const chatTimeFormatter = new Intl.DateTimeFormat('pt-BR', { hour: '2-digit', minute: '2-digit' });
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
            return bootstrapHistory.map(normalizeEntry);
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

        let history = [];
        let renderedHistorySignatures = [];
        let chatWidgetHydrated = false;
        let chatHydrationScheduled = false;

        const renderHistory = (nextHistory) => {
            const normalizedHistory = nextHistory.map(normalizeEntry);
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

        const hydrate = () => {
            if (chatWidgetHydrated) return;
            chatWidgetHydrated = true;
            restoreWidgetPosition();
            history = readHistory();
            renderHistory(history);
        };

        const scheduleHydration = () => {
            if (chatWidgetHydrated || chatHydrationScheduled) return;
            chatHydrationScheduled = true;
            const runHydrate = () => {
                chatHydrationScheduled = false;
                hydrate();
            };
            if (typeof window.requestIdleCallback === 'function') {
                window.requestIdleCallback(runHydrate, { timeout: 1500 });
                return;
            }
            window.setTimeout(runHydrate, 300);
        };

        const appendAssistantAlert = (content) => {
            hydrate();
            history = history.concat([{ role: 'assistant', content, ts: nowStamp() }]);
            saveHistory(history);
            renderHistory(history);
            setMinimized(false);
        };

        const bindVisibilityInteractions = () => {
            if (supportsHover) {
                widget.addEventListener('mouseenter', () => {
                    hydrate();
                    setMinimized(false);
                });
                widget.addEventListener('mouseleave', () => {
                    if (!widget.classList.contains('dragging') && !widget.contains(document.activeElement)) setMinimized(true);
                });
                widget.addEventListener('focusin', () => {
                    hydrate();
                    setMinimized(false);
                });
                widget.addEventListener('focusout', () => {
                    window.setTimeout(() => {
                        if (!widget.classList.contains('dragging') && !widget.contains(document.activeElement)) setMinimized(true);
                    }, 0);
                });
                return;
            }
            hydrate();
            setMinimized(false);
        };

        const bindComposer = () => {
            input.addEventListener('keydown', (event) => {
                if (event.key === 'Enter' && !event.shiftKey) {
                    event.preventDefault();
                    form.requestSubmit();
                }
            });

            form.addEventListener('submit', async (event) => {
                event.preventDefault();
                hydrate();
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
                        headers: { 'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8', 'X-Requested-With': 'XMLHttpRequest', Accept: 'application/json' },
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
        };

        const initialize = () => {
            if (shouldHydrateChatEagerly) {
                hydrate();
            } else {
                scheduleHydration();
            }
            bindVisibilityInteractions();
            bindComposer();
        };

        return { appendAssistantAlert, hydrate, initialize };
    };

    global.CaixaSaasChatRuntime = { createChatRuntime };
})(window);