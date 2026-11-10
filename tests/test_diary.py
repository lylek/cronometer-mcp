"""Tests for diary days: exercises, biometrics and energy expenditure.

The expected energy numbers follow the web client's formulas; they were
checked against the live Diary page for eight days of a real account.
"""

import json
from datetime import date
from unittest.mock import MagicMock

import pytest

from cronometer_mcp import custom_foods as cf
from cronometer_mcp import diary
from cronometer_mcp import gwt_foods as g
from cronometer_mcp import server
from cronometer_mcp.client import CronometerClient

from tests.test_custom_foods import as_response, plain_food

DAY = date(2026, 11, 8)


def exercise(name, kcal, minutes, source=None, order=1):
    return {
        "$class": g.EXERCISE, "exercise_type_id": 0, "unknown_1": 0, "unknown_2": False,
        "calories": -kcal, "day": diary.day_obj(DAY), "id": g.long_to_gwt(1234),
        "external_id": None, "metadata": json.dumps({"resting_calories": 1}) if source else None,
        "minutes": minutes, "name": name, "tz_offset": None, "order": order,
        "source": source, "time": {"$class": g.TIME, "hour": 9, "minute": 5, "second": 0},
        "owner": 1, "unknown_15": 0.0,
    }


def serving(food_id, amount, measure_id, order=(1 << 16) | 1):
    return {
        "$class": g.SERVING, "day": diary.day_obj(DAY), "unknown_1": True, "unknown_2": True,
        "tz_offset": None, "order": order, "time": None, "owner": 1, "amount": amount,
        "food_id": food_id, "id": "A", "measure_id": measure_id, "unknown_11": 0,
    }


def user(settings=None, weights=(), sex="Male", birthday=date(1972, 4, 3), repro="Normal"):
    prefs = {"heightInCM": "180.0", "calories.activity": "0.2",
             "use.thermic.effect.food": "true", "weightInKG": "70"}
    prefs.update(settings or {})
    return {
        "$class": g.USER,
        "birthday": diary.day_obj(birthday) if birthday else None,
        "gender": g.enum(g.GENDER, sex),
        "reproductive_status": g.enum(g.REPRODUCTIVE_STATUS, repro),
        "settings": {"$class": g.USER_PREFERENCES, "entries": list(prefs.items())},
        "weight_history": {"$class": g.BIOMETRIC_HISTORY, "points": {"$class": g.ARRAY_LIST, "items": [
            {"$class": g.DATA_POINT, "day": diary.day_obj(d), "time": None, "value": v}
            for d, v in weights]}, "unknown_1": 1, "unknown_2": 1, "owner": 1},
    }


# --- codec --------------------------------------------------------------------


def test_day_info_round_trips_through_codec():
    info = {
        "$class": g.DAY_INFO, "unknown_0": False, "day": diary.day_obj(DAY),
        "entries": {"$class": g.ARRAY_LIST, "items": [
            exercise("Daily Activity (Garmin)", 300, 24, "Garmin"),
            serving(10, 150.0, 7),
        ]},
        "unknown_3": {"$class": g.ARRAY_LIST, "items": []}, "unknown_4": False,
        "properties": "{}",
    }
    out = g.decode_response(as_response(info))
    assert [g.short_class(e) for e in diary.entries(out)] == ["Exercise", "Serving"]
    ex = diary.entries(out, "Exercise")[0]
    assert ex["name"] == "Daily Activity (Garmin)" and ex["calories"] == -300
    assert diary.entries(out, "Serving")[0]["amount"] == 150.0


def test_user_settings_decode_as_a_map():
    out = g.decode_response(as_response({"$class": g.USER_PREFERENCES,
                                         "entries": [("bmr", "1600"), ("heightInCM", "175")]}))
    assert dict(out["entries"]) == {"bmr": "1600", "heightInCM": "175"}


def test_get_day_info_request_encodes_day_then_user():
    c = CronometerClient.__new__(CronometerClient)
    c.gwt_header, c.nonce, c.user_id = "H", "N", "42"
    c.authenticate = lambda: None
    c._gwt_post = MagicMock(return_value="//OK[0,[],0,7]")
    c.get_day_info(date(2026, 1, 9))
    body = c._gwt_post.call_args[0][0]
    method, params = g.RequestDecoder(body).call()
    assert method == "getDayInfo"
    assert params[0] == "N"
    assert (params[1]["day"], params[1]["month"], params[1]["year"]) == (9, 1, 2026)
    assert params[2] == 42


# --- exercises ----------------------------------------------------------------


@pytest.mark.parametrize("name,source,tracker,kcal", [
    ("Daily Activity (Garmin)", "Garmin", True, 300),
    ("Daily Activity (Garmin)", None, True, 0),  # typed by hand: no tracker kcal
    ("Running (Garmin)", "Garmin", False, 0),
    ("Fitbit Activity", "Fitbit", True, 300),
    ("Walking", None, False, 0),
])
def test_tracker_activity_rules(name, source, tracker, kcal):
    ex = exercise(name, 300, 30, source)
    assert diary.is_tracker_activity(ex) is tracker
    assert diary.tracker_kcal(ex) == -kcal


def test_apple_health_active_energy_is_tracker_activity():
    ex = exercise("Active Energy (Apple Health)", 250, 0, "Apple Health")
    assert diary.tracker_kcal(ex) == -250
    other = exercise("Yoga", 100, 30, "Apple Health")
    assert not diary.is_tracker_activity(other)


def test_summarize_exercise():
    s = diary.summarize_exercise(exercise("Running (Garmin)", 465.8, 41.85, "Garmin"),
                                 {0: "Uncategorized"})
    assert s["calories_burned"] == 465.8
    assert s["kind"] == "exercise"
    assert s["time"] == "09:05:00"
    assert s["diary_group"] == "Uncategorized"
    assert s["entry_id"] == 1234
    assert s["metadata"] == {"resting_calories": 1}


# --- day totals ---------------------------------------------------------------


def test_serving_nutrient_grams_and_recipe_relative():
    food = plain_food(10, 200, [(7, "cup", 1, 240)])
    assert diary.serving_nutrient(food, 120.0, 7, 200) == pytest.approx(240)  # 120 g
    rel = {"id": 9, "measure_type": g.GwtEnum(g.MEASURE_TYPE, cf.RECIPE_RELATIVE), "weight": 4.0}
    food["measures"]["measures"]["items"].append(rel)
    # 1 of 4 servings of a whole-recipe value of 800
    assert diary.serving_nutrient(food, 1.0, 9, 800) == pytest.approx(200)


def test_day_totals_splits_tracker_and_exercise():
    food = plain_food(10, 200, [(7, "g", 1, 1)])
    cf.set_nutrients(food, {"energy_kcal": 200, "protein_g": 10})
    food["nutrients"]["values"]["entries"].append(
        (-203, {"$class": g.NUTRIENT, "amount": 40.0, "nutrient_id": -203, "nutrient_type": None}))
    totals = diary.day_totals([
        serving(10, 150.0, 7),
        exercise("Daily Activity (Garmin)", 300, 24, "Garmin"),
        exercise("Running (Garmin)", 400, 40, "Garmin"),
    ], {10: food})
    assert totals[208] == pytest.approx(300)
    assert totals[-203] == pytest.approx(60)
    assert totals[diary.TRACKER_KCAL] == -300
    assert totals[diary.EXERCISE_KCAL] == -400
    assert totals[diary.EXERCISE_MINUTES] == 40  # tracker minutes don't count


# --- profile and energy -------------------------------------------------------


def test_weight_on_uses_last_weight_on_or_before_day():
    u = user(weights=[(date(2026, 10, 29), 66.0), (date(2026, 11, 5), 66.4)])
    assert diary.weight_on(u, date(2026, 11, 4))[0] == 66.0
    assert diary.weight_on(u, date(2026, 11, 5))[0] == 66.4
    assert diary.weight_on(u, date(2026, 1, 1))[0] == 66.0  # before any: earliest
    assert diary.weight_on(user(), DAY) == (70.0, "profile weight")


def test_age_truncates_to_whole_years():
    u = user(birthday=date(1972, 11, 9))
    assert diary.age_on(u, date(2026, 11, 8)) == 53
    assert diary.age_on(u, date(2026, 11, 10)) == 54


def test_bmr_mifflin_st_jeor_and_override():
    u = user(weights=[(date(2026, 11, 5), 66.379)])
    assert diary.bmr(u, DAY) == pytest.approx(10 * 66.379 + 6.25 * 180 - 5 * 54 + 5)
    f = user(weights=[(date(2026, 11, 5), 60)], sex="Female", repro="Lactating")
    assert diary.bmr(f, DAY) == pytest.approx(600 + 1125 - 270 - 161 + 300)
    assert diary.bmr(user({"bmr": "1700.4"}), DAY) == 1700


def test_energy_expenditure_matches_web_diary():
    # 2026-11-08 on the live account: BMR 1524, adjusted 0, tracker 298,
    # exercise 1166, TEF 275.
    u = user(weights=[(date(2026, 11, 5), 66.37910687200001)])
    totals = {208: 3119.0, -203: 500.0, -204: 800.0, -205: 1800.0,
              diary.TRACKER_KCAL: -298.0, diary.EXERCISE_KCAL: -1166.14,
              diary.EXERCISE_MINUTES: 123.67}
    out = diary.energy_expenditure(totals, u, DAY)
    b = out["breakdown"]
    assert b["bmr"] == 1523.8
    assert b["adjusted_baseline_activity"] == 0
    assert b["tracker_activity"] == 298
    assert b["exercise"] == 1166.1
    assert b["thermic_effect_of_food"] == pytest.approx(500 * .25 + 800 * .015 + 1800 * .075, abs=.1)
    assert out["burned_kcal"] == pytest.approx(sum(v for k, v in b.items() if k != "baseline_activity"), abs=.2)


def test_adjusted_baseline_scales_by_waking_minutes_and_subtracts_tracker():
    u = user({"bmr": "1500", "calories.activity": "0.5", "use.thermic.effect.food": "false"})
    out = diary.energy_expenditure({diary.EXERCISE_MINUTES: 240, diary.TRACKER_KCAL: -100}, u, DAY)
    b = out["breakdown"]
    assert b["baseline_activity"] == 750
    assert b["adjusted_baseline_activity"] == pytest.approx(750 * 720 / 960 - 100)
    assert b["thermic_effect_of_food"] == 0
    none = diary.energy_expenditure({diary.EXERCISE_MINUTES: 1000}, u, DAY)
    assert none["breakdown"]["adjusted_baseline_activity"] == 0


def test_custom_activity_level_uses_fixed_kcal():
    u = user({"calories.activity": "-1", "calories.activity.custom": "420"})
    assert diary.energy_expenditure({}, u, DAY)["breakdown"]["baseline_activity"] == 420


# --- biometrics -----------------------------------------------------------------


def test_summarize_biometric_names_metric_and_samples():
    b = {
        "$class": g.BIOMETRIC, "value": 73.0, "day": diary.day_obj(DAY), "external_id": "x",
        "id": g.long_to_gwt(5), "metadata": None, "metric_id": 40002, "tz_offset": None,
        "order": 1, "sample_stats": {
            "$class": g.SAMPLE_STATS, "count": 3, "earliest": None, "earliest_value": 1.0,
            "stats": {"$class": g.ARRAY_LIST, "items": [
                {"$class": g.SAMPLE_STAT, "label": "Last Night Average", "type": None,
                 "unit": "ms", "value": 84.0}]},
            "latest": None, "latest_value": 1.0, "max": 119.0, "mean": 82.5, "min": 52.0, "sum": 1.0,
        },
        "unknown_9": None, "unknown_10": None, "source": "Garmin", "time": None,
        "unit_id": 39945, "owner": 1,
    }
    metrics = {40002: {"name": "Heart Rate Variability (HRV)", "units": [(39945, "ms", 1.0)]}}
    s = diary.summarize_biometric(b, metrics)
    assert s["metric"] == "Heart Rate Variability (HRV)" and s["unit"] == "ms"
    assert s["samples"]["Last Night Average"] == 84.0
    renamed = dict(b, metadata='{"display_name":"Body Battery"}', sample_stats=None)
    assert diary.summarize_biometric(renamed, metrics)["metric"] == "Body Battery"


# --- client and server ----------------------------------------------------------


def test_date_range_is_capped():
    c = CronometerClient.__new__(CronometerClient)
    with pytest.raises(ValueError, match="too long"):
        c._days(date(2026, 1, 1), date(2026, 6, 1))
    with pytest.raises(ValueError, match="before"):
        c._days(date(2026, 1, 2), date(2026, 1, 1))


def test_energy_tool_reports_breakdown(monkeypatch):
    fake = MagicMock()
    fake.get_energy_expenditure.return_value = {"profile": {"sex": "Male"}, "days": [{"date": "2026-11-08"}]}
    monkeypatch.setattr(server, "_get_client", lambda: fake)
    out = json.loads(server.get_energy_expenditure("2026-11-08"))
    assert out["status"] == "success" and out["days"][0]["date"] == "2026-11-08"
    fake.get_energy_expenditure.assert_called_once_with(date(2026, 11, 8), None)


def test_exercise_tool_reports_errors(monkeypatch):
    fake = MagicMock()
    fake.get_exercises.side_effect = ValueError("date range too long")
    monkeypatch.setattr(server, "_get_client", lambda: fake)
    assert json.loads(server.get_exercise_log("2026-01-01", "2026-12-31"))["status"] == "error"
