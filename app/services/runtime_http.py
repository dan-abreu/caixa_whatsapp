import json
from types import SimpleNamespace
from typing import Any, Callable, Dict, Optional


def build_runtime_http_helpers(
    *,
    static_asset_versions: Dict[str, str],
    static_dir: Any,
    quote: Callable[[str], str],
    os_getenv: Callable[[str], Optional[str]],
    http_exception_cls: Any,
) -> SimpleNamespace:
    def asset_url(filename: str) -> str:
        cached_version = static_asset_versions.get(filename)
        if not cached_version:
            try:
                cached_version = str(int((static_dir / filename).stat().st_mtime))
            except OSError:
                cached_version = "0"
            static_asset_versions[filename] = cached_version
        return f"/static/{quote(filename)}?v={cached_version}"

    def json_for_html_script(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")

    async def add_performance_headers(request: Any, call_next: Any):
        response = await call_next(request)
        path = request.url.path
        content_type = response.headers.get("content-type", "")
        if path.startswith("/static/"):
            response.headers.setdefault(
                "Cache-Control",
                "public, max-age=31536000, immutable, stale-while-revalidate=86400",
            )
            response.headers.setdefault("Vary", "Accept-Encoding")
        elif content_type.startswith("text/html"):
            response.headers.setdefault("Cache-Control", "private, no-store")
            response.headers.setdefault("Vary", "Cookie, Accept-Encoding")
        return response

    def validate_webhook_token(token: Optional[str]) -> None:
        expected = os_getenv("WEBHOOK_TOKEN")
        if not expected:
            raise http_exception_cls(status_code=500, detail="Token do sistema nao configurado")
        if token != expected:
            raise http_exception_cls(status_code=401, detail="Token invalido")

    return SimpleNamespace(
        asset_url=asset_url,
        json_for_html_script=json_for_html_script,
        add_performance_headers=add_performance_headers,
        validate_webhook_token=validate_webhook_token,
    )
