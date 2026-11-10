"""MCP server for Cronometer nutrition data."""

import functools
import json
import logging
import os
import threading
from datetime import date, timedelta
from pathlib import Path

from mcp.server.mcpserver import MCPServer

from . import nutrients as _nutrients
from . import targets as _tg
from .client import CronometerClient, FoodInUseError
from .markdown import generate_food_log_md

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

mcp = MCPServer(
    "cronometer",
    instructions=(
        "Cronometer MCP server for nutrition tracking. "
        "Provides access to detailed food logs, daily macro/micro summaries, "
        "exercise data, and biometrics from Cronometer Gold. "
        "Use get_food_log for individual food entries with full nutrition, "
        "get_daily_nutrition for daily macro totals, and get_micronutrients "
        "for detailed vitamin/mineral breakdowns."
    ),
)

# CRONOMETER_READ_ONLY=1 leaves every tool that changes the Cronometer account
# unregistered, so a client never sees them. The credentials still have full
# access; this limits what the server exposes, not what the login can do.
READ_ONLY = os.environ.get("CRONOMETER_READ_ONLY", "").strip().lower() in (
    "1", "true", "yes",
)
if READ_ONLY:
    logger.info("Read-only mode: tools that change Cronometer data are disabled")


# MCP SDK 2 runs sync tools in worker threads, so parallel tool calls would
# share one client's session, nonce and login state at once. SDK 1 ran them
# inline, one at a time; keep that.
_client_lock = threading.Lock()


def _tool(fn):
    """Register a tool that runs while holding the client lock."""
    @functools.wraps(fn)
    def serialized(*args, **kwargs):
        with _client_lock:
            return fn(*args, **kwargs)
    return mcp.tool()(serialized)


def _write_tool(fn):
    """Register a tool that changes Cronometer data, unless read-only."""
    return fn if READ_ONLY else _tool(fn)


_client: CronometerClient | None = None


def _get_client() -> CronometerClient:
    global _client
    if _client is None:
        _client = CronometerClient()
    return _client


def _parse_date(d: str | None) -> date | None:
    if d is None:
        return None
    return date.fromisoformat(d)


# Non-nutrient metadata columns to exclude from nutrient extraction
_META_COLS = {
    "Day", "Date", "Time", "Group", "Food Name", "Amount", "Unit",
    "Category", "Completed",
}

# Macro columns (energy + macronutrients)
_MACRO_KEYWORDS = {
    "Energy", "Protein", "Carbs", "Fat", "Fiber", "Net Carbs",
    "Sugars", "Sugar Alcohol", "Starch", "Saturated", "Monounsaturated",
    "Polyunsaturated", "Trans-Fats", "Cholesterol", "Sodium", "Potassium",
    "Water", "Alcohol", "Caffeine", "Omega-3", "Omega-6",
}

# Amino acid columns
_AMINO_KEYWORDS = {
    "Cystine", "Histidine", "Isoleucine", "Leucine", "Lysine",
    "Methionine", "Phenylalanine", "Threonine", "Tryptophan",
    "Tyrosine", "Valine",
}


def _classify_column(col: str) -> str:
    """Classify a column as 'meta', 'macro', 'amino', or 'micro'."""
    if col in _META_COLS:
        return "meta"
    base = col.split("(")[0].strip()
    if base in _MACRO_KEYWORDS:
        return "macro"
    if base in _AMINO_KEYWORDS:
        return "amino"
    return "micro"


def _extract_nutrients(row: dict, category: str | None = None) -> dict:
    """Extract nutrient values from a row, optionally filtered by category."""
    result = {}
    for col, val in row.items():
        if _classify_column(col) == "meta":
            continue
        if category and _classify_column(col) != category:
            continue
        val = str(val).strip()
        if val:
            try:
                num = float(val)
                if num != 0.0:
                    result[col] = round(num, 2)
            except ValueError:
                pass
    return result


def _format_servings(rows: list[dict]) -> list[dict]:
    """Format servings export into a cleaner structure."""
    formatted = []
    for row in rows:
        entry = {
            "date": row.get("Day", ""),
            "time": row.get("Time", ""),
            "meal": row.get("Group", ""),
            "food": row.get("Food Name", ""),
            "amount": row.get("Amount", ""),
            "category": row.get("Category", ""),
            "macros": _extract_nutrients(row, "macro"),
            "micros": _extract_nutrients(row, "micro"),
        }
        formatted.append(entry)
    return formatted


@_tool
def get_food_log(
    start_date: str | None = None,
    end_date: str | None = None,
) -> str:
    """Get detailed food log with individual food entries and full nutrition.

    Returns every food entry with macros and micronutrients.
    Great for analyzing what was eaten and spotting nutrient gaps.

    Args:
        start_date: Start date as YYYY-MM-DD (defaults to today).
        end_date: End date as YYYY-MM-DD (defaults to today).
    """
    try:
        client = _get_client()
        start = _parse_date(start_date)
        end = _parse_date(end_date)
        rows = client.get_food_log(start, end)
        formatted = _format_servings(rows)

        # Group by date
        by_date: dict[str, list] = {}
        for entry in formatted:
            d = entry["date"]
            by_date.setdefault(d, []).append(entry)

        return json.dumps({
            "status": "success",
            "date_range": {
                "start": start_date or str(date.today()),
                "end": end_date or str(date.today()),
            },
            "total_entries": len(formatted),
            "days": {
                d: {
                    "entries": entries,
                    "total_calories": round(sum(
                        e["macros"].get("Energy (kcal)", 0) for e in entries
                    ), 1),
                    "total_protein": round(sum(
                        e["macros"].get("Protein (g)", 0) for e in entries
                    ), 1),
                    "total_carbs": round(sum(
                        e["macros"].get("Carbs (g)", 0) for e in entries
                    ), 1),
                    "total_fat": round(sum(
                        e["macros"].get("Fat (g)", 0) for e in entries
                    ), 1),
                }
                for d, entries in by_date.items()
            },
        }, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "message": f"{type(e).__name__}: {e}"})


@_tool
def get_daily_nutrition(
    start_date: str | None = None,
    end_date: str | None = None,
) -> str:
    """Get daily nutrition summary with macro totals per day.

    Returns calorie, protein, carb, fat, and fiber totals for each day.
    Use this for quick daily overviews and trend analysis.

    Args:
        start_date: Start date as YYYY-MM-DD (defaults to 7 days ago).
        end_date: End date as YYYY-MM-DD (defaults to today).
    """
    try:
        client = _get_client()
        start = _parse_date(start_date) or (date.today() - timedelta(days=7))
        end = _parse_date(end_date)
        rows = client.get_daily_summary(start, end)

        summaries = []
        for row in rows:
            summaries.append({
                "date": row.get("Date", ""),
                "macros": _extract_nutrients(row, "macro"),
                "micros": _extract_nutrients(row, "micro"),
            })

        return json.dumps({
            "status": "success",
            "date_range": {
                "start": str(start),
                "end": str(end or date.today()),
            },
            "days": summaries,
        }, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "message": f"{type(e).__name__}: {e}"})


@_tool
def get_micronutrients(
    start_date: str | None = None,
    end_date: str | None = None,
) -> str:
    """Get detailed micronutrient breakdown for meal planning.

    Shows vitamins, minerals, and other micronutrients per day with
    period averages. Use this to identify nutrient gaps and plan meals.

    Args:
        start_date: Start date as YYYY-MM-DD (defaults to 7 days ago).
        end_date: End date as YYYY-MM-DD (defaults to today).
    """
    try:
        client = _get_client()
        start = _parse_date(start_date) or (date.today() - timedelta(days=7))
        end = _parse_date(end_date)
        rows = client.get_daily_summary(start, end)

        days = []
        for row in rows:
            micros = _extract_nutrients(row, "micro")
            if micros:
                days.append({
                    "date": row.get("Date", ""),
                    "micronutrients": micros,
                })

        # Compute averages across the range
        averages = {}
        if days:
            all_keys = set()
            for d in days:
                all_keys.update(d["micronutrients"].keys())
            for key in sorted(all_keys):
                vals = [
                    d["micronutrients"][key]
                    for d in days
                    if key in d["micronutrients"]
                    and isinstance(d["micronutrients"][key], (int, float))
                ]
                if vals:
                    averages[key] = round(sum(vals) / len(vals), 2)

        return json.dumps({
            "status": "success",
            "date_range": {
                "start": str(start),
                "end": str(end or date.today()),
            },
            "daily_breakdown": days,
            "period_averages": averages,
        }, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "message": f"{type(e).__name__}: {e}"})


@_tool
def export_raw_csv(
    export_type: str,
    start_date: str | None = None,
    end_date: str | None = None,
) -> str:
    """Export raw CSV data from Cronometer for any data type.

    Useful when you need the full unprocessed export.

    Args:
        export_type: One of 'servings', 'daily_summary', 'exercises',
                    'biometrics', 'notes'.
        start_date: Start date as YYYY-MM-DD (defaults to today).
        end_date: End date as YYYY-MM-DD (defaults to today).
    """
    try:
        client = _get_client()
        start = _parse_date(start_date)
        end = _parse_date(end_date)
        raw = client.export_raw(export_type, start, end)
        if len(raw) > 50000:
            return json.dumps({
                "status": "success",
                "truncated": True,
                "total_chars": len(raw),
                "data": raw[:50000] + "\n... (truncated)",
            })
        return json.dumps({"status": "success", "data": raw})
    except Exception as e:
        return json.dumps({"status": "error", "message": f"{type(e).__name__}: {e}"})


# Legacy fallback only. Diary group slots are user-configurable, so these
# names are correct for a default account and wrong for a customized one.
# _resolve_diary_group() consults the account's real groups instead.
_DIARY_GROUP_MAP: dict[str, int] = {
    "breakfast": 1,
    "lunch": 2,
    "dinner": 3,
    "snacks": 4,
}


def _default_group_name() -> str:
    """The canonical meal name for the current time of day."""
    from datetime import datetime

    hour = datetime.now().hour
    if hour < 11:
        return "breakfast"
    elif hour < 16:
        return "lunch"
    elif hour < 21:
        return "dinner"
    return "snacks"


def _format_groups(groups: list[dict]) -> str:
    """Render the account's groups for an error message."""
    return ", ".join(
        f"{g['wire_index']}={g['name']!r}" + ("" if g["enabled"] else " (disabled)")
        for g in groups
    )


def _resolve_diary_group(client, diary_group) -> tuple[int | None, str | None]:
    """Resolve a diary_group argument to a wire index.

    Accepts a group name (case-insensitive, matched against the account's own
    group names) or a 0-based wire index. Returns ``(wire_index, None)`` on
    success or ``(None, error_message)`` on failure.

    Never falls back to a default when a name fails to match: silently logging
    food into the wrong meal is worse than an error, and is the defect this
    resolver exists to fix. Cronometer itself accepts out-of-range indices
    without complaint and the resulting entries are unreachable, so every
    check here has to happen client-side.
    """
    groups = client.diary_groups

    if diary_group is None or (isinstance(diary_group, str) and not diary_group.strip()):
        wanted = _default_group_name()
        match = next(
            (g for g in groups if g["enabled"] and g["name"].strip().lower() == wanted),
            None,
        )
        if match is None:
            return None, (
                f"No enabled diary group named '{wanted}' on this account, so the "
                f"time-of-day default cannot be applied. Pass diary_group "
                f"explicitly. Your groups: {_format_groups(groups)}"
            )
        return match["wire_index"], None

    # Explicit wire index
    raw = str(diary_group).strip()
    if raw.lstrip("-").isdigit():
        idx = int(raw)
        match = next((g for g in groups if g["wire_index"] == idx), None)
        if match is None:
            return None, (
                f"diary_group {idx} is out of range. Your groups: "
                f"{_format_groups(groups)}"
            )
        if not match["enabled"]:
            return None, (
                f"Diary group {idx} ({match['name']!r}) is not enabled on this "
                f"account. Enable it in Cronometer settings first. Your groups: "
                f"{_format_groups(groups)}"
            )
        return idx, None

    # Name match against the account's own group names
    key = raw.lower()
    match = next((g for g in groups if g["name"].strip().lower() == key), None)
    if match is None:
        return None, (
            f"No diary group named {raw!r} on this account. Your groups: "
            f"{_format_groups(groups)}"
        )
    if not match["enabled"]:
        return None, (
            f"Diary group {match['name']!r} is not enabled on this account. "
            f"Enable it in Cronometer settings first. Your groups: "
            f"{_format_groups(groups)}"
        )
    return match["wire_index"], None


@_tool
def list_diary_groups() -> str:
    """List this account's diary groups, with the values add_food_entry accepts.

    Diary group names are user-configured in Cronometer, so they are NOT
    always Breakfast/Lunch/Dinner/Snacks. Call this before assuming a name
    exists — an account may rename any slot or enable extra ones.

    Returns each group's wire_index (the 0-based value add_food_entry takes),
    its name, and whether it is enabled. Only enabled groups can be written to.
    """
    try:
        client = _get_client()
        groups = client.diary_groups
        return json.dumps({
            "status": "success",
            "count": len(groups),
            "groups": [
                {
                    "wire_index": g["wire_index"],
                    "name": g["name"],
                    "enabled": g["enabled"],
                }
                for g in groups
            ],
            "note": (
                "Pass either the name or the wire_index as diary_group. "
                "Disabled groups are rejected."
            ),
        }, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "message": f"{type(e).__name__}: {e}"})


@_tool
def search_foods(query: str) -> str:
    """Search Cronometer's food database by name.

    Returns matching foods with their IDs and source information needed
    to add a serving (food_id, food_source_id, measure_id).

    Args:
        query: Food name or keyword to search for (e.g. "eggs", "chicken breast").
    """
    try:
        client = _get_client()
        foods = client.find_foods(query)
        return json.dumps(
            {
                "status": "success",
                "query": query,
                "count": len(foods),
                "foods": foods,
            },
            indent=2,
        )
    except Exception as e:
        return json.dumps({"status": "error", "message": f"{type(e).__name__}: {e}"})


@_tool
def get_food_details(food_source_id: int) -> str:
    """Get detailed food information including available serving measures.

    Use this after search_foods to get the measure_id needed for add_food_entry.
    Returns all available serving sizes with their numeric IDs and gram weights.

    Args:
        food_source_id: Food source ID from search_foods results.
    """
    try:
        client = _get_client()
        result = client.get_food(food_source_id)
        # Remove raw_response from the output to keep it clean
        output = {
            "status": "success",
            "food_source_id": result["food_source_id"],
            "measures": result["measures"],
        }
        return json.dumps(output, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "message": f"{type(e).__name__}: {e}"})


@_write_tool
def add_food_entry(
    food_id: int,
    food_source_id: int,
    weight_grams: float,
    date: str,
    measure_id: int = 0,
    quantity: float = 0,
    diary_group: str | int | None = None,
) -> str:
    """Add a food entry to the Cronometer diary.

    Use search_foods to find food_id and food_source_id, then
    get_food_details for measure_id and weight_grams.

    For CRDB/custom foods, you can omit measure_id (defaults to a
    universal NCCDB measure that works for all food sources).
    When measure_id is omitted, quantity is set to weight_grams.

    Args:
        food_id: Numeric food ID from search_foods results.
        food_source_id: Food source ID from search_foods results.
        weight_grams: Weight of the serving in grams.
        date: Date to log the entry as YYYY-MM-DD (e.g. "2026-03-04").
        measure_id: Measure/unit ID. Pass 0 (default) to use the universal
                    measure that works for all food sources.
        quantity: Number of servings. Defaults to weight_grams when
                  measure_id is 0 (universal gram-based measure).
        diary_group: Diary group name (case-insensitive) or 0-based index.
                     Names are user-configured and are not always
                     Breakfast/Lunch/Dinner/Snacks — call list_diary_groups to
                     see this account's groups. Defaults to a time-of-day
                     choice among enabled groups if omitted.
    """
    try:
        client = _get_client()
        group_int, group_err = _resolve_diary_group(client, diary_group)
        if group_err is not None:
            return json.dumps({"status": "error", "message": group_err})

        if measure_id == 0 and quantity == 0:
            quantity = weight_grams

        from datetime import date as date_type
        log_date = date_type.fromisoformat(date)

        result = client.add_serving(
            food_id=food_id,
            food_source_id=food_source_id,
            measure_id=measure_id,
            quantity=quantity,
            weight_grams=weight_grams,
            day=log_date,
            diary_group=group_int,
        )
        return json.dumps({
            "status": "success",
            "entry": result,
            "note": (
                "Use the serving_id to remove this entry with remove_food_entry "
                "if needed."
            ),
        }, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "message": f"{type(e).__name__}: {e}"})


@_write_tool
def remove_food_entry(serving_id: str) -> str:
    """Remove a food entry from the Cronometer diary.

    Args:
        serving_id: The serving ID returned by add_food_entry (e.g. "D80lp$").
    """
    try:
        client = _get_client()
        client.remove_serving(serving_id)
        return json.dumps({
            "status": "success",
            "serving_id": serving_id,
            "message": "Serving removed from diary.",
        }, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "message": f"{type(e).__name__}: {e}"})


@_tool
def get_macro_targets(
    target_date: str | None = None,
) -> str:
    """Get current daily macro targets from Cronometer.

    Returns the effective macro targets (protein, fat, carbs, calories)
    and the template name for a specific date or all days of the week.
    A macro with no target set in Cronometer is null. For a date, "source"
    says where the targets come from: "day" (set for that day in the Daily
    Target Editor), "weekly_schedule", or "none" (no template applies).
    Templates in ratio mode give protein/fat/carbs as percent of energy,
    not grams; list_macro_templates shows each template's units.

    Args:
        target_date: Date as YYYY-MM-DD to get targets for (defaults to today).
                     Pass "all" to get the full weekly schedule.
    """
    try:
        client = _get_client()

        if target_date == "all":
            schedules = client.get_all_macro_schedules()
            return json.dumps({
                "status": "success",
                "type": "weekly_schedule",
                "schedules": schedules,
            }, indent=2)

        day = _parse_date(target_date)
        targets = client.get_daily_macro_targets(day)
        return json.dumps({
            "status": "success",
            "type": "daily_targets",
            "date": target_date or str(date.today()),
            "targets": targets,
        }, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "message": f"{type(e).__name__}: {e}"})


@_write_tool
def set_macro_targets(
    protein_grams: float | None = None,
    fat_grams: float | None = None,
    carbs_grams: float | None = None,
    calories: float | None = None,
    target_date: str | None = None,
    template_name: str | None = None,
) -> str:
    """Update daily macro targets in Cronometer.

    Reads current targets first, then updates only the provided values.
    Omitted values remain unchanged.

    Args:
        protein_grams: Protein target in grams.
        fat_grams: Fat target in grams.
        carbs_grams: Net carbs target in grams.
        calories: Calorie target in kcal.
        target_date: Date as YYYY-MM-DD (defaults to today).
        template_name: Template name (defaults to "Custom Targets").
    """
    try:
        from datetime import date as date_type

        client = _get_client()
        day = date_type.fromisoformat(target_date) if target_date else date.today()

        # Read current targets to preserve unchanged values
        current = client.get_daily_macro_targets(day)

        new_protein = protein_grams if protein_grams is not None else current["protein_g"]
        new_fat = fat_grams if fat_grams is not None else current["fat_g"]
        new_carbs = carbs_grams if carbs_grams is not None else current["carbs_g"]
        new_calories = calories if calories is not None else current["calories"]
        name = template_name or "Custom Targets"

        # A macro with no target in Cronometer reads as None. Writing it back
        # would mean inventing a value, so ask for it instead.
        unset = [
            label for label, value in (
                ("protein_grams", new_protein), ("fat_grams", new_fat),
                ("carbs_grams", new_carbs), ("calories", new_calories),
            ) if value is None
        ]
        if unset:
            return json.dumps({
                "status": "error",
                "message": (
                    f"No current target for {', '.join(unset)} on {day}; "
                    "pass explicit values for them."
                ),
                "current": current,
            }, indent=2)

        client.update_daily_targets(
            day=day,
            protein_g=new_protein,
            fat_g=new_fat,
            carbs_g=new_carbs,
            calories=new_calories,
            template_name=name,
        )

        # Read back to confirm
        updated = client.get_daily_macro_targets(day)
        return json.dumps({
            "status": "success",
            "date": str(day),
            "previous": current,
            "updated": updated,
        }, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "message": f"{type(e).__name__}: {e}"})


_DOW_NAMES = ["Sunday", "Monday", "Tuesday", "Wednesday",
              "Thursday", "Friday", "Saturday"]


@_write_tool
def set_weekly_macro_schedule(
    template_name: str,
    days: str = "all",
) -> str:
    """Set the recurring weekly macro schedule by assigning a template to days.

    This updates the DEFAULT schedule that applies to all future dates,
    not just a specific date override.

    First finds the template by name (from existing saved templates or
    from a recently created per-date template), then assigns it to the
    specified days of the week.

    Args:
        template_name: Name of a saved macro target template
                       (e.g. "Retatrutide GI-Optimized", "Keto Rigorous").
        days: Comma-separated day names or "all" (default).
              E.g. "Monday,Wednesday,Friday" or "all".
    """
    try:
        client = _get_client()

        # Get available templates
        templates = client.get_macro_target_templates()
        template_map = {t["template_name"]: t for t in templates}

        if template_name not in template_map:
            return json.dumps({
                "status": "error",
                "message": f"Template '{template_name}' not found.",
                "available_templates": [t["template_name"] for t in templates],
            }, indent=2)

        template_id = template_map[template_name]["template_id"]

        # Parse which days to update
        if days.strip().lower() == "all":
            target_days = list(range(7))  # 0=Sun through 6=Sat (US ordering)
        else:
            day_name_map = {name.lower(): i for i, name in enumerate(_DOW_NAMES)}
            target_days = []
            for d in days.split(","):
                d = d.strip().lower()
                if d in day_name_map:
                    target_days.append(day_name_map[d])
                else:
                    return json.dumps({
                        "status": "error",
                        "message": f"Invalid day name: '{d}'",
                        "valid_days": _DOW_NAMES,
                    }, indent=2)

        # Apply template to each day
        results = []
        for dow in target_days:
            client.save_macro_schedule(dow, template_id)
            results.append({
                "day": _DOW_NAMES[dow],
                "template_name": template_name,
                "template_id": template_id,
            })

        # Read back the full schedule to confirm
        updated_schedule = client.get_all_macro_schedules()

        return json.dumps({
            "status": "success",
            "days_updated": results,
            "current_schedule": updated_schedule,
        }, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "message": f"{type(e).__name__}: {e}"})


@_tool
def list_macro_templates() -> str:
    """List all saved macro target templates in Cronometer.

    Returns template names, IDs, and their macro values.
    Use this to find the template_name for set_weekly_macro_schedule.
    """
    try:
        client = _get_client()
        templates = client.get_macro_target_templates()
        return json.dumps({
            "status": "success",
            "count": len(templates),
            "templates": templates,
        }, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "message": f"{type(e).__name__}: {e}"})


@_write_tool
def create_macro_template(
    template_name: str,
    protein_grams: float,
    fat_grams: float,
    carbs_grams: float,
    calories: float,
    assign_to_all_days: bool = False,
) -> str:
    """Create a new saved macro target template in Cronometer.

    Optionally assigns it to all days of the week as the recurring default.

    Args:
        template_name: Name for the new template (e.g. "Retatrutide GI-Optimized").
        protein_grams: Protein target in grams.
        fat_grams: Fat target in grams.
        carbs_grams: Net carbs target in grams.
        calories: Calorie target in kcal.
        assign_to_all_days: If True, also set this as the recurring weekly
                            schedule for all 7 days (default False).
    """
    try:
        client = _get_client()

        # Check if template already exists
        existing = client.get_macro_target_templates()
        for t in existing:
            if t["template_name"] == template_name:
                return json.dumps({
                    "status": "error",
                    "message": (
                        f"Template '{template_name}' already exists "
                        f"(id={t['template_id']}). Use set_weekly_macro_schedule "
                        "to assign it to days."
                    ),
                    "existing_template": t,
                }, indent=2)

        # Create the template
        template_id = client.save_macro_target_template(
            template_name=template_name,
            protein_g=protein_grams,
            fat_g=fat_grams,
            carbs_g=carbs_grams,
            calories=calories,
        )

        result = {
            "status": "success",
            "template_name": template_name,
            "template_id": template_id,
            "macros": {
                "protein_g": protein_grams,
                "fat_g": fat_grams,
                "carbs_g": carbs_grams,
                "calories": calories,
            },
        }

        # Optionally assign to all days
        if assign_to_all_days and template_id:
            for dow in range(7):
                client.save_macro_schedule(dow, template_id)
            schedule = client.get_all_macro_schedules()
            result["weekly_schedule_updated"] = True
            result["current_schedule"] = schedule

        return json.dumps(result, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "message": f"{type(e).__name__}: {e}"})


@_tool
def get_fasting_history(
    start_date: str | None = None,
    end_date: str | None = None,
) -> str:
    """Get fasting history from Cronometer.

    Returns all fasts (or fasts within a date range) with their status,
    names, recurrence rules, and timestamps.

    Args:
        start_date: Start date as YYYY-MM-DD (omit for all history).
        end_date: End date as YYYY-MM-DD (omit for all history).
    """
    try:
        client = _get_client()

        if start_date and end_date:
            start = _parse_date(start_date)
            end = _parse_date(end_date)
            fasts = client.get_user_fasts_for_range(start, end)
        else:
            fasts = client.get_user_fasts()

        active = [f for f in fasts if f.get("is_active")]
        completed = [f for f in fasts if not f.get("is_active")]

        return json.dumps({
            "status": "success",
            "total_fasts": len(fasts),
            "active_fasts": len(active),
            "completed_fasts": len(completed),
            "fasts": fasts,
        }, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "message": f"{type(e).__name__}: {e}"})


@_tool
def get_fasting_stats() -> str:
    """Get aggregate fasting statistics from Cronometer.

    Returns total fasting hours, longest fast, 7-fast average,
    and completed fast count.
    """
    try:
        client = _get_client()
        stats = client.get_fasting_stats()
        return json.dumps({
            "status": "success",
            "stats": stats,
        }, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "message": f"{type(e).__name__}: {e}"})


@_write_tool
def delete_fast(fast_id: int) -> str:
    """Delete a fast entry from Cronometer.

    Use get_fasting_history first to find the fast_id.

    Args:
        fast_id: The fast ID to delete.
    """
    try:
        client = _get_client()
        client.delete_fast(fast_id)
        return json.dumps({
            "status": "success",
            "fast_id": fast_id,
            "message": "Fast deleted.",
        }, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "message": f"{type(e).__name__}: {e}"})


@_write_tool
def cancel_active_fast(fast_id: int) -> str:
    """Cancel an active (in-progress) fast while preserving the recurring schedule.

    Use get_fasting_history to find active fasts (is_active=true).

    Args:
        fast_id: The fast ID of the active fast to cancel.
    """
    try:
        client = _get_client()
        client.cancel_fast_keep_series(fast_id)
        return json.dumps({
            "status": "success",
            "fast_id": fast_id,
            "message": "Active fast cancelled. Recurring schedule preserved.",
        }, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "message": f"{type(e).__name__}: {e}"})


@_tool
def get_recent_biometrics() -> str:
    """Get the most recently logged biometric entries from Cronometer.

    Returns recent values for weight, blood glucose, blood pressure,
    heart rate, body fat, and other tracked biometrics.
    """
    try:
        client = _get_client()
        biometrics = client.get_recent_biometrics()
        return json.dumps({
            "status": "success",
            "count": len(biometrics),
            "biometrics": biometrics,
        }, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "message": f"{type(e).__name__}: {e}"})


@_write_tool
def add_biometric(
    metric_type: str,
    value: float,
    entry_date: str,
) -> str:
    """Add a biometric entry to Cronometer.

    Supported metric types: weight (lbs), blood_glucose (mg/dL),
    heart_rate (bpm), body_fat (%).

    Args:
        metric_type: One of 'weight', 'blood_glucose', 'heart_rate', 'body_fat'.
        value: The value in display units (lbs, mg/dL, bpm, %).
        entry_date: Date as YYYY-MM-DD.
    """
    try:
        from datetime import date as date_type

        client = _get_client()
        day = date_type.fromisoformat(entry_date)
        biometric_id = client.add_biometric(
            metric_type=metric_type,
            value=value,
            day=day,
        )
        return json.dumps({
            "status": "success",
            "metric_type": metric_type,
            "value": value,
            "date": entry_date,
            "biometric_id": biometric_id,
            "note": "Use biometric_id with remove_biometric to delete this entry.",
        }, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "message": f"{type(e).__name__}: {e}"})


@_write_tool
def remove_biometric(biometric_id: str) -> str:
    """Remove a biometric entry from Cronometer.

    Use get_recent_biometrics to find biometric_id values.

    Args:
        biometric_id: The biometric entry ID (e.g. "BXW0DA").
    """
    try:
        client = _get_client()
        client.remove_biometric(biometric_id)
        return json.dumps({
            "status": "success",
            "biometric_id": biometric_id,
            "message": "Biometric entry removed.",
        }, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "message": f"{type(e).__name__}: {e}"})


def _get_data_dir() -> Path:
    """Get the data directory for sync output.

    Uses CRONOMETER_DATA_DIR env var if set, otherwise defaults to
    ~/.local/share/cronometer-mcp/.
    """
    env_dir = os.environ.get("CRONOMETER_DATA_DIR")
    if env_dir:
        return Path(env_dir)
    return Path.home() / ".local" / "share" / "cronometer-mcp"


@_tool
def sync_cronometer(
    start_date: str | None = None,
    end_date: str | None = None,
    days: int = 14,
    diet_label: str | None = None,
) -> str:
    """Download Cronometer data and save locally as JSON + food-log.md.

    Downloads servings and daily summary data, saves JSON exports,
    and regenerates food-log.md.

    Output directory defaults to ~/.local/share/cronometer-mcp/ but can
    be overridden with the CRONOMETER_DATA_DIR environment variable.

    Args:
        start_date: Start date as YYYY-MM-DD (defaults to `days` ago).
        end_date: End date as YYYY-MM-DD (defaults to today).
        days: Number of days to look back if start_date not specified (default 14).
        diet_label: Optional diet label for the markdown header (e.g., "Keto Rigorous").
    """
    try:
        client = _get_client()

        end = _parse_date(end_date) or date.today()
        start = _parse_date(start_date) or (end - timedelta(days=days))

        # Download both exports
        servings = client.get_food_log(start, end)
        daily_summary = client.get_daily_summary(start, end)

        # Save to data directory
        data_dir = _get_data_dir()
        exports_dir = data_dir / "exports"
        exports_dir.mkdir(parents=True, exist_ok=True)

        servings_path = exports_dir / f"servings_{start}_{end}.json"
        servings_path.write_text(json.dumps(servings, indent=2))

        summary_path = exports_dir / f"daily_summary_{start}_{end}.json"
        summary_path.write_text(json.dumps(daily_summary, indent=2))

        # Also save a "latest" copy for easy access
        latest_servings = exports_dir / "servings_latest.json"
        latest_servings.write_text(json.dumps(servings, indent=2))

        latest_summary = exports_dir / "daily_summary_latest.json"
        latest_summary.write_text(json.dumps(daily_summary, indent=2))

        # Generate food-log.md
        food_log_path = data_dir / "food-log.md"
        md_content = generate_food_log_md(
            servings, daily_summary, start, end, diet_label=diet_label,
        )
        food_log_path.write_text(md_content)

        return json.dumps({
            "status": "success",
            "date_range": {"start": str(start), "end": str(end)},
            "servings_count": len(servings),
            "days_count": len(daily_summary),
            "files_saved": [
                str(servings_path),
                str(summary_path),
                str(latest_servings),
                str(latest_summary),
                str(food_log_path),
            ],
        }, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "message": f"{type(e).__name__}: {e}"})


@_write_tool
def copy_day(source_date: str, destination_date: str) -> str:
    """Copy all diary entries from one date to another.

    Server-side operation that copies ALL entries (food, exercise,
    notes, biometrics) from source to destination. Additive — does
    not remove existing entries on the destination date.

    Args:
        source_date: Date to copy FROM as YYYY-MM-DD.
        destination_date: Date to copy TO as YYYY-MM-DD.
    """
    try:
        from datetime import date as date_type
        src = date_type.fromisoformat(source_date)
        dst = date_type.fromisoformat(destination_date)
        client = _get_client()
        client.copy_day(src, dst)
        return json.dumps({
            "status": "success",
            "message": f"Copied all entries from {source_date} to {destination_date}.",
            "source_date": source_date,
            "destination_date": destination_date,
        }, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "message": f"{type(e).__name__}: {e}"})


@_write_tool
def set_day_complete(date: str, complete: bool = True) -> str:
    """Mark a diary day as complete or incomplete.

    Args:
        date: Date to mark as YYYY-MM-DD.
        complete: True to mark complete, False to mark incomplete.
    """
    try:
        from datetime import date as date_type
        day = date_type.fromisoformat(date)
        client = _get_client()
        client.set_day_complete(day, complete)
        status = "complete" if complete else "incomplete"
        return json.dumps({
            "status": "success",
            "message": f"Marked {date} as {status}.",
            "date": date,
            "complete": complete,
        }, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "message": f"{type(e).__name__}: {e}"})


@_tool
def get_repeated_items() -> str:
    """List all recurring food entries.

    Returns all repeat items configured in Cronometer, including
    their food name, quantity, measure, diary group, and which
    days of the week they repeat on.
    """
    try:
        client = _get_client()
        items = client.get_repeated_items()
        return json.dumps({
            "status": "success",
            "count": len(items),
            "items": items,
        }, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "message": f"{type(e).__name__}: {e}"})


@_write_tool
def add_repeat_item(
    food_id: int,
    food_source_id: int,
    quantity: float,
    food_name: str,
    diary_group: str | int | None = None,
    days_of_week: str = "all",
) -> str:
    """Add a recurring food entry that auto-logs on selected days.

    Quantity is in default servings for the food (e.g., for coffee where
    the default serving is 1 cup, quantity=12 means 12 cups).

    Use search_foods to find food_id and food_source_id.

    Args:
        food_id: Numeric food ID from search_foods results.
        food_source_id: Food source ID from search_foods results.
        quantity: Number of default servings.
        food_name: Display name for the food.
        diary_group: Diary group name (case-insensitive) or 0-based index.
                     Names are user-configured — call list_diary_groups to see
                     this account's groups. Defaults to a time-of-day choice
                     among enabled groups if omitted.
        days_of_week: Comma-separated day numbers (0=Sun, 1=Mon, ..., 6=Sat),
                      or "all" for every day (default), or "weekdays", or "weekends".
    """
    try:
        client = _get_client()
        group_int, group_err = _resolve_diary_group(client, diary_group)
        if group_err is not None:
            return json.dumps({"status": "error", "message": group_err})

        # Parse days_of_week
        days_str = days_of_week.strip().lower()
        if days_str == "all":
            days = [0, 1, 2, 3, 4, 5, 6]
        elif days_str == "weekdays":
            days = [1, 2, 3, 4, 5]
        elif days_str == "weekends":
            days = [0, 6]
        else:
            days = [int(d.strip()) for d in days_of_week.split(",")]

        client.add_repeat_item(
            food_source_id=food_source_id,
            food_id=food_id,
            quantity=quantity,
            food_name=food_name,
            diary_group=group_int,
            days_of_week=days,
        )
        day_names = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
        day_labels = [day_names[d] for d in days]
        return json.dumps({
            "status": "success",
            "message": f"Added '{food_name}' as recurring entry.",
            "food_name": food_name,
            "diary_group": diary_group,
            "days": day_labels,
            "quantity": quantity,
        }, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "message": f"{type(e).__name__}: {e}"})


@_write_tool
def delete_repeat_item(repeat_item_id: int) -> str:
    """Delete a recurring food entry.

    Use get_repeated_items to find the repeat_item_id.

    Args:
        repeat_item_id: The ID of the repeat item to delete.
    """
    try:
        client = _get_client()
        client.delete_repeat_item(repeat_item_id)
        return json.dumps({
            "status": "success",
            "message": f"Deleted repeat item {repeat_item_id}.",
            "repeat_item_id": repeat_item_id,
        }, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "message": f"{type(e).__name__}: {e}"})


# --- custom foods, recipes and meals ------------------------------------------

def _error(e: Exception) -> str:
    if isinstance(e, FoodInUseError):
        return json.dumps({
            "status": "needs_decision",
            "food_source_id": e.food_id,
            "usage": e.usage,
            "message": str(e),
        }, indent=2)
    return json.dumps({"status": "error", "message": f"{type(e).__name__}: {e}"})


@_tool
def list_custom_foods(food_type: str | None = None, query: str | None = None) -> str:
    """List your own custom foods, recipes and meals, newest first.

    Args:
        food_type: Optional filter: "FOOD", "RECIPE" or "MEAL".
        query: Optional case-insensitive text to match in the name.
    """
    try:
        foods = _get_client().list_custom_foods(food_type, query)
        return json.dumps({"status": "success", "count": len(foods), "foods": foods}, indent=2)
    except Exception as e:
        return _error(e)


@_tool
def get_custom_food(food_source_id: int, depth: int = 1, all_nutrients: bool = False) -> str:
    """Show a food, recipe or meal: nutrients, serving sizes and ingredients.

    Works for any food id, including database foods. Recipes report nutrients
    for the whole recipe and per serving; plain foods per 100 g. Each
    ingredient shows its quantity, measure, grams and calories.

    Args:
        food_source_id: Food id (from list_custom_foods or search_foods).
        depth: Levels of nested recipes to expand inline (0 = just name them).
        all_nutrients: Include every nutrient (unnamed ones keyed by
                       Cronometer nutrient id), not just the common ones.
    """
    try:
        food = _get_client().get_custom_food(food_source_id, depth, all_nutrients)
        return json.dumps({"status": "success", "food": food}, indent=2, ensure_ascii=False)
    except Exception as e:
        return _error(e)


@_tool
def get_exercise_log(start_date: str | None = None, end_date: str | None = None) -> str:
    """List exercise entries, including those synced from fitness trackers.

    Reads the diary directly, so it does not use up CSV export quota. Each
    entry has its minutes, calories burned (positive), source ("Garmin",
    "manual"...), any tracker metadata, and a kind:
    - "tracker_activity": a tracker's all-day burn, e.g. "Daily Activity
      (Garmin)". Cronometer counts it as Tracker Activity and subtracts it
      from baseline activity.
    - "exercise": workouts and manual entries (Cronometer's Exercise line).

    Args:
        start_date: First day, YYYY-MM-DD (defaults to today).
        end_date: Last day, YYYY-MM-DD (defaults to start_date). At most 62 days.
    """
    try:
        rows = _get_client().get_exercises(_parse_date(start_date), _parse_date(end_date))
        return json.dumps({"status": "success", "count": len(rows), "exercises": rows},
                          indent=2, ensure_ascii=False)
    except Exception as e:
        return _error(e)


@_tool
def get_daily_biometrics(start_date: str | None = None, end_date: str | None = None) -> str:
    """List biometric entries per day, including tracker imports.

    Covers weight, heart rate, HRV, sleep (with stage breakdown), sleep
    score, Body Battery, respiration rate and anything else logged. Values
    are in the metric's base unit (kg, bpm, ms, hours...). Overnight
    sampled metrics include sample statistics.

    Args:
        start_date: First day, YYYY-MM-DD (defaults to today).
        end_date: Last day, YYYY-MM-DD (defaults to start_date). At most 62 days.
    """
    try:
        rows = _get_client().get_daily_biometrics(_parse_date(start_date), _parse_date(end_date))
        return json.dumps({"status": "success", "count": len(rows), "biometrics": rows},
                          indent=2, ensure_ascii=False)
    except Exception as e:
        return _error(e)


@_tool
def get_energy_expenditure(start_date: str | None = None, end_date: str | None = None) -> str:
    """Daily Energy Expenditure breakdown, as on the web Diary page.

    Cronometer computes this in the browser; this repeats its calculation:
    - bmr: the profile's custom BMR, else Mifflin-St Jeor from sex, age,
      height and the last weight logged on or before the day.
    - baseline_activity: BMR x activity level (Sedentary 0.2 ... Very
      Active 0.9). adjusted_baseline_activity scales it by
      (960 - exercise minutes) / 960, then subtracts tracker activity
      (floored at 0).
    - tracker_activity: all-day tracker burn (e.g. Daily Activity (Garmin)).
    - exercise: workouts and manual exercise entries.
    - thermic_effect_of_food (if enabled): 25% of protein kcal, 7.5% of
      net-carb kcal, 1.5% of fat kcal, 16% of alcohol kcal.
    burned_kcal is their sum; balance_kcal = consumed - burned. The profile
    inputs used are returned too.

    Args:
        start_date: First day, YYYY-MM-DD (defaults to today).
        end_date: Last day, YYYY-MM-DD (defaults to start_date). At most 62 days.
    """
    try:
        res = _get_client().get_energy_expenditure(_parse_date(start_date), _parse_date(end_date))
        return json.dumps({"status": "success", **res}, indent=2, ensure_ascii=False)
    except Exception as e:
        return _error(e)


# --- Targets + Profile ---------------------------------------------------------

_KEEP = object()


def _amount(v):
    """A tool amount argument: None keeps the current value, "none" clears it."""
    if v is None:
        return _KEEP
    if isinstance(v, str):
        if v.strip().lower() in ("none", "null", ""):
            return None
        return float(v)
    return float(v)


def _pref_num(v: float) -> str:
    """A number as the web client writes it into a preference ("181", "0.375")."""
    v = float(v)
    return str(int(v)) if v.is_integer() else repr(v)


@_tool
def get_nutrient_targets(nutrients: list[str] | None = None, visible_only: bool = False) -> str:
    """Nutrient targets from Targets + Profile (min = Daily Target, max = Maximum).

    Each row: nutrient_id, nutrient name, unit, min, max, visible, and custom
    (false = Cronometer's default for your profile; true = your own values).
    Energy (208) is computed from the energy settings, see get_profile.

    Args:
        nutrients: Only these nutrients, by name ("Vitamin D", "sodium") or id.
            Omit for all targets.
        visible_only: Only the nutrients shown in the Diary.
    """
    try:
        rows = [_tg.summarize_target(t) for t in _get_client().get_nutrient_targets()]
        if nutrients:
            have = {r["nutrient_id"] for r in rows}
            want = {_nutrients.resolve(n, prefer=have) for n in nutrients}
            rows = [r for r in rows if r["nutrient_id"] in want]
        if visible_only:
            rows = [r for r in rows if r["visible"]]
        return json.dumps({"status": "success", "count": len(rows), "targets": rows},
                          indent=2, ensure_ascii=False)
    except Exception as e:
        return _error(e)


@_write_tool
def set_nutrient_target(
    nutrient: str,
    min_amount: float | str | None = None,
    max_amount: float | str | None = None,
    visible: bool | None = None,
    use_default: bool = False,
) -> str:
    """Edit one nutrient target, like the Edit Target dialog on Targets + Profile.

    Setting min_amount or max_amount makes the target custom; the other bound
    keeps its current value. use_default=True switches back to Cronometer's
    default for your profile. Amounts are in the nutrient's unit (see
    get_nutrient_targets).

    Args:
        nutrient: Name ("Sodium", "B12") or nutrient id. Where a name is
            ambiguous, the id that already has a target is used.
        min_amount: Daily target; "none" removes it.
        max_amount: Maximum threshold; "none" removes it.
        visible: Show or hide the nutrient in the Diary.
        use_default: Go back to the default target (no min/max with this).
    """
    try:
        client = _get_client()
        rows = {t["id"]: t for t in client.get_nutrient_targets()}
        nid = _nutrients.resolve(nutrient, prefer=set(rows))
        cur = rows.get(nid, {"id": nid, "min": None, "max": None, "vis": False, "custom": False})
        new_min, new_max = _amount(min_amount), _amount(max_amount)
        bounds = new_min is not _KEEP or new_max is not _KEEP
        if use_default and bounds:
            raise ValueError("use_default can't be combined with min_amount/max_amount")
        lo = cur.get("min") if new_min is _KEEP else new_min
        hi = cur.get("max") if new_max is _KEEP else new_max
        if lo is not None and hi is not None and lo > hi:
            raise ValueError(f"min_amount {lo} is above max_amount {hi}")
        custom = False if use_default else (True if bounds else bool(cur.get("custom")))
        vis = bool(cur.get("vis")) if visible is None else visible
        before = _tg.summarize_target(cur)
        if (lo, hi, vis, custom) == (cur.get("min"), cur.get("max"), bool(cur.get("vis")),
                                     bool(cur.get("custom"))):
            return json.dumps({"status": "success", "message": "No change.", "target": before},
                              indent=2, ensure_ascii=False)
        client.set_nutrient_target(_tg.target_obj(nid, lo, hi, vis, custom))
        if use_default:
            client.init_targets()  # recompute the default min/max
        after = next((_tg.summarize_target(t) for t in client.get_nutrient_targets()
                      if t["id"] == nid), None)
        return json.dumps({"status": "success", "previous": before, "updated": after},
                          indent=2, ensure_ascii=False)
    except Exception as e:
        return _error(e)


@_tool
def get_profile() -> str:
    """Profile, weight goal and energy settings from Targets + Profile.

    Sex, birthday, age, pregnancy/lactation status, height, current weight and
    body fat (latest logged); the weight goal (goal weight, rate, the energy
    target it gives and a forecast date); and the energy settings: BMR (custom or
    Mifflin-St Jeor), activity level and its baseline kcal, thermic effect of
    food, tracker activity, and any custom energy target.
    """
    try:
        user = _get_client().get_user()
        return json.dumps({"status": "success", "profile": _tg.profile_summary(user)},
                          indent=2, ensure_ascii=False)
    except Exception as e:
        return _error(e)


@_write_tool
def update_profile(
    sex: str | None = None,
    birthday: str | None = None,
    reproductive_status: str | None = None,
    height_cm: float | None = None,
    bmr: float | str | None = None,
    activity_level: str | None = None,
    custom_activity_kcal: float | None = None,
    thermic_effect_of_food: bool | None = None,
    custom_energy_target: float | str | None = None,
) -> str:
    """Change profile and energy settings on Targets + Profile. Only the
    arguments given change. Default nutrient targets are recomputed after a
    change to sex, birthday, pregnancy status or height, as the website does.

    Weight and body fat are biometrics: log them with add_biometric.

    Args:
        sex: "Male" or "Female".
        birthday: YYYY-MM-DD.
        reproductive_status: "Normal", "Pregnant" or "Lactating" (Female only).
        height_cm: Height in centimeters.
        bmr: A custom BMR in kcal, or "default" for Mifflin-St Jeor.
        activity_level: Baseline activity: "None", "Sedentary",
            "Lightly Active", "Moderately Active" or "Very Active".
        custom_activity_kcal: A custom baseline activity in kcal/day instead
            of a level.
        thermic_effect_of_food: Count TEF in energy burned.
        custom_energy_target: A fixed daily energy target in kcal (turns off
            the weight-goal target and the "include exercise" option), or
            "off" to go back to the weight-goal target.
    """
    try:
        client = _get_client()
        user = client.get_user()
        before = _tg.profile_summary(user)
        if activity_level is not None and custom_activity_kcal is not None:
            raise ValueError("give activity_level or custom_activity_kcal, not both")

        prefs: list[tuple[str, str | None]] = []
        if height_cm is not None:
            if not 50 <= height_cm <= 275:
                raise ValueError(f"height_cm {height_cm} is out of range")
            prefs.append((_tg.HEIGHT_CM, _pref_num(height_cm)))
        if bmr is not None:
            if isinstance(bmr, str) and bmr.strip().lower() == "default":
                prefs.append((_tg.BMR, None))
            else:
                prefs.append((_tg.BMR, _pref_num(round(float(bmr)))))
        if activity_level is not None:
            prefs += [(_tg.ACTIVITY, _pref_num(_tg.resolve_activity(activity_level))),
                      (_tg.ACTIVITY_CUSTOM, "0")]
        if custom_activity_kcal is not None:
            prefs += [(_tg.ACTIVITY, _pref_num(_tg.CUSTOM_ACTIVITY)),
                      (_tg.ACTIVITY_CUSTOM, _pref_num(custom_activity_kcal))]
        if thermic_effect_of_food is not None:
            prefs.append((_tg.TEF, "true" if thermic_effect_of_food else "false"))
        if custom_energy_target is not None:
            if isinstance(custom_energy_target, str) and custom_energy_target.strip().lower() == "off":
                prefs += [(_tg.INCLUDE_EXERCISE, "true"), (_tg.CUSTOM_ENERGY, None),
                          (_tg.CUSTOM_ENERGY_MAX, None)]
            else:
                kcal = _pref_num(float(custom_energy_target))
                prefs += [(_tg.INCLUDE_EXERCISE, "false"), (_tg.CUSTOM_ENERGY, kcal),
                          (_tg.CUSTOM_ENERGY_MAX, kcal)]

        rest_change = any(v is not None for v in (sex, birthday, reproductive_status))
        if rest_change:
            new_sex = _tg.resolve_sex(sex) if sex else before["sex"]
            status = (_tg.resolve_reproductive_status(reproductive_status)
                      if reproductive_status else before["reproductive_status"] or "Normal")
            if new_sex == "Male" and status != "Normal":
                if reproductive_status:
                    raise ValueError("reproductive_status other than Normal needs sex Female")
                status = "Normal"
            bday = date.fromisoformat(birthday) if birthday else date.fromisoformat(before["birthday"])
            client.update_profile(bday, new_sex, status)
        for key, value in prefs:
            client.set_preference(key, value)
        if rest_change or height_cm is not None:
            client.init_targets()
        if not rest_change and not prefs:
            return json.dumps({"status": "success", "message": "Nothing to change.",
                               "profile": before}, indent=2, ensure_ascii=False)
        after = _tg.profile_summary(client.get_user())
        return json.dumps({"status": "success", "previous": before, "updated": after},
                          indent=2, ensure_ascii=False)
    except Exception as e:
        return _error(e)


@_write_tool
def set_weight_goal(
    goal_weight: float | None = None,
    rate_per_week: float | None = None,
    units: str = "lb",
    maintain: bool = False,
) -> str:
    """Set the weight goal on Targets + Profile (the website's "Fixed Goal
    Plan"), which sets the energy target: BMR + baseline activity + about
    500 kcal/day per lb/week to gain (minus to lose).

    The goal weight decides whether you lose or gain; rate_per_week is the
    pace. Cronometer allows up to 2 lb (0.91 kg) a week; its website offers
    0.25 lb steps and asks you to acknowledge a warning for faster rates that
    would leave a low energy target. The weight goal has no effect while a
    custom energy target is set (turn it off with
    update_profile(custom_energy_target="off")).

    Args:
        goal_weight: The goal weight, in `units`.
        rate_per_week: How fast to get there, a positive amount in `units`
            per week. Omit to keep the current rate.
        units: "lb" or "kg", for both goal_weight and rate_per_week.
        maintain: Maintain the current weight (clears the goal weight and rate).
    """
    try:
        unit = units.strip().lower().rstrip("s")
        if unit in ("lb", "pound"):
            to_kg = _tg.LB_KG
        elif unit in ("kg", "kilogram"):
            to_kg = 1.0
        else:
            raise ValueError(f"units must be 'lb' or 'kg', not {units!r}")
        if maintain and (goal_weight is not None or rate_per_week is not None):
            raise ValueError("give maintain=True or a goal weight/rate, not both")
        if not maintain and goal_weight is None and rate_per_week is None:
            raise ValueError("give goal_weight, rate_per_week, or maintain=True")

        client = _get_client()
        user = client.get_user()
        before = _tg.weight_goal_summary(user)
        if before["replaced_by"] == "Guided Goal Plan":
            raise ValueError("a Guided Goal Plan is active; change it on the website")
        if before["replaced_by"] == "custom energy target" and not maintain:
            raise ValueError("a custom energy target is set, so a weight goal would have no "
                             "effect; turn it off first with "
                             'update_profile(custom_energy_target="off")')
        current = _tg.current_weight_kg(user)
        if maintain:
            rate, goal_kg = 0.0, None
        else:
            prev_goal = None if before["rate_lb_per_week"] == 0 else _tg.goal_weight_kg(user)
            goal = goal_weight * to_kg if goal_weight is not None else prev_goal
            if goal is None:
                raise ValueError("no goal weight is set; give goal_weight too")
            if not 20 <= goal <= 400:
                raise ValueError(f"goal weight {goal_weight} {units} is out of range")
            rate_lb = None if rate_per_week is None else rate_per_week * to_kg / _tg.LB_KG
            rate, goal_kg = _tg.resolve_goal(current, goal, rate_lb, before["rate_lb_per_week"])
        saved_goal = _tg.goal_weight_kg(user)
        if rate == before["rate_lb_per_week"] and (
                saved_goal is None if goal_kg is None
                else saved_goal is not None and abs(goal_kg - saved_goal) < 0.005):
            return json.dumps({"status": "success", "message": "Nothing to change.",
                               "weight_goal": before}, indent=2, ensure_ascii=False)
        client.set_preference(_tg.WEIGHT_GOAL_RATE, _pref_num(rate))
        client.set_preference(_tg.GOAL_WEIGHT_KG, None if goal_kg is None else _pref_num(goal_kg))
        after = _tg.weight_goal_summary(client.get_user())
        return json.dumps({"status": "success", "previous": before, "updated": after},
                          indent=2, ensure_ascii=False)
    except Exception as e:
        return _error(e)


@_write_tool
def apply_macro_template(template_name: str, target_date: str | None = None) -> str:
    """Use a saved macro template for one day (the Daily Target Editor's
    "Choose a Template for today"). Only that day changes; the weekly
    schedule is untouched. For one-off custom macros use set_macro_targets.

    Args:
        template_name: A saved template's name (see list_macro_templates).
        target_date: YYYY-MM-DD (defaults to today).
    """
    try:
        client = _get_client()
        day = _parse_date(target_date) or date.today()
        templates = client.get_macro_templates_raw()
        match = [t for t in templates if (t.get("name") or "").lower() == template_name.lower()]
        if not match:
            names = [t.get("name") for t in templates]
            raise ValueError(f"no template named {template_name!r}; have {names}")
        before = client.get_daily_macro_targets(day)
        client.apply_macro_template(day, match[0])
        after = client.get_daily_macro_targets(day)
        return json.dumps({"status": "success", "date": str(day),
                           "template": _tg.template_summary(match[0]),
                           "previous": before, "updated": after}, indent=2)
    except Exception as e:
        return _error(e)


@_write_tool
def create_custom_recipe(
    name: str,
    ingredients: list[dict],
    servings: float = 1.0,
    notes: str = "",
    food_type: str = "RECIPE",
    cooked_weight_g: float | None = None,
) -> str:
    """Create a custom recipe or meal. Cronometer computes its nutrients.

    Args:
        name: Recipe name.
        ingredients: Ingredient list (see below).
        servings: Servings per recipe.
        notes: Free-text notes.
        food_type: "RECIPE" (default) or "MEAL".
        cooked_weight_g: Optional Cooked Recipe Weight: the finished weight
                         after cooking (e.g. moisture loss). It replaces the
                         ingredient total as the recipe's weight, so per-gram
                         and per-serving values follow it.

    Each ingredient is an object:
        {"food_source_id": <id from search_foods or list_custom_foods>,
         "grams": <weight>}  or
        {"food_source_id": ..., "quantity": <number>, "measure": <name or measure_id>}
    "measure" is optional: plain foods default to grams ("g"), recipes and
    meals to "full recipe". Use get_food_details / get_custom_food to see a
    food's measures. Recipes and meals can be ingredients (nesting). To keep
    an existing ingredient row when editing, include its "ingredient_id".
    """
    try:
        food = _get_client().create_recipe(
            name, ingredients, servings, notes, food_type, cooked_weight_g
        )
        return json.dumps({"status": "success", "food": food}, indent=2, ensure_ascii=False)
    except Exception as e:
        return _error(e)


@_write_tool
def edit_custom_recipe(
    food_source_id: int,
    name: str | None = None,
    notes: str | None = None,
    servings: float | None = None,
    ingredients: list[dict] | None = None,
    update_past_entries: bool | None = None,
    cooked_weight_g: float | None = None,
) -> str:
    """Edit a custom recipe or meal. Omitted fields are left unchanged.

    If the recipe is already logged in the diary or used in other recipes or
    meals, this returns status "needs_decision" with the usage counts and
    changes nothing. Ask the user, then call again with update_past_entries:
    True changes the recipe in place (past diary entries change too); False
    saves the edit as a new recipe with a new id and leaves the old one alone.
    To copy a recipe on purpose before editing, use copy_custom_food.

    Args:
        food_source_id: Recipe or meal id.
        name: New name.
        notes: New notes.
        servings: New servings per recipe.
        ingredients: Replacement ingredient list, the whole list (get the
                     current one with get_custom_food first).
        update_past_entries: See above. Only needed when the recipe is in use.
        cooked_weight_g: Set the Cooked Recipe Weight (finished weight after
                         cooking); 0 clears it so the ingredient total applies.
                         get_custom_food shows original_weight_g (ingredient
                         total), cooked_weight_g and the effective
                         total_weight_g.

    Each ingredient is an object:
        {"food_source_id": <id from search_foods or list_custom_foods>,
         "grams": <weight>}  or
        {"food_source_id": ..., "quantity": <number>, "measure": <name or measure_id>}
    "measure" is optional: plain foods default to grams ("g"), recipes and
    meals to "full recipe". Use get_food_details / get_custom_food to see a
    food's measures. Recipes and meals can be ingredients (nesting). To keep
    an existing ingredient row when editing, include its "ingredient_id".
    """
    try:
        food = _get_client().edit_recipe(
            food_source_id, name, notes, servings, ingredients, update_past_entries,
            cooked_weight_g,
        )
        return json.dumps({"status": "success", "food": food}, indent=2, ensure_ascii=False)
    except Exception as e:
        return _error(e)


@_write_tool
def edit_custom_food(
    food_source_id: int,
    name: str | None = None,
    notes: str | None = None,
    nutrients: dict[str, float] | None = None,
    per_grams: float = 100.0,
    measures: list[dict] | None = None,
    update_past_entries: bool | None = None,
) -> str:
    """Edit one of your custom (non-recipe) foods. Omitted fields are unchanged.

    The same "needs_decision" / update_past_entries rule as edit_custom_recipe
    applies when the food is in use.

    Args:
        food_source_id: Custom food id.
        name: New name.
        notes: New notes.
        nutrients: Amounts to set, keyed by name (energy_kcal, protein_g,
                   fat_g, carbs_g, fiber_g, sugars_g, saturated_fat_g,
                   trans_fat_g, monounsaturated_fat_g, polyunsaturated_fat_g,
                   cholesterol_mg, sodium_mg, potassium_mg, calcium_mg, iron_mg,
                   magnesium_mg, phosphorus_mg, zinc_mg, vitamin_c_mg, water_g,
                   caffeine_mg, alcohol_g) or by Cronometer nutrient id. Values
                   are for per_grams grams of food; others stay as they are.
        per_grams: Weight the nutrient values describe (e.g. one serving's
                   grams from the label). Default 100.
        measures: Replacement serving sizes, the whole list:
                  [{"name": "bar", "grams": 51}, {"name": "Tbsp", "quantity": 2,
                  "grams": 30}]. Same-named sizes keep their ids.
        update_past_entries: Only needed when the food is in use.
    """
    try:
        food = _get_client().edit_custom_food(
            food_source_id, name, notes, nutrients, per_grams, measures, update_past_entries
        )
        return json.dumps({"status": "success", "food": food}, indent=2, ensure_ascii=False)
    except Exception as e:
        return _error(e)


@_write_tool
def copy_custom_food(food_source_id: int, name: str | None = None) -> str:
    """Copy a food, recipe or meal to a new custom item with a new id.

    Args:
        food_source_id: Item to copy.
        name: Name for the copy (default: original name + " (copy)").
    """
    try:
        food = _get_client().copy_food(food_source_id, name)
        return json.dumps({"status": "success", "food": food}, indent=2, ensure_ascii=False)
    except Exception as e:
        return _error(e)


@_write_tool
def delete_custom_food(food_source_id: int, delete_usages: bool = False) -> str:
    """Delete one of your custom foods, recipes or meals.

    If it is logged in the diary or used in recipes/meals, this returns
    "needs_decision" and deletes nothing. delete_usages=True deletes it anyway,
    and Cronometer removes those diary servings and ingredient rows as well:
    confirm with the user first.

    Args:
        food_source_id: Item to delete.
        delete_usages: Also delete everywhere it is used.
    """
    try:
        result = _get_client().delete_custom_food(food_source_id, delete_usages)
        return json.dumps({"status": "success", "deleted": result}, indent=2, ensure_ascii=False)
    except Exception as e:
        return _error(e)



def main():
    # CRONOMETER_TRANSPORT=streamable-http serves http://HOST:PORT/mcp instead
    # of stdio. There is no authentication: whoever can reach the port acts
    # as the account in the environment, so the default host stays local.
    transport = os.environ.get("CRONOMETER_TRANSPORT", "").strip().lower() or "stdio"
    if transport == "stdio":
        mcp.run(transport="stdio")
        return
    if transport != "streamable-http":
        logger.error("CRONOMETER_TRANSPORT must be 'stdio' or 'streamable-http', got %r", transport)
        raise SystemExit(2)
    host = os.environ.get("CRONOMETER_HOST", "").strip() or "127.0.0.1"
    port_raw = os.environ.get("CRONOMETER_PORT", "").strip() or "8000"
    try:
        port = int(port_raw)
    except ValueError:
        port = 0
    if not 1 <= port <= 65535:
        logger.error("CRONOMETER_PORT must be an integer from 1 to 65535, got %r", port_raw)
        raise SystemExit(2)
    # The SDK validates Host and Origin headers (DNS rebinding protection) on
    # its own only for loopback binds. Anywhere else it accepts any Host, so
    # CRONOMETER_ALLOWED_HOSTS names the values clients may send, as
    # "host:port" or "host:*", comma separated.
    kwargs = {}
    allowed = [h.strip() for h in os.environ.get("CRONOMETER_ALLOWED_HOSTS", "").split(",")
               if h.strip()]
    if allowed:
        from mcp.server.transport_security import TransportSecuritySettings
        kwargs["transport_security"] = TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=allowed,
            allowed_origins=[f"{scheme}://{h}" for h in allowed for scheme in ("http", "https")],
        )
    elif host not in ("127.0.0.1", "localhost", "::1"):
        logger.warning(
            "Binding %s with no CRONOMETER_ALLOWED_HOSTS: any Host header is accepted, "
            "so a web page on the same network can reach this server (DNS rebinding)", host,
        )
    url_host = f"[{host}]" if ":" in host else host
    logger.info("Serving streamable HTTP at http://%s:%d/mcp", url_host, port)
    mcp.run(transport="streamable-http", host=host, port=port, **kwargs)


if __name__ == "__main__":
    main()
