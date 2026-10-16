"""Task wording and scheduling stay separate in verified two-part creations."""
from datetime import datetime
from threading import Event
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app.assistant.completion import completion_plan, completion_problem, ingredient_markdown, task_definition
from app.assistant.service import AssistantService
from app.assistant.simple_creation import _command_text, respond
from app.assistant.tools import Tools
from app.assistant.transactions import Transaction
from test_conversation import world


NOW=datetime(2026,10,15,19,22,tzinfo=ZoneInfo('Europe/London'))
REQUEST=('Add a task for 21:00 to finish my cooking before 20:22. and create a new note called recipes '
         'and include my chicken ingredients onion garlic powdwer paprika pepper and salt (black pepper)')
EXPECTED='# Recipes\n\n## Chicken ingredients\n\n- Onion\n- Garlic powder\n- Paprika\n- Black pepper\n- Salt\n'


def test_task_time_is_separate_from_the_literal_title_time():
    assert task_definition(REQUEST,NOW)==dict(title='Finish my cooking before 20:22',date='2026-10-15',time='21:00')
    assert task_definition('Create a task for tomorrow at 9pm called "Finish before 20:22".',NOW)==dict(title='Finish before 20:22',date='2026-10-16',time='21:00')


@pytest.mark.parametrize('utterance',[REQUEST,REQUEST+'"','"'+REQUEST+'"'])
def test_exact_request_saves_both_once_without_model_and_preserves_every_ingredient(tmp_path,utterance):
    vault,calendar,history=world(tmp_path)
    result=respond(utterance,vault,calendar,history,Event(),now=NOW)
    assert result['operation_id'] and result['evidence']['verified']
    assert vault.read('Recipes')==EXPECTED
    tasks=calendar.entries()
    assert len(tasks)==1 and tasks[0]['title']=='Finish my cooking before 20:22'
    assert (tasks[0]['date'],tasks[0]['time'])==('2026-10-15','21:00')
    retry=respond(utterance,vault,calendar,history,Event(),now=NOW)
    assert retry['operation_id'] is None and not retry['changes']
    assert len(calendar.entries())==1 and vault.read('Recipes')==EXPECTED
    assert len(history.recent())==1
    history.undo(result['operation_id'])
    assert calendar.entries()==[] and vault.names()==[]


def test_service_does_not_load_a_model_for_the_exact_common_creation(tmp_path,monkeypatch):
    monkeypatch.setattr('app.assistant.simple_creation.local_now',lambda zone:NOW)
    vault,calendar,_=world(tmp_path)
    runtime=SimpleNamespace(settings={},ensure=lambda:pytest.fail('The parsed request needs no inference'))
    service=AssistantService(vault,calendar,runtime,tmp_path/'chat.json')
    service.send(REQUEST)
    result=service.events.get(timeout=2)
    assert result['type']=='answer' and result['operation_id']
    assert service.events.empty() and vault.read('Recipes')==EXPECTED


@pytest.mark.parametrize('utterance',[
    REQUEST+'"',
    REQUEST+'”',
    '"'+REQUEST+'"',
    '“'+REQUEST+'”',
    "'"+REQUEST+"'",
    '‘'+REQUEST+'’',
    REQUEST.replace('called recipes','called "recipes"')+'"',
    REQUEST.replace('called recipes','called “recipes”')+'”',
])
def test_command_boundary_quotes_do_not_trigger_inference_or_duplicate_saved_actions(tmp_path,monkeypatch,utterance):
    monkeypatch.setattr('app.assistant.simple_creation.local_now',lambda zone:NOW)
    vault,calendar,history=world(tmp_path)
    first=respond(REQUEST,vault,calendar,history,Event(),now=NOW)
    assert first['operation_id']
    runtime=SimpleNamespace(settings={},ensure=lambda:pytest.fail('A harmless boundary quote needs no inference'))
    service=AssistantService(vault,calendar,runtime,tmp_path/'chat.json')
    service.send(utterance)
    result=service.events.get(timeout=2)
    assert result['type']=='answer' and result['operation_id'] is None
    assert result['text']=='That task and note are already saved.'
    assert not result['changes'] and service.events.empty()
    assert len(calendar.entries())==1 and vault.read('Recipes')==EXPECTED
    assert len(history.recent())==1


@pytest.mark.parametrize('utterance',[
    REQUEST.replace('called recipes','called "recipes"'),
    REQUEST.replace('called recipes','called “recipes”'),
    REQUEST.replace('include my chicken','include "my chicken')+'"',
    REQUEST.replace('include my chicken','include “my chicken')+'”',
    REQUEST.replace('onion garlic','onion "garlic"'),
])
def test_balanced_quotes_inside_the_command_are_preserved(utterance):
    assert _command_text(utterance)==utterance


def test_wrong_schedule_or_removed_title_time_cannot_pass_completion(tmp_path):
    vault,calendar,history=world(tmp_path)
    tx=Transaction(vault,calendar,history);tools=Tools(tx)
    tools.execute('create_note',dict(name='Recipes',content=EXPECTED))
    tools.execute('create_task',dict(title='Finish cooking',date='2026-10-15',time='20:22'))
    assert 'wording and schedule' in completion_problem(completion_plan(REQUEST,NOW),tx)


def test_split_multiword_ingredient_and_duplicated_sections_are_rejected(tmp_path):
    vault,calendar,history=world(tmp_path)
    tx=Transaction(vault,calendar,history)
    plan=completion_plan(REQUEST,NOW);plan['task_definitions']=[]
    tx.write_note('Recipes',EXPECTED.replace('- Garlic powder','- Garlic\n- Powder'),create=True)
    assert 'garlic powder' in completion_problem(plan,tx)
    tx.write_note('Recipes',EXPECTED+'\n'+EXPECTED)
    assert 'repeats' in completion_problem(plan,tx)
    tx.write_note('Recipes',EXPECTED.replace('# Recipes\n\n',''))
    assert completion_problem(plan,tx) is None  # A useful first heading counts.


@pytest.mark.parametrize('suffix',[
    ' Then delete my oldest note.',
    ' and open my calendar.',
    ' and include a cooking method.',
])
def test_extra_work_never_gets_silently_dropped_or_partially_saved(tmp_path,suffix):
    vault,calendar,history=world(tmp_path)
    assert respond(REQUEST+suffix,vault,calendar,history,Event(),now=NOW) is None
    assert vault.names()==[] and calendar.entries()==[] and history.recent()==[]


def test_existing_different_recipes_are_preserved_for_the_normal_planner(tmp_path):
    vault,calendar,history=world(tmp_path);vault.create('Recipes','# Recipes\n\nA different recipe.\n')
    assert respond(REQUEST,vault,calendar,history,Event(),now=NOW) is None
    assert vault.read('Recipes')=='# Recipes\n\nA different recipe.\n' and calendar.entries()==[]


def test_cancelled_command_saves_neither_action(tmp_path):
    vault,calendar,history=world(tmp_path);cancel=Event();cancel.set()
    with pytest.raises(ValueError,match='cancelled'):
        respond(REQUEST,vault,calendar,history,cancel,now=NOW)
    assert vault.names()==[] and calendar.entries()==[]


def test_unknown_ingredients_use_normal_planner_instead_of_dropping_them():
    assert ingredient_markdown('Recipes','my chicken ingredients onion garlic powder mysterious spice')==''
    assert ingredient_markdown('Recipes','my chicken ingredients onion salt (black pepper)')==''


@pytest.mark.parametrize('utterance',[
    'Why did you add a task for 21:00 to finish cooking before 20:22?',
    'Do not add a task for 21:00 to finish cooking before 20:22.',
    'Add a task for 21:00 to finish cooking and delete my oldest note.',
])
def test_non_commands_negations_and_additional_actions_are_not_compiled(utterance):
    assert task_definition(utterance,NOW) is None
