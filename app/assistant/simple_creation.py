"""Execute fully understood task-plus-note creations as one verified transaction."""
import re

from app.assistant.completion import _NOTE_CLAUSE, completion_plan, completion_problem
from app.assistant.dates import local_now
from app.assistant.tools import Tools
from app.assistant.transactions import Transaction
from app.integrations import request_text


def _command_text(request):
    """Remove copy/paste quotation marks at the command boundary only.

    Balanced quotes within the command may delimit a task title or note text.
    Keep those intact rather than stripping punctuation from the payload.
    """
    text = request.strip()
    pairs = {'"': '"', '“': '”', "'": "'", '‘': '’'}
    if len(text) > 1 and text[:1] in pairs and text[-1:] == pairs[text[0]]:
        text = text[1:-1].strip()
    elif text.endswith('"') and text.count('"') % 2:
        text = text[:-1].rstrip()
    elif text.endswith('”') and text.count('”') == text.count('“') + 1:
        text = text[:-1].rstrip()
    return request_text(re.sub(r'^\s*(?:and|also|then)\s+', '', text, flags=re.I))


def respond(request, vault, calendar, history, cancel, zone='Europe/London', now=None):
    text = _command_text(request)
    parts = re.split(_NOTE_CLAUSE,text,maxsplit=1,flags=re.I)
    if len(parts)!=2:
        return None
    now = now or local_now(zone)
    plan = completion_plan(text,now,zone)
    tasks, notes = plan.get('task_definitions',[]), plan['notes']
    if len(tasks)!=1 or len(notes)!=1 or not notes[0].get('suggested_markdown'):
        return None
    note = notes[0]
    # Require the final clause to be exactly a note creation, not a recognised
    # prefix followed by work that the parser cannot perform.
    head = re.match(r'(?:create|make|add)\s+(?:a\s+)?(?:new\s+)?note\s+(?:called|named|titled)\s+',parts[1],re.I)
    if head is None:
        return None
    tail = parts[1][head.end():]
    expected = re.fullmatch(r'(?:["“\']?'+re.escape(note['name'])+r'["”\']?)\s+(?:and\s+)?(?:include|including|add|with(?:\s+(?:text|content))?|containing)\s+(?:that\s+)?'+re.escape(note['content'])+r'[.!\s]*',tail,re.I|re.S)
    if expected is None:
        return None
    if cancel.is_set():
        raise ValueError('Request cancelled. Nothing was saved.')
    history.recover()
    tx = Transaction(vault,calendar,history)
    existing = next((name for name in tx.names() if name.casefold()==note['name'].casefold()),None)
    if existing and tx.read_note(existing).strip()!=note['suggested_markdown'].strip():
        return None  # The ordinary planner can inspect/merge existing contents.
    tools = Tools(tx,zone,today=now.date().isoformat())
    executed = []
    if existing is None:
        result = tools.execute('create_note',dict(name=note['name'].title(),content=note['suggested_markdown']))
        executed.append(dict(tool='create_note',result=result))
    result = tools.execute('create_task',dict(tasks[0],details=''))
    executed.append(dict(tool='create_task',result=result))
    problem = completion_problem(plan,tx,executed)
    if problem:
        raise ValueError('Nothing was saved. '+problem)
    if cancel.is_set():
        raise ValueError('Request cancelled. Nothing was saved.')
    operation_id = tx.commit(request)
    if operation_id:
        answer = f"Added “{tasks[0]['title']}” for {tasks[0]['date']} at {tasks[0]['time']} and saved the ingredients in {note['name'].title()}."
    else:
        answer = 'That task and note are already saved.'
    return dict(text=answer,changes=list(tx.summaries),sources=tx.sources,operation_id=operation_id,
                evidence=dict(tools=[row['tool'] for row in executed],workspace_action='task_and_note_creation',
                              task_title=tasks[0]['title'],date=tasks[0]['date'],time=tasks[0]['time'],
                              note=note['name'].title(),verified=True))
