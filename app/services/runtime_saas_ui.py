from html import escape
from types import SimpleNamespace
from typing import Any, Callable, Dict, Optional


def build_runtime_saas_ui_helpers(
    *,
    asset_url: Callable[[str], str],
    normalize_text: Callable[[str], str],
) -> SimpleNamespace:
    def build_saas_chat_welcome(user_name: str) -> Dict[str, str]:
        return {
            "role": "assistant",
            "content": f"Ola, {user_name}. Estou disponivel para apoiar o registro de operacoes, a consulta de saldos, extratos, estoque e demais rotinas operacionais do painel.",
        }

    def normalize_saas_page(raw: Optional[str]) -> str:
        text = normalize_text(str(raw or "dashboard"))
        aliases = {
            "dashboard": "dashboard",
            "inicio": "dashboard",
            "home": "dashboard",
            "operacao": "operation",
            "operacoes": "operation",
            "operation": "operation",
            "lancar": "operation",
            "perfil": "profile",
            "profile": "profile",
            "usuario": "profile",
            "conta": "profile",
            "clientes": "clients",
            "cliente": "clients",
            "cadastro": "clients",
            "clients": "clients",
            "fornecedor": "suppliers",
            "fornecedores": "suppliers",
            "supplier": "suppliers",
            "suppliers": "suppliers",
            "monitor": "monitors",
            "monitores": "monitors",
            "monitoria": "monitors",
            "monitors": "monitors",
            "noticia": "news_hub",
            "noticias": "news_hub",
            "news": "news_hub",
            "mercado": "news_hub",
            "extrato": "statement",
            "statement": "statement",
            "movimentos": "statement",
        }
        return aliases.get(text, "dashboard")

    def format_cliente_code(cliente_id: Any) -> str:
        try:
            return f"CL-{int(cliente_id):06d}"
        except (TypeError, ValueError):
            return "CL-000000"

    def format_fornecedor_code(fornecedor_id: Any) -> str:
        try:
            return f"FN-{int(fornecedor_id):06d}"
        except (TypeError, ValueError):
            return "FN-000000"

    def build_cliente_lookup_meta(cliente: Dict[str, Any]) -> str:
        bits = [format_cliente_code(cliente.get("id"))]
        telefone = str(cliente.get("telefone") or "").strip()
        documento = str(cliente.get("documento") or "").strip()
        apelido = str(cliente.get("apelido") or "").strip()
        if telefone:
            bits.append(telefone)
        if documento:
            bits.append(documento)
        if apelido:
            bits.append(f"apelido: {apelido}")
        return " | ".join(bits)

    def build_fornecedor_lookup_meta(fornecedor: Dict[str, Any]) -> str:
        bits = [format_fornecedor_code(fornecedor.get("id"))]
        telefone = str(fornecedor.get("telefone") or "").strip()
        documento = str(fornecedor.get("documento") or "").strip()
        apelido = str(fornecedor.get("apelido") or "").strip()
        if telefone:
            bits.append(telefone)
        if documento:
            bits.append(documento)
        if apelido:
            bits.append(f"apelido: {apelido}")
        return " | ".join(bits)

    def render_saas_login_html(message: Optional[str] = None, telefone: str = "") -> str:
        alert = ""
        if message:
            alert = f"<div class='alert error'>{escape(message)}</div>"
        login_css_url = asset_url("saas-login.css")
        return f"""
    <html>
        <head>
            <title>Caixa SaaS</title>
            <meta name='viewport' content='width=device-width, initial-scale=1' />
            <link rel='preload' href='{login_css_url}' as='style'>
            <link href='{login_css_url}' rel='stylesheet'>
        </head>
        <body>
            <div class='shell'>
                <h1>Caixa SaaS</h1>
                <p>Painel web para operar o mesmo motor do WhatsApp com leitura mais clara, relatórios e entrada rápida de dados.</p>
                {alert}
                <form method='post' action='/saas/login'>
                    <label>Telefone do operador</label>
                    <input name='telefone' value='{escape(telefone)}' placeholder='+59711111111' required />
                    <label>PIN web</label>
                    <input type='password' name='pin' inputmode='numeric' placeholder='Seu PIN numérico' required />
                    <p class='hint'>Primeiro acesso após a migração: use os últimos 6 dígitos do telefone e troque o PIN logo após entrar.</p>
                    <button type='submit'>Entrar no painel</button>
                </form>
            </div>
        </body>
    </html>
    """

    return SimpleNamespace(
        build_saas_chat_welcome=build_saas_chat_welcome,
        normalize_saas_page=normalize_saas_page,
        format_cliente_code=format_cliente_code,
        format_fornecedor_code=format_fornecedor_code,
        build_cliente_lookup_meta=build_cliente_lookup_meta,
        build_fornecedor_lookup_meta=build_fornecedor_lookup_meta,
        render_saas_login_html=render_saas_login_html,
    )