"""Bounded public discovery from independent skill catalogs.

Catalog metadata never authorizes installation. Skills still pass through the
repository/revision/file/digest review owned by the gateway installer.
"""

from __future__ import annotations

import asyncio
import copy
import json
import re
import time
from html.parser import HTMLParser
from typing import Any
from urllib.parse import quote, unquote, urlsplit

import httpx

MARKETS = {
    "skills.sh": {"id": "skills.sh", "name": "skills.sh", "url": "https://www.skills.sh"},
    "skillsmp": {"id": "skillsmp", "name": "SkillsMP", "url": "https://skillsmp.com"},
}
_SOURCE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}/[A-Za-z0-9][A-Za-z0-9_.-]{0,99}")
_SLUG = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,199}")
_MAX_BYTES = 2 * 1024 * 1024
_cache: dict[tuple[str, str, int], tuple[float, dict[str, Any]]] = {}
_pending: dict[tuple[asyncio.AbstractEventLoop, tuple[str, str, int]], asyncio.Task] = {}
_FEATURED = {
    "anthropics/skills/frontend-design": "Build interfaces with a clear visual direction.",
    "vercel-labs/agent-browser/agent-browser": "Give your agent a browser workflow.",
    "vercel-labs/agent-skills/web-design-guidelines": "Review an interface against practical design guidelines.",
    "anthropics/skills/skill-creator": "Create and improve reusable agent skills.",
    "obra/superpowers/systematic-debugging": "Investigate failures before changing code.",
    "vercel-labs/skills/find-skills": "Find reusable capabilities for the work ahead.",
}


def clear_cache() -> None:
    _cache.clear()


def _text(value: Any, maximum: int = 300) -> str:
    return " ".join(value.split())[:maximum] if isinstance(value, str) else ""


def _count(value: Any) -> int | None:
    return value if type(value) is int and 0 <= value <= 10**15 else None


def _skill(source: str, slug: str, name: str, market: str, count: Any, description=""):
    if not _SOURCE.fullmatch(source) or not _SLUG.fullmatch(slug) or not name:
        return None
    if any(part in (".", "..") for part in source.split("/")) or slug in (".", ".."):
        return None
    count = _count(count)
    return {
        "id": f"{source}/{slug}",
        "name": name,
        "source": source,
        "skillSlug": slug,
        "description": _text(description, 500),
        "repository": f"https://github.com/{source}",
        "url": f"https://skills.sh/{quote(source + '/' + slug, safe='/')}",
        "markets": [market],
        "metrics": []
        if count is None
        else [
            {
                "market": market,
                "label": "installs" if market == "skills.sh" else "GitHub stars",
                "value": count,
            }
        ],
    }


def _skills_sh(rows: list[Any], limit: int) -> list[dict[str, Any]]:
    result = []
    for row in rows[:500]:
        if not isinstance(row, dict):
            continue
        source = _text(row.get("source"), 202)
        slug = _text(row.get("skillId"), 200)
        identity = row.get("id")
        if not slug and isinstance(identity, str) and identity.startswith(source + "/"):
            slug = identity[len(source) + 1 :]
        item = _skill(source, slug, _text(row.get("name"), 200), "skills.sh", row.get("installs"))
        if item:
            result.append(item)
        if len(result) >= limit:
            break
    return result


def _skills_mp(rows: list[Any], limit: int) -> list[dict[str, Any]]:
    result = []
    for row in rows[:100]:
        if not isinstance(row, dict) or not isinstance(row.get("githubUrl"), str):
            continue
        try:
            url = urlsplit(row["githubUrl"])
        except ValueError:
            continue
        parts = unquote(url.path).strip("/").split("/")
        if (
            url.scheme != "https"
            or url.hostname != "github.com"
            or url.username
            or url.password
            or url.port not in (None, 443)
        ):
            continue
        if (
            len(parts) < 5
            or parts[2] not in ("tree", "blob")
            or any(part in (".", "..") or "\\" in part for part in parts)
        ):
            continue
        if parts[-1].lower() == "skill.md":
            parts.pop()
        if len(parts) < 5:
            continue
        source, slug = "/".join(parts[:2]), parts[-1]
        item = _skill(
            source,
            slug,
            _text(row.get("name"), 200),
            "skillsmp",
            row.get("stars"),
            row.get("description"),
        )
        if item:
            item["catalogUrl"] = "https://skillsmp.com"
            result.append(item)
        if len(result) >= limit:
            break
    return result


class _Leaderboard(HTMLParser):
    def __init__(self):
        super().__init__()
        self.script = False
        self.frames: list[str] = []

    def handle_starttag(self, tag, attrs):
        self.script = tag == "script"

    def handle_endtag(self, tag):
        if tag == "script":
            self.script = False

    def handle_data(self, data):
        if not self.script or not data.startswith("self.__next_f.push("):
            return
        # Parse JSON only, never evaluate scripts from a catalog page.
        try:
            frame, _ = json.JSONDecoder().raw_decode(data[len("self.__next_f.push(") :])
        except (ValueError, RecursionError):
            return
        if (
            isinstance(frame, list)
            and len(frame) == 2
            and frame[0] == 1
            and isinstance(frame[1], str)
        ):
            self.frames.append(frame[1])

    def skills(self):
        text = "".join(self.frames)
        marker = '"initialSkills":'
        position = text.find(marker)
        if position < 0:
            raise ValueError("leaderboard format changed")
        rows, _ = json.JSONDecoder().raw_decode(text[position + len(marker) :].lstrip())
        if not isinstance(rows, list):
            raise ValueError("leaderboard format changed")
        return rows


async def _read(url: str, params=None) -> bytes:
    async with httpx.AsyncClient(timeout=8, follow_redirects=False) as client:
        async with client.stream(
            "GET", url, params=params, headers={"User-Agent": "Machina-Desktop/0.1"}
        ) as response:
            response.raise_for_status()
            body = bytearray()
            async for chunk in response.aiter_bytes():
                if len(body) + len(chunk) > _MAX_BYTES:
                    raise ValueError("catalog response exceeds size limit")
                body.extend(chunk)
            return bytes(body)


async def _market(market: str, query: str, limit: int, discover: bool) -> dict[str, Any]:
    key = (market, "" if discover else query.casefold(), limit)
    cached = _cache.get(key)
    if cached and time.monotonic() - cached[0] < (900 if discover else 120):
        return copy.deepcopy(cached[1])
    flight = (asyncio.get_running_loop(), key)
    if flight in _pending:
        return copy.deepcopy(await asyncio.shield(_pending[flight]))

    async def load():
        status = {**MARKETS[market], "status": "ready"}
        try:
            if market == "skills.sh" and discover:
                parser = _Leaderboard()
                parser.feed((await _read("https://www.skills.sh/")).decode("utf-8"))
                skills = _skills_sh(parser.skills(), limit)
                if not skills:
                    raise ValueError("leaderboard has no supported GitHub skills")
            elif market == "skills.sh":
                payload = json.loads(
                    await _read("https://skills.sh/api/search", {"q": query, "limit": limit})
                )
                if not isinstance(payload, dict) or not isinstance(payload.get("skills"), list):
                    raise ValueError("unsupported search response")
                skills = _skills_sh(payload["skills"], limit)
            else:
                payload = json.loads(
                    await _read(
                        "https://skillsmp.com/api/v1/skills/search",
                        {
                            "q": "development" if discover else query,
                            "sortBy": "stars",
                            "limit": limit,
                        },
                    )
                )
                data = payload.get("data") if isinstance(payload, dict) else None
                if (
                    not isinstance(data, dict)
                    or not isinstance(data.get("skills"), list)
                    or payload.get("success") is not True
                ):
                    raise ValueError("unsupported search response")
                skills = _skills_mp(data["skills"], limit)
        except (httpx.HTTPError, ValueError, UnicodeError, RecursionError) as error:
            code = error.response.status_code if isinstance(error, httpx.HTTPStatusError) else None
            status.update(
                status="unavailable",
                error=f"{MARKETS[market]['name']} is unavailable"
                + (f" (HTTP {code})" if code else ". Try again shortly."),
            )
            return {"market": status, "skills": []}
        result = {"market": status, "skills": skills}
        while len(_cache) >= 64:
            _cache.pop(next(iter(_cache)))
        _cache[key] = (time.monotonic(), copy.deepcopy(result))
        return result

    task = asyncio.create_task(load())
    _pending[flight] = task
    try:
        return copy.deepcopy(await asyncio.shield(task))
    finally:
        if task.done():
            _pending.pop(flight, None)
        else:
            task.add_done_callback(lambda _: _pending.pop(flight, None))


def _merge(groups: list[list[dict[str, Any]]], limit: int) -> list[dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    # Alternate source ranks; stars and installations cannot be added or compared.
    for index in range(max((len(group) for group in groups), default=0)):
        for group in groups:
            if index >= len(group):
                continue
            item = group[index]
            key = item["id"].casefold()
            if key in result:
                current = result[key]
                current["markets"] = list(dict.fromkeys(current["markets"] + item["markets"]))
                current["metrics"] = list(
                    {
                        metric["market"]: metric for metric in current["metrics"] + item["metrics"]
                    }.values()
                )
                if not current["description"]:
                    current["description"] = item["description"]
            else:
                result[key] = copy.deepcopy(item)
    return list(result.values())[:limit]


async def search(query: str, limit: int = 30, markets: list[str] | None = None) -> dict[str, Any]:
    selected = markets or list(MARKETS)
    rows = await asyncio.gather(*(_market(market, query, limit, False) for market in selected))
    return {
        "query": query,
        "skills": _merge([row["skills"] for row in rows], limit),
        "markets": [row["market"] for row in rows],
    }


async def discover(limit: int = 12) -> dict[str, Any]:
    rows = await asyncio.gather(
        _market("skills.sh", "", 100, True), _market("skillsmp", "", limit, True)
    )
    merged = _merge([row["skills"] for row in rows], 150)
    by_id = {row["id"].casefold(): row for row in merged}
    featured = [
        {**by_id[key], "description": description}
        for key, description in _FEATURED.items()
        if key in by_id
    ][:3]
    return {
        "featured": featured,
        "sections": [
            {
                "market": market,
                "title": "Popular on skills.sh"
                if market == "skills.sh"
                else "Development on SkillsMP",
                "description": "Ranked by installations"
                if market == "skills.sh"
                else "Development skills, sorted by repository stars",
                "skills": _merge([row["skills"]], limit),
            }
            for market, row in zip(MARKETS, rows, strict=True)
        ],
        "markets": [row["market"] for row in rows],
    }
