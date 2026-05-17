"""EventLog: recording, flag get/set/has, filter, recent."""
from world_gal_game.core.event_log import EventLog


def test_record_and_recent():
    log = EventLog()
    log.record("scene", "first")
    log.record("choice", "second", actors=["alice"])
    log.record("dialogue", "third", location="library")
    recent = log.recent(2)
    assert [e.title for e in recent] == ["second", "third"]


def test_flag_set_get_has_increment():
    log = EventLog()
    assert log.get_flag("foo", "miss") == "miss"
    assert log.has_flag("foo") is False
    log.set_flag("foo", True)
    assert log.has_flag("foo") is True
    log.set_flag("counter", 0)
    log.increment("counter", 3)
    log.increment("counter")
    assert log.get_flag("counter") == 4


def test_filter_by_kind_actor_location():
    log = EventLog()
    log.record("scene", "intro", location="dorm")
    log.record("dialogue", "hi", actors=["alice"])
    log.record("dialogue", "bye", actors=["bob"])
    log.record("location", "moved", location="dorm")
    assert len(log.filter(kind="dialogue")) == 2
    assert len(log.filter(actor="alice")) == 1
    assert len(log.filter(location="dorm")) == 2
