"""Foods, recipes and meals on top of the gwt_foods codec: serving sizes
and amounts.

How Cronometer stores these (verified against the live web app):

- Recipes and meals are ``Food`` objects with ``food_type`` RECIPE or MEAL.
- A measure is either gram-based or recipe-relative (``measure_type`` 3).
  A gram-based measure's ``weight`` is the grams in ``quantity`` of the unit
  (``2 Tbsp`` -> 30 g). A recipe-relative one's ``weight`` is how many of the
  unit make the whole recipe (``g`` holds the total weight, ``Serving`` the
  servings, ``full recipe`` is 1).
- An amount (a diary serving's or a recipe ingredient's) is a count of its
  ``measure_id`` unit when that measure is recipe-relative (1.0 "Serving" is
  one serving), and grams otherwise, whatever unit ``measure_id`` displays
  (184.27 with "oz" is 184.27 g, shown as 6.5 oz).
- Measure types 1 and 2 are label servings with no known weight, stored with
  a stand-in weight (usually 100 "grams") so nutrients scale.
"""

from __future__ import annotations

import copy

from . import gwt_foods as g

RECIPE_TYPES = ("RECIPE", "MEAL")
RECIPE_RELATIVE = 3  # Measure$Type ordinals
UNWEIGHED = (1, 2)  # label servings with no known weight; their weight is a stand-in


def items(coll) -> list:
    return coll["items"] if coll else []


def food_type(food: dict) -> str:
    return food["food_type"].name if food.get("food_type") else "FOOD"


def is_recipe(food: dict) -> bool:
    return food_type(food) in RECIPE_TYPES


def food_name(food: dict) -> str:
    trans = items(food.get("translations"))
    for t in trans:
        if t["language"] and t["language"].get("code") == "en":
            return t["name"]
    return trans[0]["name"] if trans else ""


def measures(food: dict) -> list[dict]:
    return items(food["measures"]["measures"]) if food.get("measures") else []


def find_measure(food: dict, name: str) -> dict | None:
    for m in measures(food):
        if m["name"] == name:
            return m
    return None


def is_unweighed(m: dict | None) -> bool:
    return bool(m and m.get("measure_type")) and m["measure_type"].ordinal in UNWEIGHED


def is_relative(m: dict) -> bool:
    return bool(m.get("measure_type")) and m["measure_type"].ordinal == RECIPE_RELATIVE


def whole_recipe_basis(food: dict) -> bool:
    """True if nutrients are stored for the whole recipe, not per 100 g."""
    return any(is_relative(m) for m in measures(food))


def nutrient_values(food: dict) -> dict[int, float]:
    nm = food.get("nutrients")
    if not nm or not nm.get("values"):
        return {}
    return {k: v["amount"] for k, v in nm["values"]["entries"] if v is not None}


def total_weight_g(food: dict) -> float | None:
    """A recipe's total (or cooked) weight; None for plain foods."""
    if not is_recipe(food):
        return None
    if whole_recipe_basis(food):
        m = find_measure(food, "g")
        return m["weight"] if m and is_relative(m) else None
    m = find_measure(food, "full recipe")
    return m["weight"] / m["quantity"] if m and m["quantity"] else None


def grams_per_unit(food: dict, m: dict) -> float | None:
    if is_relative(m):
        # A recipe-relative measure is a share of the recipe: "weight" of them
        # make the whole recipe, and an amount counts them. "quantity" (the
        # web app's "#") is only part of the label: 0.25 "cup" with 6 per
        # recipe is six 1/4 cups, and an amount of 4 is four 1/4 cups.
        total = total_weight_g(food)
        return total / m["weight"] if total and m["weight"] else None
    return m["weight"] / m["quantity"] if m["quantity"] else None


def measure_label(m: dict | None) -> str | None:
    """A measure's name with its "#" when that isn't 1, e.g. "0.25 cup"."""
    if m is None:
        return None
    if is_relative(m) and m["quantity"] not in (None, 1.0):
        return f"{m['quantity']:g} {m['name']}"
    return m["name"]


def recipe_fraction_per_gram(food: dict) -> float | None:
    """Multiplier from grams eaten to the stored nutrient values."""
    if whole_recipe_basis(food):
        total = total_weight_g(food)
        return 1.0 / total if total else None
    return 0.01


def describe_measures(food: dict) -> list[dict]:
    out = []
    for m in measures(food):
        gpu = None if is_unweighed(m) else grams_per_unit(food, m)
        out.append({
            "measure_id": m["id"],
            "name": m["name"],
            "grams": round(gpu, 3) if gpu is not None else None,
        })
    return out


def ingredient_measure(ing: dict, child: dict) -> dict | None:
    return next((m for m in measures(child) if m["id"] == ing["measure_id"]), None)


def ingredient_grams(ing: dict, child: dict) -> float | None:
    m = ingredient_measure(ing, child)
    if m is not None and is_relative(m):
        gpu = grams_per_unit(child, m)
        return ing["amount"] * gpu if gpu is not None else None
    return ing["amount"]


def ingredient_contribution(ing: dict, child: dict | None) -> tuple[float | None, float | None, str | None, float | None]:
    """(grams, kcal, measure name, quantity in that measure) for an ingredient."""
    if child is None:
        return None, None, None, None
    m = ingredient_measure(ing, child)
    grams = ingredient_grams(ing, child)
    if m is not None and is_relative(m):
        qty = ing["amount"]
    else:
        gpu = grams_per_unit(child, m) if m else None
        qty = grams / gpu if gpu else None
    per_gram = recipe_fraction_per_gram(child)
    kcal = nutrient_values(child).get(208)
    contrib = kcal * grams * per_gram if None not in (kcal, grams, per_gram) else None
    if is_unweighed(m):
        grams = None  # stand-in weight, not real grams
    return grams, contrib, measure_label(m), qty


def build_ingredient(spec: dict, child: dict, existing: dict | None = None) -> dict:
    """Turn a caller's ingredient spec into an Ingredient for ``child``.

    spec keys: ``food_source_id``; then either ``grams``, or ``quantity`` with an
    optional ``measure`` (name or measure_id). Without a measure, plain foods
    use grams and recipes use "full recipe".
    """
    measure = spec.get("measure")
    grams = spec.get("grams")
    quantity = spec.get("quantity")
    recipe = is_recipe(child)
    name = food_name(child)
    if grams is None and quantity is None:
        raise ValueError(f"ingredient {name!r}: give grams or quantity")
    if grams is not None and quantity is not None:
        raise ValueError(f"ingredient {name!r}: give grams or quantity, not both")

    if measure is not None:
        m = next((m for m in measures(child)
                  if m["id"] == measure or m["name"].strip().lower() == str(measure).strip().lower()), None)
        if m is None:
            raise ValueError(
                f"ingredient {name!r} has no measure {measure!r}; options: "
                + ", ".join(repr(x["name"]) for x in measures(child)))
    else:
        default = "g" if grams is not None or not recipe else "full recipe"
        m = find_measure(child, default) or find_measure(child, "full recipe")
        if m is None:
            m = next((x for x in measures(child) if x["id"] == child["measures"]["default_measure_id"]), None)
        if m is None and measures(child):
            m = measures(child)[0]

    if m is not None and is_relative(m):
        # Recipe-relative measures store a count of the unit.
        if grams is not None:
            gpu = grams_per_unit(child, m)
            if not gpu:
                raise ValueError(f"recipe {name!r} has no total weight; use quantity")
            amount = float(grams) / gpu
        else:
            amount = float(quantity)
    elif grams is not None:
        if is_unweighed(m):
            raise ValueError(f"{name!r} has no gram weight (its serving sizes are "
                             "label units); give quantity and measure instead")
        amount = float(grams)
    else:
        gpu = grams_per_unit(child, m) if m else None
        if not gpu:
            raise ValueError(f"ingredient {name!r}: measure has no weight; use grams")
        amount = float(quantity) * gpu

    ing = copy.deepcopy(existing) if existing else {
        "$class": g.INGREDIENT, "id": "A", "unknown_4": 0,
    }
    ing.update({"amount": amount, "food_id": child["id"], "measure_id": m["id"] if m else 0})
    return ing
