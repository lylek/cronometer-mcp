"""Recipe-ingredient and diary-entry reordering."""

import json
from unittest.mock import MagicMock

import pytest

from cronometer_mcp import custom_foods as cf
from cronometer_mcp import diary
from cronometer_mcp import gwt_foods as g
from cronometer_mcp import server
from cronometer_mcp.client import CronometerClient
from tests.test_diary import DAY, exercise, serving


def ing(row_id, food_id, amount=10.0):
    return {"$class": g.INGREDIENT, "amount": amount, "food_id": food_id, "id": row_id,
            "measure_id": 0, "unknown_4": 7}


ROWS = [ing("cUeJc", 1), ing("cUeJd", 2), ing("cUeJe", 3)]


@pytest.mark.parametrize("order", [["cUeJe", "cUeJc", "cUeJd"], [3, 1, 2], ["3", "cUeJc", 2]])
def test_reorder_ingredients(order):
    assert [r["food_id"] for r in cf.reorder_ingredients(ROWS, order)] == [3, 1, 2]


@pytest.mark.parametrize("order,msg", [
    ([1, 2], "list all 3"), ([1, 1, 2], "twice"), ([1, 2, 4], "position 4"),
    (["nope", 1, 2], "nope"), ([True, 1, 2], "bad"),
])
def test_reorder_ingredients_rejects(order, msg):
    with pytest.raises(ValueError, match=msg):
        cf.reorder_ingredients(ROWS, order)


def _client_with_recipe():
    c = CronometerClient.__new__(CronometerClient)
    food = {"id": 99, "food_type": g.enum(g.FOOD_TYPE, "RECIPE"),
            "ingredients": {"$class": g.ARRAY_LIST, "items": [dict(r) for r in ROWS]}}
    c._get_food_raw = MagicMock(return_value=food)
    c._save_food = MagicMock(side_effect=lambda f, upd: f)
    c._summary_after_save = MagicMock(side_effect=lambda f: f)
    return c


def test_client_reorder_saves_new_rows():
    c = _client_with_recipe()
    saved = c.reorder_recipe_ingredients(99, [3, 1, 2], update_past_entries=True)
    rows = saved["ingredients"]["items"]
    assert [(r["food_id"], r["id"], r["amount"], r["unknown_4"]) for r in rows] == [
        (3, "A", 10.0, 7), (1, "A", 10.0, 7), (2, "A", 10.0, 7)]
    assert c._save_food.call_args.args[1] is True


def test_client_reorder_leaves_in_use_decision_to_caller():
    c = _client_with_recipe()
    c.reorder_recipe_ingredients(99, [2, 1, 3])
    assert c._save_food.call_args.args[1] is None  # _save_food asks if the recipe is in use


def test_client_reorder_same_order_saves_nothing():
    c = _client_with_recipe()
    assert c.reorder_recipe_ingredients(99, [1, 2, 3]) is None
    c._save_food.assert_not_called()


# --- diary entries -------------------------------------------------------------


def _entry(fn, gid, slot, pos, *args):
    e = fn(*args, order=(slot << 16) | pos)
    e["id"] = gid
    return e


def day_info():
    return {"$class": g.DAY_INFO, "entries": {"$class": g.ARRAY_LIST, "items": [
        _entry(exercise, "EX1", 0, 1, "Walk", 100, 30),
        _entry(serving, "B1", 1, 1, 10, 100.0, 0),
        _entry(serving, "B2", 1, 2, 11, 100.0, 0),
        _entry(serving, "B3", 1, 3, 12, 100.0, 0),
        _entry(serving, "L1", 2, 1, 13, 100.0, 0),
        _entry(serving, "L2", 2, 2, 14, 100.0, 0),
    ]}}


def _orders(changed):
    return {e["id"]: (e["order"] >> 16, e["order"] & 0xFFFF) for e in changed}


def test_reorder_group_sends_only_changed_entries():
    changed = diary.reorder_group(day_info(), 1, ["B3", 1, 2])
    assert _orders(changed) == {"B3": (1, 1), "B1": (1, 2), "B2": (1, 3)}
    assert diary.reorder_group(day_info(), 1, [1, 2, 3]) == []
    changed = diary.reorder_group(day_info(), 1, [1, 3, 2])
    assert _orders(changed) == {"B3": (1, 2), "B2": (1, 3)}


def test_reorder_group_moves_entry_in_and_closes_the_gap():
    changed = diary.reorder_group(day_info(), 1, [1, "L1", 2, 3])
    assert _orders(changed) == {"L1": (1, 2), "B2": (1, 3), "B3": (1, 4), "L2": (2, 1)}


def test_reorder_group_into_empty_group():
    assert _orders(diary.reorder_group(day_info(), 5, ["L2"])) == {"L2": (5, 1)}


@pytest.mark.parametrize("order,msg", [
    ([1, 2], "missing"), ([1, 1, 2, 3], "twice"), ([1, 2, 4], "position 4"),
    (["ZZ", 1, 2, 3], "ZZ"),
])
def test_reorder_group_rejects(order, msg):
    with pytest.raises(ValueError, match=msg):
        diary.reorder_group(day_info(), 1, order)


def test_group_layout_sorts_by_order():
    info = day_info()
    info["entries"]["items"].reverse()
    layout = diary.group_layout(info)
    assert [e["id"] for e in layout[1]] == ["B1", "B2", "B3"]
    assert sorted(layout) == [0, 1, 2]


def test_reorder_diary_group_calls_update_orders():
    c = CronometerClient.__new__(CronometerClient)
    c.get_day_info = MagicMock(return_value=day_info())
    c._food_rpc = MagicMock()
    assert c.reorder_diary_group(DAY, 2, [2, 1]) == 2
    method, params = c._food_rpc.call_args.args
    assert method == "updateOrders"
    assert params[0][0] == "java.util.List"
    assert [e["id"] for e in params[0][1]["items"]] == ["L2", "L1"]
    assert params[1][0] == "I"


def test_updated_entries_encode():
    changed = diary.reorder_group(day_info(), 1, [3, 2, 1])
    body = g.encode_request("H", "updateOrders", [
        (g.STRING, "nonce"), ("java.util.List", {"$class": g.ARRAY_LIST, "items": changed}),
        ("I", 1)])
    assert "java.util.List" in body and g.SERVING in body
