import threading
from types import SimpleNamespace
from pathlib import Path

import pytest

from app.opening import OpenRequests, document_path, launch_target, open_request
from app.storage import Vault


def setup(tmp_path):
    home = tmp_path / 'home'
    (home / 'Desktop').mkdir(parents=True)
    vault = Vault(tmp_path / 'notes')
    opened = []
    return OpenRequests(vault, home=home, launch=opened.append), home, opened


def mail(account='person@example.test'):
    source = dict(kind='email', id='abc123', account=account, title='Example offer',
                  url='https://mail.google.com/mail/?authuser=' + account + '#all/abc123')
    gmail = SimpleNamespace(status=lambda: dict(connected=True, account='person@example.test'),
        latest=lambda **kw: dict(account='person@example.test', messages=[dict(id='abc123', subject='Example offer', url=source['url'])]))
    return source, SimpleNamespace(gmail=gmail)


@pytest.mark.parametrize('message', ['Open it', 'Peter, can you open that email please?', 'Could you open the email for me?'])
def test_followup_opens_the_grounded_mail_without_requiring_id(tmp_path, message):
    opener, _, opened = setup(tmp_path)
    source, external = mail()
    result = opener.respond(message, [dict(role='assistant', text='An email.', sources=[source])], external)
    assert opened == [source] and 'Opened' in result['text']


def test_offer_needs_affirmative_current_reply_and_expires_on_other_turns(tmp_path):
    opener, _, opened = setup(tmp_path)
    source, external = mail()
    offer = dict(role='assistant', text='Would you like me to open it?', open_offer=source)
    assert opener.respond('No.', [offer], external) is None
    assert opener.respond('What time is it?', [offer], external) is None
    assert opener.respond('Yes please', [offer], external)['sources'] == [source]
    assert len(opened) == 1
    assert opener.respond('Yes', [offer, dict(role='assistant', text='It is noon.')], external) is None


def test_changed_account_never_opens_previous_account_mail(tmp_path):
    opener, _, opened = setup(tmp_path)
    source, external = mail('old@example.test')
    with pytest.raises(ValueError, match='account changed'):
        opener.respond('open it', [dict(role='assistant', sources=[source])], external)
    assert not opened


def test_latest_email_can_be_opened_without_previous_search(tmp_path):
    opener, _, opened = setup(tmp_path)
    _, external = mail()
    result = opener.respond('Please open my latest email.', [], external)
    assert opened and result['sources'][0]['id'] == 'abc123'


def test_filename_lookup_and_exact_paths_preserve_spaces_and_shell_characters(tmp_path):
    opener, home, opened = setup(tmp_path)
    path = home / 'Desktop' / 'Report $(echo secret).pdf'
    path.write_bytes(b'%PDF example')
    result = opener.respond('Open file "' + str(path) + '"', [])
    assert opened[0]['path'] == str(path) and 'Opened' in result['text']
    opened.clear()
    opener.respond('Can you open Report $(echo secret).pdf?', [])
    assert opened[0]['path'] == str(path)


def test_duplicate_files_are_offered_without_opening_either(tmp_path):
    opener, home, opened = setup(tmp_path)
    for directory in [home / 'Desktop', home / 'Documents']:
        directory.mkdir(exist_ok=True)
        (directory / 'report.pdf').write_bytes(b'example')
    result = opener.respond('open report.pdf', [])
    assert 'more than one' in result['text'] and len(result['sources']) == 2 and not opened
    assert 'Which' in opener.respond('open it', [dict(role='assistant', **result)])['text']


def test_note_reference_and_document_reference_can_be_opened(tmp_path):
    opener, _, opened = setup(tmp_path)
    opener.vault.create('Plan', '# Plan')
    opener.respond('open that note', [dict(role='assistant', sources=[dict(kind='note', name='Plan')])])
    assert opened[0]['path'] == str(opener.vault.path('Plan'))
    opener.respond('open Plan', [])
    assert len(opened) == 2


def test_execution_formats_and_fabricated_email_links_are_rejected(tmp_path):
    opener, home, opened = setup(tmp_path)
    script = home / 'Desktop' / 'run.desktop'; script.write_text('Exec=rm -rf /')
    with pytest.raises(ValueError):
        document_path(script)
    source, external = mail()
    source['url'] = 'https://example.com/phishing'
    with pytest.raises(ValueError, match='verified Gmail'):
        opener.respond('open it', [dict(role='assistant', sources=[source])], external)
    assert not opened


def test_opening_uses_argument_list_and_reports_system_failure(tmp_path, monkeypatch):
    path = tmp_path / 'file with spaces.pdf'; path.write_bytes(b'example')
    calls = []
    monkeypatch.setattr('app.opening.shutil.which', lambda _: '/usr/bin/xdg-open')
    monkeypatch.setattr('app.opening.subprocess.run', lambda argv, **kw: calls.append((argv, kw)) or SimpleNamespace(returncode=1))
    with pytest.raises(ValueError, match='could not open'):
        launch_target(dict(kind='file', path=str(path)))
    assert calls[0][0] == ['/usr/bin/xdg-open', str(path)] and 'shell' not in calls[0][1]


def test_unrelated_and_compound_requests_do_not_authorize_opening(tmp_path):
    opener, _, opened = setup(tmp_path)
    for request in ['What is in my file?', 'Do you open files?', 'Send this email', 'Open my latest email and send a reply']:
        result = opener.respond(request, [])
        assert result is None or not opened
    assert open_request('Peter, please open it.') == 'it'


def test_ambiguous_opening_accepts_the_next_turns_numbered_choice(tmp_path):
    opener, home, opened = setup(tmp_path)
    for directory in [home / 'Desktop', home / 'Documents']:
        directory.mkdir(exist_ok=True)
        (directory / 'report.pdf').write_bytes(b'example')
    result = opener.respond('open report.pdf', [])
    history = [dict(role='assistant', **result)]
    chosen = result['open_choices'][1]
    opener.respond('The second one.', history)
    assert opened == [chosen]
    assert opener.respond('second', [*history, dict(role='assistant', text='Another topic.')]) is None


def two_mails():
    source, external = mail()
    second = dict(source, id='def456', title='Second message', url=source['url'].replace('abc123', 'def456'))
    return [source, second], external


@pytest.mark.parametrize('phrase', ['Open both of them.', 'Can you open the for me?',
    'No need to read them, just open them on the browser.'])
def test_explicit_plural_opening_uses_only_verified_references(tmp_path, phrase):
    opener, _, opened = setup(tmp_path)
    sources, external = two_mails()
    result = opener.respond(phrase, [dict(role='assistant', text='Two messages.', sources=sources)], external)
    assert opened == sources and result['text'] == 'Opened both in Gmail.'


def test_both_is_a_continuation_of_opening_not_a_new_read_instruction(tmp_path):
    opener, _, opened = setup(tmp_path)
    sources, external = two_mails()
    history = [dict(role='user', text='Can you open them?'),
               dict(role='assistant', text='Which ones?', sources=sources, open_choices=sources)]
    assert opener.respond('both of the ones you just said.', history, external)['sources'] == sources
    assert len(opened) == 2
    assert opener.respond('both of the ones you just said.', [dict(role='user', text='Read those messages.'),
        dict(role='assistant', text='Which ones?', sources=sources)], external) is None


def test_last_two_from_sender_searches_and_opens_actual_returned_messages(tmp_path):
    opener, _, opened = setup(tmp_path)
    sources, external = two_mails()
    searches = []
    def search(query, **kwargs):
        searches.append((query, kwargs))
        return dict(account='person@example.test', messages=[dict(source, subject=source['title']) for source in sources])
    external.gmail.search = search
    result = opener.respond('Peter, can you open the last two emails from Recruiter?', [], external)
    assert opened == sources and result['text'] == 'Opened both in Gmail.'
    assert 'from:"Recruiter"' in searches[0][0] and searches[0][1]['limit'] == 2


def test_old_fabricated_prose_cannot_supply_browser_links(tmp_path):
    opener, _, opened = setup(tmp_path)
    _, external = mail()
    result = opener.respond('Open them on the browser.', [dict(role='assistant',
        text='Two messages: https://mail.google.com/mail/u/0/#inbox/msg_01 and msg_02.')], external)
    assert not opened and not result['sources'] and 'Which' in result['text']


def test_close_request_is_honest_about_missing_browser_control(tmp_path):
    opener, _, opened = setup(tmp_path)
    result = opener.respond('close it.', [])
    assert "can't close browser tabs" in result['text'] and not opened


def test_invalid_second_reference_prevents_opening_the_first(tmp_path):
    opener, _, opened = setup(tmp_path)
    sources, external = two_mails()
    sources[1]['id'] = 'mismatched'
    with pytest.raises(ValueError):
        opener.respond('Open both.', [dict(role='assistant', sources=sources)], external)
    assert not opened


def test_opening_can_recover_a_user_requested_search_after_unsupported_old_claim(tmp_path):
    from app.integrations import ExternalAccess
    opener, _, opened = setup(tmp_path)
    sources, external = two_mails()
    calls = []
    def search(query, **kwargs):
        calls.append(query)
        return dict(account='person@example.test', messages=[dict(source, subject=source['title']) for source in sources])
    external.gmail.search = search
    history = [dict(role='user', text='Find emails from Recruiter yesterday around 11 pm.'),
               dict(role='assistant', text='I found two emails called msg_01 and msg_02.', sources=[])]
    result = opener.respond('Open both of them.', history, ExternalAccess(gmail=external.gmail))
    assert opened == sources and calls and 'from:"Recruiter"' in calls[0]
    assert 'msg_01' not in result['text'] and result['evidence']['tools'] == ['open_items']
