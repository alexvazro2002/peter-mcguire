import json
import threading
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo
import pytest
from app.assistant.mail_flow import EmailReviewFlow, review_plan, select_reference, link_request
from app.integrations import ExternalAccess
from test_mail_review import Gmail,email
from test_conversation import Model
from test_integrations import stores
from app.assistant.service import AssistantService
from app.assistant.action_grounding import action_problem

NOW=datetime(2026,10,15,18,tzinfo=ZoneInfo('Europe/London'))


def runtime(*replies):
    return SimpleNamespace(ensure=lambda:None,client=Model(*replies),settings=dict(context_size=16384,max_tokens=512))


def test_date_window_and_quoted_fuzzy_person_use_current_local_clock():
    p=review_plan('Peter check my emails from "Jose Angel" From the past 2 weeks.',NOW,'Europe/London')
    assert p['participant']=='Jose Angel'
    assert p['date_window']['start']=='2026-10-01T18:00:00+01:00'
    assert p['date_window']['end']=='2026-10-15T18:00:00+01:00'
    assert 'from:' not in p['query']  # Match all bounded headers locally, including Cc.
    p=review_plan('Check my emails from last Monday.',NOW,'Europe/London')
    assert p['participant']=='' and 'Monday, 12 October 2026' in p['date_label']
    assert p['date_window']['start'].startswith('2026-10-12T00:00')


def test_abstract_sender_variants_and_missing_period_are_explicit():
    p=review_plan('Fetch emails from JoseAngel over the past two weeks.',NOW,'Europe/London')
    assert p['participant']=='JoseAngel'
    assert 'clarification' in review_plan('Check my emails from Jose Angel.',NOW,'Europe/London')
    assert review_plan('Read my emails and create a task.',NOW,'Europe/London') is None


def history_for(*messages):
    return [dict(role='user',text='List my latest email subjects.'),dict(role='assistant',text='The subjects are listed.',sources=[dict(kind='email',id=m['id'],account='me@example.test',url=m['url'],title=m['subject']) for m in messages])]


def test_link_uses_verified_target_after_invented_assistant_description(tmp_path):
    camera=email('cam')
    camera['subject']='Your camera roll has a type. So do you.'
    other=email('other');h=history_for(camera,other)
    h += [dict(role='user',text="What's the camera roll you said?"),dict(role='assistant',text='That was Google Photos, probably a metadata warning.')]
    flow=EmailReviewFlow(tmp_path,ExternalAccess(gmail=Gmail([camera,other])),runtime())
    result=flow.respond('Yes, can you send me a link?',h,threading.Event())
    assert result['sources'][0]['id']=='cam' and result['sources'][0]['url']==camera['url']
    assert result['evidence']['tools']==['email_link']
    assert "I'll" not in result['text']


def test_link_never_uses_fabricated_prose_url_or_sends_mail(tmp_path):
    flow=EmailReviewFlow(tmp_path,ExternalAccess(gmail=Gmail()),runtime())
    h=[dict(role='assistant',text='Use https://mail.google.com/#all/fake')]
    result=flow.respond('Send me a link.',h,threading.Event())
    assert not result['sources'] and 'Which email' in result['text']
    assert not link_request('Send a link to Jose Angel by email.')


def test_prepare_report_fuzzy_matches_from_and_cc_without_model(tmp_path,monkeypatch):
    monkeypatch.setattr('app.assistant.mail_flow.local_now',lambda zone:NOW)
    gmail=Gmail([email(),email('a2',sender='Other <o@example.test>',cc='JoseAngel <ja@example.test>'),email('a3',sender='Unrelated <u@example.test>')])
    rt=runtime();rt.ensure=lambda:pytest.fail('Fetching and preparing a report does not need inference')
    flow=EmailReviewFlow(tmp_path,ExternalAccess(gmail=gmail),rt)
    events=[]
    result=flow.respond('Peter check my emails from "Jose Angel" From the past 2 weeks.',[],threading.Event(),lambda **e:events.append(e))
    assert len(result['sources'])==2 and gmail.reads==['a1','a2'] and events
    assert result['evidence']['email_review_directory']
    assert 'prepared 2 emails' in result['text']


def test_selected_subject_question_reads_actual_message_and_checks_quotes(tmp_path):
    m=email('cam');m['subject']='Your camera roll has a type. So do you.'
    m['body']='Try our new photo filters. This is a promotional message.'
    gmail=Gmail([m]);rt=runtime(dict(selections=[2],assessment='',missing=False))
    flow=EmailReviewFlow(tmp_path,ExternalAccess(gmail=gmail),rt)
    result=flow.respond("What's the camera roll you said?",history_for(m),threading.Event())
    assert gmail.reads==['cam'] and 'promotional' in result['text']
    assert result['evidence']['citations_verified'] and 'Google Photos' not in result['text']


def test_invented_explanation_is_rejected_even_with_a_real_but_unrelated_quote(tmp_path):
    m=email('cam');m['subject']='Your camera roll has a type. So do you.'
    bad=dict(selections=[999],assessment='',missing=False)
    gmail=Gmail([m]);flow=EmailReviewFlow(tmp_path,ExternalAccess(gmail=gmail),runtime(bad,bad))
    result=flow.respond('Read that email and explain what it means.',history_for(m),threading.Event())
    assert result['evidence']['fallback']=='literal_excerpt' and 'Google Photos' not in result['text']


def test_cache_followup_uses_prepared_data_and_workspaces_dont_inherit_email_topic(tmp_path,monkeypatch):
    monkeypatch.setattr('app.assistant.mail_flow.local_now',lambda zone:NOW)
    gmail=Gmail();flow=EmailReviewFlow(tmp_path,ExternalAccess(gmail=gmail),runtime(dict(selections=[2],assessment='',missing=False)))
    prepared=flow.respond('Check my emails from Jose Angel for the past two weeks.',[],threading.Event())
    h=[dict(role='user',text='Check my emails from Jose Angel for the past two weeks.'),dict(role='assistant',**prepared)]
    result=flow.respond('What is the total?',h,threading.Event())
    assert '£215' in result['text'] and gmail.reads==['a1']
    assert flow.respond('Show my tasks for today.',h,threading.Event()) is None


def test_service_bulk_delete_reads_live_tasks_and_never_loads_model(tmp_path):
    vault,calendar,_=stores(tmp_path)
    data=calendar.read()
    for index in range(6):data['entries'].append(dict(id=f'u{index}',kind='task',title=f'Unscheduled {index}',date=None,time=None,done=False))
    data['entries'].append(dict(id='dated',kind='task',title='Keep Friday',date='2026-10-16',time=None,done=False))
    calendar.write(data)
    service=AssistantService(vault,calendar,SimpleNamespace(ensure=lambda:pytest.fail('Deletion must not need model')),tmp_path/'chat.json')
    service.send('Can you delete all my unscheduled tasks?')
    result=service.events.get(timeout=2)
    assert result['type']=='answer' and result['operation_id']
    assert len(calendar.entries())==1 and calendar.entries()[0]['id']=='dated'


@pytest.mark.parametrize('answer',["I'll remove all six tasks.","I've found three unscheduled tasks.","Shall I go ahead and delete them?",'Give me a second.'])
def test_narrating_task_actions_or_reconfirming_does_not_finish_request(answer):
    assert action_problem('Can you delete my unscheduled tasks?',answer,[])


def test_real_quote_cannot_launder_a_contradictory_generated_claim(tmp_path):
    m=email('cam');m['body']='You are invited to an interview on Monday.'
    model=runtime(dict(selections=[1],assessment='',missing=False,text='The interview was cancelled.'))
    flow=EmailReviewFlow(tmp_path,ExternalAccess(gmail=Gmail([m])),model)
    result=flow.respond('Read that email and tell me what it says.',history_for(m),threading.Event())
    assert 'cancelled' not in result['text'] and 'invited' in result['text']


def test_explicit_unknown_link_target_does_not_return_another_email(tmp_path):
    m=email('a1');m['subject']='Camera roll offer'
    flow=EmailReviewFlow(tmp_path,ExternalAccess(gmail=Gmail([m])),runtime())
    result=flow.respond('Send me a link for the hotel invoice email.',history_for(m),threading.Event())
    assert 'Which email' in result['text']


@pytest.mark.parametrize('question',['Create a note about these emails.','Schedule a task to check my emails tomorrow.'])
def test_active_review_never_swallows_workspace_actions(tmp_path,question):
    flow=EmailReviewFlow(tmp_path,ExternalAccess(gmail=Gmail()),runtime())
    flow.review.prepare('after:100 before:200')
    assert flow.respond(question,history_for(email()),threading.Event()) is None


@pytest.mark.parametrize('question',['Check emails from Jose Angel containing invoices from last Monday.','Check emails today after 2pm.','Check emails between 2026-10-12 and 2026-10-14.'])
def test_unsupported_extra_filters_never_claim_complete_review(question):
    assert 'clarification' in review_plan(question,NOW,'Europe/London')


def test_ai_library_uses_same_verified_sentence_selection_for_email_reviews(tmp_path,monkeypatch):
    from pathlib import Path
    from library.retrieval import Librarian
    gmail=Gmail();flow=EmailReviewFlow(tmp_path,ExternalAccess(gmail=gmail),runtime())
    report=flow.review.prepare('after:100 before:200')
    m=gmail.messages[0]
    result=dict(source=m['subject']+' | '+m['url'],chunk=0,text=m['body'],score=1)
    librarian=object.__new__(Librarian);librarian.settings={};librarian.review_directory=Path(report['cache_directory'])
    librarian.search=lambda *a,**k:[result]
    monkeypatch.setattr('app.config.load_config',lambda:dict(gmail={}))
    monkeypatch.setattr('app.gmail.GmailClient',lambda *a,**k:gmail)
    monkeypatch.setattr('library.retrieval.ChatClient',lambda settings:Model(dict(selections=[2],assessment='',missing=False)))
    answer,sources=librarian.ask('What is the total?')
    assert answer=='The total is £215.' and sources==[result]


def test_social_greeting_uses_short_prompt_and_never_workspace_tools():
    from app.assistant.conversation import social_answer,social_request
    model=Model(dict(answer='Hello. How are things?',apply_changes=False))
    assert social_request('Hello Peter!') and not social_request('Hello Peter, delete my tasks.')
    answer=social_answer('Hello Peter!',[],model)
    assert answer['text']=='Hello. How are things?'
    assert 'Available tools' not in model.prompts[0][0]['content']


def test_active_inbox_offer_wins_over_an_older_prepared_email_cache(tmp_path):
    from app.email_briefing import EmailBriefing
    first=email('old');second=email('new');gmail=Gmail([first,second])
    vault,calendar,_=stores(tmp_path)
    service=AssistantService(vault,calendar,SimpleNamespace(ensure=lambda:pytest.fail('A queued literal read must not use the old review model')),tmp_path/'chat.json',external=ExternalAccess(gmail=gmail))
    service.mail_flow.review.prepare_messages([first],account=gmail.account)
    service.email_briefing=EmailBriefing(gmail)
    service.email_briefing.extend([second],gmail.account)
    offer=service.email_briefing.offer_next()
    service.messages=[dict(role='user',text='Check my email.'),dict(role='assistant',**offer)]
    service.send('Yes please.')
    result=service.events.get(timeout=2)
    assert result['type']=='answer' and result['email_handled']['id']=='new'
    assert gmail.reads==['old','new']



def test_named_email_can_be_revisited_after_workspace_topic_without_reviving_bare_it(tmp_path):
    m=email('cam');m['subject']='Your camera roll has a type. So do you.'
    h=history_for(m)+[dict(role='user',text='Delete my unscheduled tasks.'),dict(role='assistant',text='Deleted six tasks.')]
    assert select_reference('Send me a link for the camera roll email.',h)['id']=='cam'
    assert select_reference('Send me a link for it.',h) is None
    flow=EmailReviewFlow(tmp_path,ExternalAccess(gmail=Gmail([m])),runtime(dict(selections=[2],assessment='',missing=False)))
    result=flow.respond("What's the camera roll you said?",h,threading.Event())
    assert result['evidence']['citations_verified']


def test_social_turn_does_not_get_swallowed_by_an_active_email_review(tmp_path):
    m=email();flow=EmailReviewFlow(tmp_path,ExternalAccess(gmail=Gmail([m])),runtime())
    flow.review.prepare_messages([m],account='me@example.test')
    assert flow.respond('How are you?',history_for(m),threading.Event()) is None
