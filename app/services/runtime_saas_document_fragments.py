from html import escape
from types import SimpleNamespace
from typing import Any, Dict, Mapping


def build_runtime_saas_document_fragment_helpers() -> SimpleNamespace:
    def build_web_ai_banner_html(current_page: str) -> str:
        if current_page != "dashboard":
            return ""
        return """
        <section class='notice info web-ai-alert-banner neutral is-hidden' id='webAiAlertBanner' data-lot-alert-endpoint='/saas/lot-monitor-snapshot' data-lot-alert-stream-endpoint='/saas/lot-monitor-stream'>
            <div class='notice-action web-ai-alert-shell'>
                <div>
                    <strong>IA da web</strong><br>
                    <span id='webAiAlertText'>Monitorando lotes em segundo plano...</span>
                </div>
                <button type='button' class='ghost-btn mini-action web-ai-notification-btn' id='webAiNotificationButton'>Ativar avisos no navegador</button>
            </div>
        </section>
        """

    def build_market_snapshot_client(market_snapshot: Mapping[str, Any]) -> Dict[str, str]:
        return {
            "xau_usd_raw": str(market_snapshot.get("xau_usd_raw") or ""),
            "grama_ref_raw": str(market_snapshot.get("grama_ref_raw") or ""),
            "usd_brl_raw": str(market_snapshot.get("usd_brl_raw") or ""),
            "eur_usd_raw": str(market_snapshot.get("eur_usd_raw") or ""),
            "eur_brl_raw": str(market_snapshot.get("eur_brl_raw") or ""),
            "xau_source": str(market_snapshot.get("xau_source") or ""),
            "xau_source_label": str(market_snapshot.get("xau_source_label") or ""),
            "status": str(market_snapshot.get("status") or ""),
            "updated_at_label": str(market_snapshot.get("updated_at_label") or ""),
        }

    def build_floating_ai_html(current_page: str, chat_operator_field: str, console_message: str) -> str:
        return f"""
    <aside class='ai-float panel minimized' id='aiChatWidget' data-page='{current_page}'>
        <div class='ai-shell'>
            <div class='ai-head' id='aiChatHandle'>
                <div>
                    <strong>IA Operacional</strong>
                    <p>Canal de apoio operacional para consultas, registros e orientacoes do fluxo.</p>
                </div>
                <span class='ai-drag-handle' aria-hidden='true'>Arraste</span>
            </div>
            <div class='ai-body' id='aiChatBody'>
                <div class='ai-status'>Disponivel</div>
                <div class='ai-thread' id='aiChatThread' aria-live='polite'></div>
                <form method='post' action='/saas/console' id='aiChatForm' class='ai-chat-form'>
                    <input type='hidden' name='page' value='{current_page}' />
                    {chat_operator_field}
                    <label class='chat-composer'>
                        <textarea id='aiChatInput' name='console_mensagem' rows='2' placeholder='Digite a solicitacao operacional...' required>{escape(console_message)}</textarea>
                    </label>
                    <div class='ai-actions'>
                        <span class='chat-helper'>Enter envia. Shift+Enter insere nova linha.</span>
                        <button type='submit' id='aiChatSend'>Enviar</button>
                    </div>
                </form>
            </div>
        </div>
    </aside>
    """

    return SimpleNamespace(
        build_web_ai_banner_html=build_web_ai_banner_html,
        build_market_snapshot_client=build_market_snapshot_client,
        build_floating_ai_html=build_floating_ai_html,
    )
