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
- Weight goal (the "Fixed Goal Plan"): preferences ``weightGoal``, the signed
  rate in lb/week whatever the display unit (negative = lose, 0 = maintain),
  and ``wgkg``, the goal weight in kg (absent when maintaining). The energy
  target is BMR + baseline activity + rate x 453.59237 g x 7.7 kcal/g / 7.
  A custom energy target or the beta Guided Goal Plan (``active.weight.goal``)
  takes the place of the weight goal.
- Current weight and body fat are biometrics (add_biometric).
"""

from __future__ import annotations

import math
from datetime import date, timedelta

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
WEIGHT_GOAL_RATE = "weightGoal"
GOAL_WEIGHT_KG = "wgkg"
GUIDED_GOAL = "active.weight.goal"

LB_KG = 0.45359237
# Daily kcal per 1 lb/week of weight change, as the web client computes it.
KCAL_PER_LB_WEEK = 453.59237 * 7.7 / 7
# The website's goal-rate stepper: 0.25 to 2 lb/week in steps of 0.25.
RATE_STEP_LB = 0.25
MAX_RATE_LB = 2.0

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
        "weight_goal": weight_goal_summary(user, day),
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


def _pref_float(s: dict, key: str) -> float | None:
    try:
        return float(s[key]) if s.get(key) not in (None, "") else None
    except ValueError:
        return None


def rate_kcal(rate_lb_week: float) -> int:
    """The daily energy-target adjustment for a weight-goal rate."""
    return math.floor(rate_lb_week * KCAL_PER_LB_WEEK + 0.5)  # Java Math.round


def weight_goal_summary(user: dict, day: date | None = None) -> dict:
    """The weight goal and the energy target it gives."""
    day = day or date.today()
    s = diary.settings(user)
    p = diary.profile(user, day)
    rate = _pref_float(s, WEIGHT_GOAL_RATE) or 0.0
    goal_kg = _pref_float(s, GOAL_WEIGHT_KG)
    weight = p["weight_kg"]
    bmr = diary.bmr(user, day)
    mult = p["activity_multiplier"]
    baseline = p["custom_activity_kcal"] if mult < 0 else round(bmr * mult)
    adjust = rate_kcal(rate)
    custom = _pref_float(s, CUSTOM_ENERGY)
    guided = bool(s.get(GUIDED_GOAL))
    pounds = s.get("weightUnit", "Pounds") != "Kilograms"
    goal = goal_kg if goal_kg is not None else weight
    forecast = None
    if rate and abs(goal - weight) >= 0.01 and (goal > weight) == (rate > 0):
        weeks = abs(goal - weight) / LB_KG / abs(rate)
        forecast = str(day + timedelta(days=math.floor(weeks * 7 + 0.5)))
    return {
        "goal": "Maintain Weight" if rate == 0 else ("Gain Weight" if rate > 0 else "Lose Weight"),
        "in_effect": not guided and custom is None,
        "replaced_by": ("Guided Goal Plan" if guided else
                        "custom energy target" if custom is not None else None),
        "current_weight_kg": round(weight, 2),
        "current_weight_lb": round(weight / LB_KG, 2),
        "goal_weight_kg": round(goal, 2),
        "goal_weight_lb": round(goal / LB_KG, 2),
        "rate_lb_per_week": rate,
        "rate_kg_per_week": round(rate * LB_KG, 3),
        "display_unit": "lb" if pounds else "kg",
        "daily_kcal_adjustment": adjust,
        "energy_target_kcal": round(bmr) + round(baseline) + adjust,
        "forecast_date": forecast,
    }


def goal_weight_kg(user: dict) -> float | None:
    """The saved goal weight (kg), unrounded; None when maintaining."""
    return _pref_float(diary.settings(user), GOAL_WEIGHT_KG)


def current_weight_kg(user: dict, day: date | None = None) -> float:
    return diary.weight_on(user, day or date.today())[0]


def resolve_goal(current_kg: float, goal_kg: float | None, rate: float | None,
                 current_rate: float) -> tuple[float, float | None]:
    """The weightGoal rate (lb/week, signed) and wgkg to save.

    rate is a positive lb/week amount (None keeps the current rate); its sign
    comes from whether the goal weight is above or below the current weight.
    A goal weight within 0.01 kg of the current weight means maintain: rate 0
    and no goal weight, as the website saves it.
    """
    goal = current_kg if goal_kg is None else goal_kg
    if abs(goal - current_kg) < 0.01:
        return 0.0, None
    if rate is None:
        rate = abs(current_rate)
        if rate == 0:
            raise ValueError("give a rate per week to reach a goal weight")
    if rate <= 0:
        raise ValueError("rate must be a positive amount per week; the goal weight sets "
                         "the direction (to maintain, use maintain=True)")
    if rate > MAX_RATE_LB + 1e-9:
        raise ValueError(f"rate {rate:g} lb/week is above Cronometer's maximum of {MAX_RATE_LB:g}")
    rate = round(rate, 3)
    return (rate if goal > current_kg else -rate), round(goal, 4)


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
