from functools import lru_cache

from pydantic import AnyUrl, SecretStr
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    api_key: SecretStr

    aws_region: str = "us-east-1"
    aws_access_key_id: str
    aws_secret_access_key: SecretStr
    aws_bucket: str = "covered"
    aws_upload_role_arn: str

    redis_url: AnyUrl | None = None

    github_token: SecretStr

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }


@lru_cache
def get_settings() -> Settings:
    return Settings.model_validate({})
