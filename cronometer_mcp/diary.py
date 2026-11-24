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
from . import nutrients as nu

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
ACTIVITY_LEVELS = {0.0: "None", 0.2: "Sedentary", 0.375: "Lightly Active",
                   0.5: "Moderately Active", 0.9: "Very Active"}
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


# --- entry order --------------------------------------------------------------
# An entry's ``order`` packs its diary group slot (high 16 bits) and its
# 1-based position within the group (low 16 bits). After a drag, the web
# client renumbers every group in display order and sends the entries whose
# order changed to ``updateOrders``.


def _position(entry: dict) -> int:
    return (entry.get("order") or 0) & 0xFFFF


def group_layout(day_info: dict) -> dict[int, list[dict]]:
    """A day's entries by diary group slot, in display order."""
    out: dict[int, list[dict]] = {}
    for e in sorted(entries(day_info), key=lambda e: e.get("order") or 0):
        out.setdefault(diary_group(e), []).append(e)
    return out


def entry_label(e: dict, foods: dict[int, dict], metrics: dict[int, dict]) -> str:
    """A short description of a diary entry."""
    kind = g.short_class(e)
    if kind == "Serving":
        food = foods.get(e.get("food_id"))
        return cf.food_name(food) if food else f"food {e.get('food_id')}"
    if kind == "Exercise":
        return f"{e.get('name')} ({round(e.get('minutes') or 0)} min)"
    if kind == "Note":
        text = (e.get("text") or "").strip().replace("\n", " ")
        return text if len(text) <= 80 else text[:77] + "..."
    if "Biometric" in kind:
        b = summarize_biometric(e, metrics)
        value = b["value"]
        if isinstance(value, float):
            value = round(value, 2)
        return f"{b['metric']}: {value} {b['unit'] or ''}".strip()
    return kind


def reorder_group(day_info: dict, slot: int, order: list) -> list[dict]:
    """Put the entries of one diary group in a new order and return copies
    of the entries whose ``order`` changes, renumbered as the web client does.

    ``order`` lists every entry of the group once, by entry id (the GWT id
    string) or by current 1-based position in the group. An entry id from
    another group moves that entry into this one.
    """
    layout = group_layout(day_info)
    current = layout.get(slot, [])
    by_id = {e["id"]: e for e in entries(day_info)}
    picked: list[dict] = []
    for key in order:
        if isinstance(key, bool):
            raise ValueError(f"bad entry {key!r}")
        if isinstance(key, str) and key in by_id:
            picked.append(by_id[key])
        elif isinstance(key, int) or (isinstance(key, str) and key.isdigit()):
            pos = int(key)
            if not 1 <= pos <= len(current):
                raise ValueError(f"no entry at position {pos}; the group has {len(current)}")
            picked.append(current[pos - 1])
        else:
            raise ValueError(f"no entry with entry_id {key!r} on this day")
    ids = [e["id"] for e in picked]
    if len(set(ids)) != len(ids):
        raise ValueError("an entry is listed twice")
    missing = [e["id"] for e in current if e["id"] not in set(ids)]
    if missing:
        raise ValueError(f"list every entry of the group in its new order; missing {missing} "
                         "(to move an entry to another group, reorder that group instead)")
    moved = set(ids)
    for other, rows in layout.items():
        if other != slot:
            layout[other] = [e for e in rows if e["id"] not in moved]
    layout[slot] = picked
    changed = []
    for grp, rows in layout.items():
        for i, e in enumerate(rows, 1):
            new = (grp << 16) | i
            if (e.get("order") or 0) != new:
                changed.append(dict(e, order=new))
    return changed


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
        "minutes": round(ex.get("minutes") or 0.0, 2),
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
    """A biometric entry with its metric name; the value is in the entry's own
    unit (unit_id), e.g. a weight logged in lbs is stored in lbs."""
    metric = metrics.get(b.get("metric_id"), {})
    units = metric.get("units", [])
    base = next((u[1] for u in units if u[0] == b.get("unit_id")), None)
    if base is None:  # unknown unit id: fall back to the base unit (factor 1)
        base = next((u[1] for u in units if u[2] == 1.0), units[0][1] if units else None)
    if base == "hr:min":
        base = "hours"  # a display format; the stored value is decimal hours
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
        meta = dict(meta)
        if isinstance(meta["stage_summary"], dict):
            meta["stage_summary"] = {
                k: round(v, 3) if isinstance(v, float) else v
                for k, v in meta["stage_summary"].items()
            }
        if isinstance(meta.get("time_in_bed"), float):
            meta["time_in_bed"] = round(meta["time_in_bed"], 3)
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


# --- the diary as read by get_diary -------------------------------------------

# get_diary's default nutrients: energy and the macros.
MACRO_IDS = (208, 203, 205, -1205, 291, 269, 204, 606)

# The nutrients Cronometer's daily summary export reports, in its column
# order. Some foods carry others (sucrose, energy in kJ...) that the site
# doesn't total; those are available by asking for them.
REPORT_IDS = (
    208, 221, 262, 10012, 246, 255, 404, 405, 406, 410, 415, 418, 417, 320, 401,
    324, 323, 430, 301, 312, 303, 304, 315, 305, 306, 317, 307, 309, -1205, 205,
    291, 297, 295, 209, 269, 204, 601, 645, 646, 606, 605, 10001, 851, 621, 629,
    10002, 853, 675, 507, 512, 503, 504, 505, 506, 508, 203, 502, 501, 509, 510,
)
_REPORT_RANK = {nid: i for i, nid in enumerate(REPORT_IDS)}


def nutrient_label(nid: int) -> str:
    """A nutrient's column name in Cronometer's exports, e.g. "Energy (kcal)"."""
    unit = nu.unit(nid)
    return f"{nu.name(nid)} ({unit})" if unit else nu.name(nid)


def serving_nutrients(serving: dict, food: dict | None) -> dict[int, float]:
    """Every nutrient in one logged serving, by nutrient id."""
    if food is None:
        return {}
    return {nid: serving_nutrient(food, serving["amount"], serving["measure_id"], v)
            for nid, v in cf.nutrient_values(food).items()}


def pick_nutrients(values: dict[int, float], ids: set[int] | None) -> dict[str, float]:
    """The nonzero nutrients in ``ids`` (None = REPORT_IDS) by label, rounded
    like the exports (2 places), in the exports' column order."""
    wanted = set(REPORT_IDS) if ids is None else ids
    out = {}
    for nid in sorted(values, key=lambda n: (_REPORT_RANK.get(n, len(REPORT_IDS)), n)):
        if nid not in wanted or nid not in nu.NUTRIENTS:
            continue
        v = round(values[nid], 2)
        if v:
            out[nutrient_label(nid)] = v
    return out


def summarize_serving(s: dict, food: dict | None, user_id: int) -> dict:
    """A logged food, recipe or meal, with the ids the other tools take."""
    grams, _, measure, qty = cf.ingredient_contribution(s, food)
    return {
        "serving_id": s["id"],
        "food_source_id": s["food_id"],
        "name": cf.food_name(food) if food else None,
        "food_type": cf.food_type(food) if food else None,
        "custom": food.get("owner") == user_id if food else None,
        "quantity": round(qty, 3) if qty is not None else None,
        "measure": measure,
        "grams": round(grams, 2) if grams is not None else None,
    }


def summarize_entry(e: dict, foods: dict[int, dict], metrics: dict[int, dict],
                    group_names: dict[int, str], user_id: int,
                    nutrient_ids: set[int] | None) -> dict:
    """One diary entry of any kind, as get_diary lists it."""
    kind = g.short_class(e)
    grp = diary_group(e)
    # entry_id is the id reorder_diary_entries takes; each kind also gets it
    # under the name its own tools use (serving_id, note_id, biometric_id).
    head = {"kind": None, "entry_id": e["id"], "diary_group": group_names.get(grp, grp),
            "position": _position(e), "time": fmt_time(e.get("time"))}
    if kind == "Serving":
        food = foods.get(e["food_id"])
        out = {**head, "kind": "food", **summarize_serving(e, food, user_id)}
        out["nutrients"] = pick_nutrients(serving_nutrients(e, food), nutrient_ids)
        return out
    if kind == "Note":
        return {**head, "kind": "note", "note_id": e["id"], "text": e.get("text")}
    if kind == "Exercise":
        ex = summarize_exercise(e, group_names)
        rest = {k: v for k, v in ex.items()
                if k not in ("date", "time", "diary_group", "entry_id", "kind")}
        return {**head, "kind": "exercise",
                "tracker_activity": ex["kind"] == "tracker_activity", **rest}
    if "Biometric" in kind:
        b = summarize_biometric(e, metrics)
        return {**head, "kind": "biometric", "biometric_id": e["id"],
                **{k: v for k, v in b.items() if k not in ("date", "time", "entry_id")}}
    return {**head, "kind": kind}


def food_totals(day_entries: list[dict], foods: dict[int, dict]) -> dict[int, float]:
    """A day's consumed nutrients (foods only), by nutrient id."""
    totals: dict[int, float] = {}
    for e in day_entries:
        if g.short_class(e) == "Serving":
            for nid, v in serving_nutrients(e, foods.get(e["food_id"])).items():
                totals[nid] = totals.get(nid, 0.0) + v
    return totals
