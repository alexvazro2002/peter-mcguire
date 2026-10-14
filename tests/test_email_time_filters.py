from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.assistant.tools import gmail_date_range


def test_corrected_ten_pm_uses_yesterday_in_the_users_timezone():
    query, applied = gmail_date_range('from:Recruiter', 'yesterday', '22:00', '00:00',
                                     today='2026-10-14', zone='Europe/London')
    assert applied['start'] == '2026-10-13T22:00:00+01:00'
    assert applied['end'] == '2026-10-14T00:00:00+01:00'
    assert f"after:{int(datetime(2026, 10, 13, 22, tzinfo=ZoneInfo('Europe/London')).timestamp())}" in query
    assert applied['before'] - applied['after'] == 7200


def test_whole_local_day_handles_clock_change_without_pacific_midnight():
    query, applied = gmail_date_range('', '2026-10-25', '', '', today='2026-10-14', zone='Europe/London')
    assert applied['before'] - applied['after'] == 25 * 3600
    assert 'after:' in query and 'before:' in query and '2026/10/25' not in query


@pytest.mark.parametrize('date,start,end,query', [('', '22:00', '', ''),
    ('yesterday', '25:00', '', ''), ('today', '', '9pm', ''),
    ('yesterday', '', '', 'from:Recruiter after:2026/10/13')])
def test_ambiguous_or_conflicting_time_filters_fail_clearly(date, start, end, query):
    with pytest.raises(ValueError):
        gmail_date_range(query, date, start, end, today='2026-10-14', zone='Europe/London')


def test_nonexistent_clock_change_time_is_not_silently_shifted():
    with pytest.raises(ValueError, match='clocks change'):
        gmail_date_range('', '2026-03-29', '01:30', '02:30', today='2026-10-14', zone='Europe/London')


def test_iso_date_operators_are_converted_to_actual_seconds_not_truncated_years():
    query, applied = gmail_date_range('after:2026-10-14T00:00:00+01:00 before:2026-10-15T00:00:00+01:00',
                                     '', '', '', today='2026-10-14', zone='Europe/London')
    assert '2026-' not in query and applied['before'] - applied['after'] == 86400
    assert applied['start'] == '2026-10-14T00:00:00+01:00'


def test_query_normalization_leaves_quoted_subject_and_valid_seconds_intact():
    query = 'subject:"before:2026/10/13" after:1791925200'
    assert gmail_date_range(query, '', '', '', today='2026-10-14', zone='Europe/London') == (query, None)


def test_simple_email_search_uses_current_correction_and_not_today_as_sender():
    from types import SimpleNamespace
    from app.assistant.mail_search import email_search_plan
    now = datetime(2026, 10, 14, 18, tzinfo=ZoneInfo('Europe/London'))
    access = SimpleNamespace(email_search_request=True, email_context=[], request='Do I have emails from today?')
    plan = email_search_plan(access, now)
    assert plan['query'] == '' and plan['date'] == 'today'
    access.email_context = ['Find emails from Recruiter yesterday around 11 pm.']
    access.request = 'Search from that same person but from 10 pm last night.'
    plan = email_search_plan(access, now)
    assert plan['query'] == 'from:"Recruiter"' and plan['start_time'] == '22:00' and plan['date'] == 'yesterday'


def test_complex_unreplied_search_is_not_silently_replaced_with_an_unread_filter():
    from types import SimpleNamespace
    from app.assistant.mail_search import email_search_plan
    access = SimpleNamespace(email_search_request=True, email_context=[], request='Find unreplied emails from Recruiter today.')
    assert email_search_plan(access, datetime.now(ZoneInfo('Europe/London'))) is None


def test_message_count_cannot_be_mistaken_for_nighttime_clock_hour():
    from types import SimpleNamespace
    from app.assistant.mail_search import email_search_plan
    access = SimpleNamespace(email_search_request=True, email_context=[],
                             request="Find the last 2 emails from Recruiter yesterday at 11 o'clock at night.")
    plan = email_search_plan(access, datetime(2026, 10, 14, 18, tzinfo=ZoneInfo('Europe/London')))
    assert plan['start_time'] == '23:00' and plan['date'] == 'yesterday'
