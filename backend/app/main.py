from fastapi import FastAPI
from redis.asyncio import Redis

from app.config import get_settings
from app.utils.github_client import GithubClient
from app.utils.aws_storage import AWSStorage
from app.routers.coverage import router as cov_upload_router
from app.routers.badge import router as badge_router


async def lifespan(_: FastAPI):
    settings = get_settings()
    redis_connection = None
    if settings.redis_url is not None:
        redis_connection = Redis.from_url(str(settings.redis_url))
    async with (
        AWSStorage(
            access_key_id=settings.aws_access_key_id,
            secret_access_key=settings.aws_secret_access_key,
            bucket=settings.aws_bucket,
            region=settings.aws_region,
            upload_role_arn=settings.aws_upload_role_arn,
        ) as aws_storage_client,
        GithubClient(
            token=settings.github_token,
        ) as gh_client,
    ):
        yield {
            "aws_storage_client": aws_storage_client,
            "gh_client": gh_client,
            "redis_connection": redis_connection,
        }
    if redis_connection is not None:
        await redis_connection.aclose()


app = FastAPI(lifespan=lifespan, openapi_url=None)


app.include_router(cov_upload_router, prefix="/coverage")

app.include_router(badge_router, prefix="/badge")
