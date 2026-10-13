from datetime import date
import json
import pytest
from app.calendar_storage import CalendarStore, month_days, shift_month


def item(**kwargs):
    return dict(title="Plan", kind="task", date="2026-09-30", time=None, details="", note="", done=False, **kwargs)


def test_legacy_import_is_once_lossless_and_unscheduled(tmp_path):
    original = tmp_path / "Tasks.md"
    original.write_text("# Work\n- [ ] Duplicate\n- [X] Duplicate\nParagraph\n", encoding="utf-8")
    before = original.read_bytes()
    path = tmp_path / "calendar.json"
    store = CalendarStore(path, [("Personal", original)])
    assert len(store.entries(unscheduled=True)) == 2
    assert len({e["id"] for e in store.entries()}) == 2
    assert sum(e["done"] for e in store.entries()) == 1
    assert all(e["date"] is None for e in store.entries())
    CalendarStore(path, [("Personal", original)])
    assert len(store.entries()) == 2
    assert original.read_bytes() == before


def test_calendar_schedule_edit_complete_delete_and_restore(tmp_path):
    store = CalendarStore(tmp_path / "calendar.json")
    entry_id = store.save(item())
    store.save(dict(store.entries()[0], date="2026-10-01", time="23:00", details="A late shift", note="Hotel ops"), entry_id)
    assert not store.entries(date(2026, 9, 30))
    assert store.entries(date(2026, 10, 1), query="late")[0]["note"] == "Hotel ops"
    store.complete(entry_id, True)
    assert not store.entries(show_done=False)
    store.delete(entry_id)
    assert not store.entries()
    backup = next((tmp_path / ".history").glob("calendar-*.json"))
    assert json.loads(backup.read_text())["entries"][0]["done"] is True


@pytest.mark.parametrize("changes", [{"date": "2026-02-30"}, {"date": "30/09/2026"}, {"time": "25:00"},
                                     {"time": "12:30", "date": None}, {"title": ""}])
def test_invalid_edits_preserve_calendar(tmp_path, changes):
    store = CalendarStore(tmp_path / "calendar.json")
    store.save(item())
    before = store.path.read_bytes()
    bad = item()
    bad.update(changes)
    with pytest.raises(ValueError):
        store.save(bad)
    assert store.path.read_bytes() == before


def test_month_grid_and_navigation_leap_year_boundaries():
    days = month_days(date(2024, 2, 29))
    assert len(days) == 42 and days[0].weekday() == 0 and days[-1].weekday() == 6
    assert date(2024, 2, 29) in days
    assert shift_month(date(2024, 1, 31), 1) == date(2024, 2, 29)
    assert shift_month(date(2026, 12, 31), 1) == date(2027, 1, 31)


def test_corrupt_calendar_is_preserved(tmp_path):
    path = tmp_path / "calendar.json"
    path.write_text("{broken")
    with pytest.raises(ValueError):
        CalendarStore(path)
    assert path.read_text() == "{broken"


def test_long_legacy_task_keeps_full_text_in_details(tmp_path):
    source = tmp_path / "Tasks.md"
    text = "A long original task " * 30
    source.write_text("- [ ] " + text)
    store = CalendarStore(tmp_path / "calendar.json", [("Personal", source)])
    saved = store.entries()[0]
    assert len(saved["title"]) == 240
    assert saved["details"] == text
