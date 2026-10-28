from datetime import date

from scout import timeutil


def test_ist_date_rolls_over_at_1830_utc(freeze):
    freeze("2026-10-28T18:29:00Z")
    assert timeutil.today_ist() == date(2026, 10, 28)
    freeze("2026-10-28T18:31:00Z")
    assert timeutil.today_ist() == date(2026, 10, 29)


def test_seconds_until_10am_ist(freeze):
    freeze("2026-10-29T03:43:00Z")  # 09:13 IST — the primary cron
    assert timeutil.seconds_until_ist(10, 0) == 47 * 60
    freeze("2026-10-29T04:30:00Z")  # exactly 10:00 IST
    assert timeutil.seconds_until_ist(10, 0) == 0
    freeze("2026-10-29T05:13:00Z")  # 10:43 IST — the fallback cron
    assert timeutil.seconds_until_ist(10, 0) < 0


def test_parse_date_iso_is_not_read_day_first():
    assert timeutil.parse_date("2026-11-05") == date(2026, 11, 5)
    assert timeutil.parse_date("05/11/2026") == date(2026, 11, 5)
    assert timeutil.parse_date("30 November 2026") == date(2026, 11, 30)
    assert timeutil.parse_date(None) is None
    assert timeutil.parse_date("rolling basis") is None


def test_days_until(freeze):
    freeze("2026-10-29T04:30:00Z")
    assert timeutil.days_until(date(2026, 11, 30)) == 32
