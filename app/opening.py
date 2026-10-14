"""User-requested desktop documents and Gmail links, without shell execution."""
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit
from types import SimpleNamespace

from app.integrations import email_source, request_text, source_url


DOCUMENTS = {'.pdf', '.txt', '.md', '.csv', '.rtf', '.doc', '.docx', '.odt', '.xls',
             '.xlsx', '.ods', '.ppt', '.pptx', '.odp', '.epub', '.jpg', '.jpeg', '.png',
             '.gif', '.webp', '.svg', '.mp3', '.wav', '.ogg', '.m4a', '.mp4', '.mkv', '.mov', '.eml'}
SKIP = {'node_modules', 'venv', 'build', 'dist', 'models', 'site-packages'}
MAX_OPEN = 6
COUNTS = {'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5, 'six': 6}
_PLURAL = (r'(?:them|those|these|both|all(?: of them)?|(?:both|all) of (?:those|these|them)|'
           r'(?:(?:both|all)(?: of)? )?(?:the |those |these )?(?:ones|emails|e-mails|messages|files|documents)'
           r'(?: (?:you (?:just )?(?:said|mentioned|found|listed)|we (?:just )?discussed))?)')


def document_path(value):
    path = Path(value).expanduser().resolve(strict=True)
    if not path.is_file() or path.suffix.casefold() not in DOCUMENTS:
        raise ValueError('Choose a document, image, audio or video file to open.')
    return path


def launch_target(source):
    target = source_url(source) if source['kind'] == 'email' else str(document_path(source['path']))
    if sys.platform == 'win32':
        os.startfile(target)
        return
    program = shutil.which('open' if sys.platform == 'darwin' else 'xdg-open')
    if not program:
        raise ValueError('The system file and browser opener is unavailable.')
    try:
        result = subprocess.run([program, target], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, timeout=8, check=False)
    except subprocess.TimeoutExpired as error:
        raise ValueError('The system took too long to open that item. Please try its link.') from error
    if result.returncode:
        raise ValueError('The system could not open that item. Check its default application.')


def open_request(request):
    text = request_text(request)
    # A correction to an unwanted read is still an explicit request to open.
    text = re.sub(r'^(?:no need to read (?:it|them)|don\'t read (?:it|them))[,;.]?\s*(?:just\s+)?', '', text, flags=re.I)
    if re.search(r'\b(?:and|then)\s+(?:please\s+)?(?:send|reply|delete|read|write|create|save|close)\b', text, re.I):
        return None
    match = re.fullmatch(
        r'(?:(?:can|could|would|will) you\s+|(?:I want|I would like|I\'d like) you to\s+)?'
        r'(?:please\s+)?(?:open(?: up)?|show me|let me see)\s+(.+?)[?.!]*', text, re.I)
    if not match:
        return None
    target = match.group(1).strip()
    # Spoken requests commonly contain several of these endings together.
    while True:
        shortened = re.sub(r'\s+(?:please|for me|now|(?:in|on)(?: the| my)? (?:browser|gmail))$', '', target, flags=re.I).strip()
        if shortened == target:
            return target
        target = shortened


def _plural_target(target):
    return bool(re.fullmatch(_PLURAL, target, re.I))


def _latest_target(target):
    return re.fullmatch(
        r'(?:(?:my|the)\s+)?(?:last|latest|newest|most recent)\s+'
        r'(?:(one|two|three|four|five|six|\d+)\s+)?(?:e-?mails?|messages?)'
        r'(?:\s+from\s+(.+))?', target, re.I)


class OpenRequests:
    def __init__(self, vault, *, home=None, launch=launch_target, zone='Europe/London'):
        self.vault, self.home, self.launch = vault, Path(home or Path.home()), launch
        self.zone = zone

    def files(self, name, cancel=None):
        query = re.sub(r'^(?:(?:the|my)\s+)?(?:file|document|note)(?:\s+(?:called|named))?\s+', '', name, flags=re.I)
        query = re.sub(r'^(?:the|my)\s+', '', query, flags=re.I).strip('"\' ')
        if '/' in query or query.startswith('~'):
            # Relative directory paths are resolved from the user's home.
            path = Path(query).expanduser()
            return [document_path(path if path.is_absolute() else self.home / path)]
        roots = [self.vault.root, self.home / 'Desktop', self.home / 'Documents', self.home / 'Downloads']
        found, seen = [], 0
        for root in roots:
            if not root.is_dir():
                continue
            for directory, dirs, names in os.walk(root, followlinks=False):
                dirs[:] = sorted(d for d in dirs if not d.startswith('.') and d not in SKIP
                                 and not (Path(directory) / d).is_symlink())
                if cancel is not None and cancel.is_set():
                    raise ValueError('Opening cancelled.')
                for filename in names:
                    seen += 1
                    path = Path(directory) / filename
                    if not path.is_symlink() and path.suffix.casefold() in DOCUMENTS and query.casefold() in {path.name.casefold(), path.stem.casefold()}:
                        found.append(path.resolve())
                if seen >= 25000:
                    return sorted(set(found))
        return sorted(set(found))

    @staticmethod
    def references(conversation):
        from app.integrations import recent_email_references
        last = next((m for m in reversed(conversation) if m['role'] == 'assistant'), {})
        sources = [s for s in last.get('sources') or [] if s.get('kind') in {'email', 'file', 'note'}]
        if sources:
            return sources
        return [email_source(row) for row in recent_email_references(conversation)]

    def validate(self, source, external):
        if source['kind'] == 'email':
            url = source_url(source)
            parsed = urlsplit(url)
            account = source.get('account') or parse_qs(parsed.query).get('authuser', [''])[0]
            if parsed.scheme != 'https' or parsed.hostname != 'mail.google.com' or parsed.port not in {None, 443} or not account:
                raise ValueError('That email does not have a verified Gmail link. Check your inbox again.')
            message_id = source.get('id') or parsed.fragment.rsplit('/', 1)[-1]
            linked_account = parse_qs(parsed.query).get('authuser', [''])[0]
            if (not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', message_id)
                    or parsed.fragment.rsplit('/', 1)[-1] != message_id
                    or linked_account and linked_account.casefold() != account.casefold()):
                raise ValueError('Check your inbox again to get that email’s Gmail link.')
            status = external.gmail.status() if external is not None and external.gmail is not None else {}
            if not status.get('connected') or (status.get('account') or status.get('email', '')).casefold() != account.casefold():
                raise ValueError('The connected Gmail account changed. Check your inbox again before opening that email.')
            folder = parsed.fragment.split('/')[0]
            folder = folder if folder in {'all', 'inbox', 'spam', 'trash', 'sent'} else 'all'
            return dict(source, account=account, url='https://mail.google.com/mail/?authuser=' + quote(account, safe='@') + '#' + folder + '/' + message_id)
        path = self.vault.path(source['name']) if source['kind'] == 'note' else source['path']
        return dict(kind='file', path=str(document_path(path)), title=Path(path).name)

    def respond(self, request, conversation, external=None, cancel=None):
        answer = dict(changes=[], operation_id=None, sources=[])
        text = request_text(request)
        from app.integrations import latest_email_request
        if latest_email_request(text) and not re.search(r'\bopen\b', text, re.I):
            return None
        if re.fullmatch(r'(?:(?:can|could|would|will) you\s+)?(?:please\s+)?(?:close|shut)\s+'
                        r'(?:it|them|that|those|(?:(?:the|that|this|those)\s+)?(?:browser|tabs?|windows?|e-?mails?))'
                        r'(?:\s+(?:please|for me|now))?[?.!]*', text, re.I):
            return dict(answer, text="I can't close browser tabs from here. You'll need to close that yourself.")
        target = open_request(request)
        last = conversation[-1] if conversation else {}
        accepted = bool(re.fullmatch(r'\s*(?:yes|yes please|please do|go ahead|sure|okay|ok)[.!]*\s*', text, re.I))
        offer = last.get('open_offer') if last.get('role') == 'assistant' else None
        choices = last.get('open_choices', []) if last.get('role') == 'assistant' else []
        choice = re.fullmatch(r'\s*(?:the\s+)?(first|second|third|fourth|fifth|sixth|[1-6])(?:\s+(?:one|file|e-?mail))?[.!]*\s*', target or text, re.I)
        selected = None
        if choices and choice:
            ordinals = ['first', 'second', 'third', 'fourth', 'fifth', 'sixth']
            value = choice.group(1).lower()
            index = int(value) - 1 if value.isdigit() else ordinals.index(value)
            selected = choices[index] if index < len(choices) else None
        # "Both of the ones you just mentioned" answers an opening question.
        # It must not turn a choice about reading/summarising into a desktop action.
        previous_user = next((m.get('text', '') for m in reversed(conversation) if m.get('role') == 'user'), '')
        if target is None and _plural_target(text.rstrip('.!?')) and (choices or open_request(previous_user) is not None):
            target = text.rstrip('.!?')
        if target is None and not (accepted and offer or selected):
            return None
        candidates = []
        open_many = False
        latest = _latest_target(target or '')
        if selected:
            candidates = [selected]
        elif accepted and offer and target is None:
            candidates = [offer]
        elif _plural_target(target or '') or (target or '').casefold() == 'the':
            candidates = choices or self.references(conversation)
            open_many = True
        elif re.fullmatch(r'(?:(?:the|that|this)\s+)?(?:it|one|e-?mail|message|file|document|note)', target, re.I):
            candidates = self.references(conversation)
        elif latest:
            if external is None or external.gmail is None:
                raise ValueError('Connect Gmail in Settings first.')
            count_word, sender = latest.groups()
            count = COUNTS.get((count_word or '').lower(), int(count_word) if count_word and count_word.isdigit() else 1)
            if not 1 <= count <= MAX_OPEN:
                return dict(answer, text='I can open up to six emails at once. How many would you like?')
            if count_word is None and re.search(r'(?:emails|e-mails|messages)\b', target, re.I):
                return dict(answer, text='How many of the latest emails would you like me to open?')
            if sender or count != 1:
                query = '-in:sent -in:drafts'
                if sender:
                    query += ' from:"' + sender.strip('"\' ').replace('"', '') + '"'
                result = external.gmail.search(query, limit=count, scope='all', cancel=cancel)
            else:
                result = external.gmail.latest(scope='all', cancel=cancel)
            candidates = [email_source(dict(row, account=result['account'])) for row in result['messages'][:count]]
            open_many = count > 1
        elif re.match(r'(?:(?:the|my)\s+)?e-?mail\s+(?:from|about|with subject)\s+', target, re.I):
            if external is None or external.gmail is None:
                raise ValueError('Connect Gmail in Settings first.')
            match = re.match(r'(?:(?:the|my)\s+)?e-?mail\s+(from|about|with subject)\s+(.+)', target, re.I)
            value = match.group(2).strip('"\' ').replace('"', '')
            query = ('from:' if match.group(1).lower() == 'from' else 'subject:') + '"' + value + '"'
            result = external.gmail.search(query, limit=6, scope='all', cancel=cancel)
            candidates = [email_source(dict(row, account=result['account'])) for row in result['messages']]
        else:
            candidates = [dict(kind='file', path=str(p), title=p.name) for p in self.files(target, cancel)]
        candidates = list({(s['kind'], s.get('account'), s.get('id') or s.get('url') or s.get('path') or s.get('name')): s for s in candidates}.values())
        if not candidates and open_many and external is not None and external.gmail is not None:
            # Recover from an earlier model's unsupported claim by repeating the
            # search the USER requested, never by trusting its fabricated links.
            from app.assistant.dates import local_now
            from app.assistant.mail_search import email_search_plan
            from app.assistant.tools import gmail_date_range
            access = external.for_request(request, conversation) if hasattr(external, 'for_request') else None
            if access is not None and access.email_authorized and access.email_context:
                now = local_now(self.zone)
                plan = email_search_plan(SimpleNamespace(email_search_request=True,
                    email_context=access.email_context, request='Did you find anything?'), now)
                if plan is not None:
                    query, _ = gmail_date_range(plan.get('query', ''), plan.get('date', ''), plan.get('start_time', ''),
                                               plan.get('end_time', ''), today=now.date().isoformat(), zone=self.zone)
                    found = external.gmail.search(query, limit=MAX_OPEN, scope=plan['scope'], cancel=cancel)
                    candidates = [email_source(dict(row, account=found['account'])) for row in found['messages']]
        if not candidates:
            return dict(answer, text='Which file or email would you like me to open?')
        if len(candidates) > MAX_OPEN or len(candidates) != 1 and not open_many or re.search(r'\bboth\b', target or '', re.I) and len(candidates) > 2:
            introduction = 'I can open up to six at once. Which should I open?' if len(candidates) > MAX_OPEN else 'I found more than one match. Which should I open?'
            return dict(answer, text=introduction + ' ' + '; '.join(f'{i + 1}. ' + (s.get('title') or s.get('name') or Path(s.get('path', 'Email')).name) for i, s in enumerate(candidates[:MAX_OPEN])), sources=candidates[:MAX_OPEN], open_choices=candidates[:MAX_OPEN])
        # Validate the entire choice before opening the first item.
        sources = [self.validate(source, external) for source in candidates]
        for source in sources:
            if cancel is not None and cancel.is_set():
                raise ValueError('Opening cancelled.')
            self.launch(source)
        if len(sources) == 1:
            spoken = 'Opened it in Gmail.' if sources[0]['kind'] == 'email' else f'Opened {sources[0]["title"]}.'
        else:
            amount = 'both' if len(sources) == 2 else 'all ' + str(len(sources))
            spoken = f'Opened {amount} in Gmail.' if all(s['kind'] == 'email' for s in sources) else f'Opened {amount}.'
        return dict(answer, text=spoken, sources=sources, evidence=dict(tools=['open_items'],
                    opened=[{k:s[k] for k in ('kind','id','account','path') if k in s} for s in sources]))
