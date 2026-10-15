"""Participant metadata and truthful, bounded read-only attachment fetching."""
import base64
import json
import threading

import pytest

from app.gmail import API_URL, SCOPE, GmailCancelled, GmailClient, GmailError, message_data


def encoded(value):
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def message(payload):
    return dict(id="mail1", internalDate="123", payload=payload)


def file_part(name="invoice.pdf", *, mime="application/pdf", attachment_id="file_1", data=None):
    body = {"size": 3}
    if attachment_id is not None:
        body["attachmentId"] = attachment_id
    if data is not None:
        body["data"] = encoded(data)
        body["size"] = len(data)
    return dict(partId="1", filename=name, mimeType=mime, body=body)


def test_metadata_exposes_participants_but_cannot_confirm_no_attachments():
    payload = dict(mimeType="multipart/mixed", headers=[
        {"name": "From", "value": "José Angel <jose@example.test>"},
        {"name": "To", "value": "Reader <reader@example.test>"},
        {"name": "Cc", "value": "JoseAngel <other@example.test>"}], parts=[file_part()])
    result = message_data(message(payload), account="reader@example.test", headers_only=True)
    assert result["cc"] == "JoseAngel <other@example.test>"
    assert result["to"] == "Reader <reader@example.test>" and result["account"] == "reader@example.test"
    assert result["attachments"] == [] and not result["attachment_inventory_complete"]
    assert not result["attachment_inventory_truncated"] and result["metadata_only"]


def test_nested_files_inventory_preserves_file_names_without_treating_them_as_paths():
    payload = dict(mimeType="multipart/mixed", parts=[
        dict(mimeType="multipart/alternative", parts=[
            dict(mimeType="text/plain", body={"data": encoded(b"Visible body")}),
            dict(mimeType="text/html", body={"data": encoded(b"<p>Visible body</p>")})]),
        file_part("../../private/invoice.pdf"),
        file_part("notes.txt", mime="text/plain", attachment_id=None, data=b"Attached text"),
        file_part("", mime="image/png", attachment_id="inline_image")])
    result = message_data(message(payload))
    assert result["body"] == "Visible body"
    assert result["attachment_inventory_complete"]
    assert len(result["attachments"]) == 3
    assert result["attachments"][0]["filename"] == "../../private/invoice.pdf"
    assert result["attachments"][1]["inline_data"] == encoded(b"Attached text")
    assert result["attachments"][2]["attachment_id"] == "inline_image"
    assert "Attached text" not in result["body"]


def test_attached_message_text_is_not_attributed_to_the_main_email_body():
    payload = dict(mimeType="multipart/mixed", parts=[
        dict(mimeType="text/plain", body={"data": encoded(b"Outer email")}),
        dict(mimeType="message/rfc822", filename="forward.eml", parts=[
            dict(mimeType="multipart/mixed", parts=[
                dict(mimeType="text/plain", body={"data": encoded(b"Attached email's claims")}),
                file_part("nested.pdf")])])])
    result = message_data(message(payload))
    assert result["body"] == "Outer email"
    assert [entry["filename"] for entry in result["attachments"]] == ["forward.eml", "nested.pdf"]
    assert result["attachment_inventory_complete"]


def test_long_cc_headers_keep_names_past_the_old_limit_and_flag_true_truncation():
    prefix = ", ".join(f"Reader {number} <reader{number}@example.test>" for number in range(80))
    payload = dict(mimeType="text/plain", headers=[
        {"name": "Cc", "value": prefix + ", José Angel <jose@example.test>"}])
    result = message_data(message(payload), headers_only=True)
    assert len(result["cc"]) > 1000 and "José Angel" in result["cc"]
    assert result["participant_headers_complete"] and result["recipient_headers_complete"]
    payload["headers"][0]["value"] = "a" * 16001
    result = message_data(message(payload), headers_only=True)
    assert len(result["cc"]) == 16000
    assert not result["participant_headers_complete"] and not result["recipient_headers_complete"]


def test_repeated_cc_headers_are_combined_and_from_truncation_is_truthful():
    payload = dict(mimeType="text/plain", headers=[
        {"name": "From", "value": "x" * 16001},
        {"name": "Cc", "value": "Alice <alice@example.test>"},
        {"name": "Cc", "value": "Jose Angel <jose@example.test>"}])
    result = message_data(message(payload), headers_only=True)
    assert result["cc"] == "Alice <alice@example.test>, Jose Angel <jose@example.test>"
    assert not result["participant_headers_complete"] and result["recipient_headers_complete"]


def test_full_message_without_files_can_confirm_empty_inventory():
    result = message_data(message(dict(mimeType="text/plain", body={"data": encoded(b"Hello")})))
    assert result["attachments"] == [] and result["attachment_inventory_complete"]
    missing_payload = message_data(dict(id="mail1"))
    assert not missing_payload["attachment_inventory_complete"]


def test_large_external_body_is_not_mistaken_for_a_file_attachment():
    result = message_data(message(dict(mimeType="text/plain", body={"attachmentId": "body1", "size": 50000})))
    assert result["attachments"] == [] and result["attachment_inventory_complete"]
    assert result["body_unavailable"] and result["body_truncated"]


def test_mime_child_limit_does_not_claim_complete_inventory():
    result = message_data(message(dict(mimeType="multipart/mixed", parts=[file_part()] * 101)))
    assert len(result["attachments"]) == 100
    assert not result["attachment_inventory_complete"] and result["attachment_inventory_truncated"]


def test_mime_depth_limit_does_not_claim_no_attachments():
    payload = file_part()
    for _ in range(22):
        payload = dict(mimeType="multipart/mixed", parts=[payload])
    result = message_data(message(payload))
    assert result["attachments"] == [] and not result["attachment_inventory_complete"]


@pytest.mark.parametrize("children", [None, ["not a MIME part"]])
def test_invalid_mime_parts_make_inventory_incomplete(children):
    result = message_data(message(dict(mimeType="multipart/mixed", parts=children)))
    assert not result["attachment_inventory_complete"]


def test_invalid_attachment_id_and_oversized_inline_data_are_reported(monkeypatch):
    monkeypatch.setattr("app.gmail.MAX_INLINE_ATTACHMENT_BYTES", 2)
    result = message_data(message(dict(mimeType="multipart/mixed", parts=[
        file_part(attachment_id="../../escape"), file_part(attachment_id=None, data=b"abc")])))
    assert not result["attachment_inventory_complete"]
    assert result["attachments"][0]["attachment_id"] == ""
    assert result["attachments"][1]["inline_data"] == ""
    assert result["attachments"][1]["inline_data_truncated"]


class Response:
    def __init__(self, value):
        self.value, self.status_code, self.closed = value, 200, False

    def iter_content(self, size):
        value = json.dumps(self.value).encode()
        for offset in range(0, len(value), size):
            yield value[offset:offset + size]

    def close(self):
        self.closed = True


@pytest.fixture
def attachment_client(tmp_path, monkeypatch):
    calls, result = [], {"size": 3, "data": encoded(b"PDF")}
    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def request(self, method, url, **kwargs):
            calls.append((method, url, kwargs))
            if url.endswith("/profile"):
                return Response(dict(emailAddress="reader@example.test", historyId="1"))
            if "/attachments/" in url:
                return Response(result)
            return Response(message(dict(mimeType="text/plain")))
    client = GmailClient(root=tmp_path / "private", session_factory=Session)
    monkeypatch.setattr(client, "_access", lambda *args, **kwargs: "synthetic-access")
    monkeypatch.setattr(client, "_credentials", lambda: ({}, {"account": "reader@example.test", "scope": SCOPE}))
    return client, calls, result


def test_attachment_read_only_get_has_verified_account_and_no_file_write(attachment_client):
    client, calls, _ = attachment_client
    assert client.read_attachment("mail1", "file_1") == b"PDF"
    assert [url for _, url, _ in calls] == [API_URL + "/profile", API_URL + "/messages/mail1/attachments/file_1"]
    assert all(method == "GET" for method, _, _ in calls)
    assert all(options["allow_redirects"] is False for _, _, options in calls)
    assert not client.root.exists()


def test_header_requests_include_to_and_cc(attachment_client):
    client, calls, _ = attachment_client
    with client._session_factory() as session:
        client._read(session, "mail1", None, headers_only=True)
    assert calls[-1][2]["params"]["metadataHeaders"] == ["From", "To", "Cc", "Subject", "Date"]


@pytest.mark.parametrize("message_id,attachment_id", [("../../other", "ok"), ("ok", "../bad"), ("ok", "a?b"), ("ok", "x" * 2049)])
def test_attachment_url_ids_are_validated_before_network(attachment_client, message_id, attachment_id):
    client, calls, _ = attachment_client
    with pytest.raises(GmailError, match="Invalid Gmail"):
        client.read_attachment(message_id, attachment_id)
    assert calls == []


@pytest.mark.parametrize("limit", [0, True, -1, 20 * 1024 * 1024 + 1])
def test_attachment_download_limit_is_bounded(attachment_client, limit):
    client, calls, _ = attachment_client
    with pytest.raises(GmailError, match="size limit"):
        client.read_attachment("mail1", "file1", max_bytes=limit)
    assert calls == []


@pytest.mark.parametrize("payload", [{"size": 21, "data": ""}, {"size": 1, "data": encoded(b"abc")},
    {"size": 3, "data": "a?c!"}, {"size": -1, "data": ""}, {"size": "3", "data": encoded(b"abc")},
    {"size": 0}, {"size": 3, "data": "a"}])
def test_invalid_oversized_or_incomplete_download_is_rejected(attachment_client, payload):
    client, _, result = attachment_client
    result.clear(); result.update(payload)
    with pytest.raises(GmailError):
        client.read_attachment("mail1", "file1", max_bytes=20)


def test_attachment_account_mismatch_is_rejected_before_download(attachment_client, monkeypatch):
    client, calls, _ = attachment_client
    monkeypatch.setattr(client, "_credentials", lambda: ({}, {"account": "other@example.test"}))
    with pytest.raises(GmailError, match="account changed"):
        client.read_attachment("mail1", "file1")
    assert len(calls) == 1 and calls[0][1].endswith("/profile")


def test_attachment_cancellation_prevents_get_and_download(attachment_client):
    client, calls, _ = attachment_client
    cancel = threading.Event(); cancel.set()
    with pytest.raises(GmailCancelled):
        client.read_attachment("mail1", "file1", cancel=cancel)
    assert calls == []
