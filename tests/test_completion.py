import json
from datetime import datetime
from zoneinfo import ZoneInfo
import pytest
from app.assistant.completion import completion_plan, completion_problem
from app.assistant.engine import Engine
from app.assistant.tools import Tools
from app.assistant.transactions import Transaction
from test_conversation import world, Model

NOW = datetime(2026, 10, 15, 8, tzinfo=ZoneInfo('Europe/London'))
REQUEST = 'schedule my unschedule task to review oustanding items for this friday 12pm and also saturday 3:13am. Then also create a net note category called "dump" and add that i had a mosnter today at 9pm and a camomile at 6am the following morning'
BODY = '# Dump\n\n## 15 October 2026\n- 21:00 — Had a Monster.\n\n## 16 October 2026\n- 06:00 — Had a chamomile.\n'

def call(tool_name, **arguments):
    return dict(name=tool_name, arguments=arguments)


def test_plan_resolves_actual_weekdays_and_preserves_both_observations():
    plan = completion_plan(REQUEST, NOW)
    assert plan['slots'] == [dict(date='2026-10-16', time='12:00'), dict(date='2026-10-17', time='03:13')]
    assert 'mosnter' in plan['notes'][0]['content'] and 'camomile' in plan['notes'][0]['content']


def test_empty_or_partial_note_blocks_commit_even_when_calendar_changed(tmp_path):
    tx = Transaction(*world(tmp_path)); tools = Tools(tx)
    plan = completion_plan(REQUEST, NOW)
    tools.create_note('Dump', '# Dump\n')
    tools.create_task(title='Review outstanding items', date='2026-10-16', time='12:00')
    tools.create_task(title='Review outstanding items', date='2026-10-17', time='03:13')
    assert 'empty' in completion_problem(plan, tx)
    tx.write_note('Dump', '# Dump\n- 21:00 — Had a Monster.')
    assert '06:00' in completion_problem(plan, tx)
    tx.write_note('Dump', BODY)
    assert completion_problem(plan, tx) is None


def test_wrong_weekday_and_duplicated_unscheduled_task_block_completion(tmp_path):
    stores = world(tmp_path); tx = Transaction(*stores); tools = Tools(tx)
    tools.create_task(title='Review outstanding items', date='', time=''); tx.commit('seed')
    tx = Transaction(*stores); tools = Tools(tx)
    tools.create_note('Dump', BODY)
    tools.create_task(title='Review outstanding items', date='2026-10-17', time='12:00')
    tools.create_task(title='Review outstanding items', date='2026-10-18', time='03:13')
    plan = completion_plan(REQUEST, NOW)
    assert '2026-10-16 12:00' in completion_problem(plan, tx)
    entries = tx.calendar_data['entries']
    entries[-2]['date']='2026-10-16'; entries[-1]['date']='2026-10-17'
    assert 'duplicated' in completion_problem(plan, tx)


def test_engine_finishes_missing_body_in_same_transaction(tmp_path):
    stores = world(tmp_path)
    model = Model(dict(tool_calls=[call('create_note', name='Dump', content='')]),
                  dict(answer='Done.', apply_changes=True),
                  dict(tool_calls=[call('edit_note', name='Dump', mode='append', text=BODY)]),
                  dict(answer='', apply_changes=True))
    request = 'Create a new note category called "dump" and add that i had a mosnter today at 9pm and a camomile at 6am the following morning'
    result = Engine(*stores, model, dict(context_size=16384), now=NOW).run(request)
    assert result['operation_id'] and stores[0].read('Dump').strip() == BODY.strip()
    assert len(stores[2].recent()) == 1


def test_repeated_incomplete_answer_does_not_save_anything(tmp_path):
    stores=world(tmp_path)
    model=Model(dict(tool_calls=[call('create_note', name='Dump', content='')]), *[dict(answer='', apply_changes=True)]*4)
    with pytest.raises(ValueError, match='incomplete'):
        Engine(*stores, model, dict(context_size=16384), now=NOW).run('Create a note called "Dump" and add that I had coffee at 9pm')
    assert stores[0].names() == [] and stores[2].recent() == []


def test_correction_recovers_only_missing_note_content():
    plan=completion_plan("you didn't add to that note what I said", NOW, conversation=[dict(role='user', text=REQUEST)])
    assert plan['notes'][0]['name'] == 'dump' and not plan['slots']


def test_fully_resolved_compound_request_preserves_original_task_and_formatted_payload(tmp_path):
    stores=world(tmp_path); tx=Transaction(*stores); tools=Tools(tx)
    original=tools.create_task(title='Review outstanding items',date='',time='',details='Keep the checklist.')
    tx.commit('seed')
    model=Model()  # A fully resolved request needs no generated facts/actions.
    result=Engine(*stores,model,dict(context_size=16384),now=NOW).run(REQUEST)
    entries=stores[1].entries()
    assert len(entries)==2 and all(e['kind']=='task' for e in entries)
    first=next(e for e in entries if e['id']==original['id'])
    assert (first['date'],first['time'],first['details'])==('2026-10-16','12:00','Keep the checklist.')
    second=next(e for e in entries if e['id']!=original['id'])
    assert (second['date'],second['time'],second['details'])==('2026-10-17','03:13','Keep the checklist.')
    assert stores[0].read('Dump') == '# Dump\n\n## 2026-10-15\n- 21:00 — Had a Monster.\n\n## 2026-10-16\n- 06:00 — Had a chamomile.\n'
    assert result['operation_id'] and not model.prompts


def test_compiler_requires_unique_task_and_full_request_coverage(tmp_path):
    from app.assistant.completion import explicit_compound_actions
    stores=world(tmp_path);tx=Transaction(*stores);tools=Tools(tx)
    tools.create_task(title='Review outstanding items',date='',time='')
    tools.create_task(title='Review outstanding item',date='',time='')
    plan=completion_plan(REQUEST,NOW)
    assert explicit_compound_actions(REQUEST,plan,tx) is None
    tx.calendar_data['entries'].pop()
    assert explicit_compound_actions(REQUEST+' and delete my other tasks',plan,tx) is None


def test_wrong_original_slot_and_conflicting_note_clock_are_rejected(tmp_path):
    stores=world(tmp_path);tx=Transaction(*stores);tools=Tools(tx)
    original=tools.create_task(title='Review outstanding items',date='',time='');tx.commit('seed')
    tx=Transaction(*stores);tools=Tools(tx)
    tools.create_note('Dump',BODY)
    tools.edit_task(original['id'],dict(date='2026-10-15',time='08:00'))
    tools.create_task(title='Review outstanding items',date='2026-10-16',time='12:00')
    tools.create_task(title='Review outstanding items',date='2026-10-17',time='03:13')
    plan=completion_plan(REQUEST,NOW)
    assert 'FIRST requested slot' in completion_problem(plan,tx)
    tx.write_note('Dump',BODY.replace('21:00','09:00 (9pm)'))
    assert 'conflicting' in completion_problem(plan,tx)
