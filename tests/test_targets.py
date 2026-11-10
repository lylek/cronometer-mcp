"""Tests for nutrient targets, profile/energy settings and per-day macro templates.

Request bodies marked "captured" were recorded from the Cronometer web app.
"""

import json
from datetime import date
from unittest.mock import MagicMock

import pytest

from cronometer_mcp import gwt_foods as g
from cronometer_mcp import nutrients
from cronometer_mcp import server
from cronometer_mcp import targets as tg
from cronometer_mcp.client import CronometerClient

from tests.test_diary import user

HEADER = "B82D6F42004DB8749BB1C2080E1A4479"
NONCE = "d965ea1adac20e2b0fef7a426cdc825f"
UID = 6979138
_PRE = f"7|0|{{n}}|https://cronometer.com/cronometer/|{HEADER}|com.cronometer.shared.rpc.CronometerService|"


# --- codec --------------------------------------------------------------------


def test_set_target_matches_captured_request():
    body = g.encode_request(HEADER, "setTarget", [
        (g.STRING, NONCE), (g.TARGET, tg.target_obj(10011, None, None, True, False)), ("I", UID),
    ])
    assert body == (
        _PRE.format(n=8) + "setTarget|java.lang.String/2004016611|" + g.TARGET + "|I|"
        + NONCE + "|1|2|3|4|3|5|6|7|8|6|0|0|0|10011|1|6979138|"
    )


def test_target_bounds_are_boxed_doubles_and_round_trip():
    body = g.encode_request(HEADER, "setTarget", [
        (g.STRING, NONCE), (g.TARGET, tg.target_obj(307, 2000, 4000.5, True, True)), ("I", UID),
    ])
    assert g.DOUBLE in body
    method, (_, target, uid) = g.RequestDecoder(body).call()
    assert (method, uid) == ("setTarget", UID)
    assert (target["min"], target["max"], target["custom"], target["nutrient_id"]) == (
        2000, 4000.5, True, 307)


def test_set_user_preference_matches_captured_requests():
    def pref(key, value):
        return g.encode_request(HEADER, "setUserPreference", [
            (g.STRING, NONCE), ("I", UID), (g.STRING, key), (g.STRING, value),
        ])
    head = "setUserPreference|java.lang.String/2004016611|I|" + NONCE + "|"
    assert pref("heightInCM", "181") == (
        _PRE.format(n=9) + head + "heightInCM|181|1|2|3|4|4|5|6|5|5|7|6979138|8|9|"
    )
    # Choosing "Default" BMR sends a null value.
    assert pref("bmr", None) == (
        _PRE.format(n=8) + head + "bmr|1|2|3|4|4|5|6|5|5|7|6979138|8|0|"
    )


TEMPLATE_RESPONSE = (
    '//OK[0,22.0,3,6,6,0,137024,5,0,0,23.0,3,0,2026,11,9,4,0,51.0,3,0,2,1,'
    '["com.cronometer.shared.targets.models.MacroTargetTemplate/3691130822",'
    '"java.lang.Boolean/476441737","java.lang.Double/858496421",'
    '"com.cronometer.shared.entries.models.Day/782579793","java.lang.Integer/3438268394",'
    '"Light Training Day"],0,7]'
)


def test_macro_template_decodes():
    t = g.decode_response(TEMPLATE_RESPONSE)
    assert (t["name"], t["id"], t["macro_mode"]) == ("Light Training Day", 137024, 6)
    assert (t["protein"], t["carbs"], t["fat"], t["calories"]) == (22.0, 51.0, 23.0, None)
    assert t["unknown_0"] is False and t["day"]["day"] == 9
    assert tg.template_summary(t)["macro_units"] == "percent of energy"


def test_macro_template_round_trips():
    t = g.decode_response(TEMPLATE_RESPONSE)
    body = g.encode_request(HEADER, "updateDailyTargetTemplate", [
        (g.STRING, NONCE), ("I", UID), (g.MACRO_TARGET_TEMPLATE, t),
    ])
    method, (_, uid, again) = g.RequestDecoder(body).call()
    assert (method, uid) == ("updateDailyTargetTemplate", UID)
    assert again == t


def test_booleans_and_floats_box_in_object_fields():
    enc = g._Encoder()
    enc.object(True)
    enc.object(2.5)
    enc.object(3)
    assert enc.strings == [g.BOOLEAN, g.DOUBLE, g.INTEGER]
    assert enc.out == ["1", "1", "2", "2.5", "3", "3"]


# --- nutrients ----------------------------------------------------------------


@pytest.mark.parametrize("key,nid", [
    ("Sodium", 307), ("sodium", 307), ("B12", 418), ("cobalamin", 418),
    ("Net Carbs", -1205), ("Beta-Hydroxybutyrate", 10011), (306, 306), ("306", 306),
])
def test_resolve_nutrient(key, nid):
    assert nutrients.resolve(key) == nid


def test_ambiguous_nutrient_needs_preference():
    with pytest.raises(ValueError, match="ambiguous"):
        nutrients.resolve("Vitamin A")
    assert nutrients.resolve("Vitamin A", prefer={320, 307}) == 320


def test_unknown_nutrient():
    with pytest.raises(ValueError):
        nutrients.resolve("unobtainium")
    with pytest.raises(ValueError):
        nutrients.resolve(99999)


def test_summarize_target_names_and_units():
    row = tg.summarize_target({"id": 418, "min": 2.4, "max": None, "vis": True, "custom": False})
    assert row == {"nutrient_id": 418, "nutrient": "B12 (Cobalamin)", "unit": "µg",
                   "min": 2.4, "max": None, "visible": True, "custom": False}


# --- profile ------------------------------------------------------------------


def test_profile_summary_reports_energy_settings():
    u = user(weights=[(date(2026, 11, 5), 66.3791)], settings={"heightUnit": "Inches"})
    u["body_fat_history"] = {"points": {"items": [
        {"day": {"day": 25, "month": 7, "year": 2026}, "value": 12.2},
        {"day": {"day": 25, "month": 12, "year": 2026}, "value": 11.0},
    ]}}
    p = tg.profile_summary(u, date(2026, 11, 9))
    assert (p["sex"], p["age"], p["height_cm"]) == ("Male", 54, 180.0)
    assert (p["body_fat_percent"], p["body_fat_logged"]) == (12.2, "2026-07-25")
    e = p["energy"]
    assert (e["bmr_kcal"], e["bmr_source"]) == (1524, "Mifflin-St Jeor")
    assert (e["activity_level"], e["baseline_activity_kcal"]) == ("Sedentary", 305)
    assert e["custom_energy_target_kcal"] is None and e["include_exercise_in_energy_target"]


def test_profile_summary_custom_bmr_and_activity():
    u = user(settings={"bmr": "1600", "calories.activity": "-1",
                       "calories.activity.custom": "400",
                       "targets.custom.energy.target": "2500",
                       "targets.include.exercises": "false"})
    e = tg.profile_summary(u, date(2026, 11, 9))["energy"]
    assert (e["bmr_kcal"], e["bmr_source"]) == (1600, "custom")
    assert (e["activity_level"], e["activity_multiplier"], e["baseline_activity_kcal"]) == (
        "Custom", None, 400)
    assert e["custom_energy_target_kcal"] == 2500.0
    assert e["include_exercise_in_energy_target"] is False


def test_resolve_choices():
    assert tg.resolve_activity("very active") == 0.9
    assert tg.resolve_activity("None") == 0.0
    assert tg.resolve_sex("female") == "Female"
    with pytest.raises(ValueError):
        tg.resolve_activity("couch")
    with pytest.raises(ValueError):
        tg.resolve_reproductive_status("Menopausal")


# --- client -------------------------------------------------------------------


def _client():
    c = CronometerClient(username="t@t.com", password="pw")
    c._authenticated = True
    c.nonce = NONCE
    c.user_id = str(UID)
    c.gwt_header = HEADER
    c.session = MagicMock()
    return c


def test_update_profile_puts_json():
    c = _client()
    c.update_profile(date(1972, 4, 3), "Male", "Normal")
    method, url = c.session.request.call_args[0]
    assert (method, url) == ("PUT", f"https://cronometer.com/api/v3/user/{UID}/profile")
    assert c.session.request.call_args[1]["json"] == {
        "birthdate": "1972-04-03", "gender": "Male", "reproductiveStatus": "Normal"}


def test_daily_macro_targets_fall_back_to_weekly_schedule():
    c = _client()
    c._gwt_post = MagicMock(return_value="//OK[0,[],0,7]")
    c.get_all_macro_schedules = MagicMock(return_value=[
        {"day_of_week": 2, "protein_g": 17.0, "fat_g": 23.0, "calories": None,
         "carbs_g": 60.0, "template_name": "Moderate Training Day", "template_id": 1},
    ])
    tue = c.get_daily_macro_targets(date(2026, 11, 10))
    assert tue["template_name"] == "Moderate Training Day"
    assert tue["source"] == "weekly_schedule"
    mon = c.get_daily_macro_targets(date(2026, 11, 16))
    assert mon["source"] == "none" and mon["template_name"] is None


def test_apply_macro_template_sends_template_with_day():
    c = _client()
    c._gwt_post = MagicMock(return_value='//OK[1,2,1,["x.ResponseEvent/1","Success"],0,7]')
    t = g.decode_response(TEMPLATE_RESPONSE)
    c.apply_macro_template(date(2026, 11, 10), t)
    method, (_, uid, sent) = g.RequestDecoder(c._gwt_post.call_args[0][0]).call()
    assert (method, uid, sent["id"], sent["name"]) == (
        "updateDailyTargetTemplate", UID, 137024, "Light Training Day")
    assert (sent["day"]["day"], sent["day"]["month"], sent["day"]["year"]) == (10, 11, 2026)
    assert t["day"]["day"] == 9  # the caller's template is not modified
    c._gwt_post.return_value = "//EX[2,1,[\"x.Exception/1\",\"boom\"],0,7]"
    with pytest.raises(RuntimeError):
        c.apply_macro_template(date(2026, 11, 10), t)


# --- tools --------------------------------------------------------------------


@pytest.fixture
def client(monkeypatch):
    c = MagicMock()
    monkeypatch.setattr(server, "_get_client", lambda: c)
    return c


def _sent_target(client):
    return client.set_nutrient_target.call_args[0][0]


def test_set_nutrient_target_min_keeps_max_and_marks_custom(client):
    rows = [{"id": 307, "min": 1500.0, "max": 2300.0, "vis": True, "custom": False}]
    client.get_nutrient_targets.side_effect = [rows, [dict(rows[0], min=2000.0, custom=True)]]
    out = json.loads(server.set_nutrient_target("sodium", min_amount=2000))
    assert out["status"] == "success"
    t = _sent_target(client)
    assert (t["min"], t["max"], t["custom"], t["visible"]) == (2000.0, 2300.0, True, True)
    client.init_targets.assert_not_called()


def test_set_nutrient_target_clear_max_and_default(client):
    rows = [{"id": 313, "min": 1000.0, "max": 2000.0, "vis": False, "custom": True}]
    client.get_nutrient_targets.return_value = rows
    server.set_nutrient_target("fluoride", max_amount="none")
    assert _sent_target(client)["max"] is None
    server.set_nutrient_target("fluoride", use_default=True)
    assert _sent_target(client)["custom"] is False
    client.init_targets.assert_called_once()


def test_set_nutrient_target_rejects_bad_input(client):
    client.get_nutrient_targets.return_value = []
    out = json.loads(server.set_nutrient_target("sodium", min_amount=5, max_amount=1))
    assert out["status"] == "error"
    out = json.loads(server.set_nutrient_target("sodium", min_amount=5, use_default=True))
    assert out["status"] == "error"
    client.set_nutrient_target.assert_not_called()


def test_set_nutrient_target_no_change(client):
    client.get_nutrient_targets.return_value = [
        {"id": 307, "min": 1500.0, "max": 2300.0, "vis": True, "custom": False}]
    out = json.loads(server.set_nutrient_target("sodium", visible=True))
    assert out["message"] == "No change."
    client.set_nutrient_target.assert_not_called()


def test_get_nutrient_targets_filters(client):
    client.get_nutrient_targets.return_value = [
        {"id": 307, "min": 1500.0, "max": 2300.0, "vis": True, "custom": False},
        {"id": 313, "min": 4000.0, "max": 10000.0, "vis": False, "custom": False},
    ]
    out = json.loads(server.get_nutrient_targets(visible_only=True))
    assert [t["nutrient"] for t in out["targets"]] == ["Sodium"]
    out = json.loads(server.get_nutrient_targets(nutrients=["fluoride"]))
    assert [t["nutrient_id"] for t in out["targets"]] == [313]


def _prefs(client):
    return [c.args for c in client.set_preference.call_args_list]


def test_update_profile_energy_preferences(client):
    client.get_user.return_value = user()
    server.update_profile(bmr=1600.4, custom_activity_kcal=400, thermic_effect_of_food=False)
    assert _prefs(client) == [("bmr", "1600"), ("calories.activity", "-1"),
                              ("calories.activity.custom", "400"),
                              ("use.thermic.effect.food", "false")]
    client.update_profile.assert_not_called()
    client.init_targets.assert_not_called()


def test_update_profile_custom_energy_target_on_and_off(client):
    client.get_user.return_value = user()
    server.update_profile(custom_energy_target=2500)
    assert _prefs(client) == [("targets.include.exercises", "false"),
                              ("targets.custom.energy.target", "2500"),
                              ("targets.custom.energy.target.max", "2500")]
    client.set_preference.reset_mock()
    server.update_profile(custom_energy_target="off", bmr="default", activity_level="Lightly Active")
    assert _prefs(client) == [("bmr", None), ("calories.activity", "0.375"),
                              ("calories.activity.custom", "0"),
                              ("targets.include.exercises", "true"),
                              ("targets.custom.energy.target", None),
                              ("targets.custom.energy.target.max", None)]


def test_update_profile_sex_birthday_height_refresh_targets(client):
    client.get_user.return_value = user(sex="Female", repro="Pregnant")
    server.update_profile(birthday="1980-01-02", height_cm=170)
    client.update_profile.assert_called_once_with(date(1980, 1, 2), "Female", "Pregnant")
    assert _prefs(client) == [("heightInCM", "170")]
    client.init_targets.assert_called_once()


def test_update_profile_male_cannot_be_pregnant(client):
    client.get_user.return_value = user()
    out = json.loads(server.update_profile(reproductive_status="Pregnant"))
    assert out["status"] == "error"
    client.update_profile.assert_not_called()
    # Switching to Male resets the status.
    client.get_user.return_value = user(sex="Female", repro="Lactating")
    server.update_profile(sex="male")
    client.update_profile.assert_called_once_with(date(1972, 4, 3), "Male", "Normal")


def test_apply_macro_template_tool(client):
    client.get_macro_templates_raw.return_value = [g.decode_response(TEMPLATE_RESPONSE)]
    client.get_daily_macro_targets.return_value = {"template_name": "Light Training Day"}
    out = json.loads(server.apply_macro_template("light training day", "2026-11-10"))
    assert out["status"] == "success"
    day, template = client.apply_macro_template.call_args[0]
    assert day == date(2026, 11, 10) and template["id"] == 137024
    out = json.loads(server.apply_macro_template("Rest Day"))
    assert out["status"] == "error" and "Light Training Day" in out["message"]
