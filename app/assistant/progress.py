"""Brief speech acknowledgements for work that is likely to take time."""
import re

from app.assistant.conversation import social_request


def workspace_write_request(request):
    return bool(re.search(r'\bremind\s+me\b', request, re.I) or (
                re.search(r'\b(?:create|make|add|save|schedule|delete|remove|update|edit|remember|move|put|record|complete)\b', request, re.I)
                and re.search(r'\b(?:tasks?|notes?|calendar|reminders?|lists?|categor(?:y|ies))\b', request, re.I)))


def acknowledgement(request):
    if social_request(request):
        return None
    if (re.search(r'\b(?:emails?|gmail|inbox)\b', request, re.I)
            and re.search(r'\b(?:past|last)\s+(?:\d+|one|two|three|four|six|twelve)\s+(?:weeks?|months?|years?)\b', request, re.I)):
        return 'Give me a minute to check those emails.'
    if workspace_write_request(request):
        return 'Give me a sec.'
    return None
