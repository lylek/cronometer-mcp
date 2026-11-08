"""Tests for the food object codec and custom food/recipe/meal support."""

import json
from unittest.mock import MagicMock

import pytest

from cronometer_mcp import custom_foods as cf
from cronometer_mcp import gwt_foods as g
from cronometer_mcp import server
from cronometer_mcp.client import CronometerClient, FoodInUseError

HEADER = "B82D6F42004DB8749BB1C2080E1A4479"


def as_response(obj) -> str:
    """Serialize ``obj`` the way the server does: tokens reversed, table last."""
    enc = g._Encoder()
    enc.object(obj)
    toks = []
    for t in reversed(enc.out):
        try:
            toks.append(int(t))
        except ValueError:
            try:
                toks.append(float(t))
            except ValueError:
                toks.append(t)  # base64 long
    return "//OK" + json.dumps(toks + [enc.strings, 0, 7])


def plain_food(fid, kcal_per_100g, measures):
    food = cf.new_recipe("Plain", 1, "RECIPE")
    food.update(id=fid, food_type=g.enum(g.FOOD_TYPE, "FOOD"), owner=0)
    food["measures"]["measures"]["items"] = [
        dict(cf.food_measure(name, qty, grams, fid), id=mid)
        for mid, name, qty, grams in measures
    ]
    cf.set_nutrients(food, {"energy_kcal": kcal_per_100g})
    return food


def recipe(fid, total_g, servings, kcal, ingredients=()):
    food = cf.new_recipe("Soup", 1, "RECIPE", servings)
    food["id"] = fid
    for i, m in enumerate(food["measures"]["measures"]["items"]):
        m["id"] = fid * 10 + i
    cf.find_measure(food, "g")["weight"] = total_g
    food["nutrients"]["values"]["entries"] = [
        (208, {"$class": g.NUTRIENT, "amount": kcal, "nutrient_id": 208,
               "nutrient_type": g.GwtEnum(g.NUTRIENT_TYPE, 0)}),
    ]
    food["ingredients"]["items"] = list(ingredients)
    return food


# A deleteFood call captured from the web app, nonce swapped for a placeholder.
DELETE_FOOD = (
    "7|0|7|https://cronometer.com/cronometer/|" + HEADER + "|"
    "com.cronometer.shared.rpc.CronometerService|deleteFood|"
    "java.lang.String/2004016611|I|NONCE|1|2|3|4|2|5|6|7|87293467|"
)


class TestCodec:
    def test_simple_request_matches_web_app_bytes(self):
        body = g.encode_request(HEADER, "deleteFood", [(g.STRING, "NONCE"), ("I", 87293467)])
        assert body == DELETE_FOOD

    def test_request_round_trip_of_full_recipe(self):
        food = cf.new_recipe("Test Soup", 6979138, "MEAL", 2, "notes")
        food["ingredients"]["items"] = [
            {"$class": g.INGREDIENT, "amount": 85.0, "food_id": 452740, "id": "A",
             "measure_id": 1009634, "unknown_4": 0},
        ]
        params = [(g.STRING, "NONCE"), ("I", 6979138), (g.FOOD, food),
                  (g.INGREDIENT_SUBSTITUTIONS, None)]
        body = g.encode_request(HEADER, "addFood", params)
        method, decoded = g.RequestDecoder(body).call()
        assert method == "addFood"
        assert decoded[2] == food
        assert g.encode_request(HEADER, "addFood", list(zip([t for t, _ in params], decoded))) == body

    def test_shared_objects_become_back_references(self):
        food = cf.new_recipe("X", 1)
        body = g.encode_request(HEADER, "editFood", [(g.FOOD, food)])
        # Three recipe measures share one Measure$Type enum: written once.
        assert body.count(g.MEASURE_TYPE) == 1
        assert "|-" in body

    def test_response_decoding(self):
        food = recipe(5, 300.0, 3, 450.0)
        decoded = g.decode_response(as_response(food))
        assert decoded == food
        assert decoded["food_type"].name == "RECIPE"

    def test_response_with_split_array(self):
        raw = as_response({"$class": g.ARRAY_LIST, "items": [1, 2]})
        head, tail = raw.split(",", 1)
        assert g.decode_response(head + "].concat([" + tail)["items"] == [1, 2]

    def test_void_response(self):
        assert g.decode_response("//OK[[],0,7]") is None

    def test_food_usage_response(self):
        raw = '//OK[0,2,1,0,1,["com.cronometer.shared.foods.models.FoodUsage/734642561"],0,7]'
        usage = g.decode_response(raw)
        assert (usage["meals"], usage["recipes"], usage["diary_servings"], usage["retire_only"]) == (0, 1, 2, False)

    def test_unknown_class_is_an_error(self):
        with pytest.raises(ValueError, match="no GWT layout"):
            g.decode_response('//OK[1,["com.example.Mystery/123456"],0,7]')

    def test_trailing_tokens_are_an_error(self):
        with pytest.raises(ValueError, match="unread"):
            g.decode_response('//OK[9,0,[],0,7]')

    @pytest.mark.parametrize("n", [0, 1, 63, 64, 324182459, 2**40 + 7])
    def test_long_round_trip(self, n):
        assert g.long_from_gwt(g.long_to_gwt(n)) == n

    def test_long_matches_gwt(self):
        # Big-endian base64 digits, A=0 ... 9=61, $=62, _=63.
        assert [g.long_to_gwt(n) for n in (0, 1, 63, 64, 4095)] == ["A", "B", "_", "BA", "__"]


class TestModel:
    def test_plain_food_ingredient_in_grams(self):
        almonds = plain_food(7, 580.0, [(70, "oz", 1.0, 28.35), (71, "g", 1.0, 1.0)])
        ing = cf.build_ingredient({"food_source_id": 7, "quantity": 2, "measure": "oz"}, almonds)
        assert (ing["amount"], ing["measure_id"], ing["food_id"]) == (56.7, 70, 7)
        assert cf.build_ingredient({"food_source_id": 7, "grams": 10}, almonds)["measure_id"] == 71

    def test_measure_quantity_divides_weight(self):
        sauce = plain_food(8, 100.0, [(80, "Tbsp", 2.0, 30.0)])
        ing = cf.build_ingredient({"food_source_id": 8, "quantity": 1, "measure": "tbsp"}, sauce)
        assert ing["amount"] == 15.0

    def test_nested_recipe_ingredient_counts_its_measure(self):
        soup = recipe(9, 400.0, 4, 800.0)
        one_serving = cf.build_ingredient({"food_source_id": 9, "quantity": 1, "measure": "Serving"}, soup)
        assert (one_serving["amount"], one_serving["measure_id"]) == (1.0, 91)
        assert cf.ingredient_grams(one_serving, soup) == 100.0
        whole = cf.build_ingredient({"food_source_id": 9, "quantity": 1}, soup)
        assert (whole["amount"], whole["measure_id"]) == (1.0, 90)
        by_weight = cf.build_ingredient({"food_source_id": 9, "grams": 100}, soup)
        assert (by_weight["amount"], by_weight["measure_id"]) == (100.0, 92)
        assert cf.ingredient_grams(by_weight, soup) == 100.0

    def test_ingredient_spec_errors(self):
        soup = recipe(9, 400.0, 4, 800.0)
        with pytest.raises(ValueError, match="grams or quantity"):
            cf.build_ingredient({"food_source_id": 9}, soup)
        with pytest.raises(ValueError, match="no measure 'cup'"):
            cf.build_ingredient({"food_source_id": 9, "quantity": 1, "measure": "cup"}, soup)

    def test_existing_ingredient_keeps_row_id(self):
        almonds = plain_food(7, 580.0, [(71, "g", 1.0, 1.0)])
        old = {"$class": g.INGREDIENT, "amount": 5.0, "food_id": 7, "id": "cD86l",
               "measure_id": 71, "unknown_4": 3}
        ing = cf.build_ingredient({"food_source_id": 7, "grams": 20}, almonds, old)
        assert (ing["id"], ing["unknown_4"], ing["amount"]) == ("cD86l", 3, 20.0)
        assert old["amount"] == 5.0

    def test_summary_of_nested_recipe(self):
        almonds = plain_food(7, 580.0, [(71, "g", 1.0, 1.0)])
        soup = recipe(9, 400.0, 4, 800.0)
        ings = [
            {"$class": g.INGREDIENT, "amount": 50.0, "food_id": 7, "id": "B", "measure_id": 71, "unknown_4": 0},
            {"$class": g.INGREDIENT, "amount": 2.0, "food_id": 9, "id": "C", "measure_id": 91, "unknown_4": 0},
        ]
        bowl = recipe(1, 250.0, 2, 690.0, ings)
        out = cf.summarize_food(bowl, {7: almonds, 9: soup}, depth=1)
        assert out["servings"] == 2.0
        assert out["nutrients_per_serving"]["energy_kcal"] == 345.0
        a, s = out["ingredients"]
        assert (a["grams"], a["energy_kcal"], a["measure"]) == (50.0, 290.0, "g")
        assert (s["grams"], s["energy_kcal"], s["measure"], s["quantity"]) == (200.0, 400.0, "Serving", 2.0)
        assert s["recipe"]["name"] == "Soup"
        assert "recipe" not in cf.summarize_food(bowl, {7: almonds, 9: soup}, depth=0)["ingredients"][1]

    def test_gram_based_recipe(self):
        # Older recipes: gram measures and nutrients per 100 g, like a plain food.
        old = plain_food(3, 30.0, [(30, "full recipe", 1.0, 280.0), (31, "Serving", 1.0, 140.0),
                                   (32, "g", 1.0, 1.0)])
        old["food_type"] = g.enum(g.FOOD_TYPE, "RECIPE")
        assert not cf.whole_recipe_basis(old)
        assert (cf.total_weight_g(old), cf.servings(old)) == (280.0, 2.0)
        out = cf.summarize_food(old, {}, 0)
        assert out["nutrients_whole_recipe"]["energy_kcal"] == 84.0
        assert out["nutrients_per_serving"]["energy_kcal"] == 42.0
        ing = cf.build_ingredient({"food_source_id": 3, "quantity": 1, "measure": "Serving"}, old)
        assert (ing["amount"], ing["measure_id"]) == (140.0, 31)  # grams
        assert cf.ingredient_contribution(ing, old)[:2] == (140.0, 42.0)
        cf.set_servings(old, 4)
        assert cf.grams_per_unit(old, cf.find_measure(old, "Serving")) == 70.0

    def test_zero_weight_serving_has_no_per_serving(self):
        old = plain_food(3, 30.0, [(30, "full recipe", 1.0, 280.0), (31, "Serving", 1.0, 0.0)])
        old["food_type"] = g.enum(g.FOOD_TYPE, "RECIPE")
        out = cf.summarize_food(old, {}, 0)
        assert out["servings"] is None and "nutrients_per_serving" not in out

    @pytest.mark.parametrize("ordinal", cf.UNWEIGHED)
    def test_unweighed_label_serving(self, ordinal):
        roll = plain_food(4, 150.0, [(40, "roll", 1.0, 100.0)])
        cf.measures(roll)[0]["measure_type"] = g.GwtEnum(g.MEASURE_TYPE, ordinal)
        ing = cf.build_ingredient({"food_source_id": 4, "quantity": 1, "measure": "roll"}, roll)
        assert ing["amount"] == 100.0
        assert cf.ingredient_contribution(ing, roll)[:3] == (None, 150.0, "roll")
        assert cf.describe_measures(roll)[0]["grams"] is None

    def test_set_nutrients_scales_to_100g(self):
        bar = plain_food(7, 380.0, [(71, "g", 1.0, 1.0)])
        cf.set_nutrients(bar, {"energy_kcal": 200, "203": 12}, per_grams=50)
        assert cf.nutrient_values(bar) == {208: 400.0, 203: 24.0}

    def test_set_nutrients_rejects_recipes_and_unknown_names(self):
        with pytest.raises(ValueError, match="ingredients"):
            cf.set_nutrients(recipe(1, 1, 1, 1), {"energy_kcal": 1})
        with pytest.raises(ValueError, match="unknown nutrient"):
            cf.set_nutrients(plain_food(7, 1, []), {"vitamin_zz": 1})

    def test_set_food_measures_keeps_ids_by_name(self):
        bar = plain_food(7, 380.0, [(70, "bar", 1.0, 51.0), (71, "g", 1.0, 1.0)])
        cf.set_food_measures(bar, [{"name": "bar", "grams": 50}, {"name": "half bar", "grams": 25}])
        assert [(m["name"], m["id"], m["weight"]) for m in cf.measures(bar)] == [
            ("bar", 70, 50.0), ("half bar", 0, 25.0)]

    def test_set_name_and_servings(self):
        food = recipe(1, 100, 1, 100)
        cf.set_food_name(food, "Renamed")
        cf.set_servings(food, 3)
        assert cf.food_name(food) == "Renamed"
        assert cf.find_measure(food, "Serving")["weight"] == 3.0


def make_client(usage=(0, 0, 0)):
    c = CronometerClient.__new__(CronometerClient)
    c.user_id = "42"
    calls = []

    def rpc(method, params):
        calls.append((method, params))
        if method == "getFoodUsage":
            return {"$class": g.FOOD_USAGE, "meals": usage[0], "recipes": usage[1],
                    "diary_servings": usage[2], "retire_only": False}
        if method in ("editFood", "addFood"):
            return params[1][1]
        raise AssertionError(method)

    c._food_rpc = rpc
    return c, calls


class TestSaveFood:
    def test_unused_food_is_edited_in_place(self):
        c, calls = make_client()
        c._save_food(recipe(5, 1, 1, 1), None)
        assert [m for m, _ in calls] == ["getFoodUsage", "editFood"]

    def test_used_food_needs_a_decision(self):
        c, calls = make_client(usage=(0, 0, 3))
        with pytest.raises(FoodInUseError) as exc:
            c._save_food(recipe(5, 1, 1, 1), None)
        assert exc.value.usage["diary_servings"] == 3
        assert [m for m, _ in calls] == ["getFoodUsage"]

    def test_update_past_entries_edits_in_place(self):
        c, calls = make_client(usage=(1, 0, 0))
        c._save_food(recipe(5, 1, 1, 1), True)
        assert calls[-1][0] == "editFood"

    def test_not_updating_past_entries_saves_a_copy(self):
        c, calls = make_client(usage=(0, 2, 0))
        food = recipe(5, 1, 1, 1)
        saved = c._save_food(food, False)
        assert calls[-1][0] == "addFood"
        assert saved["id"] == 0 and food["id"] == 5

    def test_new_food_is_added_without_usage_check(self):
        c, calls = make_client()
        c._save_food(recipe(0, 1, 1, 1), None)
        assert [m for m, _ in calls] == ["addFood"]


class TestServerTools:
    def test_food_in_use_maps_to_needs_decision(self):
        client = MagicMock()
        client.edit_recipe.side_effect = FoodInUseError(
            5, {"diary_servings": 2, "recipes": 0, "meals": 0, "retire_only": False})
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(server, "_get_client", lambda: client)
            out = json.loads(server.edit_custom_recipe(5, name="x"))
        assert out["status"] == "needs_decision"
        assert out["usage"]["diary_servings"] == 2
        assert "update_past_entries" in out["message"]

    def test_list_custom_foods(self):
        client = MagicMock()
        client.list_custom_foods.return_value = [{"food_source_id": 1, "name": "A", "type": "MEAL"}]
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(server, "_get_client", lambda: client)
            out = json.loads(server.list_custom_foods("MEAL"))
        assert out["count"] == 1
        client.list_custom_foods.assert_called_once_with("MEAL", None)
