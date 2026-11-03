"""Public multi-market discovery, source provenance and bounded caching."""

import json

import httpx
import pytest


def leaderboard(rows):
    frame = json.dumps([1, '1:{"initialSkills":' + json.dumps(rows) + "}"])
    return "<html><script>self.__next_f.push(" + frame + ")</script></html>"


@pytest.fixture
def catalog_client(monkeypatch):
    from machina.skills import catalog

    catalog.clear_cache()
    requests = []
    responses = {}

    def respond(request):
        requests.append(request)
        value = responses.get(request.url.host)
        return value(request) if callable(value) else value or httpx.Response(503)

    client = httpx.AsyncClient
    monkeypatch.setattr(
        catalog.httpx,
        "AsyncClient",
        lambda **kw: client(transport=httpx.MockTransport(respond), **kw),
    )
    return catalog, responses, requests


@pytest.mark.asyncio
async def test_discovery_loads_real_leaderboard_and_second_market_without_search(catalog_client):
    catalog, responses, requests = catalog_client
    responses["www.skills.sh"] = httpx.Response(
        200,
        text=leaderboard(
            [
                {
                    "source": "anthropics/skills",
                    "skillId": "frontend-design",
                    "name": "frontend-design",
                    "installs": 1234,
                },
                {"source": "owner/tools", "skillId": "testing", "name": "testing", "installs": 500},
            ]
        ),
    )
    responses["skillsmp.com"] = httpx.Response(
        200,
        json={
            "success": True,
            "data": {
                "skills": [
                    {
                        "name": "debugging",
                        "githubUrl": "https://github.com/other/skills/tree/main/skills/debugging",
                        "stars": 77,
                        "description": "Investigate failures",
                    }
                ]
            },
        },
    )
    result = await catalog.discover()
    assert {row["market"] for row in result["sections"]} == {"skills.sh", "skillsmp"}
    assert result["featured"][0]["name"] == "frontend-design"
    assert result["sections"][0]["skills"][0]["metrics"] == [
        {"market": "skills.sh", "label": "installs", "value": 1234}
    ]
    other = result["sections"][1]["skills"][0]
    assert other["source"] == "other/skills"
    assert other["skillSlug"] == "debugging"
    assert other["metrics"] == [{"market": "skillsmp", "label": "GitHub stars", "value": 77}]
    assert len(requests) == 2


@pytest.mark.asyncio
async def test_search_merges_same_github_skill_without_adding_unrelated_metrics(catalog_client):
    catalog, responses, _ = catalog_client
    responses["skills.sh"] = httpx.Response(
        200,
        json={
            "skills": [
                {
                    "id": "owner/tools/testing",
                    "source": "owner/tools",
                    "name": "Testing",
                    "installs": 100,
                }
            ]
        },
    )
    responses["skillsmp.com"] = httpx.Response(
        200,
        json={
            "success": True,
            "data": {
                "skills": [
                    {
                        "name": "Testing",
                        "githubUrl": "https://github.com/owner/tools/tree/main/skills/testing",
                        "stars": 12,
                        "description": "Test changes",
                    }
                ]
            },
        },
    )
    result = await catalog.search("testing")
    assert len(result["skills"]) == 1
    skill = result["skills"][0]
    assert skill["markets"] == ["skills.sh", "skillsmp"]
    assert {metric["label"]: metric["value"] for metric in skill["metrics"]} == {
        "installs": 100,
        "GitHub stars": 12,
    }
    assert skill["description"] == "Test changes"


@pytest.mark.asyncio
async def test_one_unavailable_market_keeps_other_results_and_reports_failure(catalog_client):
    catalog, responses, _ = catalog_client
    responses["skills.sh"] = httpx.Response(
        200,
        json={
            "skills": [
                {
                    "id": "owner/tools/testing",
                    "source": "owner/tools",
                    "name": "Testing",
                    "installs": 10,
                }
            ]
        },
    )
    responses["skillsmp.com"] = httpx.Response(429)
    result = await catalog.search("testing")
    assert result["skills"][0]["name"] == "Testing"
    assert (
        next(row for row in result["markets"] if row["id"] == "skillsmp")["status"] == "unavailable"
    )
    assert "429" in next(row for row in result["markets"] if row["id"] == "skillsmp")["error"]


@pytest.mark.asyncio
async def test_unchanged_queries_use_cache_and_do_not_mutate_cached_metadata(catalog_client):
    catalog, responses, requests = catalog_client
    responses["skills.sh"] = httpx.Response(200, json={"skills": []})
    responses["skillsmp.com"] = httpx.Response(200, json={"success": True, "data": {"skills": []}})
    first = await catalog.search("testing")
    first["markets"].clear()
    second = await catalog.search("testing")
    assert len(requests) == 2
    assert len(second["markets"]) == 2
    await catalog.search("design")
    assert len(requests) == 4


@pytest.mark.asyncio
async def test_untrusted_sources_and_fake_counts_are_not_passed_to_installer(catalog_client):
    catalog, responses, _ = catalog_client
    responses["skills.sh"] = httpx.Response(
        200,
        json={
            "skills": [
                {"id": "safe/repo/good", "source": "safe/repo", "name": "good", "installs": True},
                {"id": "bad", "source": "https://evil.test/repo", "name": "bad", "installs": 10},
            ]
        },
    )
    responses["skillsmp.com"] = httpx.Response(
        200,
        json={
            "success": True,
            "data": {
                "skills": [
                    {
                        "name": "bad",
                        "githubUrl": "https://github.com.evil.test/owner/repo/tree/main/bad",
                        "stars": 99,
                    },
                    {
                        "name": "bad",
                        "githubUrl": "https://github.com/owner/repo/tree/main/%2e%2e/bad",
                        "stars": 99,
                    },
                ]
            },
        },
    )
    result = await catalog.search("testing")
    assert len(result["skills"]) == 1
    assert result["skills"][0]["metrics"] == []


@pytest.mark.asyncio
async def test_bad_response_shape_is_an_unavailable_source_not_an_empty_success(catalog_client):
    catalog, responses, _ = catalog_client
    responses["skills.sh"] = httpx.Response(200, json={"unexpected": []})
    responses["skillsmp.com"] = httpx.Response(
        200, json={"success": False, "error": "upstream unavailable"}
    )
    result = await catalog.search("testing")
    assert result["skills"] == []
    assert all(row["status"] == "unavailable" for row in result["markets"])


@pytest.mark.asyncio
async def test_concurrent_queries_share_the_same_public_requests(catalog_client):
    import asyncio

    catalog, responses, requests = catalog_client
    responses["skills.sh"] = httpx.Response(200, json={"skills": []})
    responses["skillsmp.com"] = httpx.Response(200, json={"success": True, "data": {"skills": []}})
    first, second = await asyncio.gather(catalog.search("testing"), catalog.search("testing"))
    assert first == second
    assert len(requests) == 2


@pytest.mark.asyncio
async def test_oversized_catalog_is_rejected_without_losing_the_other_source(
    catalog_client, monkeypatch
):
    catalog, responses, _ = catalog_client
    monkeypatch.setattr(catalog, "_MAX_BYTES", 256)
    responses["skills.sh"] = httpx.Response(200, content=b"x" * 300)
    responses["skillsmp.com"] = httpx.Response(200, json={"success": True, "data": {"skills": []}})
    result = await catalog.search("testing")
    assert result["markets"][0]["status"] == "unavailable"
    assert result["markets"][1]["status"] == "ready"
