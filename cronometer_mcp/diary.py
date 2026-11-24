"""A day's diary (getDayInfo): its entries and their order."""

from __future__ import annotations

from datetime import date

from . import custom_foods as cf
from . import gwt_foods as g


def day_obj(d: date) -> dict:
    return {"$class": g.DAY, "day": d.day, "month": d.month, "year": d.year}


def entries(day_info: dict, kind: str | None = None) -> list[dict]:
    out = cf.items(day_info.get("entries")) if day_info else []
    return [e for e in out if kind is None or g.short_class(e) == kind]


def diary_group(entry: dict) -> int:
    """0-based diary group slot (DG01 = 0) packed into an entry's order."""
    return (entry.get("order") or 0) >> 16



# --- entry order --------------------------------------------------------------
# An entry's ``order`` packs its diary group slot (high 16 bits) and its
# 1-based position within the group (low 16 bits).


def _position(entry: dict) -> int:
    return (entry.get("order") or 0) & 0xFFFF


def group_layout(day_info: dict) -> dict[int, list[dict]]:
    """A day's entries by diary group slot, in display order."""
    out: dict[int, list[dict]] = {}
    for e in sorted(entries(day_info), key=lambda e: e.get("order") or 0):
        out.setdefault(diary_group(e), []).append(e)
    return out
