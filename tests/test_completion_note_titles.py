"""Note title/content requirements survive ordinary speech and compound tasks."""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.assistant.completion import completion_guidance, completion_plan, completion_problem
from app.assistant.transactions import Transaction
from test_conversation import world


NOW = datetime(2026, 10, 15, 20, 0, tzinfo=ZoneInfo('Europe/London'))
INGREDIENTS = 'my chicken ingredients onion garlic powdwer paprika pepper and salt (black pepper)'
REQUEST = ('Add a task for 21:00 to finish my cooking before 20:22. and create a new note '
           'called recipes and include ' + INGREDIENTS)


def note(utterance):
    return completion_plan(utterance, NOW)['notes']


def test_actual_compound_request_requires_full_recipes_payload():
    notes = note(REQUEST)
    assert len(notes) == 1
    assert notes[0]['name'].casefold() == 'recipes'
    assert notes[0]['content'] == INGREDIENTS
    assert notes[0]['times'] == []


@pytest.mark.parametrize('utterance, name', [
    ('Create a note called recipes and include onion and garlic.', 'recipes'),
    ('Please make a new note titled Weeknight Chicken Recipes including onion and garlic.', 'Weeknight Chicken Recipes'),
    ('Could you add a note named Family Dinner with content onion and garlic.', 'Family Dinner'),
    ('Create a note called "Weeknight Chicken Recipes" and include onion and garlic.', 'Weeknight Chicken Recipes'),
    ('Create a note named “Family Recipes” including onion and garlic.', 'Family Recipes'),
    ("Create a note called 'Family Recipes' and add onion and garlic.", 'Family Recipes'),
    ('Create a note called ‘Family Recipes’ with onion and garlic.', 'Family Recipes'),
    ('Peter, create a note named Recipes I Include and include onion and garlic.', 'Recipes I Include'),
    ("Create a note called Chef's Recipes and include onion and garlic.", "Chef's Recipes"),
    ('Create a note called Include All Details and include onion and garlic.', 'Include All Details'),
])
def test_quoted_and_unquoted_multiword_titles(utterance, name):
    notes = note(utterance)
    assert len(notes) == 1
    assert notes[0]['name'] == name
    assert notes[0]['content'] == 'onion and garlic'


@pytest.mark.parametrize('ending', [
    ' Then archive the older recipes.',
    ' And then transform the old recipe file.',
    ' and open my calendar.',
    ' and also rename the old note.',
    ' then alphabetise my shopping list.',
    ' and add a task for tomorrow.',
])
def test_following_actions_do_not_become_note_content(ending):
    notes = note('Create a note called Recipes and include onion and garlic.' + ending)
    assert notes[0]['content'] == 'onion and garlic'


def test_literal_include_word_and_quoted_action_words_are_preserved():
    content = 'The heading should include the phrase "then open my calendar" and also salt'
    notes = note('Create a note called Writing Ideas and include ' + content)
    assert notes[0]['content'] == content


def test_quoted_payload_is_literal_even_when_it_contains_next_action_words():
    notes = note('Create a note called Example and include "Include the words then archive the recipes."')
    assert notes[0]['content'] == 'Include the words then archive the recipes.'


def test_narrative_sequence_with_then_is_not_an_extra_workspace_action():
    content = 'I had coffee at 9pm then I had chamomile at 6am'
    assert note('Create a note called Drinks and include ' + content)[0]['content'] == content


@pytest.mark.parametrize('utterance', [
    'Tell me about the note called Recipes and include its size.',
    'Explain how to create a note called Recipes and include ingredients.',
    'Do not create a note called Recipes and include ingredients.',
    'I never asked you to create a note called Recipes and include ingredients.',
    'Read the note titled Recipes including onion and garlic.',
    'The email says "create a note called Recipes and include ingredients".',
    'The email says "and create a note called Recipes and include ingredients".',
    'Create a note called Recipes.',
    'Create a note called Recipes then archive old recipes and include onion.',
])
def test_mentions_negations_and_title_only_requests_supply_no_content_requirements(utterance):
    assert note(utterance) == []


def test_another_creation_action_does_not_become_part_of_an_unquoted_title():
    notes = note('Create a note called Recipes and create a new note called Other and include onion.')
    assert notes[0]['name'] == 'Other'
    assert notes[0]['content'] == 'onion'


@pytest.mark.parametrize('utterance, name', [
    ('Append onion and garlic to the note "Recipes".', 'Recipes'),
    ('Put onion and garlic into my note Family Recipes.', 'Family Recipes'),
    ('Include onion and garlic in the note Weeknight Recipes.', 'Weeknight Recipes'),
    ('Please add onion and garlic to note Recipes then open my calendar.', 'Recipes'),
])
def test_explicit_existing_destinations_keep_the_payload(utterance, name):
    notes = note(utterance)
    assert notes[0]['name'] == name
    assert notes[0]['content'] == 'onion and garlic'


def test_missing_ingredient_prevents_note_completion_even_when_the_title_exists(tmp_path):
    tx = Transaction(*world(tmp_path))
    tx.write_note('Recipes', '# Recipes\n\n## Chicken ingredients\n- Onion\n- Garlic powder\n- Black pepper\n- Salt\n', create=True)
    plan = completion_plan(REQUEST, NOW)
    plan['task_definitions'] = []  # This test checks the independent note requirement.
    assert 'paprika' in completion_problem(plan, tx)
    tx.write_note('Recipes', tx.read_note('Recipes') + '- Paprika\n')
    assert completion_problem(plan, tx) is None
    # Checking completion stages and reads data; it does not save partial work.
    assert tx.vault.names() == [] and tx.history.recent() == []


def test_note_guidance_asks_for_readable_ingredients_without_new_facts():
    guide = completion_guidance(completion_plan(REQUEST, NOW), None)['note_rules']
    assert 'one bullet per ingredient' in guide
    assert 'without adding quantities, ingredients or facts' in guide
    assert 'deduplicate only' in guide and 'black pepper' in guide
