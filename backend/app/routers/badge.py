import logging
import re
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from opentelemetry import trace
from fastapi.responses import RedirectResponse
from redis import RedisError
from redis.asyncio import Redis

from app.constants import BADGE_CACHE_KEY
from app.dependencies.gh_client import get_github_client
from app.utils.github_client import GithubClient
from app.schemas import GhCommitStatus
from app.dependencies.redis_client import get_redis_client
from app.telemetry import BADGE_CACHE_LOOKUPS, BADGE_RENDERED, tracer

logger = logging.getLogger(__name__)

COV_RE = re.compile(r"([\d.]+)%")


router = APIRouter()

BADGE_SVG = """\
<svg xmlns="http://www.w3.org/2000/svg" width="110" height="20">
  <title>coverage: {cov}</title>
  <defs>
    <linearGradient id="workflow-fill" x1="50%" y1="0%" x2="50%" y2="100%">
      <stop stop-color="#444D56" offset="0%"/>
      <stop stop-color="#24292E" offset="100%"/>
    </linearGradient>
    <linearGradient id="state-fill" x1="50%" y1="0%" x2="50%" y2="100%">
      <stop stop-color="{color_top}" offset="0%"/>
      <stop stop-color="{color_bot}" offset="100%"/>
    </linearGradient>
  </defs>
  <g font-family="DejaVu Sans,Verdana,Geneva,sans-serif" font-size="11">
    <path d="M0,3 C0,1.3431 1.3552,0 3.02702703,0 L70,0 L70,20 L3.02702703,20 C1.3552,20 0,18.6569 0,17 L0,3 Z" fill="url(#workflow-fill)"/>
    <text fill="#010101" fill-opacity=".3">
      <tspan x="10" y="15">coverage</tspan>
    </text>
    <text fill="#FFF">
      <tspan x="10" y="14">coverage</tspan>
    </text>
  </g>
  <g transform="translate(70)" font-family="DejaVu Sans,Verdana,Geneva,sans-serif" font-size="11">
    <path d="M0 0h36.939C38.629 0 40 1.343 40 3v14c0 1.657-1.37 3-3.061 3H0V0z" fill="url(#state-fill)"/>
    <text text-anchor="middle" fill="#010101" fill-opacity=".3">
      <tspan x="20" y="15">{cov}</tspan>
    </text>
    <text text-anchor="middle" fill="#FFF">
      <tspan x="20" y="14">{cov}</tspan>
    </text>
  </g>
</svg>
"""


def _badge_color(
    coverage_percent: float | None, status: GhCommitStatus | None
) -> tuple[str, str]:
    if coverage_percent is None:
        return "#9F9F9F", "#8C8C8C"
    assert status is not None
    if status.state == "success":
        return "#34D058", "#28A745"
    return "#CB2431", "#B31D28"


async def _get_coverage(
    org: str, repo: str, gh_client: GithubClient
) -> tuple[float, GhCommitStatus] | tuple[None, None]:

    # TODO: add caching
    context_pattern = re.compile("coverage", re.IGNORECASE)
    with tracer.start_as_current_span(
        "badge.resolve_coverage", attributes={"org": org, "repo": repo}
    ) as span:
        commits = await gh_client.get_latest_commits(owner=org, repo=repo, limit=5)
        span.set_attribute("commits_fetched", len(commits))
        for index, commit in enumerate(commits):
            if "\n\n[skip ci]" in commit.message:
                continue
            span.set_attribute("commit_index", index)
            span.set_attribute("commit_sha", commit.sha)
            statuses = await gh_client.get_commit_statuses(
                owner=org, repo=repo, sha=commit.sha
            )
            for status in statuses:
                if context_pattern.search(status.context):
                    m = COV_RE.search(status.description)
                    if m:
                        coverage = float(m.group(1))
                        span.set_attribute("coverage_found", True)
                        span.set_attribute("coverage_percent", coverage)
                        return coverage, status
            break
        span.set_attribute("coverage_found", False)
        return None, None


@router.get("/redirect/{org}/{repo}/")
async def redirect(
    *,
    org: str,
    repo: str,
    gh_client: Annotated[GithubClient, Depends(get_github_client)],
) -> Response:
    _, status = await _get_coverage(org, repo, gh_client)
    if status:
        return RedirectResponse(status.target_url)
    return Response(content="Status Not found", status_code=404)


def get_response(request: Request, content: str) -> Response:
    user_agent = request.headers.get("user-agent", "")
    headers = (
        {
            "cache-control": "private, no-store",
            "cdn-cache-control": "no-store",
        }
        if user_agent.startswith("github-camo")
        else {
            "cache-control": "public, max-age=10",
            "cdn-cache-control": "max-age=10",
        }
    )
    return Response(
        content=content,
        media_type="image/svg+xml",
        headers=headers,
    )


async def _read_cache(
    redis_client: Redis | None, cache_key: str
) -> tuple[bytes | None, str]:
    """The cached SVG, and the lookup result for the metric."""
    if redis_client is None:
        return None, "unavailable"
    try:
        cached = await redis_client.get(cache_key)
    except RedisError:
        logger.warning(
            "badge cache read failed", extra={"cache_key": cache_key}, exc_info=True
        )
        return None, "error"
    return cached, "hit" if cached else "miss"


async def _write_cache(redis_client: Redis | None, cache_key: str, svg: str) -> None:
    if redis_client is None:
        return
    try:
        await redis_client.set(cache_key, svg.encode("utf-8"), ex=60)
    except RedisError:
        logger.warning(
            "badge cache write failed", extra={"cache_key": cache_key}, exc_info=True
        )


@router.get("/{org}/{repo}.svg")
async def badge(
    *,
    org: str,
    repo: str,
    gh_client: Annotated[GithubClient, Depends(get_github_client)],
    request: Request,
    redis_client: Annotated[Redis | None, Depends(get_redis_client)],
) -> Response:

    cache_key = BADGE_CACHE_KEY.format(org=org, repo=repo)
    cached, cache_result = await _read_cache(redis_client, cache_key)
    trace.get_current_span().set_attribute("badge.cache", cache_result)
    BADGE_CACHE_LOOKUPS.add(1, {"result": cache_result})

    if cached:
        return get_response(request, cached.decode("utf-8"))

    coverage, status = await _get_coverage(org, repo, gh_client)
    BADGE_RENDERED.add(1, {"found": coverage is not None})

    color_top, color_bot = _badge_color(coverage, status)
    cov_text = f"{coverage:.0f}%" if coverage is not None else "??%"

    svg = (
        BADGE_SVG.replace("{cov}", cov_text)
        .replace("{color_top}", color_top)
        .replace("{color_bot}", color_bot)
    )

    await _write_cache(redis_client, cache_key, svg)

    return get_response(request, svg)
