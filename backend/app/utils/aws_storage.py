import json
from dataclasses import dataclass
from uuid import uuid4
from contextlib import AsyncExitStack

from botocore.exceptions import ClientError
from pydantic import SecretStr
from aiobotocore.session import get_session
from types_aiobotocore_s3 import S3Client as AIOS3Client

from app.telemetry import (
    S3_FILE_SIZE,
    S3_FILES_SERVED,
    UPLOAD_SESSIONS_CREATED,
    tracer,
)


class AWSStorageError(Exception):
    pass


SITE_ID_ATTEMPTS = 3


@dataclass
class UploadSession:
    site_id: str
    bucket: str
    region: str
    access_key_id: str
    secret_access_key: str
    session_token: str


class AWSStorage:
    def __init__(
        self,
        access_key_id: str,
        secret_access_key: SecretStr,
        bucket: str,
        region: str,
        upload_role_arn: str,
    ):
        self._session = get_session()
        self._access_key_id = access_key_id
        self._secret_access_key = secret_access_key
        self._bucket = bucket
        self._region = region
        self._upload_role_arn = upload_role_arn
        self._exit_stack = AsyncExitStack()
        self._client: AIOS3Client | None = None

    async def __aenter__(self):
        if self._client is not None:
            raise AWSStorageError("Client already initialized")

        client = self._session.create_client(
            "s3",
            aws_access_key_id=self._access_key_id,
            aws_secret_access_key=self._secret_access_key.get_secret_value(),
            region_name=self._region,
        )
        self._client = await self._exit_stack.enter_async_context(client)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self._exit_stack.aclose()
        self._client = None

    async def _generate_site_id(self) -> str:
        if self._client is None:
            raise AWSStorageError("Client not initialized")
        with tracer.start_as_current_span("s3.generate_site_id") as span:
            attempt = 0
            try:
                for attempt in range(1, SITE_ID_ATTEMPTS + 1):
                    site_id = uuid4().hex[:12]
                    full_key = f"sites/{site_id}/.keep"
                    try:
                        await self._client.put_object(
                            Bucket=self._bucket,
                            Key=full_key,
                            Body=b"",
                            IfNoneMatch="*",
                        )
                        span.set_attribute("site_id", site_id)
                        return site_id
                    except ClientError as e:
                        code = e.response.get("Error", {}).get("Code")
                        if code == "PreconditionFailed":
                            continue
                        raise AWSStorageError(f"Failed to create site directory: {e}")
                raise AWSStorageError(
                    "Failed to generate unique site ID after multiple attempts"
                )
            finally:
                span.set_attribute("attempts", attempt)

    async def create_upload_session(self) -> UploadSession:
        with tracer.start_as_current_span("s3.create_upload_session") as span:
            site_id = await self._generate_site_id()
            span.set_attribute("site_id", site_id)

            session_policy = json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Action": "s3:PutObject",
                            "Resource": f"arn:aws:s3:::{self._bucket}/sites/{site_id}/*",
                        }
                    ],
                }
            )

            async with self._session.create_client(
                "sts",
                aws_access_key_id=self._access_key_id,
                aws_secret_access_key=self._secret_access_key.get_secret_value(),
                region_name=self._region,
            ) as sts_client:
                with tracer.start_as_current_span(
                    "sts.assume_role",
                    attributes={"aws.role_arn": self._upload_role_arn},
                ):
                    response = await sts_client.assume_role(
                        RoleArn=self._upload_role_arn,
                        RoleSessionName=f"upload-{site_id}",
                        DurationSeconds=3600,
                        Policy=session_policy,
                    )

            credentials = response["Credentials"]
            UPLOAD_SESSIONS_CREATED.add(1)
            return UploadSession(
                site_id=site_id,
                bucket=self._bucket,
                region=self._region,
                access_key_id=credentials["AccessKeyId"],
                secret_access_key=credentials["SecretAccessKey"],
                session_token=credentials["SessionToken"],
            )

    async def get_file(self, site_id: str, key: str) -> bytes | None:
        """File content, or None if it does not exist in S3."""
        if self._client is None:
            raise AWSStorageError("Client not initialized")
        full_key = f"sites/{site_id}/{key}"
        with tracer.start_as_current_span(
            "s3.get_object",
            attributes={
                "aws.s3.bucket": self._bucket,
                "aws.s3.key": full_key,
                "site_id": site_id,
            },
        ) as span:
            try:
                res = await self._client.get_object(Bucket=self._bucket, Key=full_key)
                content = await res["Body"].read()
            except ClientError as e:
                if e.response.get("Error", {}).get("Code") == "NoSuchKey":
                    span.set_attribute("result", "not_found")
                    S3_FILES_SERVED.add(1, {"result": "not_found"})
                    return None
                span.set_attribute("result", "error")
                S3_FILES_SERVED.add(1, {"result": "error"})
                raise AWSStorageError(f"Failed to get file: {e}")
            span.set_attribute("result", "ok")
            span.set_attribute("file_size", len(content))
            S3_FILES_SERVED.add(1, {"result": "ok"})
            S3_FILE_SIZE.record(len(content))
            return content
