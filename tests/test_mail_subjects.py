import threading
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo
import pytest
from app.assistant.mail_subjects import subject_plan, subject_answer
from app.assistant.engine import Engine
from app.assistant.service import AssistantService
from app.assistant.grounding import unsupported_reply
from app.integrations import ExternalAccess
from test_integrations import stores, selected_mail_history

NOW=datetime(2026,10,15,17,tzinfo=ZoneInfo('Europe/London'))


def fake_gmail(count=4):
    calls=[]
    rows=[dict(id=f'abc{i}',sender='Sender',subject=f'Actual subject {i}',date='',received_ms=1000-i,
               url=f'https://mail.google.com/mail/?authuser=person@example.test#all/abc{i}') for i in range(count)]
    def headers(query,**kw):
        calls.append((query,kw));limit=kw['limit']
        return dict(account='person@example.test',messages=rows if limit is None else rows[:limit],complete=True)
    return SimpleNamespace(list_headers=headers),calls,rows


@pytest.mark.parametrize('question', [
    'tell me the subject of all the emals i have received today',
    "Can you read the subject of the last five emails I've received?",
    'List all email subjects received today.',
    'Read out every subject line from my emails today.',
    'What are the subjects of my emails today?',
])
def test_header_requests_are_completed_with_actual_subjects_without_model(tmp_path,question):
    gmail,calls,rows=fake_gmail(6)
    model=SimpleNamespace(complete=lambda *a,**k:pytest.fail('A routine header list needs no generated answer'))
    result=Engine(*stores(tmp_path),model,dict(context_size=16384),now=NOW,external=ExternalAccess(gmail=gmail)).run(question)
    expected=5 if 'five' in question else 6
    assert len(result['sources'])==expected and len(calls)==1
    assert all(row['subject'] in result['text'] for row in rows[:expected])
    assert result['evidence']['email_searches'][0]['complete']
    assert 'second to fetch' not in result['text']
    query,args=calls[0]
    assert args['scope']=='all' and '-in:sent -in:drafts' in query
    if 'today' in question:
        assert args['limit'] is None and 'after:1792018800 before:1792105200' in query


def test_service_delivers_complete_subjects_in_one_answer_event(tmp_path,monkeypatch):
    monkeypatch.setattr('app.assistant.mail_subjects.local_now',lambda zone:NOW)
    gmail,calls,rows=fake_gmail()
    runtime=SimpleNamespace(ensure=lambda:pytest.fail('Subject lists must not wait for inference'))
    vault,calendar,_=stores(tmp_path)
    service=AssistantService(vault,calendar,runtime,tmp_path/'conversation.json',external=ExternalAccess(gmail=gmail))
    service.send('tell me the subject of all the emals i have received today')
    result=service.events.get(timeout=2)
    assert result['type']=='answer' and len(calls)==1
    assert all(row['subject'] in result['text'] for row in rows)
    assert service.events.empty()


def test_all_subjects_are_not_capped_at_four_or_fifty():
    gmail,calls,rows=fake_gmail(63)
    result=subject_answer('Read all the subjects of my emails today.',[],gmail,'Europe/London',now=NOW)
    assert len(result['sources'])==63 and rows[-1]['subject'] in result['text']
    assert calls[0][1]['limit'] is None


def test_read_subjects_of_them_does_not_ask_which_body_to_read():
    history=selected_mail_history()
    history[-1]['sources'] *= 2
    result=subject_answer('But can you read the subject to them?',history,None,'Europe/London',now=NOW)
    assert result and history[-1]['sources'][0]['title'] in result['text']
    access=ExternalAccess().for_request('Read the subject of those emails.',history)
    assert not access.email_body_request and not access.ambiguous_email


def test_date_and_count_fragment_and_waiting_followup_resume_original_request():
    history=[dict(role='user',text='Can you read the subject of the last five emails I received?'),
             dict(role='assistant',text='Give me a second to fetch those subjects.')]
    assert subject_plan('the last 4 I received today.',history)==dict(date='today',count=4,scope='all')
    gmail,calls,rows=fake_gmail()
    result=subject_answer('Peter, I gave you a minute.',history,gmail,'Europe/London',now=NOW)
    assert result and len(calls)==1 and all(r['subject'] in result['text'] for r in rows)


@pytest.mark.parametrize('question',[
    'Read the subjects of all emails today and then delete them.',
    'List subjects of emails from Alice today.',
    'List subjects of emails about my interview today.',
    'Read the subjects of unread emails today.',
    'Tell me about the subject of my notes today.',
    'Find all emails with subject: invoice today.',
    'Tell me the subjects today and create a task.',
    'List email subjects from this morning.',
])
def test_header_route_preserves_unsupported_filters_and_extra_actions(question):
    assert subject_plan(question) is None


@pytest.mark.parametrize('answer',[
    "Right, let's get that list. Give me just a second to fetch the subjects for every email you've received today.",
    "Give me a moment to check.","I'll fetch the messages.","Let's pull up the emails."])
def test_announcing_work_cannot_be_a_final_email_answer(answer):
    access=SimpleNamespace(email_authorized=True,email_search_request=False,email_body_request=False)
    assert unsupported_reply('List the subjects.',answer,access,[],[])


def test_cancelled_or_incomplete_subject_search_never_reports_success():
    gmail,_,_=fake_gmail();cancel=threading.Event();cancel.set()
    with pytest.raises(ValueError,match='cancelled'):
        subject_answer('List all email subjects today.',[],gmail,'Europe/London',now=NOW,cancel=cancel)
    gmail.list_headers=lambda *a,**k:dict(messages=[],complete=False)
    with pytest.raises(ValueError,match='partial'):
        subject_answer('List all email subjects today.',[],gmail,'Europe/London',now=NOW)
