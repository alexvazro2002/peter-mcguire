"""Reject a narrated action or made-up workspace search as a completed reply."""
import re


def action_problem(request,answer,results,*,clarification=False):
    workspace=bool(re.search(r'\b(?:tasks?|notes?|calendar|list|reminders?)\b',request,re.I))
    asks_action=bool(workspace and re.search(r'\b(?:delete|remove|clear|wipe|add|create|save|schedule|move|update|edit|mark|complete)\b',request,re.I))
    tools={row['tool'] for row in results}
    live=bool(tools & {'search_tasks','search_calendar','read_calendar_entry','search_notes','read_note','workspace_overview','read_list','find_list'})
    if workspace and re.search(r"\b(?:I(?:'ve| have)? (?:found|checked|searched)|there are \d+|you have \d+)\b",answer,re.I) and not live:
        return 'No live workspace search supports those task/note claims. Read current app data before naming or counting items.'
    if asks_action and re.search(r"\b(?:I(?:'ll| will| am going to)|let(?:'s| us))\s+(?:delete|remove|clear|wipe|add|create|save|schedule|move|update|edit)|\b(?:give me|just) (?:(?:just|a|one) )*(?:second|moment)\b",answer,re.I):
        return 'A promise is not an executed action. Execute the authorized workspace tools and finish, or ask one question identifying a real ambiguity.'
    if asks_action and not tools.intersection({'create_task','edit_task','delete_task','create_note','edit_note','delete_note','create_calendar_note','update_calendar_entry'}) and re.search(r'\b(?:shall I|should I|go ahead|would you like me to)\b',answer,re.I):
        return 'The user already requested the action. Do not ask for the same confirmation instead of searching and doing it.'
    return None
