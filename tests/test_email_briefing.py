"""Sender offers fetch no bodies; consent only applies to the current question."""
import threading

import pytest

from app.email_briefing import EmailBriefing, header_message, sender_name
from app.gmail import GmailCancelled, GmailError
from app.integrations import inbox_digest


def message(message_id="one", sender="Alice Smith <alice@example.test>", **extra):
    return dict(id=message_id, sender=sender, subject="Today's schedule", body="Private email body.",
                snippet="Private email preview.", date="Today", received_ms=10,
                url="https://mail.google.com/mail/?authuser=person%40example.test#inbox/" + message_id,
                **extra)


class Client:
    def __init__(self):
        self.account = "person@example.test"
        self.calls = []
        self.body = "Private email body."
        self.fail = False

    def status(self):
        return dict(connected=True, account=self.account)

    def read(self, message_id, *, cancel=None):
        self.calls.append(message_id)
        if self.fail:
            raise GmailError("The inbox is unavailable.")
        row = message(message_id)
        row["body"] = self.body
        return dict(account=self.account, message=row)


def offer(client=None):
    client = client or Client()
    flow = EmailBriefing(client)
    flow.extend([message()], account=client.account)
    result = flow.offer_next()
    return flow, client, result


def test_offer_is_exact_sender_question_and_does_not_fetch_or_persist_body():
    flow, client, result = offer()
    assert result["text"] == "You have a new email from Alice Smith, would you like me to read it for you, sir?"
    assert client.calls == []
    assert "body" not in result["email_offer"] and "snippet" not in result["email_offer"]
    assert "Private email" not in str(flow.export_state())


@pytest.mark.parametrize("reply", ["Yes.", "Yes, please.", "read it", "Go ahead", "Please read it for me, Peter."])
def test_direct_affirmative_reads_current_email_and_marks_it_handled(reply):
    flow, client, _ = offer()
    result = flow.respond(reply)
    assert client.calls == ["one"]
    assert "Subject: Today's schedule." in result["text"] and "Private email body." in result["text"]
    assert result["email_handled"] == dict(account=client.account, id="one")
    assert result["email_briefing_next"] and result["email_content"] and result["untrusted"]
    assert result["sources"][0]["kind"] == "email"
    assert flow.awaiting is None and not flow.pending
    assert flow.respond("yes") is None and flow.offer_next() is None


@pytest.mark.parametrize("reply", ["No", "No thanks", "Skip it", "Leave it for now", "Next email"])
def test_decline_never_fetches_body_and_advances_one_at_a_time(reply):
    flow, client, _ = offer()
    flow.extend([message("two", sender="Bob <bob@example.test>")], account=client.account)
    result = flow.respond(reply)
    assert client.calls == [] and result["email_handled"]["id"] == "one"
    assert result["email_briefing_next"]
    assert flow.offer_next()["text"].startswith("You have a new email from Bob,")
    assert flow.respond("yes")["email_handled"]["id"] == "two"
    assert client.calls == ["two"]


@pytest.mark.parametrize("reply", ["What time is it?", "Yes, but first show my tasks", "No, read it", "Alice says yes please read it", "Maybe"])
def test_unrelated_or_ambiguous_request_expires_consent_without_losing_queue(reply):
    flow, client, _ = offer()
    assert flow.respond(reply) is None
    assert flow.awaiting is None and len(flow.pending) == 1
    assert flow.respond("yes") is None and client.calls == []
    flow.offer_next()
    assert flow.respond("yes")["email_handled"]["id"] == "one"


def test_no_active_offer_cannot_grant_a_generic_yes_even_with_queued_mail():
    client = Client()
    flow = EmailBriefing(client)
    flow.add([message()], account=client.account)
    assert flow.respond("yes") is None and client.calls == []


def test_account_change_rejects_stale_offer_without_fetching_body():
    flow, client, _ = offer()
    client.account = "another@example.test"
    with pytest.raises(GmailError, match="account changed"):
        flow.respond("yes")
    assert client.calls == [] and flow.awaiting is None and len(flow.pending) == 1


def test_cancel_and_failed_read_leave_message_pending_for_explicit_retry():
    flow, client, _ = offer()
    cancel = threading.Event(); cancel.set()
    with pytest.raises(GmailCancelled):
        flow.respond("yes", cancel)
    assert client.calls == [] and flow.awaiting["id"] == "one"
    client.fail = True
    with pytest.raises(GmailError, match="unavailable"):
        flow.respond("yes")
    assert len(flow.pending) == 1 and flow.awaiting["id"] == "one"
    client.fail = False
    assert flow.respond("read it")["email_handled"]["id"] == "one"


def test_long_or_attachment_only_mail_is_truthful_and_fits_speech_limit():
    flow, client, _ = offer()
    client.body = "Read email contents. " * 900
    result = flow.respond("yes")
    assert len(result["text"]) < 8000 and "partial reading" in result["text"]
    flow.add([message("two")], account=client.account)
    flow.offer_next(); client.body = ""
    assert "no readable text body" in flow.respond("yes")["text"]


def test_deduplication_persists_handled_ids_but_stale_consent_does_not_restore():
    flow, client, _ = offer()
    assert flow.add([message()], account=client.account) == 0
    restarted = EmailBriefing(client)
    restarted.restore_state(flow.export_state())
    assert restarted.awaiting is None and restarted.respond("yes") is None
    restarted.offer_next(); result = restarted.respond("no")
    restarted.commit_handled(result["email_handled"])
    again = EmailBriefing(client); again.restore_state(restarted.export_state())
    assert again.add([message()], account=client.account) == 0 and again.offer_next() is None


def test_sender_sanitizes_controls_and_uses_address_if_name_is_absent():
    assert sender_name("Alice\r\n\x00\u202e Smith <alice@example.test>") == "Alice Smith"
    assert sender_name("alice@example.test") == "alice@example.test"
    assert sender_name("") == "an unknown sender"


def test_persisted_headers_ignore_contents_and_unsafe_links():
    row = message()
    row["url"] = "javascript:steal()"
    saved = header_message(row, "person@example.test")
    assert saved["url"] == "" and "body" not in saved and "snippet" not in saved
    row["url"] = "https://mail.google.com:broken/"
    assert header_message(row)["url"] == ""
    result = inbox_digest(dict(batch_id="batch", account="person@example.test", messages=[message()]))
    assert result["gmail_messages"][0]["account"] == "person@example.test"
    assert "Private email" not in str(result)


def test_offer_filters_current_account_but_retains_other_account_headers():
    client = Client(); flow = EmailBriefing(client)
    flow.add([message("old", sender="Old account")], account="other@example.test")
    flow.add([message("current", sender="Current account")], account=client.account)
    assert "Current account" in flow.offer_next()["text"]
    flow.respond("no")
    assert flow.offer_next() is None and flow.pending[0]["id"] == "old"
    client.account = "other@example.test"
    assert "Old account" in flow.offer_next()["text"]


@pytest.mark.parametrize("state", [None, [], {"pending": None}, {"seen": "broken"},
                                    {"pending": [None]}, {"pending": [{"id": "invalid/id"}]},
                                    {"seen": [{"id": "invalid/id"}]}])
def test_malformed_state_does_not_replace_the_existing_queue(state):
    flow, _, _ = offer()
    before = flow.export_state()
    with pytest.raises(ValueError):
        flow.restore_state(state)
    assert flow.export_state() == before


def test_slow_read_does_not_block_queued_headers_and_stale_consent_cannot_finish():
    entered, resume = threading.Event(), threading.Event()
    client = Client(); original_read = client.read
    def read(message_id, *, cancel=None):
        entered.set()
        assert resume.wait(2)
        return original_read(message_id, cancel=cancel)
    client.read = read
    flow, _, _ = offer(client)
    errors = []
    def respond():
        try:
            flow.respond("yes")
        except GmailError as error:
            errors.append(str(error))
    worker = threading.Thread(target=respond); worker.start()
    try:
        assert entered.wait(1)
        flow.add([message("two")], account=client.account)
        assert len(flow.export_state()["pending"]) == 2
        flow.expire()
        flow.offer_next()
    finally:
        resume.set(); worker.join(timeout=2)
    assert errors and "no longer active" in errors[0]
    assert [row["id"] for row in flow.pending] == ["one", "two"]


def test_read_progress_is_durable_only_after_answer_delivery_is_committed():
    flow, client, _ = offer()
    result = flow.respond("yes")
    assert flow.pending == [] and flow.offer_next() is None
    # The Gmail poll loop may persist another incoming batch before it consumes
    # the assistant answer event. The first email must survive that save.
    flow.add([message("two")], account=client.account)
    state = flow.export_state()
    assert [row["id"] for row in state["pending"]] == ["one", "two"]
    assert "Private email" not in str(state)
    restarted = EmailBriefing(client); restarted.restore_state(state)
    assert restarted.offer_next()["email_offer"]["id"] == "one"
    assert flow.commit_handled(result["email_handled"])
    assert not flow.commit_handled(result["email_handled"])
    assert [row["id"] for row in flow.export_state()["pending"]] == ["two"]
    assert flow.offer_next()["email_offer"]["id"] == "two"


def test_decline_progress_also_waits_for_answer_delivery_and_ignores_wrong_marker():
    flow, client, _ = offer()
    result = flow.respond("no")
    assert not flow.commit_handled(dict(account="another@example.test", id="one"))
    assert flow.export_state()["pending"][0]["id"] == "one"
    assert flow.commit_handled(result["email_handled"])
    assert flow.export_state()["pending"] == []
