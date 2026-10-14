"""Resolve straightforward spoken Gmail searches from the user's own recent wording."""
import re
from datetime import timedelta


def email_search_plan(access, now):
    if not getattr(access, 'email_search_request', False):
        return None
    requests = [*getattr(access, 'email_context', []), access.request]
    current = access.request
    # Rich filters need the model's tool planner. Don't silently discard them.
    if re.search(r'\b(?:subject|unread|unreplied|replied|attachments?|containing|labelled|labeled|older than|before|after)\b', current, re.I) or re.search(
            r'\b(?:and|then)\s+(?:(?:please|can you)\s+)?(?:open|read|create|add|save|remember|send|delete|schedule)\b', current, re.I):
        return None
    sender, date, clock, approximate = '', '', '', False
    for request in requests:
        match = re.search(r'\bfrom\s+(.+?)(?=\s+(?:today|yesterday|last night|within|since|around|at|on|but|received|after|before)\b|[,?.!]|$)', request, re.I)
        if match:
            value = match[1].strip(' "\'')
            if (not re.search(r'\b(?:same|person|sender|inbox|all|any|my|today|yesterday|last night)\b', value, re.I)
                    and not re.match(r'\d', value) and len(value) <= 120):
                sender = value
        explicit_date = re.search(r'\b(today|yesterday|last night|lastnight)\b|\b(\d{4}-\d{2}-\d{2})\b', request, re.I)
        if explicit_date:
            date = explicit_date[0].lower()
            date = 'yesterday' if date in {'last night', 'lastnight'} else date
            # A newer date without a time means the whole day, not an older window.
            clock, approximate = '', False
        match = re.search(r'\b(\d{1,2})(?::([0-5]\d))?\s*(?:o[\'’]?clock\s*)?(a\.?m\.?|p\.?m\.?)\b', request, re.I)
        if not match and re.search(r'\b(?:last night|at night|yesterday evening)\b', request, re.I):
            match = re.search(r'\b(?:at|around|about|from|since)\s+(\d{1,2})(?::([0-5]\d))?(?:\s*o[\'’]?clock)?\b', request, re.I)
            match = match or re.search(r'\b(\d{1,2})(?::([0-5]\d))?\s*o[\'’]?clock\b', request, re.I)
            meridiem = 'pm'
        else:
            meridiem = match[3].lower().replace('.', '') if match else ''
        if match:
            hour, minute = int(match[1]), int(match[2] or 0)
            if not 1 <= hour <= 12:
                return None
            hour = hour % 12 + (12 if meridiem == 'pm' else 0)
            clock = f'{hour:02}:{minute:02}'
            approximate = bool(re.search(r'\b(?:around|about|roughly)\b', request, re.I))
    recent = re.search(r'\blast\s+(\d{1,2})\s+hours?\b', current, re.I)
    query = 'from:"' + sender.replace('"', '') + '"' if sender else ''
    scope = 'inbox' if re.search(r'\binbox\b', current, re.I) else 'all'
    plan = dict(query=query, scope=scope, limit=4)
    if recent:
        hours = int(recent[1])
        if not 1 <= hours <= 48:
            return None
        plan['query'] = (query + f' after:{int((now-timedelta(hours=hours)).timestamp())} before:{int(now.timestamp())}').strip()
    elif date:
        plan['date'] = date
        if clock:
            plan['start_time'] = clock
            if approximate:
                hour, minute = map(int, clock.split(':'))
                # Avoid wrapping the start into the wrong day; the model can
                # plan unusual midnight-spanning approximations explicitly.
                if hour == 0:
                    return None
                plan['start_time'] = f'{hour-1:02}:{minute:02}'
                plan['end_time'] = f'{(hour+1)%24:02}:{minute:02}'
    elif clock:
        return None
    if not sender and not date and not recent:
        return None
    return plan
