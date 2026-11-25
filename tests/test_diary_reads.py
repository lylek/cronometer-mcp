"""get_diary and get_diary_nutrition: the diary read live, not from the CSV export."""

import json
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from cronometer_mcp import diary, server
from cronometer_mcp import gwt_foods as g
from cronometer_mcp.client import CronometerClient
from tests.test_client import _food, _measure


def food(fid, values, ftype="FOOD", measures=None, owner=0, name="Test food"):
    f = _food(fid, ftype, measures or [_measure(1, "g", 1.0, 1.0), _measure(2, "cup", 1.0, 240.0)])
    f["translations"]["items"][0]["name"] = name
    f["owner"] = owner
    f["nutrients"] = {"values": {"entries": [(k, {"amount": v}) for k, v in values.items()]}}
    return f


def serving(sid, fid, group, pos, amount, measure_id, hm=None):
    t = {"$class": g.TIME, "hour": hm[0], "minute": hm[1], "second": 0} if hm else None
    return {"$class": g.SERVING, "id": sid, "food_id": fid, "order": group << 16 | pos,
            "amount": amount, "measure_id": measure_id, "time": t}


OATS = food(7, {208: 389.0, 203: 16.9, 210: 1.0, 268: 1628.0})  # per 100 g
STEW = food(8, {208: 1200.0, 203: 90.0}, ftype="RECIPE", owner=42, name="My Stew",
            measures=[_measure(3, "full recipe", 1.0, 1.0, mtype=3),
                      _measure(4, "serving", 1.0, 4.0, mtype=3),
                      _measure(5, "g", 1.0, 1000.0, mtype=3)])
FOODS = {7: OATS, 8: STEW}
GROUPS = {1: "Breakfast", 3: "Dinner"}


class TestPickNutrients:
    def test_report_set_by_default_in_export_order(self):
        out = diary.pick_nutrients({203: 10.004, 208: 50.0, 210: 1.0, 268: 9.0, 291: 0.001}, None)
        assert out == {"Energy (kcal)": 50.0, "Protein (g)": 10.0}

    def test_explicit_ids_include_off_report_nutrients(self):
        assert diary.pick_nutrients({210: 1.234, 208: 5.0}, {210}) == {"Sucrose (g)": 1.23}


class TestEntries:
    def test_food_by_weight(self):
        e = diary.summarize_entry(serving("S1", 7, 1, 2, 40.0, 2, (7, 5)), FOODS, {}, GROUPS, 42, None)
        assert e["kind"] == "food" and e["entry_id"] == e["serving_id"] == "S1"
        assert (e["diary_group"], e["position"], e["time"]) == ("Breakfast", 2, "07:05:00")
        assert (e["food_source_id"], e["food_type"], e["custom"]) == (7, "FOOD", False)
        assert (e["quantity"], e["measure"], e["grams"]) == (0.167, "cup", 40.0)
        assert e["nutrients"] == {"Energy (kcal)": 155.6, "Protein (g)": 6.76}

    def test_name_is_the_alias_picked_when_logging(self):
        f = food(9, {208: 100.0}, name="Coffee, Prepared From Grounds")
        f["translations"]["items"].append(
            {**f["translations"]["items"][0], "id": 57480966, "name": "Coffee"})
        s = serving("S3", 9, 1, 1, 100.0, 1)
        assert diary.summarize_entry(s, {9: f}, {}, GROUPS, 42, None)["name"] == \
            "Coffee, Prepared From Grounds"
        s["translation_id"] = 57480966
        assert diary.summarize_entry(s, {9: f}, {}, GROUPS, 42, None)["name"] == "Coffee"

    def test_recipe_by_recipe_relative_serving(self):
        e = diary.summarize_entry(serving("S2", 8, 3, 1, 1.0, 4), FOODS, {}, GROUPS, 42, None)
        assert (e["food_type"], e["custom"], e["name"]) == ("RECIPE", True, "My Stew")
        assert (e["quantity"], e["measure"]) == (1.0, "serving")
        assert e["nutrients"] == {"Energy (kcal)": 300.0, "Protein (g)": 22.5}

    def test_note_and_unknown_group(self):
        note = {"$class": g.NOTE, "id": "N1", "order": 6 << 16 | 1, "text": "hi", "time": None}
        e = diary.summarize_entry(note, {}, {}, GROUPS, 42, None)
        assert (e["kind"], e["note_id"], e["text"], e["diary_group"]) == ("note", "N1", "hi", 6)

    def test_exercise_keeps_kind_and_flags_tracker(self):
        ex = {"$class": g.EXERCISE, "id": "X1", "order": 0, "name": "Walking", "minutes": 30.0,
              "calories": -120.0, "source": None, "time": None, "day": None}
        e = diary.summarize_entry(ex, {}, {}, GROUPS, 42, None)
        assert (e["kind"], e["entry_id"], e["tracker_activity"]) == ("exercise", "X1", False)
        assert "date" not in e

    def test_biometric_uses_the_short_id(self):
        b = {"$class": g.BIOMETRIC, "id": "BXW0DA", "order": 0, "metric_id": 1, "unit_id": 2,
             "value": 150.0, "time": None, "day": None}
        metrics = {1: {"name": "Weight", "units": [(1, "kg", 1.0), (2, "lbs", 0.4536)]}}
        e = diary.summarize_entry(b, {}, metrics, GROUPS, 42, None)
        assert (e["kind"], e["entry_id"], e["biometric_id"], e["unit"]) == ("biometric", "BXW0DA", "BXW0DA", "lbs")

    def test_food_totals_skip_other_entries(self):
        entries = [serving("S1", 7, 1, 1, 100.0, 1), serving("S2", 8, 3, 1, 0.5, 3),
                   {"$class": g.NOTE, "id": "N", "order": 0}]
        assert diary.food_totals(entries, FOODS) == pytest.approx(
            {208: 989.0, 203: 61.9, 210: 1.0, 268: 1628.0})


class TestClient:
    def _client(self, entries):
        c = CronometerClient(username="t@x.com", password="pw")
        c._authenticated = True
        c.user_id = "42"
        c._diary_groups = [{"wire_index": 1, "name": "Breakfast", "enabled": True}]
        c.get_day_info = MagicMock(side_effect=lambda d: {"entries": {
            "$class": g.ARRAY_LIST, "items": entries.get(d, [])}})
        c.get_foods_raw = MagicMock(return_value=FOODS)
        c.get_metrics = MagicMock()
        return c

    def test_diary_in_display_order_with_totals(self):
        d1, d2 = date(2000, 1, 1), date(2000, 1, 2)
        c = self._client({d1: [serving("B", 7, 1, 2, 50.0, 1), serving("A", 8, 1, 1, 1.0, 3)]})
        days = c.get_diary(d1, d2)
        assert [e["serving_id"] for e in days[0]["entries"]] == ["A", "B"]
        assert days[0]["totals"]["Energy (kcal)"] == 1394.5
        assert days[1] == {"date": "2000-01-02", "entries": [], "totals": {}}
        c.get_foods_raw.assert_called_once_with([7, 8])
        c.get_metrics.assert_not_called()

    def test_nutrition_counts_foods(self):
        d1 = date(2000, 1, 1)
        c = self._client({d1: [serving("A", 7, 1, 1, 100.0, 1)]})
        [day] = c.get_diary_nutrition(d1, d1, {203})
        assert day == {"date": "2000-01-01", "foods_logged": 1, "totals": {"Protein (g)": 16.9}}


class TestTools:
    @pytest.mark.parametrize("arg,want", [
        (None, set(diary.MACRO_IDS)), ("macros", set(diary.MACRO_IDS)), ("all", None),
        ("vitamin d, sodium", {324, 307}), (["Protein", 208], {203, 208}),
    ])
    def test_nutrient_choice(self, arg, want):
        assert server._nutrient_ids(arg, "macros") == want

    def test_unknown_nutrient_is_an_error(self):
        out = json.loads(server.get_diary("2000-01-01", nutrients=["nope"]))
        assert out["status"] == "error"

    def test_averages_cover_only_days_with_food(self):
        days = [{"date": "2000-01-01", "foods_logged": 2, "totals": {"Energy (kcal)": 2000.0}},
                {"date": "2000-01-02", "foods_logged": 0, "totals": {}},
                {"date": "2000-01-03", "foods_logged": 1, "totals": {"Energy (kcal)": 1000.0,
                                                                     "Zinc (mg)": 4.0}}]
        with patch.object(server, "_get_client") as get:
            get.return_value.get_diary_nutrition.return_value = days
            out = json.loads(server.get_diary_nutrition("2000-01-01", "2000-01-03"))
        assert out["averages"] == {"days_with_food": 2, "macros": {"Energy (kcal)": 1500.0},
                                   "micros": {"Zinc (mg)": 2.0}}
        assert out["days"][1] == {"date": "2000-01-02", "foods_logged": 0}
