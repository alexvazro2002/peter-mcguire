"""Current city clocks and bounded, public-only internet evidence.

These functions never read NexusDesk notes, memories, email or credentials. Callers
must not turn retrieved private content into a web query. Remote text is evidence,
never executable code or an instruction to the Assistant.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from datetime import datetime, timezone
import ipaddress
import json
import math
import re
import socket
import ssl
import time
import unicodedata
from urllib.parse import parse_qsl, quote, urlencode, urljoin, urlsplit, urlunsplit
from xml.etree import ElementTree
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

from bs4 import BeautifulSoup
import urllib3


class LiveInfoError(ValueError):
    pass


_ALIASES = {}
for _city, _zone, _names in [
    ("Shanghai", "Asia/Shanghai", ("shanghai", "shang hai", "china shanghai", "shanghai china", "china", "上海", "中国")),
    ("Beijing", "Asia/Shanghai", ("beijing", "peking", "北京")),
    ("London", "Europe/London", ("london", "uk", "united kingdom", "britain")),
    ("Paris", "Europe/Paris", ("paris", "france")),
    ("Berlin", "Europe/Berlin", ("berlin", "germany")),
    ("Rome", "Europe/Rome", ("rome", "italy")),
    ("Madrid", "Europe/Madrid", ("madrid",)),
    ("New York", "America/New_York", ("new york", "nyc")),
    ("Los Angeles", "America/Los_Angeles", ("los angeles", "la")),
    ("Chicago", "America/Chicago", ("chicago",)),
    ("Denver", "America/Denver", ("denver",)),
    ("Toronto", "America/Toronto", ("toronto",)),
    ("Vancouver", "America/Vancouver", ("vancouver",)),
    ("Tokyo", "Asia/Tokyo", ("tokyo", "japan")),
    ("Seoul", "Asia/Seoul", ("seoul", "south korea")),
    ("Hong Kong", "Asia/Hong_Kong", ("hong kong",)),
    ("Singapore", "Asia/Singapore", ("singapore",)),
    ("Delhi", "Asia/Kolkata", ("delhi", "new delhi", "india")),
    ("Mumbai", "Asia/Kolkata", ("mumbai", "bombay")),
    ("Kolkata", "Asia/Kolkata", ("kolkata", "calcutta")),
    ("Dubai", "Asia/Dubai", ("dubai", "uae",)),
    ("Bangkok", "Asia/Bangkok", ("bangkok", "thailand")),
    ("Jakarta", "Asia/Jakarta", ("jakarta",)),
    ("Sydney", "Australia/Sydney", ("sydney",)),
    ("Melbourne", "Australia/Melbourne", ("melbourne",)),
    ("Perth", "Australia/Perth", ("perth",)),
    ("Auckland", "Pacific/Auckland", ("auckland", "new zealand")),
    ("Johannesburg", "Africa/Johannesburg", ("johannesburg", "south africa")),
    ("Cairo", "Africa/Cairo", ("cairo", "egypt")),
    ("Moscow", "Europe/Moscow", ("moscow",)),
    ("Istanbul", "Europe/Istanbul", ("istanbul", "turkey")),
    ("São Paulo", "America/Sao_Paulo", ("sao paulo", "são paulo")),
    ("Mexico City", "America/Mexico_City", ("mexico city",)),
    ("Buenos Aires", "America/Argentina/Buenos_Aires", ("buenos aires",)),
    ("UTC", "UTC", ("utc", "gmt")),
]:
    for _name in _names:
        _ALIASES[re.sub(r"[\W_]+", "", unicodedata.normalize("NFKC", _name).casefold())] = (_city, _zone)


def resolve_timezone(location):
    if not isinstance(location, str) or not location.strip() or len(location) > 100:
        raise LiveInfoError("Give one city, country with a single timezone, or an IANA timezone such as Asia/Shanghai.")
    location = location.strip()
    key = re.sub(r"[\W_]+", "", unicodedata.normalize("NFKC", location).casefold())
    if key in _ALIASES:
        city, zone = _ALIASES[key]
        return city, ZoneInfo(zone)
    if "/" in location:
        name = next((zone for zone in available_timezones() if zone.casefold() == location.casefold()), location)
        try:
            return name.rsplit("/", 1)[-1].replace("_", " "), ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError):
            pass
    raise LiveInfoError("That location is not recognised. Give a specific city or its IANA timezone, for example Asia/Shanghai.")


def current_time(location, *, now=None):
    city, zone = resolve_timezone(location)
    utc = now or datetime.now(timezone.utc)
    if utc.tzinfo is None or utc.utcoffset() is None:
        raise LiveInfoError("The clock must include a timezone.")
    utc = utc.astimezone(timezone.utc)
    local = utc.astimezone(zone)
    offset = local.strftime("%z")
    return {"city": city, "timezone": zone.key, "local_time": local.isoformat(timespec="seconds"),
            "date": local.date().isoformat(), "time": local.strftime("%H:%M:%S"),
            "utc_time": utc.isoformat(timespec="seconds"), "offset": offset[:3] + ":" + offset[3:],
            "source": "System UTC clock and IANA timezone database"}


_DNS = ThreadPoolExecutor(max_workers=2, thread_name_prefix="peter-public-dns")
_EMAIL = re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b")
_SECRET = re.compile(r"(?i)(?:bearer\s+[\w.~-]{8,}|sk-[\w-]{16,}|(?:password|api[_ -]?key|access[_ -]?token|secret)\s*[:=]\s*\S+)")
_PRIVATE_SUFFIXES = (".localhost", ".local", ".internal", ".lan", ".home", ".test", ".invalid")


def _public_ip(value):
    address = ipaddress.ip_address(value)
    return address.is_global and not address.is_multicast and not address.is_unspecified


def _normalise_url(url):
    if not isinstance(url, str) or len(url) > 2048 or re.search(r"[\x00-\x20\x7f]", url):
        raise LiveInfoError("Use a public HTTP or HTTPS URL without control characters or credentials.")
    try:
        parts = urlsplit(url)
        hostname = (parts.hostname or "").rstrip(".").encode("idna").decode("ascii").lower()
        port = parts.port or (443 if parts.scheme.lower() == "https" else 80)
    except (ValueError, UnicodeError):
        raise LiveInfoError("That URL is not valid.") from None
    if (parts.scheme.lower() not in {"http", "https"} or not hostname or parts.username is not None
            or parts.password is not None or port not in {80, 443}):
        raise LiveInfoError("Only public HTTP/HTTPS pages on standard web ports are allowed; URL credentials are not allowed.")
    try:
        if not _public_ip(hostname):
            raise LiveInfoError("Private, local, reserved and multicast addresses cannot be accessed by web tools.")
    except ValueError as error:
        if isinstance(error, LiveInfoError):
            raise
        if "." not in hostname or hostname.endswith(_PRIVATE_SUFFIXES):
            raise LiveInfoError("Local and internal hostnames cannot be accessed by web tools.") from None
    if any(key.casefold() in {"token", "access_token", "refresh_token", "id_token", "client_secret", "api_key", "apikey", "password", "secret", "authorization"}
           for key, _value in parse_qsl(parts.query, keep_blank_values=True)):
        raise LiveInfoError("Web tools do not send credential-bearing URLs.")
    authority = "[" + hostname + "]" if ":" in hostname else hostname
    if port != (443 if parts.scheme.lower() == "https" else 80):
        authority += ":" + str(port)
    path = quote(parts.path or "/", safe="/%:@!$&'()*+,;=-._~")
    query = quote(parts.query, safe="%=&/?+;:,@!$'()*-._~")
    return urlunsplit((parts.scheme.lower(), authority, path, query, ""))


def _resolve_public(hostname, port, seconds=3):
    try:
        address = ipaddress.ip_address(hostname)
        if not _public_ip(str(address)):
            raise LiveInfoError("The destination is not a public internet address.")
        return str(address)
    except ValueError as error:
        if isinstance(error, LiveInfoError):
            raise
    future = _DNS.submit(socket.getaddrinfo, hostname, port, 0, socket.SOCK_STREAM)
    try:
        rows = future.result(timeout=seconds)
    except (FutureTimeout, OSError):
        future.cancel()
        raise LiveInfoError("The public website's DNS lookup failed or timed out.") from None
    addresses = {row[4][0] for row in rows}
    if not addresses or any(not _public_ip(address) for address in addresses):
        raise LiveInfoError("The website resolves to a private or non-public address and was blocked.")
    return sorted(addresses, key=lambda address: (ipaddress.ip_address(address).version != 4, address))[0]


def _fetch_public(url, *, max_bytes=200_000, seconds=12):
    """Pin each connection to validated DNS; retain real hostname for TLS and Host."""
    deadline = time.monotonic() + seconds
    url = _normalise_url(url)
    for redirect in range(4):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise LiveInfoError("The public web request timed out.")
        parts = urlsplit(url)
        port = parts.port or (443 if parts.scheme == "https" else 80)
        address = _resolve_public(parts.hostname, port, min(3, remaining))
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise LiveInfoError("The public web request timed out.")
        options = {"host": address, "port": port, "timeout": urllib3.Timeout(connect=min(3, remaining), read=min(6, remaining))}
        if parts.scheme == "https":
            pool = urllib3.HTTPSConnectionPool(**options, server_hostname=parts.hostname,
                                             assert_hostname=parts.hostname, ssl_context=ssl.create_default_context())
        else:
            pool = urllib3.HTTPConnectionPool(**options)
        response = None
        try:
            target = parts.path + ("?" + parts.query if parts.query else "")
            response = pool.urlopen("GET", target, redirect=False, retries=False, preload_content=False,
                                    headers={"Host": parts.netloc, "User-Agent": "Peter-NexusDesk/1.0",
                                             "Accept-Encoding": "identity", "Accept": "text/html,application/xml,application/json,text/plain"})
            if response.status in {301, 302, 303, 307, 308}:
                destination = response.headers.get("Location")
                if not destination or redirect == 3:
                    raise LiveInfoError("The public page redirected too many times or omitted its destination.")
                url = _normalise_url(urljoin(url, destination))
                continue
            if response.status != 200:
                raise LiveInfoError("The public website returned HTTP " + str(response.status) + ".")
            if response.headers.get("Content-Encoding", "identity").lower() not in {"", "identity"}:
                raise LiveInfoError("The website returned unsupported compressed content.")
            try:
                declared = int(response.headers.get("Content-Length", "0"))
            except ValueError:
                declared = 0
            if declared > max_bytes:
                raise LiveInfoError("The public response exceeds the download limit.")
            chunks, count = [], 0
            while True:
                if time.monotonic() >= deadline:
                    raise LiveInfoError("The public web request timed out.")
                chunk = response.read(min(16384, max_bytes - count + 1), decode_content=False)
                if not chunk:
                    break
                chunks.append(chunk)
                count += len(chunk)
                if count > max_bytes:
                    raise LiveInfoError("The public response exceeds the download limit.")
            return b"".join(chunks), response.headers.get("Content-Type", ""), url
        except urllib3.exceptions.HTTPError:
            raise LiveInfoError("The public website could not be reached securely or timed out.") from None
        finally:
            if response is not None:
                response.close()
            pool.close()
    raise LiveInfoError("The public redirect limit was exceeded.")


def _stamp():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _public_query(query, maximum=300):
    if not isinstance(query, str) or not query.strip() or len(query) > maximum:
        raise LiveInfoError("Use a short, public search query.")
    if _EMAIL.search(query) or _SECRET.search(query) or re.search(r"[\x00-\x1f]", query):
        raise LiveInfoError("Do not send email addresses, credentials or private message contents to a public search provider.")
    return " ".join(query.split())


def _rss_items(body):
    if b"<!DOCTYPE" in body.upper() or b"<!ENTITY" in body.upper():
        raise LiveInfoError("The search feed contains unsupported XML declarations.")
    try:
        root = ElementTree.fromstring(body)
    except ElementTree.ParseError:
        raise LiveInfoError("The search provider did not return a valid RSS feed.") from None
    items = []
    for item in root.findall("./channel/item")[:30]:
        try:
            url = _normalise_url((item.findtext("link") or "").strip())
        except LiveInfoError:
            continue
        title = BeautifulSoup(item.findtext("title") or "", "html.parser").get_text(" ", strip=True)[:240]
        snippet = BeautifulSoup(item.findtext("description") or "", "html.parser").get_text(" ", strip=True)[:700]
        if title:
            items.append({"title": title, "url": url, "snippet": snippet,
                          "published": (item.findtext("pubDate") or "")[:100]})
    return items


def search_web(query, limit=5, offset=0):
    query = _public_query(query)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 8 or not isinstance(offset, int) or not 0 <= offset <= 30:
        raise LiveInfoError("Use a search limit from 1 to 8 and a valid feed offset.")
    providers = [("Bing RSS", "https://www.bing.com/search?" + urlencode({"q": query, "format": "rss"})),
                 ("Google News RSS", "https://news.google.com/rss/search?" + urlencode({"q": query, "hl": "en-GB", "gl": "GB", "ceid": "GB:en"}))]
    if re.search(r"\b(?:news|headlines?|breaking)\b|新闻", query, re.I):
        providers.reverse()
    for provider, url in providers:
        try:
            body, _kind, final_url = _fetch_public(url, max_bytes=180_000)
            items = _rss_items(body)
            if not items:
                continue
            return {"query": query, "provider": provider, "url": final_url, "items": items[offset:offset + limit],
                    "total": len(items), "next_offset": offset + limit if offset + limit < len(items) else None,
                    "scope": "This provider's current RSS result feed; not every result on the internet.",
                    "fetched_at": _stamp(), "untrusted": True}
        except LiveInfoError:
            continue
    raise LiveInfoError("Public search is unavailable or returned no results. No result was invented; retry a narrower public query.")


def read_web_page(url, max_chars=6000, offset=0):
    if isinstance(max_chars, bool) or not isinstance(max_chars, int) or not 200 <= max_chars <= 8000 or not isinstance(offset, int) or not 0 <= offset <= 1_000_000:
        raise LiveInfoError("Use a page size from 200 to 8,000 characters and a valid offset.")
    body, kind, final_url = _fetch_public(url)
    kind = kind.split(";", 1)[0].lower().strip()
    if kind not in {"text/html", "application/xhtml+xml", "text/plain", "application/json", "application/xml", "text/xml", "application/rss+xml", "application/atom+xml"}:
        raise LiveInfoError("Only public HTML and text pages can be read; binary files are not downloaded.")
    soup = BeautifulSoup(body, "html.parser")
    title = soup.title.get_text(" ", strip=True)[:240] if soup.title else urlsplit(final_url).hostname
    for element in soup(["script", "style", "noscript", "iframe", "object", "embed", "form"]):
        element.decompose()
    main = soup.find("main") or soup.find("article") or soup.body or soup
    text = "\n".join(re.sub(r"\s+", " ", line).strip() for line in main.get_text("\n", strip=True).splitlines())
    next_offset = offset + max_chars if offset + max_chars < len(text) else None
    return {"title": title, "url": final_url, "text": text[offset:offset + max_chars], "characters": len(text),
            "next_offset": next_offset, "truncated": next_offset is not None, "fetched_at": _stamp(), "untrusted": True}


def current_weather(city):
    city = _public_query(city, maximum=100)
    try:
        canonical, expected_zone = resolve_timezone(city)
        city = canonical
    except LiveInfoError:
        expected_zone = None
    geocode_url = "https://geocoding-api.open-meteo.com/v1/search?" + urlencode({"name": city, "count": 3, "language": "en", "format": "json"})
    body, _kind, _url = _fetch_public(geocode_url, max_bytes=40_000)
    try:
        candidates = json.loads(body).get("results", [])
        if not candidates:
            raise LiveInfoError("The weather provider could not find that city.")
        place = next((item for item in candidates if expected_zone and item.get("timezone") == expected_zone.key), candidates[0])
        latitude, longitude = float(place["latitude"]), float(place["longitude"])
        if not math.isfinite(latitude) or not math.isfinite(longitude) or not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
            raise ValueError("Invalid coordinates")
        url = "https://api.open-meteo.com/v1/forecast?" + urlencode({"latitude": latitude, "longitude": longitude,
            "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m", "timezone": "auto", "forecast_days": 1,
            "temperature_unit": "celsius", "wind_speed_unit": "kmh"})
        body, _kind, final_url = _fetch_public(url, max_bytes=40_000)
        data = json.loads(body)
        current = data["current"]
        return {"city": place["name"], "country": place.get("country", ""), "timezone": data["timezone"],
                "valid_at": current["time"], "temperature_c": current["temperature_2m"],
                "feels_like_c": current["apparent_temperature"], "weather_code": current["weather_code"],
                "wind_kmh": current["wind_speed_10m"], "provider": "Open-Meteo", "url": final_url,
                "geocoding_url": geocode_url, "kind": "Modelled current weather conditions", "fetched_at": _stamp()}
    except LiveInfoError:
        raise
    except (ValueError, KeyError, TypeError, IndexError):
        raise LiveInfoError("The weather provider returned incomplete or invalid data.") from None
