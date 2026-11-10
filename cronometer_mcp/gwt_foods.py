"""GWT-RPC object codec for Cronometer's object model.

Covers foods, recipes and meals, a day's diary entries (servings, exercises,
biometrics, notes), the logged-in User (profile, settings, weight history)
and the biometric metric catalog.

The macro-target code in client.py reads a handful of fixed shapes by hand.
Recipes are different: a Food carries nested measures, a nutrient map,
translations and an ingredient list, and editing one means sending the whole
graph back. So this module models GWT's object stream generically:

- ``LAYOUTS`` lists each class's fields in serialization order. The order and
  kinds were read from Cronometer's compiled field serializers (the GWT
  ``deserialize``/``serialize`` pair for each type, which agree) and checked by
  decoding live responses to the last token and re-encoding captured requests
  byte for byte. Field names are ours; Cronometer's are obfuscated.
- Objects decode to plain dicts tagged with ``"$class"``, so callers can edit a
  decoded Food and hand it straight back to ``encode_request``.
- Enums decode to ``GwtEnum``. Unknown ordinals survive a round trip untouched.

If Cronometer changes one of these classes its type signature hash changes
too; ``tests/test_drift.py`` catches that from the live serialization policy.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

MODULE_BASE = "https://cronometer.com/cronometer/"
SERVICE = "com.cronometer.shared.rpc.CronometerService"

_F = "com.cronometer.shared.foods."
_M = _F + "models."

STRING = "java.lang.String/2004016611"
INTEGER = "java.lang.Integer/3438268394"
ARRAY_LIST = "java.util.ArrayList/4159755760"
HASH_SET = "java.util.HashSet/3273092938"
HASH_MAP = "java.util.HashMap/1797211028"

FOOD = _M + "Food/3482863798"
INGREDIENT = _M + "Ingredient/1003004245"
INGREDIENT_SUBSTITUTIONS = _M + "IngredientSubstitutions/1892525086"
FOOD_MEASURES = _M + "FoodMeasures/2106205728"
MEASURE = _M + "Measure/1979099908"
DERIVED_MEASURE = "com.cronometer.shared.measurement.DerivedMeasure/4214796590"
MEASURE_TRANSLATION = _M + "MeasureTranslation/3000345244"
NUTRIENT_MAP = _M + "NutrientMap/168231382"
NUTRIENT = _M + "Nutrient/331784102"
TRANSLATION = _M + "Translation/4034452093"
SEARCH_HIT = _M + "SearchHit/1904627920"
SEARCH_HIT_ARRAY = "[L" + _M + "SearchHit;/3199648558"
FOOD_USAGE = _M + "FoodUsage/734642561"
LANGUAGE = "com.cronometer.shared.user.models.Language/1257207975"
LOCALIZATION = "com.cronometer.shared.localization.Localization/2254641304"

_E = "com.cronometer.shared.entries.models."
_U = "com.cronometer.shared.user."

DAY = _E + "Day/782579793"
TIME = _E + "Time/1552252503"
DAY_INFO = _E + "DayInfo/416556043"
SERVING = _E + "Serving/2895043750"
DIARY_GROUP = _E + "DiaryGroup/1846062643"
EXERCISE = "com.cronometer.shared.exercise.Exercise/2894167537"
BIOMETRIC = "com.cronometer.shared.biometrics.Biometric/2989635787"
SLEEP_BIOMETRIC = "com.cronometer.shared.sleep.SleepBiometric/489251654"
NOTE = "com.cronometer.shared.notes.Note/252681293"
METRIC = "com.cronometer.shared.metrics.Metric/4111043909"
UNIT = _E + "Unit/128683555"
UNIT_ARRAY = "[L" + _E + "Unit;/391530421"
USER = _U + "models.User/91151502"
USER_PREFERENCES = _U + "models.UserPreferences/1003470664"
BIOMETRIC_HISTORY = "com.cronometer.shared.biometrics.BiometricHistory/3940541420"
BODY_FAT_HISTORY = "com.cronometer.shared.biometrics.BodyFatPercentBiometricHistory/774386790"
DATA_POINT = "com.cronometer.shared.charts.models.DataPoint/3560061380"
BRAZE_EVENT = "com.cronometer.shared.braze.models.BrazeEvent/1015848879"
FAVOURITE_FOOD = _M + "FavouriteFood/2337815137"
GOLD_STATUS = _U + "models.GoldStatus/1501850739"
GOLD_PRODUCT_GRANT = _U + "models.GoldProductGrant/1713595410"
MACRO_TARGETS_CONTEXT = "com.cronometer.shared.targets.MacroTargetsContext/4067100785"
LINKED_LIST = "java.util.LinkedList/3953877921"
SQL_DATE = "java.sql.Date/730999118"
SAMPLE_STATS = "com.cronometer.shared.biometrics.SampleStats/1121030289"
SAMPLE_STAT = "com.cronometer.shared.biometrics.SampleStat/4191588438"
SAMPLE_STAT_TYPE = "com.cronometer.shared.biometrics.SampleStat$SampleStatType/3708992629"
DAY_AND_TIME = _E + "DayAndTime/1495394183"
SLEEP_SUMMARY = "com.cronometer.shared.sleep.SleepSummary/1875351125"
GENDER = _U + "Gender/2456086264"
REPRODUCTIVE_STATUS = _U + "ReproductiveStatus/1664778317"

ENUMS = {
    GENDER: ["Male", "Female", "Other"],
    REPRODUCTIVE_STATUS: ["Normal", "Pregnant", "Lactating", "Menopausal"],
    SAMPLE_STAT_TYPE: ["MAX", "MIN", "LATEST", "MEAN", "OTHER"],
    _U + "AccountState/3173264896": None,
    _U + "LoginSite/3970781398": None,
    _F + "FoodSource/4236433762": [
        "All", "USDA", "FDCBranded", "CNF", "CRDB", "IFCDB", "Custom", "NUTTAB",
        "NCCDB", "Nutritionix", "USDAWeb", "Data_Entry", "Unknown", "CoFID",
        "NEVO", "MyPlate", "Draft", "CFCD",
    ],
    _F + "FoodType/2323555378": ["FOOD", "RECIPE", "MEAL", "FORMULATION"],
    _F + "FoodTag/3220417118": None,
    _F + "NutritionLabelType/1598919019": None,
    _M + "Measure$Type/2365167904": None,
    _M + "Nutrient$Type/4187872513": None,
    _M + "NutrientMap$NutrientFilter/1990310964": None,
}
FOOD_TYPE = _F + "FoodType/2323555378"
FOOD_SOURCE = _F + "FoodSource/4236433762"
NUTRITION_LABEL_TYPE = _F + "NutritionLabelType/1598919019"
MEASURE_TYPE = _M + "Measure$Type/2365167904"
NUTRIENT_TYPE = _M + "Nutrient$Type/4187872513"
NUTRIENT_FILTER = _M + "NutrientMap$NutrientFilter/1990310964"

# (field name, kind). Kinds: int, bool, double, long (GWT base64), str, obj.
LAYOUTS: dict[str, list[tuple[str, str]]] = {
    FOOD: [
        ("unknown_0", "int"),
        ("unknown_1", "bool"),
        ("barcodes", "obj"),
        ("unknown_3", "int"),
        ("notes", "str"),
        ("unknown_5", "int"),
        ("id", "int"),
        ("ingredients", "obj"),
        ("label_type", "obj"),
        ("version", "long"),
        ("measures", "obj"),
        ("nutrients", "obj"),
        ("properties", "obj"),
        ("unknown_13", "bool"),
        ("source", "str"),
        ("tags", "obj"),
        ("translations", "obj"),
        ("food_type", "obj"),
        ("owner", "int"),
    ],
    INGREDIENT: [
        ("amount", "double"),
        ("food_id", "int"),
        ("id", "long"),
        ("measure_id", "int"),
        ("unknown_4", "int"),
    ],
    FOOD_MEASURES: [
        ("default_measure_id", "int"),
        ("measures", "obj"),
    ],
    MEASURE: [
        ("quantity", "double"),
        ("unknown_1", "bool"),
        ("food_id", "int"),
        ("unknown_3", "bool"),
        ("id", "int"),
        ("unknown_5", "obj"),
        ("name", "str"),
        ("translations", "obj"),
        ("measure_type", "obj"),
        ("weight", "double"),
    ],
    MEASURE_TRANSLATION: [
        ("language", "obj"),
        ("unknown_1", "int"),
        ("id", "long"),
        ("name", "str"),
    ],
    NUTRIENT_MAP: [
        ("filter", "obj"),
        ("values", "obj"),
    ],
    NUTRIENT: [
        ("amount", "double"),
        ("nutrient_id", "int"),
        ("nutrient_type", "obj"),
    ],
    TRANSLATION: [
        ("language", "obj"),
        ("name", "str"),
        ("id", "int"),
    ],
    LANGUAGE: [
        ("code", "str"),
        ("name", "str"),
        ("flag_url", "str"),
        ("native_name", "str"),
    ],
    LOCALIZATION: [
        ("value", "str"),
    ],
    FOOD_USAGE: [
        # Where a food is used; the web app asks "Update old entries?" if any > 0.
        ("meals", "int"),
        ("recipes", "int"),
        ("diary_servings", "int"),
        ("retire_only", "bool"),  # delete dialog offers only "Retire"
    ],
    SEARCH_HIT: [
        ("unknown_0", "int"),
        ("unknown_1", "int"),
        ("id", "int"),
        ("unknown_3", "str"),
        ("unknown_4", "str"),
        ("unknown_5", "int"),
        ("name", "str"),
        ("unknown_7", "bool"),
        ("unknown_8", "int"),
        ("source", "obj"),
        ("unknown_10", "str"),
        ("unknown_11", "int"),
        ("food_type", "obj"),
        ("unknown_13", "int"),
    ],
}

# --- diary, profile and metrics ---------------------------------------------
# Field names below are ours too; "unknown_*" fields round-trip untouched.

_BIOMETRIC_FIELDS = [
    ("value", "double"),  # in the metric's base unit (kg, bpm, hours...)
    ("day", "obj"),
    ("external_id", "str"),
    ("id", "long"),
    ("metadata", "str"),  # JSON, e.g. Garmin sample statistics
    ("metric_id", "int"),
    ("tz_offset", "obj"),
    ("order", "int"),
    ("sample_stats", "obj"),  # SampleStats for metrics sampled overnight (HRV...)
    ("unknown_9", "obj"),
    ("unknown_10", "obj"),
    ("source", "str"),
    ("time", "obj"),
    ("unit_id", "int"),  # the unit it was entered in
    ("owner", "int"),
]

LAYOUTS.update({
    DAY: [("day", "int"), ("month", "int"), ("year", "int")],
    TIME: [("hour", "int"), ("minute", "int"), ("second", "int")],
    DAY_INFO: [
        ("unknown_0", "bool"),
        ("day", "obj"),
        ("entries", "obj"),
        ("unknown_3", "obj"),
        ("unknown_4", "bool"),
        ("properties", "str"),
    ],
    SERVING: [
        ("day", "obj"),
        ("unknown_1", "bool"),
        ("unknown_2", "bool"),
        ("tz_offset", "obj"),
        ("order", "int"),  # diary group in the high 16 bits
        ("time", "obj"),
        ("owner", "int"),
        ("amount", "double"),  # grams, or a count of a recipe-relative measure
        ("food_id", "int"),
        ("id", "long"),
        ("measure_id", "int"),
        ("unknown_11", "int"),
    ],
    EXERCISE: [
        ("exercise_type_id", "int"),
        ("unknown_1", "int"),
        ("unknown_2", "bool"),
        ("calories", "double"),  # negative: energy burned
        ("day", "obj"),
        ("id", "long"),
        ("external_id", "str"),
        ("metadata", "str"),  # JSON from the tracker, if any
        ("minutes", "double"),
        ("name", "str"),
        ("tz_offset", "obj"),
        ("order", "int"),
        ("source", "str"),  # "Garmin", "Fitbit"... or None if entered by hand
        ("time", "obj"),
        ("owner", "int"),
        ("unknown_15", "double"),
    ],
    BIOMETRIC: _BIOMETRIC_FIELDS,
    SLEEP_BIOMETRIC: [("sleep", "obj")] + _BIOMETRIC_FIELDS,
    NOTE: [
        ("day", "obj"),
        ("id", "long"),
        ("unknown_2", "str"),
        ("tz_offset", "obj"),
        ("order", "int"),
        ("text", "str"),
        ("time", "obj"),
        ("owner", "int"),
    ],
    DIARY_GROUP: [
        ("day", "obj"),
        ("unknown_1", "bool"),
        ("group", "int"),
        ("user", "obj"),
    ],
    SAMPLE_STATS: [
        ("count", "int"),
        ("earliest", "obj"),
        ("earliest_value", "double"),
        ("stats", "obj"),
        ("latest", "obj"),
        ("latest_value", "double"),
        ("max", "double"),
        ("mean", "double"),
        ("min", "double"),
        ("sum", "double"),
    ],
    SAMPLE_STAT: [("label", "str"), ("type", "obj"), ("unit", "str"), ("value", "double")],
    DAY_AND_TIME: [("day", "obj"), ("time", "obj"), ("timestamp", "long")],
    SLEEP_SUMMARY: [
        ("unknown_0", "double"),
        ("unknown_1", "str"),
        ("unknown_2", "double"),
        ("unknown_3", "obj"),
        ("unknown_4", "obj"),
        ("unknown_5", "double"),
        ("unknown_6", "str"),
        ("unknown_7", "double"),
        ("unknown_8", "double"),
        ("unknown_9", "str"),
        ("unknown_10", "double"),
        ("unknown_11", "double"),
        ("unknown_12", "str"),
        ("unknown_13", "double"),
        ("unknown_14", "double"),
        ("unknown_15", "double"),
        ("unknown_16", "str"),
        ("unknown_17", "obj"),
    ],
    METRIC: [
        ("unknown_0", "bool"),
        ("id", "int"),
        ("icon", "str"),
        ("unknown_3", "bool"),
        ("name", "str"),
        ("units", "obj"),
        ("unknown_6", "int"),
    ],
    UNIT: [("factor", "double"), ("id", "int"), ("name", "str")],
    USER: [
        ("unknown_0", "long"),
        ("account_state", "obj"),
        ("unknown_2", "str"),
        ("birthday", "obj"),
        ("body_fat_history", "obj"),
        ("braze_events", "obj"),
        ("unknown_6", "obj"),
        ("unknown_7", "obj"),
        ("connected_apps", "obj"),
        ("unknown_9", "str"),
        ("email", "str"),
        ("favourite_foods", "obj"),
        ("name", "str"),
        ("unknown_13", "obj"),
        ("gender", "obj"),
        ("gold_status", "obj"),
        ("unknown_16", "str"),
        ("unknown_17", "bool"),
        ("unknown_18", "str"),
        ("id", "int"),
        ("unknown_20", "bool"),
        ("unknown_21", "long"),
        ("unknown_22", "long"),
        ("localization", "obj"),
        ("unknown_24", "obj"),
        ("energy_target", "obj"),
        ("macro_targets_context", "obj"),
        ("unknown_27", "bool"),
        ("settings", "obj"),
        ("unknown_29", "obj"),
        ("flags", "obj"),
        ("unknown_31", "bool"),
        ("unknown_32", "long"),
        ("nonce", "str"),
        ("login_site", "obj"),
        ("unknown_35", "str"),
        ("reproductive_status", "obj"),
        ("unknown_37", "obj"),
        ("unknown_38", "obj"),
        ("unknown_39", "obj"),
        ("time_zone", "str"),
        ("weight_history", "obj"),
    ],
    BIOMETRIC_HISTORY: [
        ("points", "obj"),
        ("unknown_1", "int"),
        ("unknown_2", "int"),
        ("owner", "int"),
    ],
    DATA_POINT: [("day", "obj"), ("time", "obj"), ("value", "double")],
    BRAZE_EVENT: [("name", "str"), ("date", "obj")],
    FAVOURITE_FOOD: [("food_id", "int"), ("unknown_1", "int")],
    GOLD_STATUS: [
        ("grant", "obj"),
        ("unknown_1", "int"),
        ("unknown_2", "long"),
        ("unknown_3", "obj"),
    ],
    GOLD_PRODUCT_GRANT: [
        ("unknown_0", "obj"),
        ("unknown_1", "long"),
        ("unknown_2", "obj"),
        ("unknown_3", "str"),
        ("unknown_4", "str"),
        ("unknown_5", "str"),
        ("unknown_6", "str"),
        ("unknown_7", "obj"),
        ("unknown_8", "int"),
    ],
    MACRO_TARGETS_CONTEXT: [(f"unknown_{i}", "double") for i in range(10)] + [
        ("unknown_10", "obj"),
        ("unknown_11", "double"),
        ("unknown_12", "double"),
        ("unknown_13", "double"),
        ("unknown_14", "obj"),
    ],
})
LAYOUTS[BODY_FAT_HISTORY] = LAYOUTS[BIOMETRIC_HISTORY]

# A Measure subclass with no extra serialized fields (seen on some CRDB foods).
LAYOUTS[DERIVED_MEASURE] = LAYOUTS[MEASURE]

_COLLECTIONS = {ARRAY_LIST, HASH_SET, SEARCH_HIT_ARRAY, LINKED_LIST, UNIT_ARRAY}
_MAPS = {HASH_MAP, USER_PREFERENCES}
# Values serialized as a single token: boxed numbers, and java.sql dates as a long.
_BOXED = {INTEGER, "java.lang.Double/858496421", "java.lang.Long/4227064769",
          "java.lang.Short/551743396", SQL_DATE}


@dataclass(frozen=True)
class GwtEnum:
    signature: str
    ordinal: int

    @property
    def name(self) -> str | None:
        names = ENUMS.get(self.signature)
        return names[self.ordinal] if names and self.ordinal < len(names) else None


def enum(signature: str, name: str) -> GwtEnum:
    return GwtEnum(signature, ENUMS[signature].index(name))


def short_class(obj: dict) -> str:
    return obj["$class"].split("/")[0].rsplit(".", 1)[-1]


# --- decoding ---------------------------------------------------------------


def _split_response(raw: str) -> tuple[list, list[str]]:
    if not raw.startswith("//OK["):
        raise ValueError(f"not a GWT-RPC success response: {raw[:300]}")
    # Large responses split the token array for old JS engines.
    data = json.loads(raw[4:].replace("].concat([", ","))
    return data[:-3], data[-3]


class _Decoder:
    """Shared object-stream logic; subclasses say where tokens come from."""

    strings: list[str]

    def __init__(self):
        self.seen: list = []

    def next(self):
        raise NotImplementedError

    def string(self) -> str | None:
        ref = self.next()
        return self.strings[ref - 1] if ref else None

    def value(self, kind: str):
        if kind == "obj":
            return self.object()
        if kind == "str":
            return self.string()
        if kind == "bool":
            return bool(self.next())
        if kind == "long":
            return self.next()
        if kind == "double":
            return float(self.next())
        return int(self.next())

    def object(self):
        ref = self.next()
        if ref == 0:
            return None
        if ref < 0:
            return self.seen[-ref - 1]
        sig = self.strings[ref - 1]
        slot = len(self.seen)
        self.seen.append(None)
        if sig in LAYOUTS:
            # Register before reading fields, as GWT does, so self-references resolve.
            obj = {"$class": sig}
            self.seen[slot] = obj
            for name, kind in LAYOUTS[sig]:
                obj[name] = self.value(kind)
            return obj
        self.seen[slot] = self._special(sig)
        return self.seen[slot]

    def _special(self, sig: str):
        if sig == STRING:
            return self.string()
        if sig in _BOXED:
            return self.next()
        if sig == "java.lang.Boolean/476441737":
            return bool(self.next())
        if sig in ENUMS:
            return GwtEnum(sig, self.next())
        if sig in _COLLECTIONS:
            return {"$class": sig, "items": [self.object() for _ in range(self.next())]}
        if sig in _MAPS:
            n = self.next()
            return {"$class": sig, "entries": [(self.object(), self.object()) for _ in range(n)]}
        if sig == INGREDIENT_SUBSTITUTIONS:
            return {"$class": sig, "map": self.object()}
        raise ValueError(f"no GWT layout for {sig}")


class ResponseDecoder(_Decoder):
    """Decode a ``//OK[...]`` response (tokens are read from the end)."""

    def __init__(self, raw: str):
        super().__init__()
        self.tokens, self.strings = _split_response(raw)

    def next(self):
        if not self.tokens:
            raise ValueError("GWT response ran out of tokens")
        return self.tokens.pop()

    def done(self) -> None:
        if self.tokens:
            raise ValueError(f"GWT response has {len(self.tokens)} unread tokens")


def decode_response(raw: str):
    """Decode a response holding one object; a void response gives None."""
    dec = ResponseDecoder(raw)
    if not dec.tokens:
        return None
    out = dec.object()
    dec.done()
    return out


class RequestDecoder(_Decoder):
    """Decode a request payload (forward order). Used by tests and debugging."""

    def __init__(self, body: str):
        super().__init__()
        parts = body.split("|")
        n = int(parts[2])
        self.strings = parts[3:3 + n]
        self.tokens = parts[3 + n:-1]
        self.pos = 0

    def next(self):
        tok = self.tokens[self.pos]
        self.pos += 1
        try:
            return int(tok)
        except ValueError:
            try:
                return float(tok)
            except ValueError:
                return tok  # GWT base64 long

    def call(self) -> tuple[str, list]:
        """Return (method, [params]) with params decoded by their declared types."""
        self.string(), self.string(), self.string()
        method = self.string()
        types = [self.string() for _ in range(self.next())]
        params = []
        for t in types:
            if t == "I":
                params.append(int(self.next()))
            elif t == STRING:
                params.append(self.string())
            else:
                params.append(self.object())
        return method, params


# --- encoding ---------------------------------------------------------------


def _fmt_double(v: float) -> str:
    v = float(v)
    return str(int(v)) if v.is_integer() and abs(v) < 1e15 else repr(v)


class _Encoder:
    def __init__(self):
        self.strings: list[str] = []
        self.string_ix: dict[str, int] = {}
        self.out: list[str] = []
        self.seen: dict = {}

    def sref(self, s: str | None) -> str:
        if s is None:
            return "0"
        if s not in self.string_ix:
            self.strings.append(s)
            self.string_ix[s] = len(self.strings)
        return str(self.string_ix[s])

    def emit(self, tok) -> None:
        self.out.append(str(tok))

    def value(self, kind: str, v) -> None:
        if kind == "obj":
            self.object(v)
        elif kind == "str":
            self.emit(self.sref(v))
        elif kind == "bool":
            self.emit(1 if v else 0)
        elif kind == "long":
            self.emit(v if isinstance(v, str) else long_to_gwt(v))
        elif kind == "double":
            self.emit(_fmt_double(v))
        else:
            self.emit(int(v))

    def _backref(self, key) -> bool:
        if key in self.seen:
            self.emit(-self.seen[key])
            return True
        self.seen[key] = len(self.seen) + 1
        return False

    def object(self, v) -> None:
        if v is None:
            self.emit(0)
            return
        if isinstance(v, GwtEnum):
            if self._backref(("enum", v)):
                return
            self.emit(self.sref(v.signature))
            self.emit(v.ordinal)
            return
        if isinstance(v, str):
            # Strings written as objects are interned by value, like the JS client.
            if self._backref(("str", v)):
                return
            self.emit(self.sref(STRING))
            self.emit(self.sref(v))
            return
        if isinstance(v, bool):
            raise TypeError("write booleans through a typed field")
        if isinstance(v, int):
            self.seen[("int", object())] = len(self.seen) + 1  # boxed: never shared
            self.emit(self.sref(INTEGER))
            self.emit(v)
            return
        if self._backref(("obj", id(v))):
            return
        sig = v["$class"]
        self.emit(self.sref(sig))
        if sig in LAYOUTS:
            for name, kind in LAYOUTS[sig]:
                self.value(kind, v.get(name))
        elif sig in _COLLECTIONS:
            self.emit(len(v["items"]))
            for item in v["items"]:
                self.object(item)
        elif sig in _MAPS:
            self.emit(len(v["entries"]))
            for k, item in v["entries"]:
                self.object(k)
                self.object(item)
        elif sig == INGREDIENT_SUBSTITUTIONS:
            self.object(v["map"])
        else:
            raise ValueError(f"cannot encode {sig}")


def encode_request(gwt_header: str, method: str, params: list[tuple[str, object]]) -> str:
    """Build a GWT-RPC request body.

    ``params`` is a list of (declared type, value): ``"I"`` for int, ``STRING``
    for a String, or a class signature for an object.
    """
    enc = _Encoder()
    for s in (MODULE_BASE, gwt_header, SERVICE, method):
        enc.emit(enc.sref(s))
    enc.emit(len(params))
    for t, _ in params:
        enc.emit(enc.sref(t))
    for t, v in params:
        if t == "I":
            enc.emit(int(v))
        elif t == STRING:
            enc.emit(enc.sref(v))
        else:
            enc.object(v)
    head = ["7", "0", str(len(enc.strings))] + enc.strings
    return "|".join(head + enc.out) + "|"


_B64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789$_"


def long_to_gwt(n: int) -> str:
    if n == 0:
        return "A"
    n &= (1 << 64) - 1
    out = ""
    while n:
        out = _B64[n & 63] + out
        n >>= 6
    return out


def long_from_gwt(s: str) -> int:
    n = 0
    for ch in s:
        n = (n << 6) | _B64.index(ch)
    return n - (1 << 64) if n >= 1 << 63 else n
