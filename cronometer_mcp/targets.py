"""Nutrient targets, profile and energy settings (the Targets + Profile page).

Where each setting lives in Cronometer:

- Nutrient targets: read from REST ``GET /api/v3/user/{id}/targets`` as
  ``{id, min, max, vis, custom}``; written one at a time with the GWT
  ``setTarget`` RPC. ``custom=False`` means Cronometer's DRI default for the
  profile; ``initTargets`` recomputes those defaults (the web app calls it
  after any profile change).
- Sex, birthday and pregnancy/lactation: REST ``PUT /api/v3/user/{id}/profile``
  with ``{birthdate, gender, reproductiveStatus}``.
- Height, custom BMR, activity level, TEF and a custom energy target: user
  preferences, written with the GWT ``setUserPreference(key, value)`` RPC.
  A null value removes a preference.
- Current weight and body fat are biometrics (add_biometric).
"""

from __future__ import annotations

from datetime import date

from . import diary
from . import gwt_foods as g
from . import nutrients

# Preference keys.
HEIGHT_CM = "heightInCM"
BMR = "bmr"
ACTIVITY = "calories.activity"
ACTIVITY_CUSTOM = "calories.activity.custom"
TEF = "use.thermic.effect.food"
CUSTOM_ENERGY = "targets.custom.energy.target"
CUSTOM_ENERGY_MAX = "targets.custom.energy.target.max"
INCLUDE_EXERCISE = "targets.include.exercises"

# Activity levels as BMR multipliers; a negative multiplier means a custom
# daily kcal amount in calories.activity.custom.
ACTIVITY_LEVELS = {name: mult for mult, name in diary.ACTIVITY_LEVELS.items()}
CUSTOM_ACTIVITY = -1

# MacroTargetTemplate.macro_mode: how protein/carbs/fat are expressed.
MACRO_MODES = {0: "grams", 6: "percent of energy"}

SEXES = ("Male", "Female")
REPRODUCTIVE_STATUSES = ("Normal", "Pregnant", "Lactating")


def summarize_target(t: dict) -> dict:
    """One REST target row, with the nutrient's name and unit."""
    nid = t["id"]
    return {
        "nutrient_id": nid,
        "nutrient": nutrients.name(nid),
        "unit": nutrients.unit(nid),
        "min": t.get("min"),
        "max": t.get("max"),
        "visible": bool(t.get("vis")),
        "custom": bool(t.get("custom")),
    }


def target_obj(nutrient_id: int, min_amount: float | None, max_amount: float | None,
               visible: bool, custom: bool) -> dict:
    """A GWT Target for setTarget. min/max are boxed Doubles, so floats."""
    return {
        "$class": g.TARGET,
        "custom": custom,
        "max": None if max_amount is None else float(max_amount),
        "min": None if min_amount is None else float(min_amount),
        "nutrient_id": nutrient_id,
        "visible": visible,
    }


def resolve_activity(level: str) -> float:
    """An activity multiplier from a level name ("Sedentary", "very active")."""
    want = level.strip().lower()
    for name, mult in ACTIVITY_LEVELS.items():
        if name.lower() == want:
            return mult
    raise ValueError(f"unknown activity level {level!r}; use one of {list(ACTIVITY_LEVELS)} "
                     "or set custom_activity_kcal")


def _choice(value: str, options: tuple[str, ...], what: str) -> str:
    for o in options:
        if o.lower() == value.strip().lower():
            return o
    raise ValueError(f"unknown {what} {value!r}; use one of {list(options)}")


def resolve_sex(value: str) -> str:
    return _choice(value, SEXES, "sex")


def resolve_reproductive_status(value: str) -> str:
    return _choice(value, REPRODUCTIVE_STATUSES, "reproductive status")


def body_fat_on(user: dict, day: date) -> tuple[float | None, str | None]:
    """Latest body fat % logged on or before the day, and its date."""
    hist = user.get("body_fat_history")
    points = hist["points"]["items"] if hist and hist.get("points") else []
    best = None
    for p in points:
        d = diary.to_date(p.get("day"))
        if d and d <= day and (best is None or d >= best[0]):
            best = (d, p["value"])
    return (best[1], str(best[0])) if best else (None, None)


def profile_summary(user: dict, day: date | None = None) -> dict:
    """Profile and energy settings as shown on the Targets + Profile page."""
    day = day or date.today()
    s = diary.settings(user)
    p = diary.profile(user, day)
    bmr = diary.bmr(user, day)
    mult = p["activity_multiplier"]
    baseline = p["custom_activity_kcal"] if mult < 0 else bmr * mult
    fat, fat_day = body_fat_on(user, day)
    custom_energy = s.get(CUSTOM_ENERGY)
    return {
        "sex": p["sex"],
        "birthday": p["birthday"],
        "age": p["age"],
        "reproductive_status": p["reproductive_status"],
        "height_cm": p["height_cm"],
        "weight_kg": p["weight_kg"],
        "weight_source": p["weight_source"],
        "body_fat_percent": fat,
        "body_fat_logged": fat_day,
        "display_units": {"height": s.get("heightUnit"), "weight": s.get("weightUnit")},
        "energy": {
            "bmr_kcal": round(bmr),
            "bmr_source": "custom" if p["bmr_override"] is not None else "Mifflin-St Jeor",
            "activity_level": "Custom" if mult < 0 else p["activity_level"],
            "activity_multiplier": None if mult < 0 else mult,
            "baseline_activity_kcal": round(baseline),
            "thermic_effect_of_food": p["thermic_effect_of_food"],
            "use_tracker_activity": s.get("use.tracker.activity", "true") == "true",
            "custom_energy_target_kcal": float(custom_energy) if custom_energy else None,
            "include_exercise_in_energy_target": s.get(INCLUDE_EXERCISE, "true") == "true",
        },
    }


def template_summary(t: dict) -> dict:
    """A MacroTargetTemplate as returned by getMacroTargetTemplates."""
    mode = t.get("macro_mode")
    return {
        "template_id": t.get("id"),
        "template_name": t.get("name"),
        "macro_units": MACRO_MODES.get(mode, f"mode {mode}"),
        "protein": t.get("protein"),
        "carbs": t.get("carbs"),
        "fat": t.get("fat"),
        "calories": t.get("calories"),
    }
