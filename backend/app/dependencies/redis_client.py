from typing import cast

from fastapi import Request
from redis.asyncio import Redis


async def get_redis_client(request: Request) -> Redis | None:
    return cast(Redis | None, request.state.redis_connection)
