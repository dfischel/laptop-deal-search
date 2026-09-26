"""Polite HTTP: honest User-Agent, robots.txt checks, and a delay between requests to the same site."""

import re
import time
from urllib.parse import urlsplit

import requests


class RobotsDisallowed(Exception):
    """The site's robots.txt asks bots not to fetch this URL."""


def parse_robots(text: str) -> list[tuple[bool, re.Pattern, int]]:
    """Return the (allow, pattern, specificity) rules that apply to all bots (User-agent: *)."""
    rules = []
    agents: list[str] = []
    in_rules = False
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        field, value = (part.strip() for part in line.split(":", 1))
        field = field.lower()
        if field == "user-agent":
            if in_rules:  # a user-agent line after rules starts a new group
                agents, in_rules = [], False
            agents.append(value.lower())
        elif field in ("allow", "disallow"):
            in_rules = True
            if "*" in agents and value:
                rules.append((field == "allow", _robots_pattern(value), len(value)))
    return rules


def _robots_pattern(value: str) -> re.Pattern:
    anchored = value.endswith("$")
    if anchored:
        value = value[:-1]
    regex = ".*".join(re.escape(part) for part in value.split("*"))
    return re.compile(regex + ("$" if anchored else ""))


def is_allowed(rules: list[tuple[bool, re.Pattern, int]], path: str) -> bool:
    """Longest matching rule wins; Allow wins a tie (the standard robots.txt semantics)."""
    best: tuple[bool, int] | None = None
    for allow, pattern, length in rules:
        if pattern.match(path) and (best is None or length > best[1] or (length == best[1] and allow)):
            best = (allow, length)
    return True if best is None else best[0]


class PoliteSession:
    def __init__(self, user_agent: str, delay_seconds: float, timeout: float = 30):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": user_agent, "Accept-Language": "en-US,en;q=0.9"})
        self.delay = delay_seconds
        self.timeout = timeout
        self._last_request: dict[str, float] = {}
        self._robots: dict[str, list] = {}

    def get(self, url: str) -> requests.Response:
        parts = urlsplit(url)
        path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        if not is_allowed(self._robots_rules(parts.scheme, parts.netloc), path):
            raise RobotsDisallowed(url)
        self._wait(parts.netloc)
        response = self.session.get(url, timeout=self.timeout)
        response.raise_for_status()
        return response

    def _robots_rules(self, scheme: str, host: str) -> list:
        if host not in self._robots:
            self._wait(host)
            response = self.session.get(f"{scheme}://{host}/robots.txt", timeout=self.timeout)
            if response.status_code == 200:
                self._robots[host] = parse_robots(response.text)
            elif 400 <= response.status_code < 500:  # no robots.txt means no restrictions
                self._robots[host] = []
            else:  # server error: robots.txt status unknown, so don't crawl this run
                response.raise_for_status()
        return self._robots[host]

    def _wait(self, host: str) -> None:
        last = self._last_request.get(host)
        if last is not None:
            remaining = self.delay - (time.monotonic() - last)
            if remaining > 0:
                time.sleep(remaining)
        self._last_request[host] = time.monotonic()
