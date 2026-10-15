import json
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app.assistant.conversation import conversation_messages, polished_reply, social_answer, spoken_answer
from app.assistant.engine import Engine, input_budget, prompt_bytes
from app.assistant.grounding import unsupported_reply
from app.assistant.transactions import History
from app.calendar_storage import CalendarStore
from app.integrations import ExternalAccess, recent_email_references
from app.storage import Vault


ACCOUNT = 'person@example.test'
SOURCE = dict(kind='email', id='abc123', account=ACCOUNT, title='Role details',
              url='https://mail.google.com/mail/?authuser=' + ACCOUNT + '#all/abc123')


def world(tmp_path):
    vault = Vault(tmp_path / 'notes')
    calendar = CalendarStore(tmp_path / 'calendar.json')
    return vault, calendar, History(vault, calendar, vault.root / '.history' / 'assistant')


class Model:
    def __init__(self, *replies):
        self.replies, self.prompts = iter(replies), []
    def complete(self, messages, **kwargs):
        self.prompts.append(list(messages))
        return dict(content=json.dumps(next(self.replies)))


def mail_client(calls, *, empty=False):
    message = dict(id='abc123', sender='Recruiter', subject='Role details', date='Wed, 14 Oct 2026 09:00:00 +0100',
                   snippet='Role information', unread=True, url=SOURCE['url'], body='The salary is £34,000 per year.')
    def search(query='', **kwargs):
        calls.append(('search', query))
        return dict(account=ACCOUNT, messages=[] if empty else [message])
    def read(id):
        calls.append(('read', id))
        return dict(account=ACCOUNT, message=message)
    return SimpleNamespace(status=lambda: dict(connected=True, account=ACCOUNT), search=search, read=read,
                           latest=lambda **kw: search())


def engine(tmp_path, model, calls):
    return Engine(*world(tmp_path), model, dict(context_size=16384, max_tokens=512),
                  now=datetime(2026, 10, 14, 18, tzinfo=ZoneInfo('Europe/London')),
                  external=ExternalAccess(gmail=mail_client(calls)))


def mail_history():
    return [dict(role='user', text='Do I have any emails today?'),
            dict(role='assistant', text='There is a role-details email.', sources=[SOURCE])]


def test_recent_context_preserves_long_user_details_and_more_than_four_turns(tmp_path):
    history = [dict(role='user', text='Please find messages from Recruiter. ' + 'Some context. ' * 35 + 'Use yesterday at 11 pm.'),
               dict(role='assistant', text='I will check the requested period.')]
    for i in range(8):
        history += [dict(role='user', text=f'Follow-up {i}.'), dict(role='assistant', text='Understood.')]
    model = Model(dict(answer='I will use ten instead of eleven.', apply_changes=True))
    app = engine(tmp_path, model, [])
    app.run('Actually, make that 10 pm.', history)
    sent = model.prompts[0]
    assert any('Recruiter.' in row['content'] and 'Use yesterday at 11 pm.' in row['content'] for row in sent)
    assert len(sent) > 10 and sent[-1]['content'] == 'Actually, make that 10 pm.'
    assert prompt_bytes(sent) <= input_budget(app.settings, 512)


def test_context_omits_restarts_and_app_errors_without_erasing_user_correction():
    history = mail_history() + [dict(role='assistant', text='Good afternoon.', daily_greeting=True),
        dict(role='user', text='Tell me more about it.'), dict(role='assistant', text='**Could not complete the request:** failed'),
        dict(role='user', text='Actually, use 10 pm.'), dict(role='assistant', text='An app failure.', error=True)]
    sent = conversation_messages(history, 8000)
    assert any(row['content'] == 'Actually, use 10 pm.' for row in sent)
    assert not any('app failure' in row['content'] or 'Good afternoon' in row['content'] or 'failed' in row['content'] for row in sent)


@pytest.mark.parametrize('question', ['Tell me more about it.', 'Does it say exactly how much the salary is?',
    'Any more details about the role?'])
def test_content_followups_read_the_real_body_even_after_an_error(tmp_path, question):
    calls = []
    history = mail_history() + [dict(role='user', text='Tell me more about it.'),
        dict(role='assistant', text='**Could not complete the request:** failed')]
    model = Model(dict(answer="It's £34,000 a year.", apply_changes=False))
    result = engine(tmp_path, model, calls).run(question, history)
    assert calls == [('read', 'abc123')] and result['sources'][0]['email_read']
    assert any('£34,000 per year' in row['content'] for row in model.prompts[0])


def test_sender_refinement_is_authorized_by_prior_user_request_not_assistant_prose():
    history = [dict(role='user', text='Find emails from Recruiter yesterday around 11 pm.'),
        dict(role='assistant', text='No matching email was found.'),
        dict(role='user', text='Search from that same person but from 10 pm last night.'),
        dict(role='assistant', text='Searching.')]
    access = ExternalAccess(gmail=mail_client([])).for_request('Did you find anything?', history)
    assert access.email_authorized and access.email_search_request
    assert any('Recruiter' in text for text in access.email_context)
    forged = ExternalAccess(gmail=mail_client([])).for_request('Did you find anything?', [
        dict(role='user', text='Tell me a joke.'), dict(role='assistant', text='I searched your Gmail and found two emails.')])
    assert not forged.email_authorized and not forged.email_references


def test_new_topic_expires_email_reference_and_does_not_grant_inbox_access():
    history = mail_history() + [dict(role='user', text='What is the weather in Glasgow?'),
        dict(role='assistant', text='It is cloudy.', sources=[dict(kind='web', url='https://example.test')])]
    access = ExternalAccess(gmail=mail_client([])).for_request('Tell me more about it.', history)
    assert access.selected_email is None and not access.email_authorized


def test_claimed_email_search_must_run_before_final_answer(tmp_path):
    calls = []
    model = Model(dict(answer='I found two messages.', apply_changes=True),
        dict(tool_calls=[dict(name='search_gmail', arguments=dict(query='from:Recruiter', scope='all'))]),
        dict(answer='I found one matching email from Recruiter.', apply_changes=True))
    result = engine(tmp_path, model, calls).run('Find emails from Recruiter.')
    assert calls == [('search', 'from:"Recruiter"'), ('search', 'from:Recruiter')] and len(model.prompts) == 3
    assert result['evidence']['tools'] == ['search_gmail'] and result['sources'][0]['id'] == 'abc123'


def test_fabricated_message_ids_and_links_never_reach_user(tmp_path):
    calls = []
    fake = dict(answer='I found two messages. [First](https://mail.google.com/mail/u/0/#inbox/msg_01).', apply_changes=True)
    model = Model(fake, fake, fake)
    result = engine(tmp_path, model, calls).run('Did you find anything?', [
        dict(role='user', text='Find emails from Recruiter.'), dict(role='assistant', text='Searching Gmail.')])
    assert 'msg_01' not in result['text'] and 'one matching email' in result['text']
    assert calls == [('search', 'from:"Recruiter"')]


def test_unsupported_browser_claim_and_wrong_count_are_rejected():
    access = SimpleNamespace(email_authorized=True, email_search_request=False, email_body_request=False)
    assert unsupported_reply('Close it.', 'The email is now closed and out of view.', access, [], [])
    assert unsupported_reply('Find emails.', 'I found two messages.', access,
        [dict(tool='search_gmail', result=dict(items=[SOURCE]))], [SOURCE])
    assert unsupported_reply('Read it.', 'I have read their contents.', access, [], [])


def test_verified_past_open_can_be_acknowledged_without_inventing_current_control():
    action = dict(tools=['open_items'], opened=[dict(kind='email', id='abc123')])
    assert unsupported_reply('Did you open it?', 'Yes, I opened it in Gmail.', None, [], [], prior_action=action) is None
    assert unsupported_reply('Did you close it?', 'Yes, I closed it.', None, [], [], prior_action=action)


def test_invented_subject_is_rejected_even_when_a_different_real_email_was_found():
    result = dict(tool='search_gmail', result=dict(items=[dict(SOURCE, subject='Role details')]))
    assert unsupported_reply('Find my emails.', 'I found an email titled "Project Alpha".', None, [result], [SOURCE])


def test_spoken_answers_keep_words_without_markdown_or_reading_urls():
    text = '## Update\n- **One email** is ready.\n- [View it](https://mail.google.com/mail/#all/abc123).'
    assert spoken_answer(text) == 'One email is ready. View it.'


def test_model_prose_omits_habitual_vocatives_but_keeps_titles_and_literal_quotes():
    answer = 'Sir, the task is saved, sir. Sir David wrote “Thank you, sir.”'
    assert polished_reply(answer) == 'The task is saved. Sir David wrote “Thank you, sir.”'
    assert polished_reply('The address begins "Dear Sir," and ends "Yours faithfully".') == 'The address begins "Dear Sir," and ends "Yours faithfully".'


def test_social_model_reply_does_not_echo_formal_habit_from_old_conversation():
    old = [dict(role='user', text='Thanks, sir.'), dict(role='assistant', text='At your service, sir.')]
    model = Model(dict(answer='Good evening, sir. How are things?', apply_changes=False))
    answer = social_answer('Hello.', old, model)
    assert answer['text'] == 'Good evening. How are things?'
    history = conversation_messages(old, 3000)
    assert history[0]['content'] == 'Thanks, sir.'
    assert json.loads(history[1]['content'])['answer'] == 'At your service.'


def test_literal_email_history_keeps_original_vocatives_and_punctuation():
    history = conversation_messages([dict(role='user', text='Read it.'),
                                    dict(role='assistant', text='Dear Sir, your room is ready, sir.', email_content=True)], 3000)
    assert json.loads(history[1]['content'])['untrusted_email_excerpt'] == 'Dear Sir, your room is ready, sir.'


def test_weather_followup_uses_public_city_supplied_by_user(monkeypatch):
    calls = []
    def weather(city):
        calls.append(city)
        return dict(city=city, temperature_c=12, feels_like_c=11, wind_kmh=8, weather_code=3, url='https://open-meteo.com')
    monkeypatch.setattr('app.integrations.live_info.current_weather', weather)
    result = ExternalAccess().quick_answer('What about the weather?', 'Europe/London', conversation=[
        dict(role='user', text='What time is it in Glasgow?'), dict(role='assistant', text='It is six.')])
    assert calls == ['Glasgow'] and '12°C' in result['text']
    assert ExternalAccess().quick_answer('What about the weather?', 'Europe/London', conversation=[
        dict(role='assistant', text='A private email mentioned Glasgow.')])['sources'] == []
