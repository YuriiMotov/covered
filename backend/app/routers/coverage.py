import logging
import mimetypes

import re
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path
from fastapi.responses import Response
from redis import RedisError
from redis.asyncio import Redis

from app.dependencies.redis_client import get_redis_client
from app.dependencies.auth import verify_api_key

from app.utils.aws_storage import AWSStorage, AWSStorageError
from app.dependencies.aws_storage import get_aws_storage
from app.constants import BADGE_CACHE_KEY
from app.schemas import AWSUploadSessionResponse

SAFE_PATH_RE = re.compile(r"^(?!.*(?:^|/)\.\.(?:/|$)).*$")

SiteId = Annotated[str, Path(pattern=r"^[a-f0-9]{12}$")]
SafePath = Annotated[str, Path(pattern=SAFE_PATH_RE)]  # ty: ignore[invalid-argument-type]

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post(
    "/invalidate-cache/{repo_owner}/{repo_name}/",
    dependencies=[Depends(verify_api_key)],
)
async def invalidate_cache(
    repo_owner: str,
    repo_name: str,
    redis_client: Annotated[Redis | None, Depends(get_redis_client)],
):
    cache_key = BADGE_CACHE_KEY.format(org=repo_owner, repo=repo_name)
    try:
        if redis_client:
            await redis_client.delete(cache_key)
    except RedisError:
        logger.exception("cache invalidation failed", extra={"cache_key": cache_key})
        raise HTTPException(status_code=500, detail="Failed to invalidate cache")

    return {"status": "success"}


@router.get("/{site_id}/{path:path}")
async def get_file(
    site_id: SiteId,
    path: SafePath,
    aws_storage: Annotated[AWSStorage, Depends(get_aws_storage)],
):
    if path == "" or path.endswith("/"):
        path = path + "index.html"

    try:
        content = await aws_storage.get_file(site_id, path)
    except AWSStorageError:
        return Response(status_code=404)

    media_type, _ = mimetypes.guess_type(path)
    return Response(
        content=content, media_type=media_type or "application/octet-stream"
    )


@router.post("/create-site/", dependencies=[Depends(verify_api_key)])
async def create_upload_session(
    aws_storage: Annotated[AWSStorage, Depends(get_aws_storage)],
) -> AWSUploadSessionResponse:
    session = await aws_storage.create_upload_session()
    return AWSUploadSessionResponse(
        site_id=session.site_id,
        bucket=session.bucket,
        region=session.region,
        access_key_id=session.access_key_id,
        secret_access_key=session.secret_access_key,
        session_token=session.session_token,
    )
