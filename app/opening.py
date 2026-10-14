"""User-requested desktop documents and Gmail links, without shell execution."""
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit

from app.integrations import email_source, source_url


DOCUMENTS = {'.pdf', '.txt', '.md', '.csv', '.rtf', '.doc', '.docx', '.odt', '.xls',
             '.xlsx', '.ods', '.ppt', '.pptx', '.odp', '.epub', '.jpg', '.jpeg', '.png',
             '.gif', '.webp', '.svg', '.mp3', '.wav', '.ogg', '.m4a', '.mp4', '.mkv', '.mov', '.eml'}
SKIP = {'node_modules', 'venv', 'build', 'dist', 'models', 'site-packages'}


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
    text = re.sub(r'^\s*peter\s*[,.:]?\s*', '', request, flags=re.I).strip()
    match = re.fullmatch(r'(?:(?:can|could|would|will) you\s+|(?:I want|I would like|I\'d like) you to\s+)?(?:please\s+)?open\s+(?:up\s+)?(.+?)(?:\s+(?:please|for me|now))?[?.!]*', text, re.I)
    return match.group(1).strip() if match else None


class OpenRequests:
    def __init__(self, vault, *, home=None, launch=launch_target):
        self.vault, self.home, self.launch = vault, Path(home or Path.home()), launch

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
        last = next((m for m in reversed(conversation) if m['role'] == 'assistant'), {})
        return [s for s in last.get('sources') or [] if s.get('kind') in {'email', 'file', 'note'}]

    def validate(self, source, external):
        if source['kind'] == 'email':
            url = source_url(source)
            parsed = urlsplit(url)
            account = source.get('account') or parse_qs(parsed.query).get('authuser', [''])[0]
            if parsed.scheme != 'https' or parsed.hostname != 'mail.google.com' or not account:
                raise ValueError('That email does not have a verified Gmail link. Check your inbox again.')
            message_id = source.get('id') or parsed.fragment.rsplit('/', 1)[-1]
            if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', message_id):
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
        target = open_request(request)
        last = conversation[-1] if conversation else {}
        accepted = bool(re.fullmatch(r'\s*(?:yes|yes please|please do|go ahead|sure|okay|ok)[.!]*\s*', request, re.I))
        offer = last.get('open_offer') if last.get('role') == 'assistant' else None
        choices = last.get('open_choices', []) if last.get('role') == 'assistant' else []
        choice = re.fullmatch(r'\s*(?:the\s+)?(first|second|third|fourth|fifth|sixth|[1-6])(?:\s+(?:one|file|e-?mail))?[.!]*\s*', target or request, re.I)
        selected = None
        if choices and choice:
            ordinals = ['first', 'second', 'third', 'fourth', 'fifth', 'sixth']
            value = choice.group(1).lower()
            index = int(value) - 1 if value.isdigit() else ordinals.index(value)
            selected = choices[index] if index < len(choices) else None
        if target is None and not (accepted and offer or selected):
            return None
        answer = dict(changes=[], operation_id=None, sources=[])
        candidates = []
        if selected:
            candidates = [selected]
        elif accepted and offer and target is None:
            candidates = [offer]
        elif re.fullmatch(r'(?:(?:the|that|this)\s+)?(?:it|one|e-?mail|message|file|document|note)', target, re.I):
            candidates = self.references(conversation)
        elif re.fullmatch(r'(?:my|the)\s+(?:last|latest|newest|most recent)\s+(?:e-?mail|message)', target, re.I):
            if external is None or external.gmail is None:
                raise ValueError('Connect Gmail in Settings first.')
            result = external.gmail.latest(scope='all', cancel=cancel)
            candidates = [email_source(dict(row, account=result['account'])) for row in result['messages']]
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
        if not candidates:
            return dict(answer, text='Which file or email should I open, sir? Please give its filename or identify the email.')
        if len(candidates) != 1:
            return dict(answer, text='I found more than one match, sir. Which should I open? ' + '; '.join(f'{i + 1}. ' + (s.get('path') or s.get('title', 'Email')) for i, s in enumerate(candidates[:6])), sources=candidates[:6], open_choices=candidates[:6])
        source = self.validate(candidates[0], external)
        if cancel is not None and cancel.is_set():
            raise ValueError('Opening cancelled.')
        self.launch(source)
        return dict(answer, text=('Opened that email in Gmail, sir.' if source['kind'] == 'email' else f'Opened {source["title"]}, sir.'), sources=[source])
