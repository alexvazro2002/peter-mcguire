"""Reject unsupported live-mail and desktop-action claims before showing or speaking them."""
import re
from urllib.parse import parse_qs, urlsplit


def verified_mail_summary(results):
    searches = [row['result'] for row in results if row['tool'] in {'search_gmail', 'latest_gmail'}]
    if not searches:
        return None
    items = searches[-1].get('items', [])
    if not items:
        return "I didn't find any matching emails in that search."
    if len(items) == 1:
        sender = items[0].get('sender', '').split('<', 1)[0].strip(' "') or 'the sender'
        return f"I found one matching email from {sender}. Its subject is {items[0].get('subject', 'not specified')}."
    return f"I found {len(items)} matching emails. Their links are available in the conversation."


def _mail_link(url):
    try:
        parsed = urlsplit(url.rstrip('.,;'))
        if parsed.scheme != 'https' or parsed.hostname != 'mail.google.com' or parsed.username or parsed.password:
            return None
        return (parse_qs(parsed.query).get('authuser', [''])[0].casefold(), parsed.fragment.rsplit('/', 1)[-1])
    except ValueError:
        return None


def unsupported_reply(request, answer, access, results, sources, *, clarification=False, prior_action=None):
    tools = {row['tool'] for row in results}
    searches = [row['result'] for row in results if row['tool'] in {'search_gmail', 'latest_gmail'}]
    reads = [row['result'] for row in results if row['tool'] == 'read_gmail']
    # The model can ask a real selection question without asserting unseen facts.
    question = clarification and '?' in answer and bool(re.search(r'\b(?:which|what (?:sender|date|day|time|account|email)|how many)\b', answer, re.I)) and not re.search(
        r'\b(?:I (?:found|read|searched|opened|closed)|you have \d|there are \d)\b', answer, re.I)
    if access is not None and not question:
        if getattr(access, 'email_body_request', False) and not reads:
            return 'No email body has been read for this question. Use read_gmail before describing its contents.'
        if getattr(access, 'email_search_request', False) and not searches:
            return 'No Gmail search has completed for this request. Use search_gmail or latest_gmail before reporting results.'
    claimed_read = re.search(r"\b(?:I(?:'ve| have)? (?:read|reviewed)|(?:contents?|emails?|messages?) (?:have|has) been (?:read|reviewed))\b", answer, re.I)
    if claimed_read and not reads and (getattr(access, 'email_authorized', False) or re.search(r'\b(?:email|gmail)\b', answer, re.I)):
        return 'No read_gmail tool completed; do not claim to have read message contents.'
    historical_open = bool(re.search(r'\b(?:did|have) you (?:open|opened)\b', request, re.I)
                           and prior_action and 'open_items' in prior_action.get('tools', [])
                           and prior_action.get('opened'))
    if re.search(r"\b(?:I(?:'ve| have)? closed|(?:email|file|tab|window|browser)(?: is| has been| was| are| have been)?(?: now)? closed)\b", answer, re.I) or (not historical_open and re.search(
            r"\b(?:I(?:'ve| have)? opened|(?:email|file|tab|window|browser)(?: is| has been| was| are| have been)?(?: now)? opened)\b", answer, re.I)):
        return 'The model has no browser or file-opening/closing tool. Do not claim that action happened.'
    if re.match(r'\s*(?:Searching|Checking|Reading|Opening)\b', answer, re.I) or re.search(
            r"\bI (?:will|shall|'ll|am going to) (?:search|check|read|open)\b", answer, re.I):
        if getattr(access, 'email_authorized', False):
            return 'This is a final reply, not background work. Complete the requested email operation now.'
    allowed = {_mail_link(row.get('url', '')) for row in sources if row.get('kind') == 'email'}
    # A user-supplied link may be discussed as a link; it never proves a fetched message.
    allowed.update(_mail_link(url) for url in re.findall(r'https?://[^\s)<>]+', request))
    for url in re.findall(r'https?://[^\s)<>]+', answer):
        if 'mail.google.com' in url and (_mail_link(url) is None or _mail_link(url) not in allowed):
            return 'That Gmail link was not returned by a verified search or read. Never invent message URLs.'
    if searches:
        latest = searches[-1]
        total = len(latest.get('items', []))
        number = re.search(r'\b(?:I (?:found|have found)|the search (?:found|returned)|there (?:are|were)|you have)\s+(\d+|one|two|three|four|five|six|seven|eight|nine|ten)\s+(?:new |unread |matching )?(?:emails?|messages?)\b', answer, re.I)
        if number:
            names = ['zero', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine', 'ten']
            value = number[1].lower()
            count = int(value) if value.isdigit() else names.index(value)
            if count > total:
                return 'The claimed number of matching emails exceeds the messages actually returned.'
        if not total and re.search(r'\b(?:subject|sender|message IDs?|I (?:found|have found)|found two|found \d+)\b', answer, re.I) and not re.search(r'\b(?:no|not|none|zero|couldn.t|didn.t)\b', answer, re.I):
            return 'The verified Gmail search returned no matches; do not invent messages or their details.'
        if re.search(r'\b(?:account|inbox)\b.*\b(?:no emails at all|empty|contains no emails)\b', answer, re.I) and latest.get('account_messages_total') != 0:
            return 'A filtered search does not establish that an account or inbox is empty.'
        subjects = {str(row.get('subject', '')).casefold() for row in latest.get('items', [])}
        for match in re.finditer(r'\b(?:subject(?: line)?(?: is|:)?|titled|called)\s*[“"\']([^”"\']+)[”"\']', answer, re.I):
            if match[1].casefold() not in subjects:
                return 'That email subject does not occur in the verified result. Do not invent email titles.'
    elif getattr(access, 'email_authorized', False) and re.search(
            r'\b(?:I (?:found|have found|searched)|you have (?:one|two|three|\d+)|(?:email|message) (?:is|was) from|subject (?:is|line))\b', answer, re.I):
        return 'No verified email result supports that claim. Read or search Gmail first.'
    return None
