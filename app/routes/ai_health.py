from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, cast
from fastapi import FastAPI

def register_ai_health_routes(
    app: FastAPI,
    *,
    get_db: Callable[[], Any],
    get_ai_conf_config: Callable[[int], Dict[str, Any]],
) -> None:
    def resolve_learning_phase(total_samples: int) -> str:
        if total_samples >= 300:
            return "advanced"
        if total_samples >= 30:
            return "learning_stable"
        return "seed"

    def resolve_model_maturity(total_samples: int) -> str:
        if total_samples >= 300:
            return "advanced"
        if total_samples >= 100:
            return "stable"
        if total_samples >= 30:
            return "learning"
        return "seed"

    def compute_ai_confidence_score(
        *,
        total_samples: int,
        risk_ratio: float,
        fail_safe_ratio: float,
        risk_alerts: int,
        total_runs: int,
    ) -> Dict[str, Any]:
        cfg = get_ai_conf_config(total_samples)
        weight_total = float(cfg["weight_maturity"]) + float(cfg["weight_stability"]) + float(cfg["weight_alerts"])
        if weight_total <= 0:
            normalized_maturity = 0.45
            normalized_stability = 0.45
            normalized_alerts = 0.10
        else:
            normalized_maturity = float(cfg["weight_maturity"]) / weight_total
            normalized_stability = float(cfg["weight_stability"]) / weight_total
            normalized_alerts = float(cfg["weight_alerts"]) / weight_total
        sample_maturity = min(max(total_samples, 0) / float(cfg["samples_target"]), 1.0)
        stability_penalty = min(max((risk_ratio * float(cfg["risk_weight"])) + (fail_safe_ratio * float(cfg["failsafe_weight"])), 0.0), 1.0)
        stability = 1.0 - stability_penalty
        alerts_per_run = (risk_alerts / max(total_runs, 1)) if total_runs >= 0 else 0.0
        alert_pressure = min(max(alerts_per_run, 0.0), 1.0)
        score_raw = (
            (sample_maturity * normalized_maturity * 100.0)
            + (stability * normalized_stability * 100.0)
            + ((1.0 - alert_pressure) * normalized_alerts * 100.0)
        )
        score = max(0.0, min(100.0, score_raw))
        cut_excellent = max(1, min(100, int(cfg["band_excellent"])))
        cut_good = max(1, min(cut_excellent, int(cfg["band_good"])))
        cut_moderate = max(1, min(cut_good, int(cfg["band_moderate"])))
        band = "low"
        if score >= cut_excellent:
            band = "excellent"
        elif score >= cut_good:
            band = "good"
        elif score >= cut_moderate:
            band = "moderate"

        return {
            "score": round(score, 2),
            "band": band,
            "profile": str(cfg["profile_effective"]),
            "profile_mode": str(cfg["profile_setting"]),
            "components": {
                "sample_maturity": round(sample_maturity, 4),
                "stability": round(stability, 4),
                "alert_pressure": round(alert_pressure, 4),
            },
        }

    def compute_ai_window_metrics(db: Any, days: int) -> Dict[str, Any]:
        window_days = max(1, days)
        now_utc = datetime.now(timezone.utc)
        start_iso = (now_utc - timedelta(days=window_days)).isoformat()
        end_iso = now_utc.isoformat()
        runs = db.get_multi_agent_runs_range(start_iso, end_iso, limit=1000)
        learning_snapshot = db.get_transaction_learning_snapshot(lookback_days=window_days)
        alerts = db.get_risk_alerts(start_iso, end_iso)
        runs_with_risk = 0
        runs_with_fail_safe = 0
        total_risks = 0
        for run in runs:
            response_payload = cast(Dict[str, Any], run.get("response_payload") or {})
            risks = cast(List[Any], response_payload.get("risks") or [])
            transcript = cast(List[Any], response_payload.get("transcript") or [])
            if risks:
                runs_with_risk += 1
                total_risks += len(risks)
            if any(isinstance(item, dict) and str(cast(Dict[str, Any], item).get("role", "")).lower() == "fail-safe" for item in transcript):
                runs_with_fail_safe += 1
        total_runs = len(runs)
        risk_ratio = round(runs_with_risk / total_runs, 4) if total_runs else 0.0
        fail_safe_ratio = round(runs_with_fail_safe / total_runs, 4) if total_runs else 0.0
        avg_risks_per_run = round(total_risks / total_runs, 4) if total_runs else 0.0
        total_samples = int(learning_snapshot.get("total_samples", 0) or 0)
        confidence = compute_ai_confidence_score(
            total_samples=total_samples,
            risk_ratio=risk_ratio,
            fail_safe_ratio=fail_safe_ratio,
            risk_alerts=len(alerts),
            total_runs=total_runs,
        )
        return {
            "window_days": window_days,
            "range": {"start": start_iso, "end": end_iso},
            "runs": total_runs,
            "runs_with_risk": runs_with_risk,
            "runs_with_fail_safe": runs_with_fail_safe,
            "risk_ratio": risk_ratio,
            "fail_safe_ratio": fail_safe_ratio,
            "avg_risks_per_run": avg_risks_per_run,
            "risk_alerts": len(alerts),
            "learning_samples": total_samples,
            "learning_phase": resolve_learning_phase(total_samples),
            "confidence_score": confidence["score"],
            "confidence_band": confidence["band"],
            "confidence_profile": confidence["profile"],
            "confidence_profile_mode": confidence["profile_mode"],
        }

    def trend_label(delta: float, good_when_negative: bool = True) -> str:
        if abs(delta) <= 0.0001:
            return "stable"
        if good_when_negative:
            return "improving" if delta < 0 else "worsening"
        return "improving" if delta > 0 else "worsening"

    def phase_transition_label(from_phase: str, to_phase: str) -> str:
        order = {"seed": 0, "learning_stable": 1, "advanced": 2}
        if from_phase == to_phase:
            return "stable"
        if order.get(to_phase, 0) > order.get(from_phase, 0):
            return "maturing"
        if order.get(to_phase, 0) < order.get(from_phase, 0):
            return "regressing"
        return "stable"

    def profile_transition_label(from_profile: str, to_profile: str) -> str:
        if from_profile == to_profile:
            return "stable"
        return f"{from_profile}_to_{to_profile}"

    def parse_trend_windows_param(windows: str) -> List[int]:
        if not windows.strip():
            return [7, 30]
        parsed: List[int] = []
        for raw in windows.split(","):
            token = raw.strip()
            if not token:
                continue
            try:
                value = int(token)
            except ValueError:
                continue
            if 1 <= value <= 365 and value not in parsed:
                parsed.append(value)
        if not parsed:
            return [7, 30]
        parsed.sort()
        return parsed[:6]

    @app.get("/ai/health")
    def ai_health_report() -> Dict[str, Any]:
        db = get_db()
        live_context = db.build_multi_agent_live_context(operation_id=None)
        learning_snapshot = cast(Dict[str, Any], live_context.get("learning_snapshot") or {})
        recent_runs = db.get_recent_multi_agent_runs(limit=50)
        total_samples = int(learning_snapshot.get("total_samples", 0) or 0)
        ops_stats = cast(Dict[str, Any], learning_snapshot.get("operations") or {})
        operator_profiles = cast(Dict[str, Any], learning_snapshot.get("operator_profiles") or {})
        runs_24h = 0
        runs_with_risk = 0
        runs_with_fail_safe = 0
        now_utc = datetime.now(timezone.utc)
        for run in recent_runs:
            created_raw = str(run.get("criado_em") or "")
            response_payload = cast(Dict[str, Any], run.get("response_payload") or {})
            risks = cast(List[Any], response_payload.get("risks") or [])
            transcript = cast(List[Any], response_payload.get("transcript") or [])
            if risks:
                runs_with_risk += 1
            if any(isinstance(item, dict) and str(cast(Dict[str, Any], item).get("role", "")).lower() == "fail-safe" for item in transcript):
                runs_with_fail_safe += 1
            if created_raw:
                try:
                    created_dt = datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
                    if created_dt.tzinfo is None:
                        created_dt = created_dt.replace(tzinfo=timezone.utc)
                    age_hours = (now_utc - created_dt.astimezone(timezone.utc)).total_seconds() / 3600
                    if age_hours <= 24:
                        runs_24h += 1
                except Exception:
                    pass
        risk_ratio = round(runs_with_risk / len(recent_runs), 4) if recent_runs else 0.0
        fail_safe_ratio = round(runs_with_fail_safe / len(recent_runs), 4) if recent_runs else 0.0
        risk_alerts_today = len(cast(List[Any], live_context.get("risk_alerts") or []))
        daily_operations = int(cast(Dict[str, Any], live_context.get("daily_summary") or {}).get("total_operacoes", 0) or 0)
        confidence = compute_ai_confidence_score(
            total_samples=total_samples,
            risk_ratio=risk_ratio,
            fail_safe_ratio=fail_safe_ratio,
            risk_alerts=risk_alerts_today,
            total_runs=max(len(recent_runs), daily_operations),
        )
        readiness = "ok"
        readiness_reasons: List[str] = []
        if total_samples < 30:
            readiness = "attention"
            readiness_reasons.append("base_historica_baixa")
        if fail_safe_ratio > 0.05:
            readiness = "attention"
            readiness_reasons.append("falha_interna_agentes")
        if risk_ratio > 0.5:
            readiness = "attention"
            readiness_reasons.append("alta_taxa_alertas_risco")
        if not readiness_reasons:
            readiness_reasons.append("operacao_dentro_do_esperado")
        return {
            "status": readiness,
            "confidence": confidence,
            "readiness_reasons": readiness_reasons,
            "learning": {
                "maturity": resolve_model_maturity(total_samples),
                "lookback_days": int(learning_snapshot.get("lookback_days", 0) or 0),
                "total_samples": total_samples,
                "operation_profiles": len(ops_stats),
                "operator_profiles": len(operator_profiles),
                "currency_mix": cast(Dict[str, Any], learning_snapshot.get("currency_mix") or {}),
            },
            "multi_agent": {
                "recent_runs": len(recent_runs),
                "runs_24h": runs_24h,
                "risk_ratio": risk_ratio,
                "fail_safe_ratio": fail_safe_ratio,
                "risk_alerts_today": risk_alerts_today,
            },
            "observability": {
                "top_divergences_today": len(cast(List[Any], live_context.get("top_divergences") or [])),
                "daily_operations": daily_operations,
            },
        }

    @app.get("/ai/health/trends")
    def ai_health_trends(windows: str = "7,30") -> Dict[str, Any]:
        db = get_db()
        selected_windows = parse_trend_windows_param(windows)
        metrics_by_window = {days: compute_ai_window_metrics(db, days=days) for days in selected_windows}
        short_window = selected_windows[0]
        long_window = selected_windows[-1]
        short_metrics = metrics_by_window[short_window]
        long_metrics = metrics_by_window[long_window]
        trend_summary: Dict[str, Dict[str, Any]] = {
            "risk_ratio": {"delta": round(short_metrics["risk_ratio"] - long_metrics["risk_ratio"], 4), "trend": trend_label(round(short_metrics["risk_ratio"] - long_metrics["risk_ratio"], 4), good_when_negative=True)},
            "fail_safe_ratio": {"delta": round(short_metrics["fail_safe_ratio"] - long_metrics["fail_safe_ratio"], 4), "trend": trend_label(round(short_metrics["fail_safe_ratio"] - long_metrics["fail_safe_ratio"], 4), good_when_negative=True)},
            "avg_risks_per_run": {"delta": round(short_metrics["avg_risks_per_run"] - long_metrics["avg_risks_per_run"], 4), "trend": trend_label(round(short_metrics["avg_risks_per_run"] - long_metrics["avg_risks_per_run"], 4), good_when_negative=True)},
            "risk_alerts": {"delta": int(short_metrics["risk_alerts"]) - int(long_metrics["risk_alerts"]), "trend": trend_label(float(int(short_metrics["risk_alerts"]) - int(long_metrics["risk_alerts"])), good_when_negative=True)},
            "learning_samples": {"delta": int(short_metrics["learning_samples"]) - int(long_metrics["learning_samples"]), "trend": trend_label(float(int(short_metrics["learning_samples"]) - int(long_metrics["learning_samples"])), good_when_negative=False)},
            "confidence_score": {"delta": round(float(short_metrics["confidence_score"]) - float(long_metrics["confidence_score"]), 4), "trend": trend_label(round(float(short_metrics["confidence_score"]) - float(long_metrics["confidence_score"]), 4), good_when_negative=False)},
            "learning_phase": {"from": long_metrics["learning_phase"], "to": short_metrics["learning_phase"], "trend": phase_transition_label(str(long_metrics["learning_phase"]), str(short_metrics["learning_phase"])), "transition": f"{long_metrics['learning_phase']} -> {short_metrics['learning_phase']}"},
            "confidence_profile": {"from": long_metrics["confidence_profile"], "to": short_metrics["confidence_profile"], "trend": profile_transition_label(str(long_metrics["confidence_profile"]), str(short_metrics["confidence_profile"])), "transition": f"{long_metrics['confidence_profile']} -> {short_metrics['confidence_profile']}"},
        }

        return {
            "selected_windows": selected_windows,
            "comparison": {
                "short_window_days": short_window,
                "long_window_days": long_window,
                "short_learning_phase": short_metrics["learning_phase"],
                "long_learning_phase": long_metrics["learning_phase"],
                "short_confidence_profile": short_metrics["confidence_profile"],
                "long_confidence_profile": long_metrics["confidence_profile"],
            },
            "windows": {f"last_{days}_days": metrics_by_window[days] for days in selected_windows},
            "trend_summary": trend_summary,
        }