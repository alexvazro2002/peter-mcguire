"""Check explicit note payloads and dated task slots against actual staged data."""
import json
import re
from difflib import SequenceMatcher
from datetime import datetime, timedelta
from app.assistant.dates import resolve_date

_DAYS = r'(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|today|tomorrow)'
_CLOCK = r'(?<!\d)(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b|(?<!\d)(\d{1,2}):(\d{2})(?!\d)'


def clocks(text):
    result = []
    for m in re.finditer(_CLOCK, text, re.I):
        hour, minute = int(m[1] or m[4]), int(m[2] or m[5] or 0)
        if m[3]:
            if not 1 <= hour <= 12:
                continue
            hour = hour % 12 + (12 if m[3].lower() == 'pm' else 0)
        if hour < 24 and minute < 60:
            result.append(f'{hour:02}:{minute:02}')
    return result


def _note(request):
    # Explicit destinations only: do not silently reinterpret calendar day notes.
    patterns = [
        r'\bnote(?:\s+category)?\s+(?:called|named|titled)\s+["“\']([^"”\']+)["”\']\s*(?:and\s+)?(?:add|put|write|record|save|with(?:\s+(?:text|content))?|containing)\s+(?:that\s+)?(.+)',
        r'\b(?:add|append|write|put|save|record)\s+(.+?)\s+(?:to|in|into)\s+(?:(?:the|my)\s+)?note\s+["“\']([^"”\']+)["”\']',
    ]
    for i, pattern in enumerate(patterns):
        m = re.search(pattern, request, re.I | re.S)
        if m:
            name, content = (m[1], m[2]) if i == 0 else (m[2], m[1])
            content = re.split(r'\b(?:then|and also)\s+(?:create|schedule|delete|move|add a task)\b', content, maxsplit=1, flags=re.I)[0].strip(' ."')
            return dict(name=name.strip(), content=content)
    return None


def completion_plan(request, now, zone='Europe/London', conversation=()):
    note = _note(request)
    # A correction repairs the omitted payload, not every already-saved action.
    if note is None and re.search(r"\b(?:didn.t|forgot|missing|left out)\b.*\b(?:note|add|text|said)\b", request, re.I):
        for row in reversed(conversation[-12:]):
            if row.get('role') == 'user' and (note := _note(row.get('text', ''))):
                break
    if note:
        note['times'] = clocks(note['content'])
        note['suggested_markdown'] = formatted_observations(note['name'], note['content'], now)
    plan = dict(notes=[note] if note else [], slots=[])
    if re.search(r'\b(?:schedule|create|add)\b', request, re.I) and re.search(r'\btask\b', request, re.I) and not re.search(r'\b(?:reschedule|from .+ to|every)\b', request, re.I):
        part = re.split(r'\b(?:then|also)\s+(?:create|add|make)\s+(?:a\s+)?(?:new |net )?note\b', request, maxsplit=1, flags=re.I)[0]
        pattern = r'\b((?:(?:this|next|upcoming)\s+)?' + _DAYS + r')\s*(?:at|for)?\s*(\d{1,2}(?::\d{2})?\s*(?:am|pm)|\d{1,2}:\d{2})\b'
        for m in re.finditer(pattern, part, re.I):
            stamp = clocks(m[2])
            if stamp:
                plan['slots'].append(dict(date=resolve_date(m[1], now.date().isoformat(), zone)['date'], time=stamp[0]))
        plan['reuse_unscheduled'] = bool(re.search(r'\bunschedule[d]?\s+task\b', part, re.I))
    return plan


_SKIP = set('i me my had have a an the and at that it was is today tomorrow following next morning evening night then with to of on for in this please add put write record save drank drink'.split())

def _words(text):
    text = re.sub(_CLOCK, ' ', text, flags=re.I)
    text = re.sub(r'\d{4}-\d{2}-\d{2}', ' ', text)
    return {w for w in re.findall(r'[a-z]+', text.casefold()) if w not in _SKIP and len(w) > 2}


def completion_problem(plan, tx, write_results=()):
    for requirement in plan['notes']:
        name = next((n for n in tx.names() if n.casefold() == requirement['name'].casefold()), None)
        if name is None:
            return f"The requested note {requirement['name']!r} has not been created. Complete that part before finishing."
        content = tx.read_note(name)
        body = re.sub(r'(?m)^\s*#.*$', '', content).strip()
        if not body:
            return f"The note {name!r} is empty. Its requested content is still missing: {requirement['content']}. Use edit_note on the existing staged note, not create_note again."
        actual = _words(body)
        missing = [w for w in _words(requirement['content']) if not any(w == a or SequenceMatcher(None, w, a).ratio() >= .78 for a in actual)]
        times = set(clocks(requirement['content'])) - set(clocks(content))
        if not tx.before.get(name) and set(clocks(content)) - set(clocks(requirement['content'])):
            return f"The note {name!r} has conflicting/unrequested times. Use exactly {requirement['times']}; 9 pm is 21:00, not 09:00."
        if missing or times:
            return f"The note {name!r} is missing requested details ({', '.join(sorted(missing) + sorted(times))}). Preserve all observations and times with clear dated headings/bullets. Append or edit the existing note; do not recreate it."
        required_times = clocks(requirement['content'])
        if len(required_times) > 1 and len(re.findall(r'(?m)^\s*[-*]\s+', body)) < len(required_times):
            return f"The note {name!r} needs readable formatting: one bullet per timed observation, with clear date headings. Keep both events and their times; edit the existing note."
    slots = plan.get('slots', [])
    if slots:
        before = {e['id']: e for e in json.loads(tx.calendar_before)['entries']}
        entries = [e for e in tx.calendar_data['entries'] if e['kind'] == 'task']
        writes = [e for e in entries if e != before.get(e['id'])]
        candidates = writes or entries  # idempotent retries may already be satisfied
        for slot in slots:
            if not any(e.get('date') == slot['date'] and e.get('time') == slot['time'] for e in candidates):
                return (f"The task at {slot['date']} {slot['time']} is missing. ALL required DISTINCT TASKS: " + json.dumps(slots) +
                        ". Do not move one task back and forth between these slots. Keep completed slots and use create_task for additional occurrences. "
                        "create_calendar_note creates a journal entry, NOT a task. Remove any unintended day notes created during this request.")
        if plan.get('reuse_unscheduled'):
            titles = {e['title'].casefold() for e in writes}
            if any(e['kind'] == 'note' and e['title'].casefold() in titles and e != before.get(e['id'])
                   for e in tx.calendar_data['entries']):
                return 'A task was also recorded as an unintended calendar day note. Remove only that staged duplicate; task occurrences must use create_task.'

            for old in before.values():
                if old['kind'] == 'task' and not old.get('date') and any(e['title'].casefold() == old['title'].casefold() for e in writes):
                    matching = [e for e in entries if e['title'].casefold() == old['title'].casefold()]
                    original = next((e for e in matching if e['id'] == old['id']), None)
                    if original is None or not any(original.get('date') == slot['date'] and original.get('time') == slot['time'] for slot in slots):
                        return 'The existing unscheduled task was duplicated instead of scheduled correctly. Update its original ID to the FIRST requested slot, not an invented time; keep only the requested additional occurrence.'
                    changed_matching = [e for e in matching if e != before.get(e['id'])]
                    if len(changed_matching) > len(slots):
                        return 'Extra task occurrences were staged. Keep exactly the requested distinct slots and preserve the original task ID.'
    return None


def completion_guidance(plan, tx):
    """Concrete schema-ready suggestions from actual existing task IDs, never guesses."""
    guide = dict(required_outcomes=plan)
    if plan.get('slots'):
        guide['task_rules'] = 'Each slot needs a DISTINCT task. Use edit_task for the existing task and create_task for each additional slot. Never use create_calendar_note for tasks.'
    if plan.get('notes'):
        guide['note_rules'] = 'Create the note WITH ALL requested text in content. Use title case, correct obvious misspellings, date headings and one bullet per timed observation. A title alone is incomplete. If it already exists, read and edit it.'
    return guide


def formatted_observations(name, content, now):
    """Format an unambiguous timed journal payload without inventing facts."""
    clauses = re.split(r'\s+and\s+', content.strip(), flags=re.I)
    if not clauses or any(len(clocks(clause)) != 1 for clause in clauses):
        return ''
    rows = []
    day = now.date()
    for index, clause in enumerate(clauses):
        if re.search(r'\b(?:following|next) morning\b', clause, re.I):
            day += timedelta(days=1)
        elif re.search(r'\btomorrow\b', clause, re.I):
            day = now.date() + timedelta(days=1)
        elif re.search(r'\btoday\b', clause, re.I):
            day = now.date()
        elif re.search(r'\b(?:yesterday|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b|\d{4}-\d{2}-\d{2}', clause, re.I):
            return ''  # General model/date tools handle richer expressions.
        stamp = clocks(clause)[0]
        text = re.sub(_CLOCK, '', clause, flags=re.I)
        text = re.sub(r'\b(?:today|tomorrow|at|the following morning|the next morning)\b', '', text, flags=re.I)
        text = re.sub(r'^\s*i\s+', '', text, flags=re.I)
        text = re.sub(r'\bmosnter\b|\bmonster\b', 'Monster', text, flags=re.I)
        text = re.sub(r'\bcamomile\b', 'chamomile', text, flags=re.I)
        text = re.sub(r'\s+', ' ', text).strip(' .')
        if index and re.match(r'^(?:a|an|some)\b', text, re.I):
            # An elided verb inherits only an explicit 'had' from the first clause.
            if not re.match(r'\s*(?:i\s+)?had\b', clauses[0], re.I):
                return ''
            text = 'had ' + text
        if not re.match(r'(?:had|drank|ate|took|played|watched|read|finished|started|visited|went)\b', text, re.I):
            return ''
        rows.append((day.isoformat(), stamp, text[0].upper() + text[1:] + '.'))
    output = '# ' + name.strip().title() + '\n'
    previous = None
    for date, stamp, text in rows:
        if date != previous:
            output += '\n## ' + date + '\n'
            previous = date
        output += '- ' + stamp + ' — ' + text + '\n'
    return output


def explicit_compound_actions(request, plan, tx):
    """Compile a fully recognised schedule-existing + timed-note request.

    No partial parsing may execute: the whole action grammar, every clock, unique
    existing task and note payload must resolve before returning any operations.
    Other requests retain the normal model/tool loop and completion checks.
    """
    if not (plan.get('reuse_unscheduled') and len(plan['slots']) >= 1 and len(plan['notes']) == 1):
        return None
    note = plan['notes'][0]
    if not note.get('suggested_markdown'):
        return None
    pattern = (r'\s*(?:please\s+)?schedule\s+(?:(?:my|the)\s+)?unschedule[d]?\s+task\s+(?:to\s+)?(.+?)\s+for\s+(.+?)'
               r'[.;]?\s+(?:then\s+)?(?:also\s+)?create\s+(?:a\s+)?(?:new|net)?\s*note(?:\s+category)?\s+(?:called|named|titled)\s+["“\']([^"”\']+)["”\']\s+and\s+add\s+(?:that\s+)?(.+?)\s*[.!]?')
    match = re.fullmatch(pattern, request, re.I | re.S)
    if not match or match[3].casefold() != note['name'].casefold() or match[4].strip(' .!') != note['content'].strip(' .!'):
        return None
    slot_pattern = r'\b(?:(?:this|next|upcoming)\s+)?' + _DAYS + r'\s*(?:at|for)?\s*(?:\d{1,2}(?::\d{2})?\s*(?:am|pm)|\d{1,2}:\d{2})\b'
    rest = re.sub(slot_pattern, '', match[2], flags=re.I)
    if re.sub(r'\b(?:and|also)\b|[,.\s]', '', rest, flags=re.I):
        return None
    wanted = re.sub(r'\s+', ' ', match[1]).strip().casefold()
    tasks = [e for e in tx.calendar_data['entries'] if e['kind'] == 'task' and not e.get('date') and not e.get('done')
             and SequenceMatcher(None, wanted, e['title'].casefold()).ratio() >= .86]
    if len(tasks) != 1:
        return None
    original = tasks[0]
    actions = [dict(name='edit_task', arguments=dict(id=original['id'], patch=plan['slots'][0]))]
    for slot in plan['slots'][1:]:
        args = {k: original[k] for k in ('title', 'details', 'note', 'metadata') if original.get(k)}
        actions.append(dict(name='create_task', arguments=dict(args, **slot)))
    existing = next((n for n in tx.names() if n.casefold() == note['name'].casefold()), None)
    if existing is None:
        actions.append(dict(name='create_note', arguments=dict(name=note['name'].title(), content=note['suggested_markdown'])))
    elif not tx.read_note(existing).strip():
        actions.append(dict(name='edit_note', arguments=dict(name=existing, mode='append', text=note['suggested_markdown'])))
    else:
        return None  # Preserve existing content; normal tools resolve duplicates/sections.
    return actions
