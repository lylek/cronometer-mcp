"""Tests for the GWT-RPC object codec (gwt_foods) and the diary helpers on it."""

import json

import pytest

from cronometer_mcp import diary
from cronometer_mcp import gwt_foods as g

HEADER = "B82D6F42004DB8749BB1C2080E1A4479"


def as_response(obj) -> str:
    """Serialize ``obj`` the way the server does: tokens reversed, table last."""
    enc = g._Encoder()
    enc.object(obj)
    toks = []
    for t in reversed(enc.out):
        try:
            toks.append(int(t))
        except ValueError:
            try:
                toks.append(float(t))
            except ValueError:
                toks.append(t)  # base64 long
    return "//OK" + json.dumps(toks + [enc.strings, 0, 7])


def serving(order, amount=1.0, sid="FHEOTg"):
    return {
        "$class": g.SERVING, "day": diary.day_obj(__import__("datetime").date(2026, 11, 23)),
        "unknown_1": True, "unknown_2": True, "tz_offset": -480, "order": order,
        "time": {"$class": g.TIME, "hour": 7, "minute": 54, "second": 0},
        "owner": 6979138, "amount": amount, "food_id": 88465891, "id": sid,
        "measure_id": 330016824, "unknown_11": 0,
    }


# A deleteFood call captured from the web app, nonce swapped for a placeholder.
DELETE_FOOD = (
    "7|0|7|https://cronometer.com/cronometer/|" + HEADER + "|"
    "com.cronometer.shared.rpc.CronometerService|deleteFood|"
    "java.lang.String/2004016611|I|NONCE|1|2|3|4|2|5|6|7|87293467|"
)


def test_simple_request_matches_web_app_bytes():
    body = g.encode_request(HEADER, "deleteFood", [(g.STRING, "NONCE"), ("I", 87293467)])
    assert body == DELETE_FOOD


def test_add_entry_request_round_trip():
    change = {"$class": g.SINGLETON_LIST, "item": {
        "$class": g.ADD_ENTRY_CHANGE, "unknown_0": True, "unknown_1": True,
        "entry": serving(65539)}}
    params = [(g.STRING, "NONCE"), ("I", 6979138), ("java.util.List", change)]
    body = g.encode_request(HEADER, "updateDiary", params)
    method, decoded = g.RequestDecoder(body).call()
    assert method == "updateDiary"
    assert decoded[2] == change


def test_day_info_decoding_and_group_layout():
    info = {"$class": g.DAY_INFO, "unknown_0": False,
            "day": diary.day_obj(__import__("datetime").date(2026, 11, 23)),
            "entries": {"$class": g.ARRAY_LIST, "items": [
                serving((1 << 16) | 2, sid="B"), serving((1 << 16) | 1, sid="A"),
                serving((3 << 16) | 1, sid="C")]},
            "unknown_3": None, "unknown_4": False, "properties": None}
    decoded = g.decode_response(as_response(info))
    assert decoded == info
    layout = diary.group_layout(decoded)
    assert [e["id"] for e in layout[1]] == ["A", "B"]
    assert [e["id"] for e in layout[3]] == ["C"]


def test_metric_catalog_decoding():
    metrics = {"$class": g.ARRAY_LIST, "items": [
        {"$class": g.METRIC, "unknown_0": False, "id": 1, "icon": None, "unknown_3": False,
         "name": "Weight", "unknown_6": 0, "units": {"$class": g.ARRAY_LIST, "items": [
             {"$class": g.UNIT, "factor": 1.0, "id": 1, "name": "kg"},
             {"$class": g.UNIT, "factor": 0.453592, "id": 2, "name": "lbs"}]}},
    ]}
    assert g.decode_response(as_response(metrics)) == metrics


def test_void_response():
    assert g.decode_response("//OK[[],0,7]") is None


def test_unknown_class_is_an_error():
    with pytest.raises(ValueError, match="no GWT layout"):
        g.decode_response('//OK[1,["com.example.Mystery/123456"],0,7]')


def test_trailing_tokens_are_an_error():
    with pytest.raises(ValueError, match="unread"):
        g.decode_response('//OK[9,0,[],0,7]')


@pytest.mark.parametrize("n", [0, 1, 63, 64, 324182459, 2**40 + 7])
def test_long_round_trip(n):
    assert g.long_from_gwt(g.long_to_gwt(n)) == n


def test_encode_long_param():
    body = g.encode_request("H", "explodeRecipe", [(g.STRING, "n"), ("I", 7), ("J", "D80lp$")])
    assert body.endswith("|D80lp$|")
    assert g.encode_request("H", "m", [("J", 64)]).endswith("|BA|")
