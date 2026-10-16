"""Check explicit note payloads and dated task slots against actual staged data."""
import json
import re
from difflib import SequenceMatcher
from datetime import datetime, timedelta
from app.assistant.dates import resolve_date
from app.integrations import request_text

_DAYS = r'(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|today|tomorrow)'
_CLOCK = r'(?<!\d)(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b|(?<!\d)(\d{1,2}):(\d{2})(?!\d)'
_NOTE_CLAUSE = r'(?:[.;]\s*)?\s+(?:and\s+|then\s+)(?:also\s+)?(?=(?:create|make|add)\s+(?:a\s+)?(?:new\s+)?note\b)'


def task_definition(request, now, zone='Europe/London'):
    """Read the schedule before 'to'; preserve times inside the task wording.

    This is a bounded creation grammar, not a consistency judgment about what
    the user chooses to call a task. A title may contain an earlier clock.
    """
    text = request_text(re.sub(r'^\s*(?:and|also|then)\s+', '', request, flags=re.I))
    clause = re.split(_NOTE_CLAUSE, text, maxsplit=1, flags=re.I)[0].strip()
    day = r'(?:today|tomorrow|yesterday|(?:(?:this|next|last|upcoming)\s+)?(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)|\d{4}-\d{2}-\d{2})'
    stamp = r'(?:\d{1,2}:\d{2}(?:\s*[ap]m)?|\d{1,2}\s*[ap]m)'
    match = re.fullmatch(r'\s*(?:(?:please|can you|could you|would you|will you)\s+)*'
                         r'(?:add|create|make|schedule)\s+(?:me\s+)?(?:a\s+|the\s+)?(?:new\s+)?task\s+'
                         r'(?:(?:for|on)\s+(?P<day>'+day+r')\s+at\s+|(?:for|at)\s+)'
                         r'(?P<time>'+stamp+r')\s+(?:to|called|named|titled)\s+(?P<title>.+?)\s*[.!]?\s*',clause,re.I|re.S)
    if not match:
        return None
    title = match['title'].strip(' .!')
    if re.search(r'\b(?:and|then)\s+(?:(?:also|please)\s+)*(?:create|add|make|delete|remove|open|send|update|edit|schedule)\b',title,re.I):
        return None
    if re.search(r'\b(?:tomorrow|next day|following day)\b',title,re.I) and not match['day']:
        return None  # Resolve genuinely date-relative descriptions through the planner.
    pairs = {'"':'"','“':'”',"'":"'"}
    if title[:1] in pairs and title[-1:] == pairs[title[0]]:
        title = title[1:-1]
    times = clocks(match['time'])
    if len(times)!=1 or not title or len(title)>240:
        return None
    date = resolve_date(match['day'],now.date().isoformat(),zone)['date'] if match['day'] else now.date().isoformat()
    return dict(title=title[0].upper()+title[1:],date=date,time=times[0])


def preserved_phrases(content):
    phrases = []
    for match in re.finditer(r'\b([a-z]+)\s+(?:powder|powdwer)\b',content,re.I):
        phrases.append(match[1].lower()+' powder')
    for match in re.finditer(r'\(([a-z]+(?:\s+[a-z]+)+)\)',content,re.I):
        phrases.append(match[1].lower())
    return list(dict.fromkeys(phrases))


def ingredient_markdown(name, content):
    """Format a fully recognised short pantry list without invented ingredients.

    Unknown phrases stay with the general planner. Parenthetic clarification
    replaces an earlier listed item only when its last noun already occurs.
    """
    match = re.fullmatch(r'(?:(?:my|the)\s+)?(?P<subject>[a-z]+(?:\s+[a-z]+)?)\s+ingredients?\s+(?P<list>.+)',content.strip(' .'),re.I)
    if not match:
        return ''
    body = re.sub(r'\bpowdwer\b','powder',match['list'],flags=re.I)
    clarifications = re.findall(r'\(([a-z]+(?:\s+[a-z]+)+)\)',body,re.I)
    body = re.sub(r'\([a-z]+(?:\s+[a-z]+)+\)','',body,flags=re.I)
    if re.search(r'[^a-z,;\s]',body,re.I):
        return ''
    body = re.sub(r'\b(?:garlic|onion|chilli|chili)\s+powder\b|\b(?:black|white|red)\s+pepper\b|\b(?:olive|sesame)\s+oil\b|\bsoy\s+sauce\b',lambda m:m[0].replace(' ','_'),body,flags=re.I)
    items = [item.lower().replace('_',' ') for item in re.split(r'[,;\s]+',body) if item and item.lower()!='and']
    pantry = {'onion','onions','garlic','paprika','pepper','salt','cumin','coriander','oregano','basil','thyme','rosemary','chicken','ginger','turmeric','cinnamon','rice','water','flour','sugar','butter','eggs','milk','lemon','tomatoes','garlic powder','onion powder','chilli powder','chili powder','black pepper','white pepper','red pepper','olive oil','sesame oil','soy sauce'}
    if not items or len(items)>30 or any(item not in pantry for item in items):
        return ''
    for phrase in clarifications:
        noun = phrase.lower().split()[-1]
        if phrase.lower() not in pantry or noun not in items:
            return ''
        items[items.index(noun)] = phrase.lower()
    items = list(dict.fromkeys(items))
    return '# '+name.strip().title()+'\n\n## '+match['subject'].strip().capitalize()+' ingredients\n\n'+''.join('- '+item[0].upper()+item[1:]+'\n' for item in items)


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
    """Find an explicit note write, with a bounded title and its full payload.

    This extracts completion requirements, not executable instructions. Verbs
    mentioned in prose or quoted examples must not introduce a required write.
    Unquoted names require an explicit content connector so they cannot absorb
    another action or silently become the note's entire body.
    """
    def outside_quotes(text, position):
        closing = None
        for index, char in enumerate(text[:position]):
            if closing:
                if char == closing:
                    if char in {"'", '’'} and index and index + 1 < len(text) and text[index - 1].isalnum() and text[index + 1].isalnum():
                        continue
                    closing = None
            elif char in {'"', '“', "'", '‘'}:
                if char in {"'", '‘'} and index and text[index - 1].isalnum():
                    continue  # Contractions and possessives are not quotations.
                closing = {'“': '”', '‘': '’'}.get(char, char)
        return closing is None

    # A subsequent imperative is a separate outcome, not note text. Clear
    # sequential connectors also delimit verbs outside the current tool list.
    boundary = (r'\b(?:then|and\s+then)\s+(?:please\s+)?'
                r'(?!(?:i|we|he|she|it|they|you|had|drank|ate|was|were|went|took|a|an|the|'
                r'next|last|this|today|tomorrow|yesterday)\b)[a-z]+\b|'
                r'\b(?:and(?:\s+also)?|also)\s+(?:please\s+)?'
                r'(?:create|make|schedule|reschedule|delete|remove|move|open|close|'
                r'fetch|download|check|search|rename|archive|send|email|call|'
                r'alphabeti[sz]e|reorder|complete|mark|add\s+(?:a|an|the|my)\s+(?:task|note|event))\b')

    def payload(text):
        for match in re.finditer(boundary, text, re.I):
            if outside_quotes(text, match.start()):
                text = text[:match.start()]
                break
        text = re.sub(r'^that\s+', '', text.strip(), flags=re.I).strip(' .;')
        pairs = {'"': '"', '“': '”', "'": "'", '‘': '’'}
        if len(text) > 1 and text[0] in pairs and text[-1] == pairs[text[0]]:
            text = text[1:-1]
        return text.strip()

    def title(text):
        text = text.strip()
        pairs = {'"': '"', '“': '”', "'": "'", '‘': '’'}
        if len(text) > 1 and text[0] in pairs and text[-1] == pairs[text[0]]:
            text = text[1:-1]
        return ' '.join(text.strip(' .;').split())

    # Require an actual command at a clause boundary. A reference such as
    # "tell me about a note called Recipes" supplies no write requirement.
    start = (r'(?:^|[.;!?]\s*|\b(?:and|then)(?:\s+also)?\s+)'
             r'(?:peter[,\s]+)?(?:please\s+)?'
             r'(?:(?:can|could|would|will)\s+you\s+|'
             r'i\s+(?:want|need|would\s+like)\s+you\s+to\s+)?(?:please\s+)?')
    creation = (start + r'(?:create|make|add)\s+(?:(?:a|an|the|my)\s+)?'
                r'(?:(?:new|net)\s+)?note(?:\s+category)?\s+(?:called|named|titled)\s+')
    quoted = r'(?:"[^"\n]+"|“[^”\n]+”|\'[^\'\n]+\'|‘[^’\n]+’)'
    verbs = r'(?:add|append|put|write|record|save|include|including|with(?:\s+(?:text|content))?|containing)'
    for match in re.finditer(creation, request, re.I):
        if not outside_quotes(request, match.start()):
            continue
        rest = request[match.end():]
        named = re.match(r'(?P<name>' + quoted + r')\s*(?:and\s+)?' + verbs + r'\s+(?P<content>.+)', rest, re.I | re.S)
        if named is None:
            named = re.match(r'(?P<name>[^\n.;!?"“”]+?)\s+(?:and\s+' + verbs +
                             r'|including|with(?:\s+(?:text|content))?|containing)\s+(?P<content>.+)', rest, re.I | re.S)
        if named:
            name, content = title(named['name']), payload(named['content'])
            if not named['name'].lstrip().startswith(('"', '“', "'", '‘')) and re.search(boundary, name, re.I):
                continue
            if name and content:
                return dict(name=name, content=content)

    append = (start + r'(?:add|append|write|put|save|record|include)\s+(?P<content>.+?)\s+'
              r'(?:to|in|into)\s+(?:(?:the|my)\s+)?note\s+(?P<name>' + quoted + r'|[^\n.;!?"“”]+)')
    for match in re.finditer(append, request, re.I | re.S):
        if not outside_quotes(request, match.start()) or not outside_quotes(request, match.start('name')):
            continue
        name, content = title(payload(match['name'])), payload(match['content'])
        if name and content:
            return dict(name=name, content=content)
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
        note['preserve_phrases'] = preserved_phrases(note['content'])
        note['suggested_markdown'] = formatted_observations(note['name'], note['content'], now)
        if not note['suggested_markdown']:
            note['suggested_markdown'] = ingredient_markdown(note['name'], note['content'])
    plan = dict(notes=[note] if note else [], slots=[])
    task = task_definition(request, now, zone)
    if task and (clocks(task['title']) or note):
        plan['task_definitions'] = [task]
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
        actual = _words(content)
        missing = [w for w in _words(requirement['content']) if not any(w == a or SequenceMatcher(None, w, a).ratio() >= .78 for a in actual)]
        times = set(clocks(requirement['content'])) - set(clocks(content))
        if not tx.before.get(name) and set(clocks(content)) - set(clocks(requirement['content'])):
            return f"The note {name!r} has conflicting/unrequested times. Use exactly {requirement['times']}; 9 pm is 21:00, not 09:00."
        if missing or times:
            return f"The note {name!r} is missing requested details ({', '.join(sorted(missing) + sorted(times))}). Preserve all observations and times with clear dated headings/bullets. Append or edit the existing note; do not recreate it."
        for phrase in requirement.get('preserve_phrases',[]):
            if not re.search(r'\b'+r'[ \t]+'.join(re.escape(word) for word in phrase.split())+r'\b',content,re.I):
                return f"The note {name!r} must preserve {phrase!r} as one phrase or ingredient; do not split it into separate list entries."
        if not tx.before.get(name):
            lines = [line.strip() for line in content.splitlines() if line.strip()]
            if len(lines)>=4 and len(lines)%2==0 and lines[:len(lines)//2]==lines[len(lines)//2:]:
                return f"The note {name!r} repeats the same section twice. Edit it to keep the requested content once."
        required_times = clocks(requirement['content'])
        if len(required_times) > 1 and len(re.findall(r'(?m)^\s*[-*]\s+', body)) < len(required_times):
            return f"The note {name!r} needs readable formatting: one bullet per timed observation, with clear date headings. Keep both events and their times; edit the existing note."
    for required in plan.get('task_definitions',[]):
        matching = [e for e in tx.calendar_data['entries'] if e['kind']=='task' and e['title'].casefold()==required['title'].casefold() and e.get('date')==required['date'] and e.get('time')==required['time']]
        if not matching:
            return 'The requested task wording and schedule must be preserved separately: '+json.dumps(required)+'. Times inside its title are content, not replacement scheduling times.'
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
    compact = dict(plan)
    compact['notes'] = []
    for requirement in plan.get('notes',[]):
        item = dict(requirement)
        if len(item['content'])>800:
            item['content_characters'] = len(item['content'])
            item['content'] = item['content'][:240]+' [Full payload is in the current user request; preserve it all.]'
        if len(item.get('suggested_markdown',''))>800:
            item['suggested_markdown'] = '[Format the full current user payload as requested.]'
        compact['notes'].append(item)
    guide = dict(required_outcomes=compact)
    if plan.get('slots'):
        guide['task_rules'] = 'Each slot needs a DISTINCT task. Use edit_task for the existing task and create_task for each additional slot. Never use create_calendar_note for tasks.'
    if plan.get('notes'):
        guide['note_rules'] = 'Save ALL requested content with readable Markdown. Edit existing notes without overwriting unrelated text or duplicating sections.'
        if any(re.search(r'\bingredients?\b',item['content'],re.I) for item in plan['notes']):
            guide['note_rules'] += (' Use one bullet per ingredient; keep garlic powder together. Correct spelling without adding quantities, ingredients or facts; '
                                   'preserve qualifiers, and deduplicate only an explicitly clarified item (pepper clarified as black pepper).')
        if any(item.get('times') for item in plan['notes']):
            guide['note_rules'] += ' Use date headings and one bullet per timed observation, preserving all supplied times.'
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
