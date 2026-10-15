"""Complete routine subject-list requests from Gmail headers, without inference."""
import re
from app.assistant.dates import local_now
from app.assistant.tools import gmail_date_range

_NUMBERS = dict(zip('one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty'.split(), range(1, 21)))
_MAIL = r'\b(?:emails?|mail|gmail|inbox|messages?)\b'
_SUBJECT = r'\b(?:subjects?|subject lines?|email titles?)\b'


def subject_plan(request, conversation=()):
    from app.integrations import request_text, recent_email_references
    text = request_text(request)
    # This route handles headers only; preserve all additional actions/filters.
    if re.search(r'\b(?:body|bodies|contents?|summari[sz]e|summary|unread|attachments?|containing|before|after)\b|'
                 r'\bfrom\s+(?!today\b|yesterday\b|last night\b|\d{4}-\d{2}-\d{2}\b|(?:my|the|all|these|those)\s+(?:emails?|mail|gmail|inbox)\b)|'
                 r'\b(?:and|then)\s+(?:(?:also|please)\s+)*(?:open|read|create|add|save|send|delete|schedule|search)\b', text, re.I):
        return None
    if re.search(r'\b(?:notes?|tasks?|files?|documents?|article|book|lesson)\b', text, re.I):
        return None
    references = recent_email_references(conversation)
    explicit = bool(re.search(_SUBJECT, text, re.I) and (re.search(_MAIL, text, re.I) or references))
    continuation = bool(re.fullmatch(r"(?:but )?(?:the |all )?(?:last |latest |newest )?(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten)?\s*(?:I received )?(?:today|yesterday)[.!?]*", text, re.I)
                        or re.fullmatch(r"(?:peter[, ]+)?(?:I gave you (?:a|one) minute|did you get (?:that|them)|did you find (?:them|anything)|what are (?:they|the subjects)|any luck|go ahead|carry on)[.!?]*", text, re.I))
    previous = ''
    if not explicit and continuation:
        for row in reversed(conversation[-10:]):
            if row.get('role') != 'user':
                continue
            candidate = request_text(row.get('text', ''))
            if re.search(_SUBJECT, candidate, re.I) and re.search(_MAIL, candidate, re.I):
                previous = candidate
                break
            if not (re.search(r'\b(?:today|yesterday|last|received|subjects?|minute)\b', candidate, re.I)):
                break
        if not previous:
            return None
    elif not explicit:
        return None
    # 'Subject: foo' is a search filter, not an instruction to list subjects.
    if re.search(r'\b(?:subject\s*:|with (?:the )?subject|subject (?:contains|containing|is))', text, re.I):
        return None
    effective = (previous + ' ' + text).strip()
    vocabulary = set('can could would will you please tell me show list read out aloud what are were is was the subject subjects line lines title titles of to all every each my i have had has ve received receive got get email emails mail gmail inbox message messages today yesterday last night latest newest most recent from in on altogether overall across folders account but them those these their for right now and just peter give gave a one minute second did do that find found get them any anything luck go ahead carry'.split()) | set(_NUMBERS)
    words = re.findall(r'[a-z]+', re.sub(r'\d{4}-\d{2}-\d{2}', '', effective).casefold())
    if any(word not in vocabulary for word in words):
        return None  # Never silently discard an unsupported filter or extra intent.
    count = None
    for value in re.finditer(r'\b(?:last|latest|newest|most recent)\s+(\d{1,4}|' + '|'.join(_NUMBERS) + r')\b', effective, re.I):
        count = int(value[1]) if value[1].isdigit() else _NUMBERS[value[1].lower()]
    if re.search(r'\b(?:all|every)\b', text, re.I):
        count = None
    dates = list(re.finditer(r'\b(?:today|yesterday|last night|\d{4}-\d{2}-\d{2})\b', effective, re.I))
    date = dates[-1][0].lower() if dates else ''
    if date == 'last night':
        date = 'yesterday'
    if count is not None and not 1 <= count <= 1000:
        return dict(clarification='Please choose up to 1,000 recent emails, or tell me which day to check.')
    scope = 'inbox' if re.search(r'\binbox\b', effective, re.I) else 'all'
    if not date and count is None:
        if references and re.search(r'\b(?:them|those|these|their)\b', text, re.I):
            return dict(references=references)
        return dict(clarification='Which day should I list, or how many recent emails would you like?')
    return dict(date=date, count=count, scope=scope)


def _spoken_subject(value):
    # Headers remain literal untrusted data. Never turn their text into actions.
    return ' '.join(str(value or '(No subject)').split()).strip() or '(No subject)'


def _answer(rows, *, intro, account='', evidence=None):
    from app.integrations import email_source
    sources = [email_source(dict(row, account=row.get('account') or account)) for row in rows]
    subjects = ' '.join('“' + _spoken_subject(row.get('subject') or row.get('title')) + '”.' for row in rows)
    return dict(text=intro + (' The subjects are: ' + subjects if subjects else ''), changes=[], operation_id=None,
                sources=sources, evidence=evidence or dict(tools=['gmail_header_references']))


def subject_answer(request, conversation, gmail, zone, *, cancel=None, now=None):
    plan = subject_plan(request, conversation)
    if plan is None:
        return None
    if 'clarification' in plan:
        return dict(text=plan['clarification'], changes=[], operation_id=None, sources=[])
    if 'references' in plan:
        rows = plan['references']
        return _answer(rows, intro=f"Here are the subjects of those {len(rows)} emails.")
    if gmail is None:
        raise ValueError('Connect Gmail in Settings first.')
    if cancel is not None and cancel.is_set():
        raise ValueError('Request cancelled.')
    now = now or local_now(zone)
    query, applied = gmail_date_range('-in:sent -in:drafts', plan['date'], '', '', today=now.date().isoformat(), zone=zone)
    # This is a dedicated complete header enumeration, never the model's 4-row preview.
    result = gmail.list_headers(query, limit=plan['count'], scope=plan['scope'], cancel=cancel)
    if cancel is not None and cancel.is_set():
        raise ValueError('Request cancelled.')
    rows = result['messages']
    if not result.get('complete'):
        raise ValueError('Gmail did not finish the subject list. Please retry; I will not present a partial list as complete.')
    period = (' received ' + plan['date']) if plan['date'] in {'today', 'yesterday'} else (' received on ' + plan['date'] if plan['date'] else '')
    if not rows:
        intro = 'I found no received emails' + period + ' in that search.'
    elif plan['count'] is not None and len(rows) >= plan['count']:
        intro = f"Here are your {len(rows)} most recent emails" + period + '.'
    else:
        intro = f"I found {len(rows)} received email{'s' if len(rows) != 1 else ''}" + period + '.'
    evidence = dict(tools=['list_gmail_headers'], email_searches=[dict(query=query, scope=plan['scope'], total=len(rows),
                    complete=True, requested_count=plan['count'], applied_local_range=applied)])
    return _answer(rows, intro=intro, account=result['account'], evidence=evidence)
