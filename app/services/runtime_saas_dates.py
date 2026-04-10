import logging
import os
import re
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Dict, Optional
from urllib.parse import parse_qs

from fastapi import HTTPException


logger = logging.getLogger("caixa_whatsapp")


def build_runtime_saas_date_helpers() -> SimpleNamespace:
    async def request_form_dict(request: Any) -> Dict[str, str]:
        raw_text = ""
        try:
            raw_text = (await request.body()).decode("utf-8", errors="ignore")
        except Exception as exc:
            logger.warning("Falha ao ler body bruto do formulario: %s", exc)
            raw_text = ""

        try:
            form = await request.form()
            return {str(k): str(v) for k, v in dict(form).items()}
        except Exception as exc:
            logger.warning("Falha ao ler request.form(); tentando querystring parseada: %s", exc)

        try:
            parsed = parse_qs(raw_text)
            return {k: v[0] for k, v in parsed.items() if v}
        except Exception as exc:
            logger.warning("Falha ao parsear body de formulario via querystring: %s", exc)
            return {}

    def build_day_range(date_str: Optional[str]) -> Dict[str, str]:
        tz_offset_hours = int(os.getenv("TZ_OFFSET_HOURS", "-3"))
        if date_str:
            try:
                base_date = datetime.strptime(date_str, "%Y-%m-%d").date()
            except ValueError as exc:
                raise HTTPException(status_code=400, detail="Data invalida. Use: AAAA-MM-DD") from exc
        else:
            utc_now = datetime.now(timezone.utc)
            local_now = utc_now + timedelta(hours=tz_offset_hours)
            base_date = local_now.date()

        start_dt = datetime(base_date.year, base_date.month, base_date.day, tzinfo=timezone.utc)
        end_dt = start_dt + timedelta(days=1)
        return {"start": start_dt.isoformat(), "end": end_dt.isoformat(), "date": str(base_date)}

    def build_week_range() -> Dict[str, str]:
        tz_offset_hours = int(os.getenv("TZ_OFFSET_HOURS", "-3"))
        utc_now = datetime.now(timezone.utc)
        local_now = utc_now + timedelta(hours=tz_offset_hours)
        today = local_now.date()
        monday = today - timedelta(days=today.weekday())
        start_dt = datetime(monday.year, monday.month, monday.day, tzinfo=timezone.utc)
        end_dt = datetime(today.year, today.month, today.day, tzinfo=timezone.utc) + timedelta(days=1)
        return {
            "start": start_dt.isoformat(),
            "end": end_dt.isoformat(),
            "label": f"{monday.isoformat()} a {today.isoformat()}",
        }

    def parse_date_user_input(text: str) -> Optional[str]:
        s = text.strip()
        match = re.match(r"^(\d{1,2})[/\-](\d{1,2})(?:[/\-](\d{2,4}))?$", s)
        if match:
            day_value, month_value = int(match.group(1)), int(match.group(2))
            year_raw = match.group(3)
            if year_raw:
                year_value = int(year_raw)
                if year_value < 100:
                    year_value += 2000
            else:
                year_value = date.today().year
            try:
                date(year_value, month_value, day_value)
                return f"{year_value:04d}-{month_value:02d}-{day_value:02d}"
            except ValueError:
                return None

        match_iso = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", s)
        if match_iso:
            return s
        return None

    def build_custom_range(start: str, end: str) -> Dict[str, str]:
        try:
            start_dt = datetime.fromisoformat(start)
            end_dt = datetime.fromisoformat(end)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Data/hora invalida. Use formato ISO.") from exc

        if end_dt <= start_dt:
            raise HTTPException(status_code=400, detail="A data final deve ser maior que a inicial.")

        return {
            "start": start_dt.astimezone(timezone.utc).isoformat(),
            "end": end_dt.astimezone(timezone.utc).isoformat(),
        }

    return SimpleNamespace(
        request_form_dict=request_form_dict,
        build_day_range=build_day_range,
        build_week_range=build_week_range,
        parse_date_user_input=parse_date_user_input,
        build_custom_range=build_custom_range,
    )