"""Evidence gates for personal documents and biographical claims.

Assistant prose, filenames, opened-file metadata and job adverts are never a
source of facts about the user. These checks complement (not replace) the model
instructions; they deliberately fail closed for unsupported personal assertions.
"""
import re

TRUTH_RULES = """Evidence discipline is mandatory, including in casual conversation.
Never invent a personal fact, file, qualification, employer, experience, degree, amount,
date or completed action. Having permission to search is not having a document.
Finding a filename or opening a file does not mean you read its contents. A job advert's
requirements are not the user's qualifications. Previous assistant replies are never evidence,
even when the user says yes. Only explicit user statements or freshly retrieved personal data
support facts about the user. Questions, hypothetical examples and third-party offers do not.
If evidence is missing, say what you have not verified and ask for the missing document or fact.
Do not invent details to be helpful. Correct a false premise instead of extending it.
You can search/read saved notes and memories; the repository reader reads application source,
not arbitrary personal PDFs or Word files. Do not claim to have a personal file unless a tool
actually located it, and do not describe its contents without a successful content read.
For a missing personal document, ask for pasted text or its saved note name. Do not suggest
an upload/attachment capability this chat does not have, or an internet search for a private CV.
Be candid and conversational; these rules do not prevent grounded opinions or useful advice."""

_DOCUMENT = r'(?:cv|résumé|resume|curriculum vitae|passport|medical records?|bank statements?|tax returns?|personal (?:file|document)s?)'
_BIO = r'(?:experience|qualifications?|background|degree|diploma|education|work history|employment|skills?|salary|employer|supervisor|management|name|address|age|birthday|birth date|phone number|savings|diagnosis|medical history)'
_NEGATIVE = re.compile(r"\b(?:not|no|never|haven't|hasn't|don't|doesn't|didn't|cannot|can't|couldn't|unverified|unconfirmed|unknown|without)\b", re.I)


def personal_topic(request, conversation=()):
    if re.search(_DOCUMENT + r'|\b(?:my|your)\s+' + _BIO, request, re.I):
        return True
    if re.fullmatch(r"\s*(?:yes|yeah|yep|sure|okay|ok|go ahead|please do|do that|tell me more|what about (?:it|that)|compare (?:it|them))[, .!?]*(?:please[.!?]*)?\s*", request, re.I):
        recent = [row.get('text', '') for row in conversation[-4:] if row.get('role') == 'user']
        return any(re.search(_DOCUMENT + r'|\b(?:my|your)\s+' + _BIO, text, re.I) for text in recent)
    return False


def _user_facts(request, conversation):
    result = []
    for text in [r.get('text', '') for r in conversation[-40:] if r.get('role') == 'user'] + [request]:
        if re.search(r"\b(?:actually|correction|you(?:'re| are) wrong|made (?:it|that) up)\b", text, re.I):
            result.clear()
        for sentence in re.split(r'(?<=[.!?])\s+|\n', text):
            if ('?' in sentence or re.match(r'\s*(?:if|suppose|imagine|pretend|write|say|what|do you|can you|could you)\b', sentence, re.I)
                    or re.search(r'\b(?:you (?:said|claimed)|made (?:it|that) up)\b', sentence, re.I)):
                continue
            if not _NEGATIVE.search(sentence) and re.search(r"\b(?:I (?:have|had|am|was|worked|studied|graduated|earn)|I(?:'ve|'m)|my\s+" + _BIO + r')\b', sentence, re.I):
                result.append(sentence)
    return result


def _personal_evidence(request, results, conversation, memories):
    facts = _user_facts(request, conversation)
    documents = []
    for row in results:
        result, tool = row.get('result', {}), row.get('tool')
        if tool == 'read_note' and result.get('content'):
            facts.append(result['content'])
            if re.search(_DOCUMENT, result.get('name', ''), re.I):
                documents.append(result)
        elif tool == 'read_memory' and result.get('content'):
            facts.append(result['content'])
        elif tool == 'search_memories':
            facts.extend(item.get('content', '') for item in result.get('items', []))
    facts.extend(item.get('content', '') for item in memories)
    return facts, documents


_STOP = set('i me my you your yours have has had am is are was were been be the a an and or of for to in at with from it that this as includes include also already previously currently years year experience skills background work worked working strong'.split())
_NUMBERS = dict(zip('zero one two three four five six seven eight nine ten'.split(), map(str, range(11))))

def _terms(text):
    tokens = re.findall(r"[\w]+", text.casefold())
    return {_NUMBERS.get(t, t) for t in tokens if t not in _STOP}


def personal_problem(request, answer, results, conversation=(), memories=()):
    facts, documents = _personal_evidence(request, results, conversation, memories)
    facts = [sentence for fact in facts for sentence in re.split(r'(?<=[.!?])\s+|\n', fact) if not _NEGATIVE.search(sentence)]
    support = _terms(' '.join(facts))
    topic = personal_topic(request, conversation)
    if topic and re.search(r"\b(?:do you have|have you got|can you (?:see|access|read)|have you (?:read|seen))\b", request, re.I) and not documents:
        if re.match(r"\s*(?:yes|yeah|certainly|of course|absolutely|I do|I can)\b", answer, re.I):
            return 'No personal document was read. Do not affirm access from chat history or an assumption.'
    for sentence in re.split(r'(?<=[.!?;])\s+|\n|,?\s+but\s+', answer.replace('’', "'")):
        if not sentence.strip() or '?' in sentence or _NEGATIVE.search(sentence):
            continue
        if re.search(r'\b(?:if|would|could|might|should|need to|please|share|provide|send me)\b', sentence, re.I):
            continue
        possession = ((topic or re.search(_DOCUMENT, sentence, re.I)) and re.search(
            r"\b(?:I(?:'ve| have)? (?:have|got|stored|read|reviewed|found|hold)|I have|on file|stored|your .{0,30}(?:says|states|shows|includes|lists|contains))\b", sentence, re.I))
        if possession and not documents:
            return 'No personal document content was read. Do not claim to have the CV/document or describe it. Search/read the actual note, or explain that its contents are not available.'
        biography = re.search(r"\b(?:you (?:have|had|are|were|worked|studied|graduated|earn|hold|live|were born|were diagnosed)|you've (?:got|worked|studied)|your\s+" + _BIO + r"|from your (?:degree|experience))\b", sentence, re.I)
        if topic and re.search(r"\b(?:here(?:'s| is) what you've got|years? (?:in|of)|degree in|diploma in)\b", sentence, re.I):
            biography = True
        if topic and re.search(r"\b(?:it|(?:the|your) (?:cv|résumé|resume|document)) (?:includes|lists|shows|states|contains|says)\b", sentence, re.I):
            biography = True
        if biography and (topic or re.search(_BIO, sentence, re.I)):
            if not facts:
                return 'No user statement or read personal record supports these biographical facts. Job requirements and earlier assistant claims are not the user\'s experience. Ask for the missing facts.'
            claimed = _terms(sentence)
            # Allow small grammatical paraphrases, but not invented quantities,
            # degrees or whole new qualifications alongside a true fact.
            missing = claimed - support
            if any(term.isdigit() for term in missing) or len(missing) > max(1, len(claimed) // 5):
                return 'The personal details exceed the verified user statements/records. State only supported facts; omit unverified years, degrees, employers and qualifications.'
    return None


def personal_fallback():
    return "I haven't verified your CV or those personal details. I shouldn't fill in the gaps. Please share the relevant text so I can give you an accurate answer."
