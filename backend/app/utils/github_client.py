from contextlib import AsyncExitStack

import httpx
import stamina
from pydantic import SecretStr

from app.schemas import GhCommit, GhCommitStatus
from app.telemetry import GITHUB_REQUESTS, GITHUB_RETRIES, tracer

BASE_URL = "https://api.github.com"


class GithubClientError(Exception):
    pass


class GithubClient:
    def __init__(self, token: SecretStr):
        self._token = token
        self._httpx_client: httpx.AsyncClient | None = None
        self._exit_stack = AsyncExitStack()

    def ensure_initialized(self) -> None:
        if self._httpx_client is None:
            raise GithubClientError("Client not initialized")

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"token {self._token.get_secret_value()}"}

    async def __aenter__(self):
        if self._httpx_client is not None:
            raise GithubClientError("Client already initialized")
        client = httpx.AsyncClient(base_url=BASE_URL, headers=self._headers())
        self._httpx_client = await self._exit_stack.enter_async_context(client)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self._exit_stack.aclose()
        self._httpx_client = None

    async def _get(
        self,
        url: str,
        *,
        endpoint: str,
        owner: str,
        repo: str,
        params: dict | None = None,
    ) -> httpx.Response:
        self.ensure_initialized()
        assert self._httpx_client is not None

        with tracer.start_as_current_span(
            "github.request",
            attributes={"endpoint": endpoint, "owner": owner, "repo": repo},
        ) as span:
            attempts = 0
            try:
                async for attempt in stamina.retry_context(
                    on=(httpx.TransportError, httpx.HTTPStatusError),
                    attempts=3,
                    wait_jitter=2.0,
                ):
                    with attempt:
                        attempts += 1
                        if attempts > 1:
                            GITHUB_RETRIES.add(1, {"endpoint": endpoint})
                        response = await self._httpx_client.get(url, params=params)
                        if response.status_code >= 500:
                            response.raise_for_status()
                response.raise_for_status()
            except Exception:
                span.set_attribute("attempts", attempts)
                GITHUB_REQUESTS.add(1, {"endpoint": endpoint, "outcome": "error"})
                raise
            span.set_attribute("attempts", attempts)
            span.set_attribute("http.status_code", response.status_code)
            GITHUB_REQUESTS.add(1, {"endpoint": endpoint, "outcome": "ok"})
            return response

    async def get_latest_commits(
        self, owner: str, repo: str, limit: int = 5
    ) -> list[GhCommit]:
        response = await self._get(
            f"/repos/{owner}/{repo}/commits",
            endpoint="commits",
            owner=owner,
            repo=repo,
            params={"per_page": limit},
        )
        resp_json = response.json()
        assert isinstance(resp_json, list)
        return [GhCommit.model_validate(item) for item in resp_json]

    async def get_commit_statuses(
        self, owner: str, repo: str, sha: str
    ) -> list[GhCommitStatus]:
        response = await self._get(
            f"/repos/{owner}/{repo}/statuses/{sha}",
            endpoint="statuses",
            owner=owner,
            repo=repo,
        )
        resp_json = response.json()
        assert isinstance(resp_json, list)
        return [GhCommitStatus.model_validate(item) for item in resp_json]
