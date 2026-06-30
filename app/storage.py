import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from app.config import get_settings


class ObjectStore:
    def __init__(self) -> None:
        s = get_settings()
        self._bucket = s.s3_bucket
        self._client = boto3.client(
            "s3",
            endpoint_url=s.s3_endpoint,
            aws_access_key_id=s.s3_access_key,
            aws_secret_access_key=s.s3_secret_key,
            region_name=s.s3_region,
            config=Config(signature_version="s3v4"),
        )

    def ensure_bucket(self) -> None:
        try:
            self._client.head_bucket(Bucket=self._bucket)
        except ClientError:
            self._client.create_bucket(Bucket=self._bucket)

    def put_stream(self, object_key: str, fileobj, content_type: str) -> int:
        start = fileobj.tell()
        fileobj.seek(0, 2)
        end = fileobj.tell()
        fileobj.seek(start)
        size = end - start
        self._client.upload_fileobj(
            fileobj, self._bucket, object_key,
            ExtraArgs={"ContentType": content_type or "application/octet-stream"},
        )
        return size

    def delete(self, object_key: str) -> None:
        self._client.delete_object(Bucket=self._bucket, Key=object_key)

    def exists(self, object_key: str) -> bool:
        try:
            self._client.head_object(Bucket=self._bucket, Key=object_key)
            return True
        except ClientError:
            return False

    def presigned_get(self, object_key: str, expires: int = 3600) -> str:
        return self._client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self._bucket, "Key": object_key},
            ExpiresIn=expires,
        )

    def get_stream(self, object_key: str):
        """Return a streaming, file-like body for the object's bytes."""
        obj = self._client.get_object(Bucket=self._bucket, Key=object_key)
        return obj["Body"]
