"""Cronometer's nutrient catalog: id -> (name, unit).

Taken from the web client's nutrient enum. Ids follow USDA SR numbering where
one exists; Cronometer's own nutrients are 10001+, and negative ids are
derived values (net carbs, exercise energy).
"""

from __future__ import annotations

NUTRIENTS: dict[int, tuple[str, str]] = {
    -2000: ('Kilojoules', 'kJ'),
    -1208: ('Energy (Net)', 'kcal'),
    -1205: ('Net Carbs', 'g'),
    -208: ('Energy (Burned from Exercise)', 'kcal'),
    203: ('Protein', 'g'),
    204: ('Fat', 'g'),
    205: ('Carbs', 'g'),
    207: ('Ash', 'g'),
    208: ('Energy', 'kcal'),
    209: ('Starch', 'g'),
    210: ('Sucrose', 'g'),
    211: ('Glucose', 'g'),
    212: ('Fructose', 'g'),
    213: ('Lactose', 'g'),
    214: ('Maltose', 'g'),
    221: ('Alcohol', 'g'),
    246: ('Phytate', 'mg'),
    255: ('Water', 'g'),
    257: ('Adjusted Protein', 'g'),
    262: ('Caffeine', 'mg'),
    263: ('Theobromine', 'mg'),
    268: ('Energy', 'kJ'),
    269: ('Sugars', 'g'),
    287: ('Galactose', 'g'),
    291: ('Fiber', 'g'),
    295: ('Soluble Fiber', 'g'),
    297: ('Insoluble Fiber', 'g'),
    301: ('Calcium', 'mg'),
    303: ('Iron', 'mg'),
    304: ('Magnesium', 'mg'),
    305: ('Phosphorus', 'mg'),
    306: ('Potassium', 'mg'),
    307: ('Sodium', 'mg'),
    309: ('Zinc', 'mg'),
    312: ('Copper', 'mg'),
    313: ('Fluoride', 'µg'),
    315: ('Manganese', 'mg'),
    317: ('Selenium', 'µg'),
    318: ('Vitamin A', 'IU'),
    319: ('Retinol', 'µg'),
    320: ('Vitamin A', 'µg'),
    321: ('Beta-carotene', 'µg'),
    322: ('Alpha-carotene', 'µg'),
    323: ('Vitamin E', 'mg'),
    324: ('Vitamin D', 'IU'),
    325: ('Vitamin D2 (ergocalciferol)', 'µg'),
    326: ('Vitamin D3 (cholecalciferol)', 'µg'),
    328: ('Vitamin D (D2 + D3)', 'µg'),
    334: ('Beta-cryptoxanthin', 'µg'),
    337: ('Lycopene', 'µg'),
    338: ('Lutein+Zeaxanthin', 'µg'),
    341: ('Beta Tocopherol', 'mg'),
    342: ('Gamma Tocopherol', 'mg'),
    343: ('Delta Tocopherol', 'mg'),
    344: ('Tocotrienol, alpha', 'mg'),
    345: ('Tocotrienol, beta', 'mg'),
    346: ('Tocotrienol, gamma', 'mg'),
    347: ('Tocotrienol, delta', 'mg'),
    401: ('Vitamin C', 'mg'),
    404: ('B1 (Thiamine)', 'mg'),
    405: ('B2 (Riboflavin)', 'mg'),
    406: ('B3 (Niacin)', 'mg'),
    410: ('B5 (Pantothenic Acid)', 'mg'),
    415: ('B6 (Pyridoxine)', 'mg'),
    417: ('Folate', 'µg'),
    418: ('B12 (Cobalamin)', 'µg'),
    421: ('Choline', 'mg'),
    428: ('Menaquinone-4', 'µg'),
    429: ('Dihydrophylloquinone', 'µg'),
    430: ('Vitamin K', 'µg'),
    431: ('Folic acid', 'µg'),
    432: ('Folate, food', 'µg'),
    435: ('Folate, DFE', 'mcg_DFE'),
    454: ('Betaine', 'mg'),
    501: ('Tryptophan', 'g'),
    502: ('Threonine', 'g'),
    503: ('Isoleucine', 'g'),
    504: ('Leucine', 'g'),
    505: ('Lysine', 'g'),
    506: ('Methionine', 'g'),
    507: ('Cystine', 'g'),
    508: ('Phenylalanine', 'g'),
    509: ('Tyrosine', 'g'),
    510: ('Valine', 'g'),
    511: ('Arginine', 'g'),
    512: ('Histidine', 'g'),
    513: ('Alanine', 'g'),
    514: ('Aspartic acid', 'g'),
    515: ('Glutamic acid', 'g'),
    516: ('Glycine', 'g'),
    517: ('Proline', 'g'),
    518: ('Serine', 'g'),
    521: ('Hydroxyproline', 'g'),
    573: ('Vitamin E, added', 'mg'),
    578: ('Vitamin B-12, added', 'µg'),
    601: ('Cholesterol', 'mg'),
    605: ('Trans-Fats', 'g'),
    606: ('Saturated', 'g'),
    607: ('4:0', 'g'),
    608: ('6:0', 'g'),
    609: ('8:0', 'g'),
    610: ('10:0', 'g'),
    611: ('12:0', 'g'),
    612: ('14:0', 'g'),
    613: ('16:0', 'g'),
    614: ('18:0', 'g'),
    615: ('20:0', 'g'),
    617: ('18:1 undifferentiated', 'g'),
    618: ('18:2 undifferentiated', 'g'),
    619: ('18:3 undifferentiated', 'g'),
    620: ('20:4 undifferentiated', 'g'),
    621: ('DHA', 'g'),
    624: ('22:0', 'g'),
    625: ('14:1', 'g'),
    626: ('16:1 undifferentiated', 'g'),
    627: ('18:4', 'g'),
    628: ('20:1', 'g'),
    629: ('EPA', 'g'),
    630: ('22:1 undifferentiated', 'g'),
    631: ('22:5 n-3 (DPA)', 'g'),
    636: ('Phytosterol', 'mg'),
    638: ('Stigmasterol', 'mg'),
    639: ('Campesterol', 'mg'),
    641: ('Beta-sitosterol', 'mg'),
    645: ('Monounsaturated', 'g'),
    646: ('Polyunsaturated', 'g'),
    652: ('15:0', 'g'),
    653: ('17:0', 'g'),
    654: ('24:0', 'g'),
    662: ('16:1 t', 'g'),
    663: ('18:1 t', 'g'),
    664: ('22:1 t', 'g'),
    665: ('18:2 t not further defined', 'g'),
    666: ('18:2 i', 'g'),
    669: ('18:2 t,t', 'g'),
    670: ('18:2 CLAs', 'g'),
    671: ('24:1 c', 'g'),
    672: ('20:2 n-6 c,c', 'g'),
    673: ('16:1 c', 'g'),
    674: ('18:1 c', 'g'),
    675: ('LA', 'g'),
    676: ('22:1 c', 'g'),
    681: ('21:0', 'g'),
    682: ('23:0', 'g'),
    685: ('18:3 n-6 c,c,c', 'g'),
    687: ('17:1', 'g'),
    689: ('20:3 undifferentiated', 'g'),
    693: ('Fatty acids, total trans-monoenoic', 'g'),
    695: ('Fatty acids, total trans-polyenoic', 'g'),
    696: ('13:0', 'g'),
    697: ('15:1', 'g'),
    851: ('ALA', 'g'),
    852: ('20:3 n-3', 'g'),
    853: ('AA', 'g'),
    855: ('20:4 n-6', 'g'),
    856: ('18:3i', 'g'),
    857: ('21:5', 'g'),
    858: ('22:4', 'g'),
    859: ('18:1-11t (18:1t n-7)', 'g'),
    10001: ('Omega-3', 'g'),
    10002: ('Omega-6', 'g'),
    10003: ('Chromium', 'µg'),
    10004: ('Biotin', 'µg'),
    10005: ('Iodine', 'µg'),
    10006: ('Chloride', 'mg'),
    10007: ('Sugar Alcohol', 'g'),
    10008: ('Molybdenum', 'µg'),
    10009: ('Added Sugars', 'g'),
    10010: ('Allulose', 'g'),
    10011: ('Beta-Hydroxybutyrate', 'g'),
    10012: ('Oxalate', 'mg'),
    10013: ('Nitrogen, total', 'g'),
    10014: ('Organic acids, total', 'g'),
    10015: ('C18:1 n-7 cis', 'g'),
    10016: ('C20:3 n-6 all-cis', 'g'),
    10017: ('Fiber, low molecular weight', 'g'),
    10018: ('Fiber, high molecular weight', 'g'),
    10019: ('Fiber, high molecular weight, water-soluble', 'g'),
    10020: ('Fiber, high molecular weight, water-insoluble', 'g'),
    10021: ('Sulphur', 'mg'),
    10022: ('Acetic acid', 'g'),
    10023: ('Citric acid', 'g'),
    10024: ('Lactic acid', 'g'),
    10025: ('Malic acid', 'g'),
    10026: ('Tartaric acid', 'g'),
    10027: ('Mannitol', 'g'),
    10028: ('Sorbitol', 'g'),
    10029: ('Xylitol', 'g'),
    10030: ('Vitamin K1', 'µg'),
    10031: ('Vitamin K2', 'µg'),
    10033: ('14:1 t', 'g'),
    10034: ('18:1 n-9 c', 'g'),
    10035: ('20:1 n-11 c', 'g'),
    10036: ('20:1 n-9 c', 'g'),
    10037: ('20:1 t', 'g'),
    10038: ('22:1 n-9 c', 'g'),
    10039: ('22:1 n-11 c', 'g'),
    10040: ('21:5 n-3 c', 'g'),
    10041: ('18:1 n-12 c', 'g'),
    10042: ('20:1 n-15 c', 'g'),
    10043: ('22:5 n-6 c', 'g'),
    10044: ('18:3 t,t', 'g'),
    10045: ('22:3 n-3 c', 'g'),
    10046: ('19:0', 'g'),
}


def name(nutrient_id: int) -> str:
    return NUTRIENTS.get(nutrient_id, (str(nutrient_id), ""))[0]


def unit(nutrient_id: int) -> str:
    return NUTRIENTS.get(nutrient_id, ("", ""))[1]


def _norm(s: str) -> str:
    return "".join(ch for ch in s.lower() if ch.isalnum())


def resolve(key: int | str, prefer: set[int] | frozenset[int] = frozenset()) -> int:
    """A nutrient id from an id or a name ("Vitamin D", "sodium", "B12").

    Names match case- and punctuation-insensitively, against the full name or
    the part in parentheses ("Cobalamin"). If a name is ambiguous, an id in
    ``prefer`` (e.g. the ids that have targets) wins; otherwise it raises.
    """
    if isinstance(key, int) or str(key).lstrip("-").isdigit():
        nid = int(key)
        if nid not in NUTRIENTS:
            raise ValueError(f"unknown nutrient id {nid}")
        return nid
    want = _norm(str(key))
    exact = [i for i, (n, _) in NUTRIENTS.items() if _norm(n) == want]
    if not exact:
        exact = [i for i, (n, _) in NUTRIENTS.items()
                 if any(_norm(p) == want for p in n.replace(")", "").split("("))]
    if len(exact) > 1 and len([i for i in exact if i in prefer]) == 1:
        exact = [i for i in exact if i in prefer]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        # e.g. "Vitamin A" is both IU (318) and RAE (320); "Energy" kcal and kJ.
        options = ", ".join(f"{i} ({NUTRIENTS[i][0]}, {NUTRIENTS[i][1]})" for i in exact)
        raise ValueError(f"{key!r} is ambiguous; use an id: {options}")
    raise ValueError(f"unknown nutrient {key!r}")
