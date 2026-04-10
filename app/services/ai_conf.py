import os
from types import SimpleNamespace
from typing import Any, Dict, Optional


_AI_CONF_PRESETS: Dict[str, Dict[str, float]] = {
    "balanced": {
        "samples_target": 300,
        "risk_weight": 0.7,
        "failsafe_weight": 1.3,
        "weight_maturity": 45,
        "weight_stability": 45,
        "weight_alerts": 10,
        "band_excellent": 85,
        "band_good": 70,
        "band_moderate": 50,
    },
    "conservative": {
        "samples_target": 450,
        "risk_weight": 0.9,
        "failsafe_weight": 1.8,
        "weight_maturity": 35,
        "weight_stability": 55,
        "weight_alerts": 10,
        "band_excellent": 90,
        "band_good": 78,
        "band_moderate": 60,
    },
    "aggressive": {
        "samples_target": 220,
        "risk_weight": 0.55,
        "failsafe_weight": 1.0,
        "weight_maturity": 55,
        "weight_stability": 35,
        "weight_alerts": 10,
        "band_excellent": 82,
        "band_good": 66,
        "band_moderate": 45,
    },
}


def build_ai_conf_helpers() -> SimpleNamespace:
    profile_setting = os.getenv("AI_CONF_PROFILE", "balanced").strip().lower()
    if profile_setting not in {*_AI_CONF_PRESETS.keys(), "auto"}:
        profile_setting = "balanced"

    def env_int(name: str, default: int, minimum: Optional[int] = None, maximum: Optional[int] = None) -> int:
        raw = os.getenv(name, str(default)).strip()
        try:
            value = int(raw)
        except ValueError:
            value = default
        if minimum is not None:
            value = max(minimum, value)
        if maximum is not None:
            value = min(maximum, value)
        return value

    def env_float(name: str, default: float, minimum: Optional[float] = None, maximum: Optional[float] = None) -> float:
        raw = os.getenv(name, str(default)).strip().replace(",", ".")
        try:
            value = float(raw)
        except ValueError:
            value = default
        if minimum is not None:
            value = max(minimum, value)
        if maximum is not None:
            value = min(maximum, value)
        return value

    def resolve_auto_ai_conf_profile(total_samples: int) -> str:
        if total_samples >= 300:
            return "conservative"
        if total_samples >= 30:
            return "balanced"
        return "aggressive"

    def get_ai_conf_config(total_samples: int) -> Dict[str, Any]:
        selected_profile = profile_setting
        if selected_profile == "auto":
            selected_profile = resolve_auto_ai_conf_profile(total_samples)

        defaults = _AI_CONF_PRESETS[selected_profile]
        return {
            "profile_setting": profile_setting,
            "profile_effective": selected_profile,
            "samples_target": env_int("AI_CONF_SAMPLES_TARGET", int(defaults["samples_target"]), minimum=50, maximum=5000),
            "risk_weight": env_float("AI_CONF_RISK_WEIGHT", float(defaults["risk_weight"]), minimum=0.0, maximum=5.0),
            "failsafe_weight": env_float("AI_CONF_FAILSAFE_WEIGHT", float(defaults["failsafe_weight"]), minimum=0.0, maximum=5.0),
            "weight_maturity": env_float("AI_CONF_WEIGHT_MATURITY", float(defaults["weight_maturity"]), minimum=0.0, maximum=100.0),
            "weight_stability": env_float("AI_CONF_WEIGHT_STABILITY", float(defaults["weight_stability"]), minimum=0.0, maximum=100.0),
            "weight_alerts": env_float("AI_CONF_WEIGHT_ALERTS", float(defaults["weight_alerts"]), minimum=0.0, maximum=100.0),
            "band_excellent": env_int("AI_CONF_BAND_EXCELLENT", int(defaults["band_excellent"]), minimum=1, maximum=100),
            "band_good": env_int("AI_CONF_BAND_GOOD", int(defaults["band_good"]), minimum=1, maximum=100),
            "band_moderate": env_int("AI_CONF_BAND_MODERATE", int(defaults["band_moderate"]), minimum=1, maximum=100),
        }

    return SimpleNamespace(get_ai_conf_config=get_ai_conf_config)