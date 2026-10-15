"""Private, expiring email dossiers using the Library's local text retrieval.

Nothing is added to permanent notes or the Library's document collection. Mail and
attachments are evidence, never instructions; no remote content is followed.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import tempfile
import time
import unicodedata
import uuid
from difflib import SequenceMatcher
from email.utils import getaddresses
from pathlib import Path

from library.indexing import SUPPORTED, extract, make_chunks
from library.search import KeywordIndex


class EmailReviewError(RuntimeError):
    pass


def _check(cancel):
    if cancel is not None and cancel.is_set():
        raise EmailReviewError('Email review was cancelled.')


def _normal(value):
    value = unicodedata.normalize('NFKD', str(value)).casefold()
    return ''.join(c for c in value if c.isalnum() and not unicodedata.combining(c))


def participant_match(participant, message):
    """Match display names and address local parts in From or Cc, not body text."""
    if not participant.strip():
        return {'score': 1.0, 'field': 'date window', 'value': ''}
    requested = _normal(participant)
    if len(requested) < 3:
        raise EmailReviewError('Use at least three letters of the sender or Cc name.')
    best = {'score': 0.0, 'field': '', 'value': ''}
    is_address = '@' in participant
    for field in ('sender', 'cc'):
        values = message.get(field, '')
        if not isinstance(values, list):
            values = [values]
        for name, address in getaddresses([str(v) for v in values if v]):
            candidates = [_normal(address)] if is_address else [_normal(name), _normal(address.split('@')[0])]
            for candidate in candidates:
                if not candidate:
                    continue
                score = 1.0 if candidate == requested else SequenceMatcher(None, requested, candidate).ratio()
                if not is_address and len(requested) >= 6 and requested in candidate:
                    score = max(score, .96)
                if score > best['score']:
                    best = {'score': round(score, 3), 'field': field, 'value': name or address}
    return best if best['score'] >= (.92 if is_address else .86) else None


def _private_write(path, data):
    if path.is_symlink():
        raise EmailReviewError('Email review cache cannot use symbolic links.')
    descriptor, temporary = tempfile.mkstemp(prefix='.review-', dir=path.parent)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
            handle.write(data if isinstance(data, str) else json.dumps(data, ensure_ascii=False, indent=2))
            handle.flush(); os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _json(path):
    if path.is_symlink():
        raise EmailReviewError('Email review cache cannot use symbolic links.')
    return json.loads(path.read_text(encoding='utf-8'))


class EmailReview:
    """Prepare and retrieve a bounded read-only mail collection for 24 hours."""
    def __init__(self, root, gmail, *, ttl_seconds=86400, clock=time.time,
                 max_messages=300, max_attachment_bytes=20 * 1024 * 1024,
                 max_total_attachment_bytes=100 * 1024 * 1024):
        self.root = Path(root).expanduser()
        self.base = self.root / '.runtime' / 'email-review'
        self.gmail, self.clock = gmail, clock
        self.ttl_seconds = max(60, min(7 * 86400, int(ttl_seconds)))
        self.max_messages = max(1, min(2000, int(max_messages)))
        self.max_attachment_bytes = max(1, int(max_attachment_bytes))
        self.max_total_attachment_bytes = max(1, int(max_total_attachment_bytes))
        self._manifest, self._index = None, None

    def _assert_account(self, expected):
        if hasattr(self.gmail, 'status'):
            status = self.gmail.status()
            account = status.get('account') or status.get('email') or ''
            if not status.get('connected', bool(account)) or account.casefold() != expected.casefold():
                raise EmailReviewError('The Gmail account changed while preparing the review. Fetch the emails again.')

    def _directories(self):
        for directory in (self.root / '.runtime', self.base):
            if directory.is_symlink():
                raise EmailReviewError('Email review cache cannot use symbolic links.')
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            directory.chmod(0o700)

    def cleanup(self):
        """Remove expired dossiers; never follow a symlink outside this cache."""
        self._directories()
        for directory in self.base.iterdir():
            if not re.fullmatch(r'review-[0-9a-f]{32}', directory.name):
                continue
            if directory.is_symlink():
                directory.unlink()
                continue
            if not directory.is_dir():
                continue
            try:
                manifest = _json(directory / 'manifest.json')
                expired = float(manifest['expires_at']) <= self.clock()
            except (OSError, ValueError, KeyError, TypeError):
                # Abandoned interrupted preparations expire from their mtime.
                expired = directory.stat().st_mtime + self.ttl_seconds <= self.clock()
            if expired:
                shutil.rmtree(directory)
        if self._manifest and self._manifest['expires_at'] <= self.clock():
            self._manifest, self._index = None, None

    def _directory(self, corpus_id):
        if not isinstance(corpus_id, str) or not re.fullmatch(r'review-[0-9a-f]{32}', corpus_id):
            raise EmailReviewError('Invalid email review identifier.')
        directory = self.base / corpus_id
        if directory.is_symlink():
            raise EmailReviewError('Email review cache cannot use symbolic links.')
        return directory

    def load(self):
        self.cleanup()
        try:
            active = _json(self.base / 'active.json')
            manifest = _json(self._directory(active['corpus_id']) / 'manifest.json')
            if manifest.get('version') != 1 or manifest['expires_at'] <= self.clock():
                return None
            if not isinstance(manifest.get('messages'), list):
                return None
            if hasattr(self.gmail, 'status'):
                status = self.gmail.status()
                account = status.get('account') or status.get('email') or ''
                if not status.get('connected', bool(account)) or account.casefold() != manifest['account'].casefold():
                    return None
            self._manifest, self._index = manifest, None
            return manifest
        except (OSError, ValueError, KeyError, TypeError, EmailReviewError):
            return None

    def prepare(self, query, *, participant='', sender='', date_label='', date_window=None,
                cancel=None, progress=None):
        """Fetch the entire date window, fuzzy-filter From/Cc, then index bodies/files.

        ``query`` must include a lower date bound. Restrict dates in Gmail first;
        participant matching stays local because Gmail's exact-name search can
        exclude joined names and near spellings before they can be compared.
        """
        if not isinstance(query, str) or not re.search(r'\b(?:after|newer|newer_than):\S+', query):
            raise EmailReviewError('Choose a bounded date range before preparing an email review.')
        participant = str(participant or sender).strip()
        self.cleanup(); _check(cancel)
        headers = self.gmail.list_headers(query, limit=None, cancel=cancel, scope='all')
        return self._prepare_headers(headers, query=query, participant=participant, date_label=date_label,
                                     date_window=date_window, cancel=cancel, progress=progress)

    def prepare_messages(self, headers, *, account='', date_label='the selected email',
                         cancel=None, progress=None):
        """Read caller-selected verified header IDs, without inventing a date query."""
        self.cleanup(); _check(cancel)
        if isinstance(headers, dict):
            account = account or headers.get('account', '')
            headers = headers.get('messages', headers.get('items', []))
        if not isinstance(headers, list):
            raise EmailReviewError('Choose verified email headers before preparing a review.')
        return self._prepare_headers(dict(account=account, messages=headers, complete=True),
                                     query='', participant='', date_label=date_label,
                                     date_window=None, cancel=cancel, progress=progress)

    def _prepare_headers(self, headers, *, query, participant, date_label, date_window, cancel, progress):
        if headers.get('complete') is not True:
            raise EmailReviewError('The complete email date window could not be fetched. Try again before reviewing it.')
        account = str(headers.get('account') or '').strip()
        if not account:
            raise EmailReviewError('Gmail did not identify the account for this review.')
        self._assert_account(account)
        matched, seen, incomplete_participants = [], set(), 0
        for message in headers.get('messages', []):
            _check(cancel)
            if not isinstance(message, dict) or not message.get('id') or message['id'] in seen:
                continue
            seen.add(message['id'])
            if participant and message.get('participant_headers_complete') is False:
                incomplete_participants += 1
            match = participant_match(participant, message)
            if match:
                matched.append((message, match))
        corpus_id = 'review-' + uuid.uuid4().hex
        directory = self._directory(corpus_id)
        directory.mkdir(mode=0o700)
        (directory / 'texts').mkdir(mode=0o700)
        manifest = dict(version=1, corpus_id=corpus_id, created_at=self.clock(),
                        expires_at=self.clock() + self.ttl_seconds, account=account,
                        query=query, participant=participant, date_label=str(date_label),
                        date_window=date_window, text_directory='texts', headers_scanned=len(seen), search_complete=not incomplete_participants,
                        participant_headers_incomplete=incomplete_participants,
                        matched_total=len(matched), download_limit_reached=len(matched) > self.max_messages,
                        messages=[])
        chunks, attachment_bytes = [], 0
        try:
            for index, (header, match) in enumerate(matched[:self.max_messages], 1):
                _check(cancel)
                if progress:
                    progress(f'Reading email {index} of {min(len(matched), self.max_messages)}')
                item = {key: header.get(key, '') for key in ('id', 'thread_id', 'sender', 'to', 'cc', 'subject', 'date', 'received_ms', 'url')}
                item.update(match=match, body_status='unavailable', attachments=[], attachment_inventory_complete=False)
                try:
                    result = self.gmail.read(header['id'], cancel=cancel)
                    message = result.get('message', {})
                    if result.get('account') != account or message.get('id') != header['id']:
                        raise EmailReviewError('The fetched email does not match this account and message.')
                    item.update({key: message.get(key, item.get(key, '')) for key in ('sender', 'to', 'cc', 'subject', 'date', 'received_ms', 'url')})
                    body = str(message.get('body') or '')
                    item['body_status'] = ('unavailable' if message.get('body_unavailable') else
                                           'truncated' if message.get('body_truncated') else
                                           'readable' if body.strip() else 'empty')
                    item['attachment_inventory_complete'] = (message.get('attachment_inventory_complete') is True
                                                             and not message.get('attachment_inventory_truncated'))
                    digest = hashlib.sha256(str(header['id']).encode()).hexdigest()[:24]
                    filename = f'message-{digest}.json'
                    _private_write(directory / filename, dict(item, body=body))
                    item['body_file'] = filename
                    if body.strip():
                        text_path = directory / 'texts' / f'email-{digest}.txt'
                        _private_write(text_path, f"Subject: {item['subject']}\nFrom: {item['sender']}\nDate: {item['date']}\n\n{body}")
                        _private_write(text_path.with_suffix('.source'), f"{item['subject']} | {item['url']}")
                    self._chunks(chunks, item, body, 'email body')
                    for number, attachment in enumerate(message.get('attachments') or []):
                        _check(cancel)
                        if not isinstance(attachment, dict):
                            item['attachment_inventory_complete'] = False
                            continue
                        record = {key: attachment.get(key) for key in ('filename', 'mime_type', 'size', 'attachment_id')}
                        record['filename'] = str(record.get('filename') or f'attachment-{number + 1}')
                        record['status'] = 'unavailable'
                        item['attachments'].append(record)
                        try:
                            declared = int(record.get('size') or 0)
                            if declared > self.max_attachment_bytes or attachment_bytes + declared > self.max_total_attachment_bytes:
                                record['status'] = 'too_large'; continue
                            inline = attachment.get('inline_data')
                            if record.get('attachment_id'):
                                self._assert_account(account)
                                raw = self.gmail.read_attachment(header['id'], record['attachment_id'],
                                                                 max_bytes=self.max_attachment_bytes, cancel=cancel)
                                self._assert_account(account)
                            elif inline and not attachment.get('inline_data_truncated'):
                                if len(inline) > self.max_attachment_bytes * 4 // 3 + 8:
                                    record['status'] = 'too_large'; continue
                                encoded = str(inline) + '=' * (-len(str(inline)) % 4)
                                raw = base64.b64decode(encoded, altchars=b'-_', validate=True)
                                if declared and len(raw) != declared:
                                    record.update(status='failed', reason='Attachment size did not match its metadata.'); continue
                            else:
                                record['reason'] = 'Gmail did not provide downloadable attachment data.'; continue
                            if len(raw) > self.max_attachment_bytes or attachment_bytes + len(raw) > self.max_total_attachment_bytes:
                                record['status'] = 'too_large'; continue
                            attachment_bytes += len(raw)
                            suffix = Path(record['filename']).suffix.lower()
                            suffix = suffix if re.fullmatch(r'\.[a-z0-9]{1,10}', suffix) else '.bin'
                            path = directory / f'attachment-{digest}-{number:03d}{suffix}'
                            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                            with os.fdopen(descriptor, 'wb') as handle:
                                handle.write(raw)
                            record.update(file=path.name, downloaded_bytes=len(raw), status='unsupported')
                            if suffix in SUPPORTED:
                                text = extract(path)
                                if not re.sub(r'\[PAGE \d+\]', '', text).strip():
                                    record['status'] = 'no_readable_text'
                                else:
                                    record['status'] = 'readable'
                                    text_path = directory / 'texts' / (path.name + '.txt')
                                    _private_write(text_path, text)
                                    record['text_file'] = 'texts/' + text_path.name
                                    _private_write(text_path.with_suffix('.source'), f"{record['filename']} | {item['subject']} | {item['url']}")
                                    self._chunks(chunks, item, text, 'attachment: ' + record['filename'])
                        except Exception as error:
                            _check(cancel)
                            record.update(status='failed', reason=type(error).__name__)
                except Exception as error:
                    _check(cancel)
                    item['body_status'] = 'failed'
                    item['body_error'] = type(error).__name__
                manifest['messages'].append(item)
            _check(cancel)
            self._assert_account(account)
            _private_write(directory / 'chunks.json', chunks)
            _private_write(directory / 'manifest.json', manifest)
            _private_write(self.base / 'active.json', {'corpus_id': corpus_id})
            self._manifest, self._index = manifest, KeywordIndex(chunks)
            return self.summary()
        except BaseException:
            shutil.rmtree(directory)
            raise

    @staticmethod
    def _chunks(chunks, message, text, kind):
        source = f"{message.get('subject') or '(No subject)'} | {kind}"
        for number, passage in enumerate(make_chunks(text)):
            chunks.append(dict(id=len(chunks), source=source, chunk=number, text=passage,
                               message_id=message['id'], sender=message.get('sender', ''),
                               date=message.get('date', ''), url=message.get('url', ''), kind=kind))

    def _current(self):
        if self._manifest is not None and hasattr(self.gmail, 'status'):
            status = self.gmail.status()
            account = status.get('account') or status.get('email') or ''
            if not status.get('connected', bool(account)) or account.casefold() != self._manifest['account'].casefold():
                self._manifest, self._index = None, None
        manifest = self.load() if self._manifest is None else self._manifest
        if manifest is None or manifest['expires_at'] <= self.clock():
            self.cleanup()
            raise EmailReviewError('No active email review is available. Ask Peter to fetch a sender and date range first.')
        return manifest

    @staticmethod
    def _sources(manifest, messages):
        return [dict(kind='email', id=item['id'], account=manifest['account'],
                     title=item.get('subject') or '(No subject)', subject=item.get('subject') or '(No subject)',
                     sender=item.get('sender', ''), date=item.get('date', ''), url=item.get('url', ''),
                     review_corpus=manifest['corpus_id']) for item in messages]

    def summary(self):
        manifest = self._current()
        messages = manifest['messages']
        count = len(messages)
        window = manifest['date_label'] or 'the requested date range'
        participant = f" matching {manifest['participant']} in From or Cc" if manifest['participant'] else ''
        answer = f"I've prepared {count} email{'s' if count != 1 else ''}{participant} for {window}."
        if manifest.get('participant_headers_incomplete'):
            answer += ' Some From or Cc headers were incomplete, so other matching emails may be missing from this review.'
        if manifest['download_limit_reached']:
            answer += f" The search matched {manifest['matched_total']}; this review includes the first {count}."
        failed = sum(item['body_status'] == 'failed' for item in messages)
        readable = sum(a['status'] == 'readable' for item in messages for a in item['attachments'])
        unavailable = sum(a['status'] != 'readable' for item in messages for a in item['attachments'])
        unknown = sum(not item['attachment_inventory_complete'] for item in messages)
        if failed:
            answer += f" {failed} email bod{'y could' if failed == 1 else 'ies could'} not be downloaded."
        if readable:
            answer += f" {readable} attachment{'s are' if readable != 1 else ' is'} ready to read."
        if unavailable or unknown:
            answer += " Some attachments could not be checked or read. Would you like me to open the relevant email so you can check?"
        elif messages and not any(item['attachments'] for item in messages):
            answer += ' Gmail reports no attachments in these downloaded messages.'
        answer += ' You can ask me about their contents now.' if count else ' Try another sender or date range.'
        return dict(answer=answer, corpus_id=manifest['corpus_id'], messages=messages,
                    cache_directory=str(self._directory(manifest['corpus_id'])),
                    text_directory=str(self._directory(manifest['corpus_id']) / 'texts'),
                    sources=self._sources(manifest, messages), manifest=manifest,
                    evidence=dict(tools=['prepare_email_review'], corpus_id=manifest['corpus_id'],
                                  query=manifest['query'], account=manifest['account'],
                                  headers_scanned=manifest['headers_scanned'], matched_total=manifest['matched_total'],
                                  messages_downloaded=count, complete=manifest['search_complete'] and not manifest['download_limit_reached'],
                                  email_review=True), attachment_readable=readable,
                    attachment_unavailable=unavailable, attachment_inventory_unknown=unknown)

    report = summary

    def read(self, message_id):
        manifest = self._current()
        item = next((row for row in manifest['messages'] if row['id'] == message_id), None)
        if item is None:
            raise EmailReviewError('That email is not part of the active review.')
        body = ''
        filename = item.get('body_file', '')
        if filename:
            if not re.fullmatch(r'message-[0-9a-f]{24}\.json', filename):
                raise EmailReviewError('Invalid cached message path.')
            body = _json(self._directory(manifest['corpus_id']) / filename).get('body', '')
        return dict(item, body=body, account=manifest['account'], untrusted=True, source='email_review')

    def search(self, question, *, limit=6, max_chars=14000):
        manifest = self._current()
        if self._index is None:
            records = _json(self._directory(manifest['corpus_id']) / 'chunks.json')
            self._index = KeywordIndex(records)
        records = self._index.metadata
        selected = self._index.search(str(question), limit=max(1, min(30, int(limit))))
        # Overview questions often have no lexical overlap; label a bounded sample.
        if not selected:
            selected = list(range(min(len(records), max(1, min(30, int(limit))))))
        passages, context, used, seen_messages = [], [], 0, set()
        max_chars = max(500, int(max_chars))
        for index in selected:
            item = dict(records[index])
            label = f"[email:{item['message_id']}; {item['kind']}; passage:{item['chunk']}]"
            block = f"{label}\nSubject: {item['source']}\nFrom: {item['sender']}\nDate: {item['date']}\n{item['text']}"
            remaining = max_chars - used
            if remaining < 200:
                break
            if len(block) > remaining:
                item['text'] = item['text'][:max(0, remaining - (len(block) - len(item['text'])))]
                block = block[:remaining] + '\n[passage excerpt ends]'
                item['excerpt_truncated'] = True
            passages.append(item); context.append(block); used += len(block)
            seen_messages.add(item['message_id'])
        messages = [item for item in manifest['messages'] if item['id'] in seen_messages]
        inventories = [{key: item.get(key) for key in ('id', 'subject', 'body_status', 'attachment_inventory_complete', 'attachments')}
                       for item in manifest['messages']]
        return dict(corpus_id=manifest['corpus_id'], passages=passages, context='\n\n'.join(context),
                    sources=self._sources(manifest, messages), manifest=manifest, inventories=inventories,
                    retrieval_complete=len(passages) == len(records), untrusted=True)
