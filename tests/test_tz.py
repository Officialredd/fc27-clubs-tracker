"""The club's time zone for the online copy. Run: python -m pytest tests/test_tz.py"""
import calendar
import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from clubs import tz  # noqa: E402

pytestmark = pytest.mark.skipif(not hasattr(time, "tzset"), reason="needs time.tzset (not on Windows)")

ZONES = {"Europe/Copenhagen": "<+01>-1<+02>-2,M3.5.0/2,M10.5.0/3",
         "America/New_York": "<-05>5<-04>4,M3.2.0/2,M11.1.0/2",
         "Australia/Sydney": "<+10>-10<+11>-11,M10.1.0/2,M4.1.0/3",
         "Asia/Kolkata": "<+0530>-5:30",
         "UTC": "<+00>0"}


@pytest.fixture(autouse=True)
def _restore_tz():
    saved = os.environ.get("TZ")
    yield
    if saved is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = saved
    time.tzset()
    tz._describe.cache_clear()


def _offsets(zone, year):
    os.environ["TZ"] = zone
    time.tzset()
    t0, t1 = calendar.timegm((year, 1, 1, 0, 0, 0)), calendar.timegm((year + 1, 1, 1, 0, 0, 0))
    return [calendar.timegm(time.localtime(t)) - t for t in range(t0, t1, 1800)]


@pytest.mark.parametrize("zone", list(ZONES))
def test_describe_matches_the_zone(zone):
    os.environ["TZ"] = zone
    time.tzset()
    tz._describe.cache_clear()
    posix = tz.describe(2026)
    assert posix == ZONES[zone]
    for year in (2025, 2026, 2027):   # the same clock as the real zone, every half hour of three years
        assert _offsets(posix, year) == _offsets(zone, year)


def test_apply():
    assert tz.apply("") is False
    assert tz.apply("<+01>-1<+02>-2,M3.5.0/2,M10.5.0/3") is True
    assert tz.apply("<+01>-1<+02>-2,M3.5.0/2,M10.5.0/3") is False   # already in use
    assert time.localtime(calendar.timegm((2026, 11, 23, 18, 0, 0))).tm_hour == 19   # 18:00 UTC is 19:00 in Denmark
    assert time.localtime(calendar.timegm((2026, 7, 1, 18, 0, 0))).tm_hour == 20     # summer time
