"""get_food_details and get_custom_food with a list of ids: one bulk fetch."""

import json
from unittest.mock import MagicMock, patch

import pytest

from cronometer_mcp import server
from cronometer_mcp import gwt_foods as g
from cronometer_mcp.client import CronometerClient
from tests.test_client import _food, _measure


def recipe(fid, ingredient_ids):
    f = _food(fid, "RECIPE", [_measure(1, "serving", 1.0, 100.0)])
    f["ingredients"] = {"$class": g.ARRAY_LIST, "items": [{"food_id": i} for i in ingredient_ids]}
    return f


DB = {1: _food(1, "FOOD", [_measure(11, "g", 1.0, 1.0)], default=11),
      2: recipe(2, [3, 4]), 3: _food(3, "FOOD", []), 4: recipe(4, [5]), 5: _food(5, "FOOD", [])}


def _client():
    c = CronometerClient(username="t@x.com", password="pw")
    c._authenticated = True
    c.get_foods_raw = MagicMock(side_effect=lambda ids: {i: DB[i] for i in ids if i in DB})
    return c


class TestClient:
    def test_foods_and_each_ingredient_level_fetched_in_bulk(self):
        c = _client()
        with patch("cronometer_mcp.custom_foods.summarize_food",
                   side_effect=lambda f, children, *a: {"id": f["id"], "kids": sorted(children)}):
            foods, missing = c.get_custom_foods([2, 99, 1, 2], depth=1)
        assert [f["id"] for f in foods] == [2, 1] and missing == [99]
        assert foods[0]["kids"] == [3, 4, 5]
        assert [call.args[0] for call in c.get_foods_raw.call_args_list] == [[2, 99, 1], [3, 4], [5]]


class TestTools:
    def test_single_id_output_unchanged(self):
        with patch.object(server, "_get_client") as get:
            get.return_value._get_food_raw.return_value = DB[1]
            out = json.loads(server.get_food_details(1))
        assert out == {"status": "success", "food_source_id": 1, "name": "Test food",
                       "default_measure_id": 11,
                       "measures": [{"measure_id": 11, "description": "g", "weight_grams": 1.0}]}

    def test_list_of_ids(self):
        with patch.object(server, "_get_client", return_value=_client()):
            out = json.loads(server.get_food_details([3, 99, 1]))
        assert [f["food_source_id"] for f in out["foods"]] == [3, 1]
        assert (out["count"], out["not_found"]) == (2, [99])

    def test_custom_food_list(self):
        with patch.object(server, "_get_client") as get:
            get.return_value.get_custom_foods.return_value = ([{"food_source_id": 1}], [99])
            out = json.loads(server.get_custom_food([1, 99], depth=0))
        get.return_value.get_custom_foods.assert_called_once_with([1, 99], 0, False)
        assert out == {"status": "success", "count": 1, "foods": [{"food_source_id": 1}],
                       "not_found": [99]}

    @pytest.mark.parametrize("ids", [[], list(range(server.MAX_BULK_FOODS + 1))])
    def test_empty_or_too_many_is_an_error(self, ids):
        with patch.object(server, "_get_client"):
            assert json.loads(server.get_food_details(ids))["status"] == "error"
            assert json.loads(server.get_custom_food(ids))["status"] == "error"
