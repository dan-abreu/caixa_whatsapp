import logging
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Dict, Optional, cast


logger = logging.getLogger("caixa_whatsapp")


def build_whatsapp_session_helpers(*, session_cache: Dict[str, Dict[str, Any]], guided_session_idle_minutes: int) -> SimpleNamespace:
    def needs_name_onboarding(usuario: Dict[str, Any]) -> bool:
        nome = str(usuario.get("nome") or "").strip().lower()
        if not nome:
            return True
        placeholders = {"operador", "usuario", "usuário", "sem nome", "unknown", "n/a"}
        return nome in placeholders

    def build_whatsapp_checklist_menu() -> str:
        return (
            "Central de atendimento operacional:\n"
            "──────────────────\n"
            "1) Registrar operacao de compra ou venda\n"
            "   Ex: compra | venda | comprei ouro 2g\n\n"
            "2) Consultar posicao de caixa\n"
            "   Ex: caixa | caixa eur | caixa srd | caixa xau\n\n"
            "3) Consultar extrato\n"
            "   Ex: extrato | extrato hoje | extrato semana\n\n"
            "4) Ajustar operacao\n"
            "   Ex: editar 123 preco 110 | editar 123 quantidade 2.5\n\n"
            "5) Cancelar operacao\n"
            "   Ex: cancelar 123\n"
            "──────────────────\n"
            "Se preferir, descreva diretamente a solicitacao operacional em texto livre."
        )

    def save_session(db: Any, remetente: str, estado: str, contexto: Dict[str, Any]) -> None:
        atualizado_em = datetime.now(timezone.utc).isoformat()
        session_cache[remetente] = {"estado": estado, "contexto": contexto, "atualizado_em": atualizado_em}
        db.save_conversation_session(remetente=remetente, estado=estado, contexto=contexto)

    def get_session(db: Any, remetente: str) -> Optional[Dict[str, Any]]:
        cached = session_cache.get(remetente)
        if cached:
            return cached
        db_session = db.get_conversation_session(remetente)
        if db_session and isinstance(db_session.get("contexto"), dict):
            session: Dict[str, Any] = {
                "estado": db_session.get("estado", ""),
                "contexto": cast(Dict[str, Any], db_session["contexto"]),
                "atualizado_em": db_session.get("atualizado_em"),
            }
            session_cache[remetente] = session
            return session
        return None

    def guided_session_idle_minutes_for(session: Dict[str, Any]) -> Optional[int]:
        updated_raw = session.get("atualizado_em")
        if not updated_raw:
            return None
        try:
            updated_dt = datetime.fromisoformat(str(updated_raw).replace("Z", "+00:00"))
        except (TypeError, ValueError) as exc:
            logger.warning("Falha ao interpretar timestamp da sessao guiada: %s", exc)
            return None
        if updated_dt.tzinfo is None:
            updated_dt = updated_dt.replace(tzinfo=timezone.utc)
        now_utc = datetime.now(timezone.utc)
        delta = now_utc - updated_dt.astimezone(timezone.utc)
        return max(0, int(delta.total_seconds() // 60))

    def is_guided_session_stale(session: Dict[str, Any]) -> bool:
        idle = guided_session_idle_minutes_for(session)
        if idle is None:
            return False
        return idle >= guided_session_idle_minutes

    def clear_session(db: Any, remetente: str) -> None:
        session_cache.pop(remetente, None)
        db.clear_conversation_session(remetente)

    return SimpleNamespace(
        needs_name_onboarding=needs_name_onboarding,
        build_whatsapp_checklist_menu=build_whatsapp_checklist_menu,
        save_session=save_session,
        get_session=get_session,
        guided_session_idle_minutes=guided_session_idle_minutes_for,
        is_guided_session_stale=is_guided_session_stale,
        clear_session=clear_session,
    )