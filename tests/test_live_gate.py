"""Offline: the safety checks that guard the live tests' writes."""

import pytest

from tests.test_live import TEST_DAY, refuse_parallel_run, require_clean_day, write_round_trips

SERVINGS_HEADER = "Day,Time,Group,Food Name,Amount\n"
BIOMETRICS_HEADER = "Day,Group,Metric,Unit,Amount\n"


class FakeClient:
    def __init__(self, serving_removed=True):
        self.serving_removed = serving_removed
        self.biometric_added = False

    def find_foods(self, query):
        return [{"food_id": 1, "food_source_id": 2}]

    def add_serving(self, **kwargs):
        assert kwargs["day"] == TEST_DAY
        return {"serving_id": "S1"}

    def remove_serving(self, serving_id):
        return self.serving_removed

    def add_biometric(self, metric, amount, day):
        assert day == TEST_DAY
        self.biometric_added = True
        return "B1"

    def remove_biometric(self, biometric_id):
        return True

    # A one-note diary for TEST_DAY.
    notes = None

    def get_notes(self, day):
        assert day == TEST_DAY
        return list(self.notes or [])

    def add_note(self, day, text, diary_group=0, at=None):
        assert day == TEST_DAY
        self.notes = [{"id": "N1", "text": text, "time": at}]
        return "N1"

    def edit_note(self, day, note_id, text=None, at=False):
        assert day == TEST_DAY and note_id == "N1"
        self.notes[0].update(text=text, time=None if at is False else at)

    def remove_note(self, day, note_id):
        assert day == TEST_DAY and note_id == "N1"
        self.note_removed = True
        self.notes = []


def test_clean_day_passes():
    require_clean_day(SERVINGS_HEADER, BIOMETRICS_HEADER)


@pytest.mark.parametrize("servings,biometrics", [
    ("", BIOMETRICS_HEADER),
    (SERVINGS_HEADER, ""),
    ("<html>error</html>\n", BIOMETRICS_HEADER),
    ("Day\n", BIOMETRICS_HEADER),
    (SERVINGS_HEADER, "Day,Unexpected\n"),
    ('Day,"unterminated\n', BIOMETRICS_HEADER),
    (SERVINGS_HEADER, 'Day,"unterminated\n'),
    (SERVINGS_HEADER, BIOMETRICS_HEADER + '2000-01-01,"unterminated\n'),
    (SERVINGS_HEADER, "<html>error</html>\n"),
    (SERVINGS_HEADER + "2000-01-01,,Breakfast,Egg,50\n", BIOMETRICS_HEADER),
    (SERVINGS_HEADER, BIOMETRICS_HEADER + "2000-01-01,,Weight,lbs,150\n"),
])
def test_unclean_or_malformed_day_refuses_writes(servings, biometrics):
    with pytest.raises(pytest.fail.Exception):
        require_clean_day(servings, biometrics)


def test_failed_serving_remove_stops_further_writes():
    fake = FakeClient(serving_removed=False)
    with pytest.raises(AssertionError):
        write_round_trips(fake)
    assert not fake.biometric_added


def test_round_trips_run_every_write():
    fake = FakeClient()
    write_round_trips(fake)
    assert fake.biometric_added and fake.note_removed


def test_parallel_run_refused(monkeypatch):
    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw0")
    with pytest.raises(pytest.fail.Exception):
        refuse_parallel_run()
