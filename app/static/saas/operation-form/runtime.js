(function (global) {
    const createOperationFormRuntime = (config) => {
        const {
            escapeHtml, grams, money, recentFx, setDatasetValueIfChanged, setInputValueIfChanged,
            setClassPresenceIfChanged, setPropertyIfChanged, setStylePropertyIfChanged, setTextIfChanged,
        } = config;

        const opForm = document.getElementById('quickOperationForm');
        if (!opForm) return { initialize() {} };

        const refs = {
            aiDraftForm: document.getElementById('aiDraftForm'),
            aiDraftInput: document.getElementById('aiDraftInput'),
            aiDraftStatus: document.getElementById('aiDraftStatus'),
            clienteIdInput: document.getElementById('opClienteId'),
            clienteLookupMeta: document.getElementById('opClienteLookupMeta'),
            clienteMeta: document.getElementById('opClienteMeta'),
            clienteResults: document.getElementById('opClienteResults'),
            closureClosed: document.getElementById('opClosureClosed'),
            closureClosedSide: document.getElementById('opClosureClosedSide'),
            closureOpen: document.getElementById('opClosureOpen'),
            closureOpenSide: document.getElementById('opClosureOpenSide'),
            closurePeso: document.getElementById('opClosurePeso'),
            closurePesoSide: document.getElementById('opClosurePesoSide'),
            diffUsd: document.getElementById('opDiffUsd'),
            fechamentoHint: document.getElementById('opFechamentoHint'),
            fechamentoHintSide: document.getElementById('opFechamentoHintSide'),
            fechamentoInput: document.getElementById('opFechamentoGramas'),
            fechamentoTipo: document.getElementById('opFechamentoTipo'),
            fineGold: document.getElementById('opFineGold'),
            goldTypeInput: document.getElementById('opGoldType'),
            inlineClienteBox: document.getElementById('inlineClienteBox'),
            inlineClienteMode: document.getElementById('inlineClienteMode'),
            inlineClienteNome: document.getElementById('inlineClienteNome'),
            inlineClienteSave: document.getElementById('inlineClienteSave'),
            inlineClienteStatus: document.getElementById('inlineClienteStatus'),
            operationFormNotice: document.getElementById('operationFormNotice'),
            operationFormNoticeText: document.getElementById('operationFormNoticeText'),
            operationFormReceiptLink: document.getElementById('operationFormReceiptLink'),
            paidUsd: document.getElementById('opPaidUsd'),
            paymentRows: Array.from(document.querySelectorAll('.js-payment-row')),
            pessoaInput: document.getElementById('opPessoa'),
            pesoInput: document.getElementById('opPeso'),
            precoInput: document.getElementById('opPrecoUsd'),
            quebraInput: document.getElementById('opQuebra'),
            quebraWrap: document.getElementById('opQuebraWrap'),
            saleLotApplySelection: document.getElementById('saleLotApplySelection'),
            saleLotChecks: Array.from(document.querySelectorAll('.js-sale-lot-check')),
            saleLotGrams: Array.from(document.querySelectorAll('.js-sale-lot-grams')),
            saleLotSelectedTotal: document.getElementById('saleLotSelectedTotal'),
            saleLotSelectionPanel: document.getElementById('saleLotSelectionPanel'),
            saleLotSelectorBox: document.getElementById('saleLotSelectorBox'),
            saleSourceMode: document.getElementById('saleSourceMode'),
            quickModeHint: document.getElementById('quickModeHint'),
            rateioHint: document.getElementById('opRateioHint'),
            summaryText: document.getElementById('opSummaryText'),
            targetUsd: document.getElementById('opTargetUsd'),
            teorInput: document.getElementById('opTeor'),
            tipoOperacao: document.getElementById('opTipoOperacao'),
            toggleInlineCliente: document.getElementById('toggleInlineCliente'),
            toggleQuickOrderMode: document.getElementById('toggleQuickOrderMode'),
            totalPagoInput: document.getElementById('opTotalPagoUsd'),
            totalUsd: document.getElementById('opTotalUsd'),
            usePesoTotal: document.getElementById('opUsePesoTotal'),
            useTotalAsUsd: document.getElementById('opUseTotalAsUsd'),
        };
        const quickModeKey = 'caixaQuickOrderMode';

        const operationClientRuntime = global.CaixaSaasOperationClientRuntime && typeof global.CaixaSaasOperationClientRuntime.createOperationClientRuntime === 'function'
            ? global.CaixaSaasOperationClientRuntime.createOperationClientRuntime({
                clienteIdInput: refs.clienteIdInput,
                clienteLookupMeta: refs.clienteLookupMeta,
                clienteMeta: refs.clienteMeta,
                clienteResults: refs.clienteResults,
                escapeHtml,
                inlineClienteBox: refs.inlineClienteBox,
                inlineClienteMode: refs.inlineClienteMode,
                inlineClienteNome: refs.inlineClienteNome,
                inlineClienteSave: refs.inlineClienteSave,
                inlineClienteStatus: refs.inlineClienteStatus,
                opForm,
                pessoaInput: refs.pessoaInput,
                setClassPresenceIfChanged,
                setInputValueIfChanged,
                setPropertyIfChanged,
                setTextIfChanged,
                toggleInlineCliente: refs.toggleInlineCliente,
            })
            : { initialize() {}, resetAfterSuccess() {}, syncLookupMeta() {} };

        const operationCalculatorRuntime = global.CaixaSaasOperationCalculatorRuntime && typeof global.CaixaSaasOperationCalculatorRuntime.createOperationCalculatorRuntime === 'function'
            ? global.CaixaSaasOperationCalculatorRuntime.createOperationCalculatorRuntime({
                closureClosed: refs.closureClosed,
                closureClosedSide: refs.closureClosedSide,
                closureOpen: refs.closureOpen,
                closureOpenSide: refs.closureOpenSide,
                closurePeso: refs.closurePeso,
                closurePesoSide: refs.closurePesoSide,
                diffUsd: refs.diffUsd,
                fechamentoHint: refs.fechamentoHint,
                fechamentoHintSide: refs.fechamentoHintSide,
                fechamentoInput: refs.fechamentoInput,
                fechamentoTipo: refs.fechamentoTipo,
                fineGold: refs.fineGold,
                goldTypeInput: refs.goldTypeInput,
                grams,
                money,
                opForm,
                operationClientRuntime,
                paymentRows: refs.paymentRows,
                precoInput: refs.precoInput,
                pesoInput: refs.pesoInput,
                quebraInput: refs.quebraInput,
                quebraWrap: refs.quebraWrap,
                rateioHint: refs.rateioHint,
                recentFx,
                scheduleFrame: window.requestAnimationFrame.bind(window),
                setClassPresenceIfChanged,
                setDatasetValueIfChanged,
                setInputValueIfChanged,
                setPropertyIfChanged,
                setStylePropertyIfChanged,
                setTextIfChanged,
                summaryText: refs.summaryText,
                targetUsd: refs.targetUsd,
                paidUsd: refs.paidUsd,
                totalPagoInput: refs.totalPagoInput,
                teorInput: refs.teorInput,
                tipoOperacao: refs.tipoOperacao,
                totalUsd: refs.totalUsd,
                usePesoTotal: refs.usePesoTotal,
                useTotalAsUsd: refs.useTotalAsUsd,
            })
            : { applyDraft() {}, initialize() {}, refreshAfterReset() {} };

        const resetOperationFormAfterSuccess = () => {
            opForm.reset();
            operationClientRuntime.resetAfterSuccess();
            operationCalculatorRuntime.refreshAfterReset();
            operationEnhancementsRuntime.refreshAfterReset();
            if (refs.pessoaInput) refs.pessoaInput.focus();
        };

        const operationSubmissionRuntime = global.CaixaSaasOperationSubmissionRuntime && typeof global.CaixaSaasOperationSubmissionRuntime.createOperationSubmissionRuntime === 'function'
            ? global.CaixaSaasOperationSubmissionRuntime.createOperationSubmissionRuntime({
                aiDraftForm: refs.aiDraftForm,
                aiDraftInput: refs.aiDraftInput,
                aiDraftStatus: refs.aiDraftStatus,
                applyDraft: (draft) => operationCalculatorRuntime.applyDraft(draft),
                opForm,
                operationFormNotice: refs.operationFormNotice,
                operationFormNoticeText: refs.operationFormNoticeText,
                operationFormReceiptLink: refs.operationFormReceiptLink,
                quickModeHint: refs.quickModeHint,
                quickModeKey,
                resetOperationFormAfterSuccess,
                setClassPresenceIfChanged,
                setPropertyIfChanged,
                setTextIfChanged,
                toggleQuickOrderMode: refs.toggleQuickOrderMode,
            })
            : { initialize() {} };

        const operationEnhancementsRuntime = global.CaixaSaasOperationEnhancementsRuntime && typeof global.CaixaSaasOperationEnhancementsRuntime.createOperationEnhancementsRuntime === 'function'
            ? global.CaixaSaasOperationEnhancementsRuntime.createOperationEnhancementsRuntime({
                clienteIdInput: refs.clienteIdInput,
                escapeHtml,
                fechamentoInput: refs.fechamentoInput,
                grams,
                paymentRows: refs.paymentRows,
                pesoInput: refs.pesoInput,
                saleLotApplySelection: refs.saleLotApplySelection,
                saleLotChecks: refs.saleLotChecks,
                saleLotGrams: refs.saleLotGrams,
                saleLotSelectedTotal: refs.saleLotSelectedTotal,
                saleLotSelectionPanel: refs.saleLotSelectionPanel,
                saleLotSelectorBox: refs.saleLotSelectorBox,
                saleSourceMode: refs.saleSourceMode,
                setClassPresenceIfChanged,
                setInputValueIfChanged,
                setTextIfChanged,
                tipoOperacao: refs.tipoOperacao,
            })
            : { initialize() {}, refreshAfterReset() {} };

        const initialize = () => {
            operationClientRuntime.initialize();
            operationCalculatorRuntime.initialize();
            operationSubmissionRuntime.initialize();
            operationEnhancementsRuntime.initialize();
        };

        return { initialize };
    };

    global.CaixaSaasOperationFormRuntime = { createOperationFormRuntime };
})(window);