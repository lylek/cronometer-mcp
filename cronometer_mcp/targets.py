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
  Weight-loss rates the website rates "not recommended" for your baseline
  expenditure need an acknowledgement, recorded in
  ``weight.goal.aggressive.rate.ack``.
- Macro settings (the default macro targets, used on days with no template
  from the Daily Target Editor or the weekly schedule): ``macroIndex`` picks
  Fixed Targets (0), Ratios (6) or the Keto Calculator (5). Ratios are
  ``macroProtein``/``macroCarbs``/``macroLipids`` (normalised to 100%); fixed
  targets are ``targets.fixed.*`` in grams, with separate net- and total-carb
  amounts. ``targets.macros.netcarbs`` tracks net instead of total carbs, and
  the four ``targets.macros.{fiber,sugaralcohol,allulose,fructose}`` flags,
  when "false", subtract that nutrient from total carbs to give net carbs.
  The kcal/grams and %/ratio switches next to these only change how the
  website displays or takes input, so they are left alone.
- Keto Calculator: ``macro.keto.level`` names a program (Rigorous,
  Moderate, Relaxed or Custom; Keto Fast is offered only to members of the
  Mercola study). Each sets protein in g per kg of
  lean body mass, a carb limit in grams and whether exercise earns extra
  carbs (1 g per 50 kcal burned); Custom takes them from
  ``macro.keto.protein``, ``macro.keto.carb`` and ``macro.keto.bonus``.
  Lean body mass uses the latest logged body fat, else an estimate from
  height and weight. Pregnancy or lactation adds 25 g protein. Fat is the
  rest of the energy target at 4/4/9 kcal per gram; Keto Fast replaces the
  energy target with 3.5 kcal per lb of lean body mass. (Members of the
  Mercola study get lower protein amounts; that isn't modelled.)
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
AGGRESSIVE_RATE_ACK = "weight.goal.aggressive.rate.ack"
MACRO_MODE = "macroIndex"
RATIO_KEYS = {"protein": "macroProtein", "carbs": "macroCarbs", "fat": "macroLipids"}
FIXED_KEYS = {"protein": "targets.fixed.protein", "net_carbs": "targets.fixed.net.carbs",
              "total_carbs": "targets.fixed.total.carbs", "fat": "targets.fixed.fats"}
NET_CARBS = "targets.macros.netcarbs"
# Nutrients that can be subtracted from total carbs to give net carbs: the
# preference is "false" when subtracted, and its default when unset.
NET_CARB_EXCLUSIONS = {
    "fiber": ("targets.macros.fiber", "false"),
    "sugar_alcohols": ("targets.macros.sugaralcohol", "false"),
    "allulose": ("targets.macros.allulose", "true"),
    "fructose": ("targets.macros.fructose", "true"),
}
MACRO_SETTING_MODES = {0: "Fixed Targets", 6: "Ratios", 5: "Keto Calculator"}
KETO_PROGRAM = "macro.keto.level"
KETO_PROTEIN = "macro.keto.protein"
KETO_CARBS = "macro.keto.carb"
KETO_BONUS = "macro.keto.bonus"
# Program: (protein g per kg lean body mass, carb limit g, exercise carb bonus).
# "Custom" uses the KETO_* preferences, with Moderate's values as defaults.
KETO_PROGRAMS = {
    "Keto Fast": (0.5, 10, False),
    "Rigorous": (1.0, 20, False),
    "Moderate": (1.5, 40, True),
    "Relaxed": (2.5, 50, True),
}
KETO_CUSTOM = "Custom"
KETO_DEFAULT = "Moderate"

LB_KG = 0.45359237
# Daily kcal per 1 lb/week of weight change, as the web client computes it.
KCAL_PER_LB_WEEK = 453.59237 * 7.7 / 7
# The website's goal-rate options (GET /api/v3/goal-rate-options): 0.25 to
# 2 lb/week, or 0.125 to 1 kg/week, always stored in lb/week. It converts kg
# with this factor.
LB_PER_KG = 2.20462
MAX_RATE_LB = 2.0
MAX_RATE_KG = 1.0

# Goal-rate review for weight loss. The baseline expenditure (BMR + baseline
# activity) puts you in a tier; each tier lists (minimum lb/week, rating)
# from the strictest. Thresholds are [female/other, male].
_RATE_TIER_LIMITS = [(1325, 1625), (1450, 1750), (1575, 1875), (1700, 2000), (1825, 2125)]
_RATE_TIERS = {
    1: [(0, "NOT_RECOMMENDED")],
    2: [(0.5, "NOT_RECOMMENDED")],
    3: [(0.75, "NOT_RECOMMENDED")],
    4: [(1, "NOT_RECOMMENDED")],
    5: [(1.25, "NOT_RECOMMENDED"), (1, "CAUTIOUS")],
    6: [(1.5, "NOT_RECOMMENDED"), (1.25, "CAUTIOUS")],
}

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
        "macro_settings": macro_settings_summary(user, day),
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
        "rate_review": rate_review(round(bmr) + round(baseline), p["sex"], rate),
        "low_rate_warning_acknowledged": s.get(AGGRESSIVE_RATE_ACK) == "true",
    }


def goal_weight_kg(user: dict) -> float | None:
    """The saved goal weight (kg), unrounded; None when maintaining."""
    return _pref_float(diary.settings(user), GOAL_WEIGHT_KG)


def current_weight_kg(user: dict, day: date | None = None) -> float:
    return diary.weight_on(user, day or date.today())[0]


def resolve_goal(current_kg: float, goal_kg: float | None, rate: float | None,
                 current_rate: float, max_rate_lb: float = MAX_RATE_LB
                 ) -> tuple[float, float | None]:
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
    if rate > max_rate_lb + 1e-9:
        raise ValueError(f"rate {rate:g} lb/week is above Cronometer's maximum "
                         f"({MAX_RATE_LB:g} lb or {MAX_RATE_KG:g} kg a week)")
    rate = round(rate, 7)
    return (rate if goal > current_kg else -rate), round(goal, 4)


def rate_review(baseline_kcal: float, sex: str | None, rate_lb_week: float) -> str:
    """The website's review of a weight-loss rate: SAFE, CAUTIOUS or
    NOT_RECOMMENDED (which needs an acknowledgement). Gains are SAFE."""
    if rate_lb_week >= 0:
        return "SAFE"
    col = 1 if sex == "Male" else 0
    tier = next((i + 1 for i, lim in enumerate(_RATE_TIER_LIMITS) if baseline_kcal < lim[col]), 6)
    for minimum, rating in _RATE_TIERS[tier]:
        if -rate_lb_week >= minimum:
            return rating
    return "SAFE"


def baseline_expenditure(user: dict, day: date | None = None) -> int:
    """BMR + baseline activity (kcal), as the website rounds them."""
    day = day or date.today()
    p = diary.profile(user, day)
    bmr = round(diary.bmr(user, day))
    mult = p["activity_multiplier"]
    return bmr + round(p["custom_activity_kcal"] if mult < 0 else bmr * mult)


def review_rate(user: dict, rate_lb_week: float, day: date | None = None) -> str:
    """rate_review for this user's baseline expenditure and sex."""
    sex = diary.profile(user, day or date.today())["sex"]
    return rate_review(baseline_expenditure(user, day), sex, rate_lb_week)


def _flag(s: dict, key: str, default: str) -> bool:
    return s.get(key, default) == "true"


def macro_settings_summary(user: dict, day: date | None = None) -> dict:
    """The default macro targets from Macro & Energy Targets."""
    s = diary.settings(user)
    mode = int(_pref_float(s, MACRO_MODE) or 0)
    net = _flag(s, NET_CARBS, "true")
    ratios = {k: _pref_float(s, key) for k, key in RATIO_KEYS.items()}
    total = sum(v for v in ratios.values() if v) or None
    return {
        "mode": MACRO_SETTING_MODES.get(mode, f"mode {mode}"),
        "applies_to": ("days with no template from the Daily Target Editor or weekly schedule"
                       if _flag(s, "enable.macro.scheduler", "false") else "every day"),
        "ratios_percent": {k: (round(v / total * 100, 2) if v is not None and total else None)
                           for k, v in ratios.items()},
        # Both carb amounts are kept; the one matching track_carbs_as is used.
        "fixed_targets_g": {k: _pref_float(s, key) for k, key in FIXED_KEYS.items()},
        "track_carbs_as": "net" if net else "total",
        "net_carbs_subtract": [k for k, (key, d) in NET_CARB_EXCLUSIONS.items()
                               if not _flag(s, key, d)],
        "add_expenditure_above_baseline": _flag(s, INCLUDE_EXERCISE, "true"),
        "keto_calculator": keto_summary(user, day),
    }


def _java_round(x: float) -> int:
    return math.floor(x + 0.5)


KETO_MERCOLA_ONLY = ("Keto Fast",)


def resolve_keto_program(value: str) -> str:
    """A program the website offers, ignoring case, spaces and underscores."""
    def key(v: str) -> str:
        return v.lower().replace(" ", "").replace("_", "")
    names = tuple(n for n in (*KETO_PROGRAMS, KETO_CUSTOM) if n not in KETO_MERCOLA_ONLY)
    for name in names:
        if key(name) == key(value):
            return name
    raise ValueError(f"unknown keto program {value!r}; use one of {list(names)}")


def keto_values(s: dict) -> tuple[str, float, float, bool]:
    """The saved program and its (protein g/kg, carb limit g, bonus)."""
    program = s.get(KETO_PROGRAM) or KETO_DEFAULT
    if program in KETO_PROGRAMS:
        return (program, *KETO_PROGRAMS[program])
    protein, carbs, bonus = KETO_PROGRAMS[KETO_DEFAULT]
    if program != KETO_CUSTOM:  # unknown name: the website falls back to Moderate
        return (KETO_DEFAULT, protein, carbs, bonus)
    p = _pref_float(s, KETO_PROTEIN)
    c = _pref_float(s, KETO_CARBS)
    return (KETO_CUSTOM, protein if p is None else p, carbs if c is None else c,
            _flag(s, KETO_BONUS, "true"))


def estimated_body_fat(weight_kg: float, height_cm: float, sex: str | None) -> float:
    """The website's body-fat estimate (a fraction) when none is logged."""
    h = height_cm / 100
    lean_guess = max(0.0, weight_kg - 18.75 * h * h)
    return max(0.05, lean_guess / weight_kg) + (0.03 if sex == "Female" else 0)


def energy_target(user: dict, day: date | None = None) -> int:
    """The energy target before exercise: a custom target, else BMR +
    baseline activity + the weight-goal adjustment."""
    custom = _pref_float(diary.settings(user), CUSTOM_ENERGY)
    if custom is not None:
        return round(custom)
    return weight_goal_summary(user, day)["energy_target_kcal"]


def keto_summary(user: dict, day: date | None = None) -> dict:
    """The Keto Calculator's settings and the targets they give (for a day
    with no exercise)."""
    day = day or date.today()
    s = diary.settings(user)
    p = diary.profile(user, day)
    program, protein_per_kg, carb_limit, bonus = keto_values(s)
    weight = min(p["weight_kg"], 2000)
    fat_pct, fat_day = body_fat_on(user, day)
    if fat_pct is not None:
        fraction, source = fat_pct / 100, f"logged {fat_day}"
    else:
        fraction = estimated_body_fat(p["weight_kg"], p["height_cm"], p["sex"])
        source = "estimated from height and weight"
    lean = weight * (1 - fraction)
    protein = protein_per_kg * lean
    if p["sex"] != "Male" and p["reproductive_status"] in ("Pregnant", "Lactating"):
        protein += 25
    protein = _java_round(protein)
    carbs = _java_round(carb_limit)
    energy = (3.5 * lean * 2.204622620001839 if program == "Keto Fast"
              else energy_target(user, day))
    fat = max(0.0, (energy - (protein + carbs) * 4) / 9)
    return {
        "program": program,
        "protein_g_per_kg_lean_mass": protein_per_kg,
        "carb_limit_g": carb_limit,
        "exercise_carb_bonus": bonus,
        "body_fat_percent": round(fraction * 100, 1),
        "body_fat_source": source,
        "lean_body_mass_kg": round(lean, 1),
        "energy_target_kcal": round(energy),
        "targets_g": {"protein": protein, "carbs": carbs, "fat": round(fat, 1)},
        "note": ("carbs are " + ("net" if _flag(s, NET_CARBS, "true") else "total") + " carbs"
                 + ("; exercise adds 1 g carbs per 50 kcal burned" if bonus else "")
                 + "; fat fills the rest of the energy target, so it rises with exercise"
                 " when that is added to the energy target"),
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
