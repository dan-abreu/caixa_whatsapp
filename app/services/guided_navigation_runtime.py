from types import SimpleNamespace
from typing import Any, Callable, Dict, Optional


def build_guided_navigation_runtime_helpers(
    *,
    guided_flow_navigation_helpers: Any,
    normalize_text: Callable[[str], str],
    save_session: Callable[[Any, str, str, Dict[str, Any]], None],
    build_cambio_prompt: Callable[[str], str],
) -> SimpleNamespace:
    def guided_prompt_for_state(state: str, contexto: Dict[str, Any]) -> str:
        return guided_flow_navigation_helpers.prompt_for_state(state, contexto, build_cambio_prompt)

    def guided_try_back_command(
        remetente: str,
        mensagem: str,
        estado: str,
        contexto: Dict[str, Any],
        db: Any,
    ) -> Optional[Dict[str, Any]]:
        return guided_flow_navigation_helpers.try_back_command(
            remetente=remetente,
            mensagem=mensagem,
            estado=estado,
            contexto=contexto,
            db=db,
            normalize_text=normalize_text,
            save_session=save_session,
            build_cambio_prompt=build_cambio_prompt,
        )

    return SimpleNamespace(
        guided_prompt_for_state=guided_prompt_for_state,
        guided_try_back_command=guided_try_back_command,
    )