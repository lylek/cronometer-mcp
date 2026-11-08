"""A recipe's servings and serving sizes, whatever the author named them."""

import pytest

from cronometer_mcp import custom_foods as cf
from tests.test_custom_foods import recipe


def add_size(food, number, name, per_recipe):
    m = cf.recipe_measure(name, per_recipe)
    m["quantity"] = number
    food["measures"]["measures"]["items"].append(m)
    return m


@pytest.mark.parametrize("name", ["serving", "Sandwich", "slice (1/8 cake)"])
def test_serving_name_is_the_authors_choice(name):
    # A real recipe had 4 "serving"s but was reported as 1.
    soup = recipe(5, 1000.0, 4, 2000.0)
    cf.serving_measure(soup)["name"] = name
    assert cf.servings(soup) == 4.0
    assert cf.summarize_food(soup, {}, 0)["nutrients_per_serving"]["energy_kcal"] == 500.0
    cf.set_servings(soup, 6)
    assert cf.servings(soup) == 6.0
    assert [m["name"] for m in cf.measures(soup)].count(name) == 1  # changed, not added


def test_advanced_serving_sizes_each_have_a_count():
    soup = recipe(5, 1200.0, 4, 2000.0)
    cf.serving_measure(soup)["name"] = "large serving"
    add_size(soup, 1.0, "medium serving", 6)
    assert cf.servings(soup) is None
    out = cf.summarize_food(soup, {}, 0)
    assert [(s["number"], s["name"], s["servings_per_recipe"], s["grams"])
            for s in out["serving_sizes"]] == [(1.0, "large serving", 4.0, 300.0),
                                                (1.0, "medium serving", 6.0, 200.0)]
    with pytest.raises(ValueError, match="several serving sizes"):
        cf.set_servings(soup, 2)
    add_size(soup, 1.0, "Serving", 5)
    assert cf.servings(soup) is None  # even with one named "Serving": no single count


def test_number_is_part_of_the_size():
    # 0.25 "cup" with 6 per recipe: the recipe is six 1/4 cups (288 g -> 48 g each).
    sauce = recipe(5, 288.0, 1, 600.0)
    sauce["measures"]["measures"]["items"].remove(cf.serving_measure(sauce))
    add_size(sauce, 0.25, "cup", 6)
    [size] = cf.summarize_food(sauce, {}, 0)["serving_sizes"]
    assert (size["number"], size["servings_per_recipe"], size["grams"]) == (0.25, 6.0, 48.0)
    assert cf.servings(sauce) == 6.0


def test_no_serving_size_is_one_serving():
    soup = recipe(5, 1000.0, 4, 2000.0)
    soup["measures"]["measures"]["items"].remove(cf.serving_measure(soup))
    assert cf.servings(soup) == 1.0
    assert cf.summarize_food(soup, {}, 0)["serving_sizes"] == []


def weight_based(sizes, total=300.0):
    """A Weight Based recipe: gram measures, nutrients per 100 g."""
    food = recipe(6, total, 1, 600.0)
    food["measures"]["measures"]["items"] = [
        cf.food_measure("full recipe", 1.0, total, 6), cf.food_measure("g", 1.0, 1.0, 6),
        *(cf.food_measure(name, number, grams, 6) for number, name, grams in sizes)]
    return food


def test_weight_based_sizes_store_grams_per_serving():
    # "2" "Tbsp" weighing 30 g: the recipe's 300 g make 10 of them.
    sauce = weight_based([(2.0, "Tbsp", 30.0)])
    out = cf.summarize_food(sauce, {}, 0)
    assert out["serving_mode"] == "weight_based"
    assert [(s["number"], s["name"], s["grams"], s["servings_per_recipe"])
            for s in out["serving_sizes"]] == [(2.0, "Tbsp", 30.0, 10.0)]
    assert out["servings"] == 10.0
    cf.set_servings(sauce, 4)
    assert (cf.serving_measure(sauce)["quantity"], cf.serving_measure(sauce)["weight"]) == (2.0, 75.0)


def test_duplicate_names_are_all_listed():
    salad = weight_based([(1.0, "Serving", 100.0), (1.0, "Serving", 150.0)])
    assert [s["grams"] for s in cf.serving_sizes(salad)] == [100.0, 150.0]
    assert cf.servings(salad) is None
    with pytest.raises(ValueError, match="several serving sizes"):
        cf.set_servings(salad, 2)


def test_advanced_switch_is_reported_as_stored():
    soup = recipe(5, 1000.0, 4, 2000.0)
    assert cf.summarize_food(soup, {}, 0)["advanced_serving_sizes"] is False
    soup["properties"] = None
    assert cf.summarize_food(soup, {}, 0)["advanced_serving_sizes"] is None
