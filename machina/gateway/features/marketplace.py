"""Human marketplace controls over skills.sh and Machina's reviewed repo installer.

Search mirrors the public endpoint used by vercel-labs/skills src/find.ts.
The authenticated /api/v1 service is deliberately not assumed available on a
local desktop. Catalog results are untrusted discovery metadata. Installation
uses the existing bounded GitHub clone, scanner, digest and atomic installer;
no package manager, upstream hook or shell command is executed.
"""

from __future__ import annotations

import asyncio
import re
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

import httpx

from machina.core.background_tasks import await_joined
from machina.gateway.errors import (
    INVALID_PARAMS,
    NOT_FOUND,
    OPERATION_FAILED,
    UNAUTHORIZED,
    RpcError,
)
from machina.gateway.features._common import _int, _str
from machina.skills import repo_install

if TYPE_CHECKING:
    from machina.gateway.server import Gateway

_SOURCE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}/[A-Za-z0-9][A-Za-z0-9_.-]{0,99}")
_REVIEW_TTL = 15 * 60
_MAX_REVIEWS = 20
_MAX_SEARCH_BYTES = 512 * 1024


def _source(value: str) -> str:
    if not _SOURCE.fullmatch(value) or any(part in (".", "..") for part in value.split("/")):
        raise RpcError(INVALID_PARAMS, "source must be a GitHub owner/repository")
    return value


def _text(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


async def skills_marketplace_search(gw: Gateway, params: dict[str, Any]) -> dict[str, Any]:
    query = _str(params, "query", max_len=200)
    if len(query) < 2:
        raise RpcError(INVALID_PARAMS, "Enter at least two characters to search skill catalogs")
    limit = _int(params, "limit", 20, 1, 50)
    if "markets" in params:
        from machina.skills import catalog

        markets = params["markets"]
        if (
            not isinstance(markets, list)
            or not 1 <= len(markets) <= 2
            or any(not isinstance(value, str) or value not in catalog.MARKETS for value in markets)
        ):
            raise RpcError(INVALID_PARAMS, "Choose skills.sh, skillsmp, or both catalogs")
        return await catalog.search(query, limit, list(dict.fromkeys(markets)))
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
            async with client.stream(
                "GET", "https://skills.sh/api/search", params={"q": query, "limit": limit}
            ) as response:
                response.raise_for_status()
                chunks = bytearray()
                async for chunk in response.aiter_bytes():
                    chunks.extend(chunk)
                    if len(chunks) > _MAX_SEARCH_BYTES:
                        raise ValueError("catalog response exceeds size limit")
        import json

        data = json.loads(chunks)
        if not isinstance(data, dict) or not isinstance(data.get("skills"), list):
            raise ValueError("catalog returned an unsupported response")
    except (httpx.HTTPError, ValueError) as exc:
        raise RpcError(
            OPERATION_FAILED, f"skills.sh is unavailable ({type(exc).__name__}). Retry the search."
        ) from None
    skills = []
    for item in data["skills"][:limit]:
        if not isinstance(item, dict):
            continue
        source = item.get("source")
        if not isinstance(source, str) or not _SOURCE.fullmatch(source):
            continue
        name = _text(item.get("name"), 200)
        slug = _text(item.get("id"), 500)
        if not name or not slug.startswith(source + "/"):
            continue
        installs = item.get("installs", 0)
        skills.append(
            {
                "id": slug,
                "name": name,
                "source": source,
                "installs": installs if type(installs) is int and installs >= 0 else 0,
                "repository": f"https://github.com/{source}",
                "url": f"https://skills.sh/{quote(slug, safe='/')}",
            }
        )
    return {
        "provider": "skills.sh",
        "sourceUrl": "https://skills.sh",
        "query": query,
        "skills": skills,
        "scope": "installation",
    }


async def skills_marketplace_discover(gw: Gateway, params: dict[str, Any]) -> dict[str, Any]:
    from machina.skills import catalog

    return await catalog.discover(_int(params, "limit", 12, 1, 24))


def _stage_sync(source: str, requested: str) -> tuple[dict[str, Any], Path]:
    parsed = repo_install.parse_repo_url(f"https://github.com/{source}")
    repo, revision = repo_install.clone_repo(parsed)
    try:
        plan = repo_install.scan_repo(repo, parsed, revision)
        # The marketplace slug normally equals SKILL.md's name. Match the
        # directory too, since display names can differ from their slug.
        wanted = requested.casefold()
        selected = [
            skill
            for skill in plan.skills
            if skill.name.casefold() == wanted or Path(skill.root).name.casefold() == wanted
        ]
        if len(selected) != 1:
            raise repo_install.RepoInstallError(
                "The selected skill was not found unambiguously in this repository. It may have moved or failed validation."
            )
        plan.skills = selected
        plan.digest = repo_install._digest_plan(plan)
        summary = plan.summary()
        for skill, public in zip(plan.skills, summary["skills"], strict=True):
            skill_file = next(
                (
                    name
                    for name in skill.files
                    if Path(name).name.casefold() == "skill.md"
                    and str(Path(name).parent).replace("\\", "/").removeprefix("./")
                    == (skill.root or ".")
                ),
                None,
            )
            if skill_file is None:
                raise repo_install.RepoInstallError("The selected skill has no readable SKILL.md")
            path = repo_install._confined(repo, skill_file)
            public["content"] = repo_install._read_bounded(path).decode("utf-8")
            public["files"] = [
                {"path": name, "sha256": digest} for name, digest in sorted(skill.files.items())
            ]
        staged = repo_install.write_staged_plan(plan, repo)
        return summary, staged
    finally:
        # Git objects are read-only on Windows. Clean up only the clone's
        # verified temporary parent, without following repository symlinks.
        try:
            import tempfile

            parent = repo.parent
            if parent.name.startswith("machina-repo-stage-"):
                repo_install._remove_owned_tree(parent, Path(tempfile.gettempdir()))
        except OSError:
            pass


async def _stage(source: str, requested: str) -> tuple[dict[str, Any], Path]:
    return await await_joined(asyncio.to_thread(_stage_sync, source, requested))


def _reviews(gw: Gateway) -> dict[str, dict[str, Any]]:
    reviews = getattr(gw, "_desktop_skill_reviews", None)
    if reviews is None:
        reviews = {}
        gw._desktop_skill_reviews = reviews
    now = time.monotonic()
    for key in list(reviews):
        if reviews[key]["expires"] < now:
            reviews.pop(key, None)
    return reviews


async def skills_marketplace_preview(gw: Gateway, params: dict[str, Any]) -> dict[str, Any]:
    gw.require_db()
    source = _source(_str(params, "source", max_len=201))
    requested = _str(params, "skill", max_len=200)
    session_id = _str(params, "sessionId", required=False, max_len=64)
    session = await gw.get_session(params) if session_id else None
    reviews = _reviews(gw)
    if len(reviews) >= _MAX_REVIEWS:
        # Evict the oldest review, with a clear expiry error if revisited.
        reviews.pop(next(iter(reviews)))
    try:
        summary, staged = await _stage(source, requested)
    except (repo_install.RepoInstallError, OSError, UnicodeError) as exc:
        message = (
            str(exc)
            if isinstance(exc, repo_install.RepoInstallError)
            else "Repository files could not be read"
        )
        raise RpcError(OPERATION_FAILED, message) from None
    review_id = str(uuid.uuid4())
    reviews[review_id] = {
        "session": str(session.id) if session else None,
        "agent": session.agent_id if session else None,
        "digest": summary["digest"],
        "staged": staged,
        "expires": time.monotonic() + _REVIEW_TTL,
    }
    return {
        **summary,
        "reviewId": review_id,
        "sessionId": str(session.id) if session else None,
        "expiresInSeconds": _REVIEW_TTL,
        "scope": "installation",
        "provider": "skills.sh",
    }


async def _activate(gw: Gateway, digest: str, staged: Path) -> dict[str, Any]:
    from machina.gateway.features.skills import _generate_installed_metadata, _registry

    detail, current_staged = await asyncio.to_thread(repo_install.load_staged_plan, digest)
    if current_staged.resolve() != staged.resolve():
        raise repo_install.RepoInstallError("The reviewed repository changed. Review it again.")
    registry = await asyncio.to_thread(_registry)
    installed = await await_joined(
        asyncio.to_thread(repo_install.activate_plan, detail, staged, registry.skills_dir),
        capture_result=True,
    )
    await asyncio.to_thread(registry.load_all, refresh=True)
    outcomes = []
    for name in installed:
        skill = registry.get(name)
        result = (
            await _generate_installed_metadata(gw, skill)
            if skill
            else {
                "usable": False,
                "metadataGenerated": False,
                "metadataError": "Skill not found after installation",
            }
        )
        outcomes.append({"name": name, **result})
    return {
        "installed": installed,
        "outcomes": outcomes,
        "usable": bool(installed) and all(row["usable"] for row in outcomes),
        "scope": "installation",
    }


async def skills_marketplace_install(gw: Gateway, params: dict[str, Any]) -> dict[str, Any]:
    gw.require_db()
    review_id = _str(params, "reviewId", max_len=64)
    digest = _str(params, "digest", max_len=64)
    reviews = _reviews(gw)
    review = reviews.get(review_id)
    if review is None:
        raise RpcError(NOT_FOUND, "Skill review expired or was already installed. Review it again.")
    session_id = _str(params, "sessionId", required=False, max_len=64)
    if review["session"] is not None:
        if not session_id:
            raise RpcError(UNAUTHORIZED, "Skill review belongs to another conversation or agent")
        session = await gw.get_session(params)
        if review["session"] != str(session.id) or review["agent"] != session.agent_id:
            raise RpcError(UNAUTHORIZED, "Skill review belongs to another conversation or agent")
    elif session_id:
        raise RpcError(UNAUTHORIZED, "Review this skill again in the current conversation")
    # Installation reviews remain local to this authenticated Gateway instance.
    # They neither manufacture a conversation nor transfer agent-owned consent.
    if params.get("confirmed") is not True or digest != review["digest"]:
        raise RpcError(
            INVALID_PARAMS, "Confirm the exact reviewed skill digest before installation"
        )
    # Consume before awaiting so concurrent install clicks cannot reuse consent.
    reviews.pop(review_id)
    try:
        return await _activate(gw, digest, review["staged"])
    except (repo_install.RepoInstallError, OSError) as exc:
        message = (
            str(exc)
            if isinstance(exc, repo_install.RepoInstallError)
            else "Skill installation failed (filesystem error)"
        )
        raise RpcError(OPERATION_FAILED, message) from None
