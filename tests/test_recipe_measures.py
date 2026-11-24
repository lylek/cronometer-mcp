"""Recipe-relative serving sizes: an amount counts servings of "# name"."""

from cronometer_mcp import custom_foods as cf
from cronometer_mcp import gwt_foods as g


def measure(mid, name, quantity, weight, mtype=3):
    return {"$class": g.MEASURE, "quantity": quantity, "unknown_1": False, "food_id": 0,
            "unknown_3": False, "id": mid, "unknown_5": None, "name": name,
            "translations": {"$class": g.HASH_MAP, "entries": []},
            "measure_type": g.GwtEnum(g.MEASURE_TYPE, mtype), "weight": weight}


# Captured shape of a real recipe: 424.8 g, 888.5 kcal, eleven 1/4-cup pancakes.
GRIDDLE_CAKES = {
    "$class": g.FOOD, "id": 31410739, "food_type": g.enum(g.FOOD_TYPE, "RECIPE"),
    "measures": {"$class": g.FOOD_MEASURES, "default_measure_id": 2,
                 "measures": {"$class": g.ARRAY_LIST, "items": [
                     measure(1, "g", 1.0, 424.8), measure(2, "cup pancake", 0.25, 11.0),
                     measure(3, "full recipe", 1.0, 1.0)]}},
    "translations": {"$class": g.ARRAY_LIST, "items": []},
    "nutrients": {"values": {"entries": [(208, {"amount": 888.5})]}},
}


def test_number_does_not_scale_the_share():
    m = cf.find_measure(GRIDDLE_CAKES, "cup pancake")
    assert round(cf.grams_per_unit(GRIDDLE_CAKES, m), 2) == 38.62  # 424.8 / 11


def test_diary_amount_counts_servings_of_the_size():
    # Logged on the web as "4.00 x 0.25 cup pancake", which the export prices at 323.09 kcal.
    serving = {"amount": 4.0, "measure_id": 2, "food_id": 31410739}
    grams, kcal, label, qty = cf.ingredient_contribution(serving, GRIDDLE_CAKES)
    assert (round(grams, 2), round(kcal, 2), label, qty) == (154.47, 323.09, "0.25 cup pancake", 4.0)
