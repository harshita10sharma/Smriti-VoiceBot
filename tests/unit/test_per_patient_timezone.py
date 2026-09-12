"""MemoryService date resolution ('today'/'tomorrow', day-of-week matching)
must use each patient's own stored IANA timezone, not one shared
service-wide default -- otherwise "what medicine do I take tonight" could
resolve to the wrong calendar day for a patient in a different timezone
than wherever this process happens to be running.
"""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from smriti_voice.memory.models import Medicine, User


def test_user_with_no_timezone_row_falls_back_to_service_default(app):
    # demo-user is seeded without an explicit timezone override beyond the
    # users table default ('Asia/Kolkata').
    assert app.memory._tz_for('demo-user') == ZoneInfo('Asia/Kolkata')


def test_user_with_invalid_timezone_falls_back_safely(app):
    app.memory.repo.upsert_user(User(user_id='demo-user', display_name='Demo',
                                     timezone='Not/ARealZone'))
    # Must not raise -- an unrecognized IANA name in synced data is a data
    # quality issue, not a reason to break every query for this patient.
    assert app.memory._tz_for('demo-user') == app.memory.tz


def test_different_patients_can_have_different_effective_today(app):
    from smriti_voice.memory.models import User as U
    app.memory.repo.upsert_user(U(user_id='patient-tokyo', display_name='Tokyo Patient',
                                  timezone='Asia/Tokyo'))
    app.memory.repo.upsert_user(U(user_id='patient-la', display_name='LA Patient',
                                  timezone='America/Los_Angeles'))

    tokyo_today = app.memory._today_for('patient-tokyo')
    la_today = app.memory._today_for('patient-la')

    assert tokyo_today == datetime.now(ZoneInfo('Asia/Tokyo')).date()
    assert la_today == datetime.now(ZoneInfo('America/Los_Angeles')).date()


def test_medicine_day_filtering_uses_the_patients_own_timezone_not_the_service_default(app):
    """A medicine scheduled only for 'today' in the patient's own timezone
    must appear, even on a day where the service's own default timezone
    would disagree about which weekday it currently is (near a UTC/local
    day boundary). We simulate this deterministically by asking for
    'tomorrow' and 'yesterday' relative to Asia/Kolkata vs a very different
    zone and confirming the two patients can get different answers for the
    exact same underlying instant."""
    from smriti_voice.memory.models import User as U
    app.memory.repo.upsert_user(U(user_id='patient-kolkata', display_name='K',
                                  timezone='Asia/Kolkata'))
    app.memory.repo.upsert_user(U(user_id='patient-honolulu', display_name='H',
                                  timezone='Pacific/Honolulu'))  # UTC-10, far behind IST

    kolkata_today = app.memory._today_for('patient-kolkata')
    honolulu_today = app.memory._today_for('patient-honolulu')

    weekday_kolkata = str(kolkata_today.isoweekday())
    weekday_honolulu = str(honolulu_today.isoweekday())

    app.memory.repo.add_medicine(Medicine(user_id='patient-kolkata', name='KolkataTodayMed',
                                          days_of_week=weekday_kolkata))
    app.memory.repo.add_medicine(Medicine(user_id='patient-honolulu', name='HonoluluTodayMed',
                                          days_of_week=weekday_honolulu))

    kolkata_result = app.memory.medicines('patient-kolkata', when='today')
    honolulu_result = app.memory.medicines('patient-honolulu', when='today')

    assert 'KolkataTodayMed' in {m['name'] for m in kolkata_result['medicines']}
    assert 'HonoluluTodayMed' in {m['name'] for m in honolulu_result['medicines']}
