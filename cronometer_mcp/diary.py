"""A day's diary (getDayInfo) and the web app's Energy Expenditure panel.

Cronometer's server sends each day as a list of entries (servings, exercises,
biometrics, notes) and leaves the arithmetic to the browser. The functions
here repeat that arithmetic, ported from the compiled web client, so the
numbers match what the Diary page shows:

- Day totals: each serving contributes every nutrient its food carries,
  scaled by the serving's measure (``serving_nutrient``). Exercises add their
  calories, split into tracker activity (a fitness tracker's all-day burn,
  e.g. "Daily Activity (Garmin)") and exercise (workouts and manual entries),
  plus exercise minutes.
- Energy expenditure: BMR + adjusted baseline activity + tracker activity +
  exercise + thermic effect of food (TEF). See ``energy_expenditure``.
"""

from __future__ import annotations

import json
from datetime import date

from . import custom_foods as cf
from . import gwt_foods as g

# Pseudo-nutrient ids the web client uses in a day's totals.
EXERCISE_KCAL = -208  # non-tracker exercise calories (negative = burned)
EXERCISE_MINUTES = -2208  # minutes of non-tracker exercise
TRACKER_KCAL = -3208  # tracker activity calories (negative = burned)
PROTEIN_KCAL, FAT_KCAL, NET_CARB_KCAL, ALCOHOL_KCAL = -203, -204, -205, -221

# Entries with these names are a tracker's all-day activity, not a workout.
TRACKER_ACTIVITY_NAMES = frozenset({
    "Fitbit Activity",
    "Daily Activity (Garmin)",
    "Daily Activity (Oura)",
    "Daily Activity (Withings)",
    "Daily Activity (Polar)",
    "Daily Activity (Suunto)",
    "Daily Activity (Google Fit)",
    "Active Energy (Google Fit)",
    "Active Energy (Health Connect)",
    "Google Health Activity",
})
_APPLE_HEALTH_ACTIVITY = frozenset({
    "Active Energy (Apple Health)",
    "Active Energy Balance (Apple Health)",
    "Resting Energy (Apple Health)",
})

WAKING_MINUTES = 960  # baseline activity is spread over 16 waking hours
ACTIVITY_LEVELS = {0.2: "Sedentary", 0.375: "Lightly Active", 0.5: "Moderately Active",
                   0.9: "Very Active"}
_MS_PER_YEAR = 31556930400  # the web client's year length for ages


def day_obj(d: date) -> dict:
    return {"$class": g.DAY, "day": d.day, "month": d.month, "year": d.year}


def to_date(day: dict | None) -> date | None:
    return date(day["year"], day["month"], day["day"]) if day else None


def fmt_time(t: dict | None) -> str | None:
    return f"{t['hour']:02d}:{t['minute']:02d}:{t['second']:02d}" if t else None


def entries(day_info: dict, kind: str | None = None) -> list[dict]:
    out = cf.items(day_info.get("entries")) if day_info else []
    return [e for e in out if kind is None or g.short_class(e) == kind]


def diary_group(entry: dict) -> int:
    """0-based diary group slot (DG01 = 0) packed into an entry's order."""
    return (entry.get("order") or 0) >> 16


# --- exercises --------------------------------------------------------------


def is_tracker_activity(ex: dict) -> bool:
    """True for a tracker's all-day activity entry (the web client's rule)."""
    name = ex.get("name")
    if name is None:
        return False
    if ex.get("source") == "Apple Health":
        return (ex.get("external_id") or "").startswith("activeEnergy") or name in _APPLE_HEALTH_ACTIVITY
    return name in TRACKER_ACTIVITY_NAMES


# Which entry name counts as tracker activity for each source.
_TRACKER_SOURCES = {
    "Fitbit": ("Fitbit Activity",),
    "Google Health": ("Google Health Activity",),
    "Garmin": ("Daily Activity (Garmin)",),
    "Oura": ("Daily Activity (Oura)",),
    "Withings": ("Daily Activity (Withings)",),
    "Polar": ("Daily Activity (Polar)",),
    "Suunto": ("Daily Activity (Suunto)",),
    "Google Fit": ("Daily Activity (Google Fit)", "Active Energy (Google Fit)"),
    "Health Connect": ("Active Energy (Health Connect)",),
}


def tracker_kcal(ex: dict) -> float:
    """The part of an exercise's (negative) calories that is tracker activity.

    Like the web client, this needs the entry's source to match its name: a
    "Daily Activity (Garmin)" entered by hand counts as plain exercise.
    """
    source, kcal = ex.get("source"), ex.get("calories") or 0.0
    if source == "Apple Health":
        return kcal if is_tracker_activity(ex) else 0.0
    return kcal if ex.get("name") in _TRACKER_SOURCES.get(source, ()) else 0.0


def summarize_exercise(ex: dict, group_names: dict[int, str]) -> dict:
    meta = None
    if ex.get("metadata"):
        try:
            meta = json.loads(ex["metadata"])
        except ValueError:
            meta = ex["metadata"]
    grp = diary_group(ex)
    return {
        "date": str(to_date(ex.get("day"))),
        "time": fmt_time(ex.get("time")),
        "name": ex.get("name"),
        "minutes": ex.get("minutes"),
        "calories_burned": round(-(ex.get("calories") or 0.0), 2),
        "source": ex.get("source") or "manual",
        "kind": "tracker_activity" if is_tracker_activity(ex) else "exercise",
        "diary_group": group_names.get(grp, grp),
        "exercise_type_id": ex.get("exercise_type_id") or None,
        "entry_id": g.long_from_gwt(ex["id"]) if isinstance(ex.get("id"), str) else ex.get("id"),
        "metadata": meta,
    }


# --- biometrics ---------------------------------------------------------------


def summarize_biometric(b: dict, metrics: dict[int, dict]) -> dict:
    """A biometric entry with its metric name; values are in the base unit."""
    metric = metrics.get(b.get("metric_id"), {})
    units = metric.get("units", [])
    # Values are stored in the base unit (conversion factor 1).
    base = next((u[1] for u in units if u[2] == 1.0), units[0][1] if units else None)
    meta = None
    if b.get("metadata"):
        try:
            meta = json.loads(b["metadata"])
        except ValueError:
            meta = b["metadata"]
    name = metric.get("name")
    if isinstance(meta, dict) and meta.get("display_name"):
        name = meta["display_name"]  # e.g. Garmin "Body Battery" stored as Recovery
    out = {
        "date": str(to_date(b.get("day"))),
        "time": fmt_time(b.get("time")),
        "metric": name,
        "metric_id": b.get("metric_id"),
        "value": b.get("value"),
        "unit": base,
        "source": b.get("source") or "manual",
        "entry_id": g.long_from_gwt(b["id"]) if isinstance(b.get("id"), str) else b.get("id"),
    }
    stats = b.get("sample_stats")
    if stats:
        out["samples"] = {
            "count": stats["count"], "min": stats["min"], "max": stats["max"],
            "mean": round(stats["mean"], 3),
            **{st["label"]: st["value"] for st in cf.items(stats.get("stats")) if st.get("label")},
        }
    if isinstance(meta, dict) and "stage_summary" in meta:
        out["sleep"] = meta
    return out


# --- servings and day totals ------------------------------------------------


def serving_nutrient(food: dict, amount: float, measure_id: int, value: float) -> float:
    """One nutrient's amount in a serving or ingredient (web client's math).

    ``value`` is the food's stored amount (per 100 g, or per whole recipe for
    recipe-relative measures). ``amount`` is grams, or a count of the measure
    when it is recipe-relative.
    """
    m = next((x for x in cf.measures(food) if x["id"] == measure_id), None)
    if m is None and cf.whole_recipe_basis(food):
        m = {"measure_type": g.GwtEnum(g.MEASURE_TYPE, cf.RECIPE_RELATIVE),
             "weight": cf.total_weight_g(food) or 0.0}
    if m is None:
        return value * amount / 100.0
    weight = m.get("weight") or 0.0
    if cf.is_relative(m):
        return value / weight * amount if weight else 0.0
    if weight == 0:
        # Weight-type measure with no weight: the amount is a plain multiplier.
        return value * amount if m.get("measure_type") and m["measure_type"].ordinal == 0 else 0.0
    return value * amount / 100.0


def day_totals(day_entries: list[dict], foods: dict[int, dict]) -> dict[int, float]:
    """Nutrient totals for a day, keyed by nutrient id (incl. pseudo ids)."""
    totals: dict[int, float] = {}

    def add(nid: int, v: float) -> None:
        if v == v and v not in (float("inf"), float("-inf")):
            totals[nid] = totals.get(nid, 0.0) + v

    for e in day_entries:
        kind = g.short_class(e)
        if kind == "Serving":
            food = foods.get(e["food_id"])
            if food is None:
                continue
            for nid, value in cf.nutrient_values(food).items():
                add(nid, serving_nutrient(food, e["amount"], e["measure_id"], value))
        elif kind == "Exercise":
            kcal = e.get("calories") or 0.0
            if is_tracker_activity(e):
                tracker = tracker_kcal(e)
                kcal -= tracker
                add(TRACKER_KCAL, tracker)
            add(EXERCISE_KCAL, kcal)
            if not is_tracker_activity(e):
                add(EXERCISE_MINUTES, e.get("minutes") or 0.0)
    return totals


# --- profile and BMR ----------------------------------------------------------


def settings(user: dict) -> dict[str, str]:
    prefs = user.get("settings")
    return dict(prefs["entries"]) if prefs else {}


def _float(v, default: float) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def weight_on(user: dict, day: date) -> tuple[float, str]:
    """Body weight (kg) the web client uses for a day, and where it came from.

    The last weight logged on or before the day; failing that the earliest
    one; failing that the profile weight (default 70 kg).
    """
    hist = user.get("weight_history")
    points = sorted(
        ((to_date(p["day"]), p.get("time") and fmt_time(p["time"]) or "", p["value"])
         for p in cf.items(hist and hist.get("points"))),
        key=lambda p: (p[0], p[1]),
    )
    chosen = None
    for d, _, v in points:
        if d <= day:
            chosen = (v, f"weight logged {d}")
        else:
            break
    if chosen is None and points:
        chosen = (points[0][2], f"earliest logged weight ({points[0][0]})")
    if chosen is None:
        chosen = (_float(settings(user).get("weightInKG"), 70.0), "profile weight")
    return min(chosen[0], 2000.0), chosen[1]


def age_on(user: dict, day: date) -> int:
    bday = to_date(user.get("birthday"))
    if bday is None:
        return 0
    ms = (day - bday).days * 86400000
    return min(max(0, int(ms / _MS_PER_YEAR)), 120)


def profile(user: dict, day: date | None = None) -> dict:
    """The profile fields and settings that drive energy expenditure."""
    day = day or date.today()
    s = settings(user)
    weight, weight_src = weight_on(user, day)
    activity = _float(s.get("calories.activity"), 0.2)
    gender = user["gender"].name if user.get("gender") else None
    repro = user["reproductive_status"].name if user.get("reproductive_status") else None
    bday = to_date(user.get("birthday"))
    return {
        "sex": gender,
        "birthday": str(bday) if bday else None,
        "age": age_on(user, day),
        "height_cm": _float(s.get("heightInCM"), 160.0),
        "weight_kg": round(weight, 3),
        "weight_source": weight_src,
        "reproductive_status": repro,
        "bmr_override": round(_float(s["bmr"], 0)) if s.get("bmr") else None,
        "activity_multiplier": activity,
        "activity_level": ACTIVITY_LEVELS.get(activity, "Custom" if activity < 0 else str(activity)),
        "custom_activity_kcal": _float(s.get("calories.activity.custom"), 0.0) if activity < 0 else None,
        "thermic_effect_of_food": s.get("use.thermic.effect.food", "false") == "true",
    }


def bmr(user: dict, day: date) -> float:
    """BMR for a day: the profile's custom BMR, else Mifflin-St Jeor."""
    s = settings(user)
    if s.get("bmr"):
        return float(round(_float(s["bmr"], 0)))
    p = profile(user, day)
    base = 10 * p["weight_kg"] + 6.25 * p["height_cm"] - 5 * p["age"]
    if p["sex"] == "Male":
        return base + 5
    # Female and Other use the female equation, +300 kcal if pregnant or lactating.
    return base - 161 + (300 if p["reproductive_status"] in ("Pregnant", "Lactating") else 0)


# --- energy expenditure -------------------------------------------------------


def _r1(x: float) -> float:
    return round(x * 10) / 10


def energy_expenditure(totals: dict[int, float], user: dict, day: date) -> dict:
    """The Diary page's Energy Expenditure breakdown for one day."""
    s = settings(user)
    tef = 0.0
    if s.get("use.thermic.effect.food", "false") == "true":
        tef = (totals.get(NET_CARB_KCAL, 0.0) * 0.075 + totals.get(FAT_KCAL, 0.0) * 0.015
               + totals.get(PROTEIN_KCAL, 0.0) * 0.25 + totals.get(ALCOHOL_KCAL, 0.0) * 0.16)
    tracker = abs(totals.get(TRACKER_KCAL, 0.0))
    exercise = _r1(abs(totals.get(EXERCISE_KCAL, 0.0)))
    minutes = totals.get(EXERCISE_MINUTES, 0.0)

    raw_bmr = bmr(user, day)
    activity = _float(s.get("calories.activity"), 0.2)
    baseline = _float(s.get("calories.activity.custom"), 0.0) if activity < 0 else raw_bmr * activity
    factor = 1.0
    if minutes > WAKING_MINUTES:
        factor = 0.0
    elif minutes > 0:
        factor = min(1.0, (WAKING_MINUTES - minutes) / WAKING_MINUTES)
    adjusted = baseline * factor
    adjusted = adjusted - tracker if adjusted > tracker else 0.0

    bmr_shown = _r1(raw_bmr)
    total = bmr_shown + tracker + exercise + tef + adjusted
    consumed = round(totals.get(208, 0.0) * 100) / 100
    return {
        "date": str(day),
        "consumed_kcal": round(consumed, 1),
        "burned_kcal": round(total, 1),
        "balance_kcal": round(consumed - total, 1),
        "breakdown": {
            "bmr": round(bmr_shown, 1),
            "baseline_activity": round(baseline, 1),
            "adjusted_baseline_activity": round(adjusted, 1),
            "tracker_activity": round(tracker, 1),
            "exercise": round(exercise, 1),
            "thermic_effect_of_food": round(tef, 1),
        },
        "exercise_minutes": round(minutes, 1),
    }
