import json
from app.assistant.personal_grounding import personal_problem
from app.assistant.conversation import conversation_messages
from test_conversation import Model, engine, mail_history


def test_cv_and_background_fabrication_is_blocked_without_evidence(tmp_path):
    bad = 'Yes, I have a copy of your CV stored. It includes your retail experience and supervisory roles.'
    model = Model(*[dict(answer=bad, apply_changes=True)] * 3)
    result = engine(tmp_path, model, []).run('Do you have my CV?')
    assert 'haven\'t verified' in result['text'] and 'retail' not in result['text']
    assert not result['changes']


def test_old_assistant_cv_claim_is_not_evidence_or_replayed_as_fact(tmp_path):
    history = [dict(role='user', text='Do you have my CV?'),
               dict(role='assistant', text='Yes, I have your CV stored. You have a degree in Business Administration.')]
    bad = "Here's what you've got: three years in retail management and a degree in Business Administration."
    assert personal_problem('yes', bad, [], history)
    sent = conversation_messages(history, 8000)
    assert 'Business Administration' not in str(sent)
    model = Model(dict(answer=bad, apply_changes=True), dict(answer="I haven't read your CV. Please share its text.", apply_changes=False))
    result = engine(tmp_path, model, []).run('yes', history)
    assert "haven't read" in result['text']


def test_opened_filename_and_email_requirements_do_not_prove_user_experience():
    answer = 'You have three years in retail management and a degree in Business Administration.'
    for results in [[], [dict(tool='open_items', result=dict(path='/tmp/CV.pdf'))],
                    [dict(tool='read_gmail', result=dict(text='Required: three years in retail management and a degree in Business Administration.'))],
                    [dict(tool='search_notes', result=dict(items=[dict(name='CV', excerpt='Retail')]))]]:
        assert personal_problem('Compare my CV with this job.', answer, results)


def test_user_supplied_fact_allowed_but_added_degree_blocked():
    history = [dict(role='user', text='I have three years in retail management.')]
    assert not personal_problem('What is my experience?', 'You have three years in retail management.', [], history)
    assert personal_problem('What is my experience?', 'You have three years in retail management and a degree in Business Administration.', [], history)
    assert personal_problem('What is my experience?', 'You have five years in retail management.', [], history)


def test_actual_read_cv_supports_possession_and_content():
    results = [dict(tool='read_note', result=dict(name='CV', content='I have three years in retail management.'))]
    assert not personal_problem('Do you have my CV?', 'Yes, I have your CV. You have three years in retail management.', results)
    assert personal_problem('Do you have my CV?', 'Yes.', [])


def test_questions_hypotheticals_and_corrections_are_not_biographical_evidence():
    for text in ['Do I have three years in retail management?', 'Pretend I have three years in retail management.',
                 'You said I have three years in retail management. You made it up.']:
        assert personal_problem('What is my experience?', 'You have three years in retail management.', [], [dict(role='user', text=text)])


def test_dedicated_email_reader_rejects_invented_personal_background(tmp_path):
    model = Model(dict(answer='You have a degree in Business Administration, so you match this role.', apply_changes=False),
                  dict(answer='The email states a salary of £34,000. I need your actual experience to assess the fit.', apply_changes=False))
    result = engine(tmp_path, model, []).run('Read it and tell me if my experience matches.', mail_history())
    assert 'degree' not in result['text']


def test_negative_and_corrected_user_facts_cannot_support_positive_claim():
    assert personal_problem('What degree do I have?', 'You have a degree in Business Administration.', [],
                            [dict(role='user', text='I have no degree in Business Administration.')])
    history = [dict(role='user',text='I have three years in retail management.'),
               dict(role='user',text='Actually I have four years in retail management.')]
    assert personal_problem('What is my experience?', 'You have three years in retail management.', [], history)
    assert not personal_problem('What is my experience?', 'You have four years in retail management.', [], history)


def test_negative_clause_does_not_hide_a_positive_invention():
    assert personal_problem('Do you have my CV?', "I don't have your CV, but you have a degree in Business Administration.", [])
