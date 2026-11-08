"""Custom foods, recipes and meals on top of the gwt_foods codec.

How Cronometer stores these (verified against the live web app and every
recipe in a real account):

- Recipes and meals are the same ``Food`` object, ``food_type`` RECIPE or MEAL.
  Meals are just recipes that the diary can "explode" back into parts.
- A measure is either gram-based or recipe-relative (``measure_type`` 3).
  A gram-based measure's ``weight`` is the grams in ``quantity`` of the unit
  (``2 Tbsp`` -> 30 g). A recipe-relative one's ``weight`` is how many of the
  unit make the whole recipe (``g`` holds the total weight, ``Serving`` the
  servings, ``full recipe`` is 1).
- Plain foods, and older or "advanced serving size" recipes, use gram-based
  measures and store nutrients per 100 g. Recipes made by the current editor
  use recipe-relative measures and store nutrients for the whole recipe.
- An ingredient's ``amount`` is a count of its ``measure_id`` unit when that
  measure is recipe-relative (1.0 "Serving" is one serving), and grams
  otherwise, whatever unit ``measure_id`` displays (51.0 with "cup" is 51 g).
- Measure types 1 and 2 are label servings with no known weight, stored with
  a stand-in weight (usually 100 "grams") so nutrients scale. Totals
  still work; the server leaves these out of a recipe's weight, and so do we.
- The server recomputes a recipe's nutrients and total weight from its
  ingredients on save, so callers only send ingredients. A ``cooked_weight``
  property overrides the total weight; edits carry it through untouched.
"""

from __future__ import annotations

import copy

from . import gwt_foods as g

RECIPE_TYPES = ("RECIPE", "MEAL")
RECIPE_RELATIVE = 3  # Measure$Type ordinals
UNWEIGHED = (1, 2)  # label servings with no known weight; their weight is a stand-in

# Cronometer nutrient ids follow USDA SR numbering; these are the ones worth
# naming in summaries. Everything is still available by id.
NUTRIENT_NAMES = {
    208: "energy_kcal",
    203: "protein_g",
    204: "fat_g",
    205: "carbs_g",
    291: "fiber_g",
    269: "sugars_g",
    606: "saturated_fat_g",
    605: "trans_fat_g",
    645: "monounsaturated_fat_g",
    646: "polyunsaturated_fat_g",
    601: "cholesterol_mg",
    307: "sodium_mg",
    306: "potassium_mg",
    301: "calcium_mg",
    303: "iron_mg",
    304: "magnesium_mg",
    305: "phosphorus_mg",
    309: "zinc_mg",
    401: "vitamin_c_mg",
    255: "water_g",
    262: "caffeine_mg",
    221: "alcohol_g",
}
NUTRIENT_IDS = {name: nid for nid, name in NUTRIENT_NAMES.items()}

_ENGLISH = {
    "$class": g.LANGUAGE,
    "code": "en",
    "name": "English",
    "flag_url": "https://cdn1.cronometer.com/media/flags/us.png",
    "native_name": "English",
}


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


def set_food_name(food: dict, name: str) -> None:
    trans = items(food.get("translations"))
    for t in trans:
        if t["language"] and t["language"].get("code") == "en":
            t["name"] = name
            return
    if trans:
        trans[0]["name"] = name
        return
    food["translations"] = {"$class": g.ARRAY_LIST, "items": [
        {"$class": g.TRANSLATION, "language": copy.deepcopy(_ENGLISH), "name": name, "id": 0},
    ]}


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
        total = total_weight_g(food)
        return total * m["quantity"] / m["weight"] if total and m["weight"] else None
    return m["weight"] / m["quantity"] if m["quantity"] else None


def recipe_fraction_per_gram(food: dict) -> float | None:
    """Multiplier from grams eaten to the stored nutrient values."""
    if whole_recipe_basis(food):
        total = total_weight_g(food)
        return 1.0 / total if total else None
    return 0.01


def servings(food: dict) -> float | None:
    m = find_measure(food, "Serving")
    if m is None:
        return 1.0
    if is_relative(m):
        return m["weight"] or None
    gpu = grams_per_unit(food, m)
    total = total_weight_g(food)
    return total / gpu if gpu and total else None


def summarize_nutrients(values: dict[int, float], scale: float = 1.0,
                        all_nutrients: bool = False) -> dict:
    if all_nutrients:
        return {NUTRIENT_NAMES.get(k, str(k)): round(v * scale, 4) for k, v in sorted(values.items())}
    return {name: round(values[nid] * scale, 3) for nid, name in NUTRIENT_NAMES.items()
            if nid in values}


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
    return grams, contrib, (m["name"] if m else None), qty


def summarize_food(food: dict, children: dict[int, dict], depth: int,
                   all_nutrients: bool = False) -> dict:
    """Readable view of a food/recipe/meal. ``children`` maps id -> Food."""
    ftype = food_type(food)
    values = nutrient_values(food)
    out = {
        "food_source_id": food["id"],
        "name": food_name(food),
        "type": ftype,
        "source": food.get("source"),
        "notes": food.get("notes") or "",
    }
    if ftype in RECIPE_TYPES:
        total = total_weight_g(food)
        n = servings(food)
        whole = 1.0 if whole_recipe_basis(food) else (total / 100.0 if total else None)
        out["servings"] = n
        out["total_weight_g"] = total
        if whole is not None:
            out["nutrients_whole_recipe"] = summarize_nutrients(values, whole, all_nutrients)
            if n:
                out["nutrients_per_serving"] = summarize_nutrients(values, whole / n, all_nutrients)
        if not whole_recipe_basis(food):
            out["nutrients_per_100g"] = summarize_nutrients(values, 1.0, all_nutrients)
    else:
        out["nutrients_per_100g"] = summarize_nutrients(values, 1.0, all_nutrients)
    out["measures"] = describe_measures(food)
    if ftype in RECIPE_TYPES or items(food.get("ingredients")):
        out["ingredients"] = [
            summarize_ingredient(ing, children, depth, all_nutrients)
            for ing in items(food.get("ingredients"))
        ]
    return out


def summarize_ingredient(ing: dict, children: dict[int, dict], depth: int,
                         all_nutrients: bool) -> dict:
    child = children.get(ing["food_id"])
    grams, kcal, mname, qty = ingredient_contribution(ing, child)
    row = {
        "ingredient_id": ing["id"],
        "food_source_id": ing["food_id"],
        "name": food_name(child) if child else None,
        "type": food_type(child) if child else None,
        "quantity": round(qty, 4) if qty is not None else None,
        "measure": mname,
        "measure_id": ing["measure_id"],
        "grams": round(grams, 3) if grams is not None else None,
        "energy_kcal": round(kcal, 2) if kcal is not None else None,
    }
    if child and is_recipe(child) and depth > 0:
        row["recipe"] = summarize_food(child, children, depth - 1, all_nutrients)
    return row


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


def recipe_measure(name: str, weight: float) -> dict:
    return {
        "$class": g.MEASURE, "quantity": 1.0, "unknown_1": False, "food_id": 0,
        "unknown_3": False, "id": 0, "unknown_5": None, "name": name,
        "translations": {"$class": g.HASH_MAP, "entries": []},
        "measure_type": g.GwtEnum(g.MEASURE_TYPE, 3), "weight": float(weight),
    }


def food_measure(name: str, quantity: float, grams: float, food_id: int) -> dict:
    return {
        "$class": g.MEASURE, "quantity": float(quantity), "unknown_1": False,
        "food_id": food_id, "unknown_3": False, "id": 0, "unknown_5": None,
        "name": name, "translations": {"$class": g.HASH_MAP, "entries": []},
        "measure_type": g.GwtEnum(g.MEASURE_TYPE, 0), "weight": float(grams),
    }


def new_recipe(name: str, owner: int, ftype: str = "RECIPE", servings: float = 1.0,
               notes: str = "") -> dict:
    """A blank recipe or meal shaped like the web app's "Create Recipe" save."""
    return {
        "$class": g.FOOD, "unknown_0": 0, "unknown_1": False,
        "barcodes": {"$class": g.ARRAY_LIST, "items": []},
        "unknown_3": 0, "notes": notes, "unknown_5": 0, "id": 0,
        "ingredients": {"$class": g.ARRAY_LIST, "items": []},
        "label_type": g.GwtEnum(g.NUTRITION_LABEL_TYPE, 1), "version": "A",
        "measures": {"$class": g.FOOD_MEASURES, "default_measure_id": 0, "measures": {
            "$class": g.ARRAY_LIST, "items": [
                recipe_measure("full recipe", 1.0),
                recipe_measure("Serving", servings),
                recipe_measure("g", 0.0),  # server fills in the real total
            ]}},
        "nutrients": {"$class": g.NUTRIENT_MAP,
                      "filter": g.GwtEnum(g.NUTRIENT_FILTER, 0),
                      "values": {"$class": g.HASH_MAP, "entries": []}},
        "properties": {"$class": g.HASH_MAP, "entries": [("advancedServingSize", "false")]},
        "unknown_13": False, "source": "Custom",
        "tags": {"$class": g.HASH_SET, "items": []},
        "translations": {"$class": g.ARRAY_LIST, "items": [
            {"$class": g.TRANSLATION, "language": copy.deepcopy(_ENGLISH), "name": name, "id": 0},
        ]},
        "food_type": g.enum(g.FOOD_TYPE, ftype), "owner": owner,
    }


def set_servings(food: dict, count: float) -> None:
    if count <= 0:
        raise ValueError("servings must be positive")
    m = find_measure(food, "Serving")
    if whole_recipe_basis(food):
        if m is None:
            food["measures"]["measures"]["items"].append(recipe_measure("Serving", count))
        else:
            m["weight"] = float(count)
        return
    total = total_weight_g(food)
    if not total:
        raise ValueError("recipe has no total weight to divide into servings")
    if m is None:
        food["measures"]["measures"]["items"].append(food_measure("Serving", 1.0, total / count, food["id"]))
    else:
        m["quantity"], m["weight"] = 1.0, total / count


def set_nutrients(food: dict, nutrients: dict, per_grams: float = 100.0) -> None:
    """Set nutrient amounts on a plain food. Keys are ids or NUTRIENT_NAMES names."""
    if is_recipe(food):
        raise ValueError("recipe nutrients come from ingredients; edit those instead")
    if per_grams <= 0:
        raise ValueError("per_grams must be positive")
    entries = food["nutrients"]["values"]["entries"]
    by_id = {k: v for k, v in entries}
    ntype = next((v["nutrient_type"] for _, v in entries if v), g.GwtEnum(g.NUTRIENT_TYPE, 0))
    for key, amount in nutrients.items():
        nid = resolve_nutrient_id(key)
        value = float(amount) * 100.0 / per_grams
        if nid in by_id and by_id[nid] is not None:
            by_id[nid]["amount"] = value
        else:
            n = {"$class": g.NUTRIENT, "amount": value, "nutrient_id": nid, "nutrient_type": ntype}
            entries.append((nid, n))
            by_id[nid] = n


def resolve_nutrient_id(key) -> int:
    if isinstance(key, int):
        return key
    s = str(key).strip()
    if s.lstrip("-").isdigit():
        return int(s)
    if s in NUTRIENT_IDS:
        return NUTRIENT_IDS[s]
    raise ValueError(f"unknown nutrient {key!r}; use an id or one of {sorted(NUTRIENT_IDS)}")


def set_food_measures(food: dict, specs: list[dict]) -> None:
    """Replace a plain food's measures; same-named measures keep their ids."""
    if is_recipe(food):
        raise ValueError("use servings for recipes")
    old = {m["name"]: m for m in measures(food)}
    new = []
    for s in specs:
        name, grams = s["name"], float(s["grams"])
        qty = float(s.get("quantity", 1.0))
        m = old.get(name) or food_measure(name, qty, grams, food["id"])
        m["quantity"], m["weight"] = qty, grams
        new.append(m)
    food["measures"]["measures"]["items"] = new
    if food["measures"]["default_measure_id"] not in {m["id"] for m in new}:
        food["measures"]["default_measure_id"] = 0
