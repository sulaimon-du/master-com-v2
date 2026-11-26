"""Cloudflare R2 storage (spec 2026-11-23 §D8): the S3 adapter, its selection and its CSP origin."""

import os

import boto3
import pytest
from botocore.exceptions import ClientError, EndpointConnectionError
from fastapi.testclient import TestClient
from moto import mock_aws
from pydantic import ValidationError

from app.core.config import Settings, get_settings
from app.core.security_headers import build_csp
from app.main import create_app
from app.services.storage import (
    LocalDiskStorage,
    R2Storage,
    StorageError,
    get_storage,
    make_r2_client,
    new_key,
)

IMAGE = b"RIFF\x04\x00\x00\x00WEBPfake"
BUCKET = "test-media"
PUBLIC = "https://pub-0123456789abcdef.r2.dev"
ACCESS = "AKIA-r2-access-key-id-value"
SECRET = "r2-secret-access-key-value-0123456789"

R2_ENV = {
    "R2_ENDPOINT": "https://0123456789abcdef.r2.cloudflarestorage.com",
    "R2_ACCESS_KEY_ID": ACCESS,
    "R2_SECRET_ACCESS_KEY": SECRET,
    "R2_BUCKET": BUCKET,
    "R2_PUBLIC_URL": PUBLIC,
}

BAD_KEYS = [
    "../x",
    "a/zz.webp",
    "p/2026/11/abc.png",
    "a/../../etc/passwd",
    "p/２０２６/１１/" + "0" * 32 + ".webp",  # full-width digits: not ASCII
    "p/٢٠٢٦/١١/" + "0" * 32 + ".webp",  # Arabic-Indic digits
]


def _settings(monkeypatch, **env: str) -> Settings:  # type: ignore[no-untyped-def]
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    get_settings.cache_clear()
    get_storage.cache_clear()
    return Settings(_env_file=None)  # type: ignore[call-arg]


@pytest.fixture
def r2_env(monkeypatch) -> Settings:  # type: ignore[no-untyped-def]
    return _settings(monkeypatch, **R2_ENV)


@pytest.fixture
def s3():  # type: ignore[no-untyped-def]
    with mock_aws():
        client = boto3.client(
            "s3",
            region_name="us-east-1",
            aws_access_key_id="testing",
            aws_secret_access_key="testing",
        )
        client.create_bucket(Bucket=BUCKET)
        yield client


class RecordingClient:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def put_object(self, **kwargs) -> None:  # type: ignore[no-untyped-def]
        self.calls.append("put")

    def delete_object(self, **kwargs) -> None:  # type: ignore[no-untyped-def]
        self.calls.append("delete")


class FailingClient:
    def __init__(self, exc: Exception) -> None:
        self.exc = exc

    def put_object(self, **kwargs) -> None:  # type: ignore[no-untyped-def]
        raise self.exc

    def delete_object(self, **kwargs) -> None:  # type: ignore[no-untyped-def]
        raise self.exc


# --- the adapter ------------------------------------------------------------------------------


def test_put_stores_bytes_with_type_and_cache_control(s3) -> None:  # type: ignore[no-untyped-def]
    storage = R2Storage(s3, BUCKET, PUBLIC)
    key = new_key("p")
    storage.put(key, IMAGE, "image/webp")
    obj = s3.get_object(Bucket=BUCKET, Key=key)
    assert obj["Body"].read() == IMAGE
    assert obj["ContentType"] == "image/webp"
    assert obj["CacheControl"] == "public, max-age=31536000, immutable"


def test_delete_removes_and_missing_is_silent(s3) -> None:  # type: ignore[no-untyped-def]
    storage = R2Storage(s3, BUCKET, PUBLIC)
    key = new_key("a")
    storage.put(key, IMAGE, "image/webp")
    storage.delete(key)
    with pytest.raises(ClientError):
        s3.get_object(Bucket=BUCKET, Key=key)
    storage.delete(new_key("a"))  # never existed


def test_url_is_public_url_plus_key() -> None:
    key = new_key("p")
    assert R2Storage(RecordingClient(), BUCKET, PUBLIC).url(key) == f"{PUBLIC}/{key}"


@pytest.mark.parametrize("key", BAD_KEYS)
def test_invalid_keys_raise_before_any_client_call(key: str) -> None:
    client = RecordingClient()
    storage = R2Storage(client, BUCKET, PUBLIC)
    for call in (lambda: storage.put(key, IMAGE, "image/webp"), lambda: storage.delete(key)):
        with pytest.raises(ValueError):
            call()
    with pytest.raises(ValueError):
        storage.url(key)
    assert client.calls == []


@pytest.mark.parametrize(
    "exc",
    [
        ClientError({"Error": {"Code": "AccessDenied", "Message": "denied"}}, "PutObject"),
        EndpointConnectionError(endpoint_url="https://example.invalid"),
    ],
)
def test_client_errors_become_storage_error(exc: Exception) -> None:
    storage = R2Storage(FailingClient(exc), BUCKET, PUBLIC)
    with pytest.raises(StorageError):
        storage.put(new_key("a"), IMAGE, "image/webp")
    with pytest.raises(StorageError):
        storage.delete(new_key("a"))


def test_client_factory_uses_endpoint_region_and_config(r2_env: Settings) -> None:
    client = make_r2_client(r2_env)
    assert client.meta.endpoint_url == R2_ENV["R2_ENDPOINT"]
    assert client.meta.region_name == "auto"
    config = client.meta.config
    assert config.signature_version == "s3v4"
    # botocore stores max_attempts=3 (retries) as total_max_attempts=4 (first try + 3).
    assert config.retries["mode"] == "standard" and config.retries["total_max_attempts"] == 4
    assert (config.connect_timeout, config.read_timeout) == (5, 30)
    creds = client._request_signer._credentials
    assert (creds.access_key, creds.secret_key) == (ACCESS, SECRET)


# --- selection by settings --------------------------------------------------------------------


def test_get_storage_is_r2_when_all_five_are_set(r2_env: Settings) -> None:
    assert r2_env.r2_enabled
    storage = get_storage()
    assert isinstance(storage, R2Storage)
    key = new_key("a")
    assert storage.url(key) == f"{PUBLIC}/{key}"


@pytest.mark.parametrize("missing", list(R2_ENV))
def test_get_storage_is_local_when_any_is_missing(monkeypatch, missing: str) -> None:  # type: ignore[no-untyped-def]
    settings = _settings(monkeypatch, **{**R2_ENV, missing: ""})
    assert not settings.r2_enabled
    assert isinstance(get_storage(), LocalDiskStorage)


def test_get_storage_is_local_by_default(settings: Settings) -> None:
    assert not settings.r2_enabled
    assert isinstance(get_storage(), LocalDiskStorage)


def test_public_url_trailing_slash_is_stripped(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    settings = _settings(monkeypatch, **{**R2_ENV, "R2_PUBLIC_URL": PUBLIC + "/"})
    assert settings.r2_public_url == PUBLIC
    assert settings.r2_public_origin == PUBLIC


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("R2_PUBLIC_URL", "http://pub-0123456789abcdef.r2.dev"),
        ("R2_ENDPOINT", "http://0123456789abcdef.r2.cloudflarestorage.com"),
        ("R2_PUBLIC_URL", "pub-0123456789abcdef.r2.dev"),
        ("R2_ENDPOINT", f"https://{ACCESS}:{SECRET}@r2.example.com"),
    ],
)
def test_non_https_urls_are_refused_without_leaking_secrets(monkeypatch, name, value) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ValidationError) as info:
        _settings(monkeypatch, **{**R2_ENV, name: value})
    message = str(info.value)
    assert name in message and "https://" in message
    for secret in (ACCESS, SECRET, os.environ["SECRET_KEY"]):
        assert secret not in message


BAD_HOSTS = [
    "https://pub.r2.dev;script-src 'unsafe-inline'",
    "https://pub.r2.dev *",
    "https://*",
    "https://a b.r2.dev",
    "https://pub.r2.dev,evil.com",
    "https://pub.r2.dev:99999",
    "https://pub.r2.dev:abc",
    "https://localhost",
    "https://pub.r2.dev/a;b",
]


@pytest.mark.parametrize("name", ["R2_PUBLIC_URL", "R2_ENDPOINT"])
@pytest.mark.parametrize("value", BAD_HOSTS)
def test_unclean_hosts_are_refused_without_echoing_the_value(monkeypatch, name, value) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ValidationError) as info:
        _settings(monkeypatch, **{**R2_ENV, name: value})
    message = str(info.value)
    assert name in message
    assert value not in message and "r2.dev" not in message.replace(name, "")
    for secret in (ACCESS, SECRET):
        assert secret not in message


def test_origin_is_built_from_host_and_port_only(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    settings = _settings(
        monkeypatch, **{**R2_ENV, "R2_PUBLIC_URL": "https://Media.Example.COM:8443/x/"}
    )
    assert settings.r2_public_origin == "https://media.example.com:8443"
    assert settings.r2_public_url == "https://media.example.com:8443/x"


def test_r2_secrets_are_stripped_and_blank_is_unset(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    settings = _settings(monkeypatch, **{**R2_ENV, "R2_ACCESS_KEY_ID": f"  {ACCESS} \n"})
    assert settings.r2_access_key_id is not None
    assert settings.r2_access_key_id.get_secret_value() == ACCESS
    settings = _settings(monkeypatch, **{**R2_ENV, "R2_SECRET_ACCESS_KEY": "   "})
    assert settings.r2_secret_access_key is None and not settings.r2_enabled


# --- partial configuration --------------------------------------------------------------------


@pytest.mark.parametrize("env", ["staging", "production"])
def test_uploads_on_without_full_r2_refuses_to_start(monkeypatch, env: str) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ValidationError) as info:
        _settings(
            monkeypatch,
            APP_ENV=env,
            UPLOADS_ENABLED="true",
            **{**R2_ENV, "R2_SECRET_ACCESS_KEY": "", "R2_BUCKET": ""},
        )
    message = str(info.value)
    assert "R2_SECRET_ACCESS_KEY" in message and "R2_BUCKET" in message
    assert "R2_ENDPOINT" not in message and "R2_PUBLIC_URL" not in message
    for secret in (ACCESS, SECRET, BUCKET, PUBLIC):
        assert secret not in message


def test_uploads_on_with_full_r2_or_off_starts_on_staging(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    assert _settings(monkeypatch, APP_ENV="staging", UPLOADS_ENABLED="true", **R2_ENV).r2_enabled
    blank = {name: "" for name in R2_ENV}
    off = _settings(monkeypatch, APP_ENV="staging", UPLOADS_ENABLED="false", **blank)
    assert off.uploads_enabled is False
    # Development with uploads on and no R2 is the normal local setup.
    assert _settings(monkeypatch, APP_ENV="development", UPLOADS_ENABLED="true", **blank)


def test_partial_r2_logs_one_warning_with_names_only(monkeypatch, caplog) -> None:  # type: ignore[no-untyped-def]
    settings = _settings(monkeypatch, **{**R2_ENV, "R2_ENDPOINT": "", "R2_PUBLIC_URL": ""})
    assert settings.r2_missing == ["R2_ENDPOINT", "R2_PUBLIC_URL"]
    with caplog.at_level("WARNING"):
        create_app(settings)
    warnings = [r.getMessage() for r in caplog.records if "R2" in r.getMessage()]
    assert len(warnings) == 1
    assert "R2_ENDPOINT" in warnings[0] and "R2_PUBLIC_URL" in warnings[0]
    for secret in (ACCESS, SECRET, BUCKET):
        assert secret not in warnings[0]


@pytest.mark.parametrize("count", [0, 5])
def test_no_r2_warning_when_none_or_all_are_set(monkeypatch, caplog, count: int) -> None:  # type: ignore[no-untyped-def]
    env = R2_ENV if count else {name: "" for name in R2_ENV}
    settings = _settings(monkeypatch, **env)
    with caplog.at_level("WARNING"):
        create_app(settings)
    assert not [r for r in caplog.records if "R2" in r.getMessage()]


def test_boto_loggers_are_pinned_to_warning(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import logging

    create_app(_settings(monkeypatch, LOG_LEVEL="DEBUG"))
    for name in ("botocore", "boto3"):
        assert logging.getLogger(name).level == logging.WARNING


def test_client_factory_does_not_use_the_default_session(r2_env: Settings) -> None:
    boto3.DEFAULT_SESSION = None
    make_r2_client(r2_env)
    assert boto3.DEFAULT_SESSION is None


# --- CSP and the /media route -----------------------------------------------------------------


def _csp(settings: Settings) -> str:
    return TestClient(create_app(settings)).get("/health").headers["content-security-policy"]


def test_csp_allows_r2_origin_in_img_src_only_when_enabled(settings: Settings, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    local = _csp(settings)
    assert "r2.dev" not in local
    assert local == build_csp(None)
    r2 = _csp(_settings(monkeypatch, **{**R2_ENV, "R2_PUBLIC_URL": PUBLIC + "/sub/path"}))
    assert r2 == local.replace("img-src 'self' data: blob:", f"img-src 'self' data: blob: {PUBLIC}")
    assert r2.count(PUBLIC) == 1 and "/sub/path" not in r2


def test_media_route_is_404_when_r2_is_enabled(r2_env: Settings) -> None:
    key = new_key("p")
    LocalDiskStorage(r2_env.media_root).put(key, IMAGE, "image/webp")  # even if a file is there
    assert TestClient(create_app(r2_env)).get(f"/media/{key}").status_code == 404


@pytest.mark.parametrize("key", BAD_KEYS[-2:])
def test_media_route_refuses_non_ascii_digits(client: TestClient, key: str) -> None:
    assert client.get(f"/media/{key}").status_code == 404


@pytest.mark.parametrize(
    "value",
    ["http://example.com", "https://localhost", "https://127.0.0.1", "http://localhost:8000"],
)
def test_production_refuses_a_local_or_non_https_site_url(monkeypatch, value) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ValidationError) as info:
        _settings(monkeypatch, APP_ENV="production", SITE_URL=value)
    message = str(info.value)
    assert "SITE_URL" in message and value not in message


def test_production_accepts_an_https_site_url_and_development_any(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    assert _settings(monkeypatch, APP_ENV="production", SITE_URL="https://ustoi.example")
    assert _settings(monkeypatch, APP_ENV="development", SITE_URL="http://localhost:8000")
