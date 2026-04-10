(function (global) {
    const createOperationSubmissionRuntime = (config) => {
        const {
            aiDraftForm, aiDraftInput, aiDraftStatus, applyDraft, opForm, operationFormNotice,
            operationFormNoticeText, operationFormReceiptLink, quickModeHint, quickModeKey,
            resetOperationFormAfterSuccess, setClassPresenceIfChanged, setPropertyIfChanged, setTextIfChanged, toggleQuickOrderMode,
        } = config;

        let isSubmittingOperation = false;

        const showOperationNotice = (message, kind = 'info', receiptUrl = '') => {
            if (!operationFormNotice || !operationFormNoticeText) return;
            setTextIfChanged(operationFormNoticeText, String(message || ''));
            setClassPresenceIfChanged(operationFormNotice, 'is-hidden', false);
            operationFormNotice.classList.remove('info', 'error');
            operationFormNotice.classList.add(kind === 'error' ? 'error' : 'info');
            if (!operationFormReceiptLink) return;
            const resolvedUrl = String(receiptUrl || '').trim();
            if (resolvedUrl) {
                operationFormReceiptLink.href = resolvedUrl;
                setClassPresenceIfChanged(operationFormReceiptLink, 'is-hidden', false);
                return;
            }
            operationFormReceiptLink.href = '#';
            setClassPresenceIfChanged(operationFormReceiptLink, 'is-hidden', true);
        };
        const clearOperationNotice = () => {
            if (!operationFormNotice || !operationFormNoticeText) return;
            setTextIfChanged(operationFormNoticeText, '');
            setClassPresenceIfChanged(operationFormNotice, 'is-hidden', true);
            operationFormNotice.classList.remove('info', 'error');
            if (!operationFormReceiptLink) return;
            operationFormReceiptLink.href = '#';
            setClassPresenceIfChanged(operationFormReceiptLink, 'is-hidden', true);
        };
        const setQuickMode = (enabled) => {
            opForm.classList.toggle('quick-order-mode', enabled);
            setTextIfChanged(toggleQuickOrderMode, enabled ? 'Desativar modo agil' : 'Modo de lancamento agil');
            setTextIfChanged(quickModeHint, enabled ? 'Enter avanca, Ctrl+Enter confirma o registro e os campos opcionais permanecem ocultos.' : 'Enter avanca campo a campo. Ctrl+Enter confirma o registro.');
            localStorage.setItem(quickModeKey, enabled ? '1' : '0');
        };
        const focusableFields = () => Array.from(opForm.querySelectorAll('input, select, textarea, button[type="submit"]')).filter((field) => !field.disabled && field.offsetParent !== null);
        const bindQuickMode = () => {
            if (toggleQuickOrderMode) {
                toggleQuickOrderMode.addEventListener('click', () => setQuickMode(!opForm.classList.contains('quick-order-mode')));
            }
            opForm.addEventListener('keydown', (event) => {
                if (event.key === 'Enter' && event.ctrlKey) {
                    event.preventDefault();
                    opForm.requestSubmit();
                    return;
                }
                if (!opForm.classList.contains('quick-order-mode') || event.key !== 'Enter' || event.shiftKey) return;
                const target = event.target;
                if (target instanceof HTMLTextAreaElement) return;
                event.preventDefault();
                const fields = focusableFields();
                const currentIndex = fields.indexOf(target);
                if (currentIndex >= 0 && currentIndex < fields.length - 1) {
                    fields[currentIndex + 1].focus();
                    if (typeof fields[currentIndex + 1].select === 'function') fields[currentIndex + 1].select();
                    return;
                }
                opForm.requestSubmit();
            });
            setQuickMode(localStorage.getItem(quickModeKey) === '1');
        };
        const bindSubmit = () => {
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
                        headers: { 'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8', 'X-Requested-With': 'XMLHttpRequest', Accept: 'application/json' },
                        body: payload.toString(),
                        credentials: 'same-origin',
                    });
                    const data = await response.json();
                    if (!response.ok || !data.ok) throw new Error(data.notice || 'Nao foi possivel concluir a operacao.');
                    const receiptUrl = String(data.receipt_url || '').trim();
                    if (receiptUrl) {
                        if (receiptWindow && !receiptWindow.closed) receiptWindow.location.replace(receiptUrl);
                        else window.open(receiptUrl, '_blank', 'noopener');
                    }
                    resetOperationFormAfterSuccess();
                    showOperationNotice(data.notice || 'Operacao registrada com sucesso. O recibo foi aberto em outra pagina.', 'info', receiptUrl);
                } catch (error) {
                    if (receiptWindow && !receiptWindow.closed) receiptWindow.close();
                    showOperationNotice((error && error.message) || 'Falha ao concluir a operacao.', 'error');
                } finally {
                    isSubmittingOperation = false;
                    setPropertyIfChanged(submitButton, 'disabled', false);
                }
            });
        };
        const bindDraft = () => {
            if (!aiDraftForm || !aiDraftInput || !aiDraftStatus) return;
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
                        headers: { 'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8', 'X-Requested-With': 'XMLHttpRequest', Accept: 'application/json' },
                        body: payload.toString(),
                        credentials: 'same-origin',
                    });
                    const data = await response.json();
                    if (!response.ok || !data.ok) throw new Error(data.notice || 'Nao foi possivel gerar o rascunho.');
                    applyDraft(data.draft || {});
                    setQuickMode(true);
                    let draftStatusText = data.summary || 'Rascunho aplicado no formulario.';
                    if (Array.isArray(data.missing_fields) && data.missing_fields.length) {
                        const missingFieldLabels = { peso: 'peso', preco_usd: 'preco', cliente: 'cliente', pessoa: 'cliente' };
                        const missingFieldsText = data.missing_fields.map((field) => missingFieldLabels[field] || field).join(', ');
                        draftStatusText += ` Campos para revisar: ${missingFieldsText}.`;
                    }
                    setTextIfChanged(aiDraftStatus, draftStatusText);
                } catch (error) {
                    setTextIfChanged(aiDraftStatus, (error && error.message) || 'Falha ao montar o rascunho.');
                }
            });
        };
        const initialize = () => {
            bindQuickMode();
            bindSubmit();
            bindDraft();
        };
        return { initialize };
    };

    global.CaixaSaasOperationSubmissionRuntime = { createOperationSubmissionRuntime };
})(window);