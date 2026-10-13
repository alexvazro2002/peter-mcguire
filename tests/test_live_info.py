from datetime import datetime, timezone
import json
import socket

import pytest

from app import live_info


@pytest.mark.parametrize("location", ["china shang hai", "Shanghai", "Shanghai China", "China", "上海", "Asia/Shanghai"])
def test_shanghai_uses_actual_clock_timezone(location):
    now = datetime(2026, 10, 13, 23, 41, 20, tzinfo=timezone.utc)
    result = live_info.current_time(location, now=now)
    assert result["timezone"] == "Asia/Shanghai"
    assert result["local_time"] == "2026-10-14T07:41:20+08:00"
    assert result["utc_time"] == "2026-10-13T23:41:20+00:00"


def test_iana_timezones_apply_dst_and_unknown_locations_require_clarification():
    winter = live_info.current_time("London", now=datetime(2026, 1, 15, 12, tzinfo=timezone.utc))
    summer = live_info.current_time("London", now=datetime(2026, 7, 15, 12, tzinfo=timezone.utc))
    assert winter["offset"] == "+00:00" and summer["offset"] == "+01:00"
    assert live_info.current_time("america/new_york", now=datetime(2026, 7, 15, 12, tzinfo=timezone.utc))["time"] == "08:00:00"
    with pytest.raises(live_info.LiveInfoError, match="specific city"):
        live_info.current_time("Springfield")


def test_cities_sharing_timezone_keep_their_own_identity():
    assert live_info.resolve_timezone("Beijing")[0] == "Beijing"
    assert live_info.resolve_timezone("Mumbai")[0] == "Mumbai"
    assert live_info.resolve_timezone("Kolkata")[0] == "Kolkata"


@pytest.mark.parametrize("url", [
    "file:///etc/passwd", "http://localhost/", "https://printer.local/", "http://127.0.0.1/",
    "http://169.254.169.254/", "http://[::1]/", "http://[::ffff:127.0.0.1]/", "http://10.0.0.1/",
    "http://user:password@example.com/", "https://example.com:8080/", "https://example.com/?access_token=hidden",
    "https://example.com/\r\nX-Header:value",
])
def test_private_local_credential_and_nonweb_urls_are_blocked_before_dns(url, monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args: pytest.fail("Forbidden URL must not reach DNS"))
    with pytest.raises(live_info.LiveInfoError):
        live_info._fetch_public(url)


def test_mixed_public_private_dns_is_rejected(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a: [(None, None, None, None, ("93.184.216.34", 443)),
                                                         (None, None, None, None, ("127.0.0.1", 443))])
    with pytest.raises(live_info.LiveInfoError, match="private or non-public"):
        live_info._resolve_public("example.com", 443)


class Response:
    def __init__(self, body=b"page", status=200, headers=None):
        self.body, self.status = body, status
        self.headers = headers or {"Content-Type": "text/plain"}
    def read(self, size, **kwargs):
        result, self.body = self.body[:size], self.body[size:]
        return result
    def close(self): pass


def test_connection_is_pinned_to_public_ip_with_original_tls_hostname(monkeypatch):
    lookups, pools = [], []
    def lookup(*args):
        lookups.append(args)
        return [(None, None, None, None, ("93.184.216.34" if len(lookups) == 1 else "127.0.0.1", 443))]
    monkeypatch.setattr(socket, "getaddrinfo", lookup)
    class Pool:
        def __init__(self, **kwargs): pools.append(kwargs)
        def urlopen(self, method, target, **kwargs):
            assert kwargs["headers"]["Host"] == "example.com"
            assert kwargs["redirect"] is False and kwargs["retries"] is False
            return Response()
        def close(self): pass
    monkeypatch.setattr(live_info.urllib3, "HTTPSConnectionPool", Pool)
    body, _kind, url = live_info._fetch_public("https://example.com/page")
    assert body == b"page" and url == "https://example.com/page"
    assert len(lookups) == 1 and pools[0]["host"] == "93.184.216.34"
    assert pools[0]["server_hostname"] == pools[0]["assert_hostname"] == "example.com"


def test_redirect_to_private_destination_never_connects(monkeypatch):
    monkeypatch.setattr(live_info, "_resolve_public", lambda *a: "93.184.216.34")
    connections = []
    class Pool:
        def __init__(self, **kwargs): connections.append(kwargs["host"])
        def urlopen(self, *a, **kw): return Response(status=302, headers={"Location": "http://127.0.0.1/private"})
        def close(self): pass
    monkeypatch.setattr(live_info.urllib3, "HTTPSConnectionPool", Pool)
    with pytest.raises(live_info.LiveInfoError, match="Private"):
        live_info._fetch_public("https://example.com/")
    assert connections == ["93.184.216.34"]


def test_response_download_size_is_bounded(monkeypatch):
    monkeypatch.setattr(live_info, "_resolve_public", lambda *a: "93.184.216.34")
    class Pool:
        def __init__(self, **kwargs): pass
        def urlopen(self, *a, **kw): return Response(body=b"x" * 50)
        def close(self): pass
    monkeypatch.setattr(live_info.urllib3, "HTTPSConnectionPool", Pool)
    with pytest.raises(live_info.LiveInfoError, match="download limit"):
        live_info._fetch_public("https://example.com/", max_bytes=20)


def test_search_returns_fetched_rss_evidence_and_feed_pagination(monkeypatch):
    body = b'<rss><channel><item><title>Current item</title><link>https://example.com/news</link><description>Live &lt;b&gt;snippet&lt;/b&gt;</description></item><item><title>Second item</title><link>https://example.com/two</link></item></channel></rss>'
    queries = []
    def fetch(url, **kwargs):
        queries.append(url)
        return body, "text/xml", url
    monkeypatch.setattr(live_info, "_fetch_public", fetch)
    result = live_info.search_web("Shanghai city", limit=1)
    assert result["provider"] == "Bing RSS" and "q=Shanghai+city" in queries[0]
    assert result["items"] == [{"title": "Current item", "url": "https://example.com/news", "snippet": "Live snippet", "published": ""}]
    assert result["total"] == 2 and result["next_offset"] == 1 and result["untrusted"]
    assert live_info.search_web("Shanghai city", limit=1, offset=1)["items"][0]["title"] == "Second item"


def test_explicit_news_queries_prioritise_live_news_feed(monkeypatch):
    body = b'<rss><channel><item><title>Current headline</title><link>https://example.com/news</link></item></channel></rss>'
    urls = []
    monkeypatch.setattr(live_info, "_fetch_public", lambda url, **kw: (urls.append(url) or body, "text/xml", url))
    result = live_info.search_web("Shanghai latest news")
    assert result["provider"] == "Google News RSS" and urls[0].startswith("https://news.google.com/")


def test_search_failures_do_not_invent_results(monkeypatch):
    monkeypatch.setattr(live_info, "_fetch_public", lambda *a, **kw: (_ for _ in ()).throw(live_info.LiveInfoError("Unavailable")))
    with pytest.raises(live_info.LiveInfoError, match="No result was invented"):
        live_info.search_web("Shanghai news")


@pytest.mark.parametrize("query", ["person@example.com", "api_key=private-value", "Bearer secret-token-value", "private\nmessage"])
def test_obvious_private_or_credential_queries_never_leave_machine(query, monkeypatch):
    monkeypatch.setattr(live_info, "_fetch_public", lambda *a, **kw: pytest.fail("Private query must not be sent"))
    with pytest.raises(live_info.LiveInfoError):
        live_info.search_web(query)


def test_html_reader_strips_active_elements_and_paginates_plain_text(monkeypatch):
    body = ('<html><title>Public title</title><body><nav>Navigation</nav><main><script>do evil()</script>'
            '<p>' + 'Visible evidence ' * 100 + '</p></main></body></html>').encode()
    monkeypatch.setattr(live_info, "_fetch_public", lambda *a, **kw: (body, "text/html; charset=utf-8", "https://example.com/public"))
    first = live_info.read_web_page("https://example.com/public", max_chars=200)
    second = live_info.read_web_page("https://example.com/public", max_chars=200, offset=first["next_offset"])
    assert first["title"] == "Public title" and len(first["text"]) == 200
    assert "evil" not in first["text"] and "Navigation" not in first["text"]
    assert first["next_offset"] == 200 and second["next_offset"] == 400
    assert first["truncated"] and first["untrusted"]


def test_weather_uses_real_provider_payload_units_and_timezone(monkeypatch):
    calls = []
    def fetch(url, **kwargs):
        calls.append(url)
        if "geocoding-api" in url:
            data = {"results": [{"name": "Shanghai", "latitude": 31.22, "longitude": 121.46,
                                  "timezone": "Asia/Shanghai", "country": "China"}]}
        else:
            data = {"timezone": "Asia/Shanghai", "current": {"time": "2026-10-14T07:45", "temperature_2m": 20.3,
                "apparent_temperature": 21.0, "weather_code": 1, "wind_speed_10m": 8.2}}
        return json.dumps(data).encode(), "application/json", url
    monkeypatch.setattr(live_info, "_fetch_public", fetch)
    result = live_info.current_weather("china shang hai")
    assert "name=Shanghai" in calls[0] and "temperature_unit=celsius" in calls[1]
    assert result["temperature_c"] == 20.3 and result["timezone"] == "Asia/Shanghai"
    assert result["provider"] == "Open-Meteo" and result["kind"] == "Modelled current weather conditions"
