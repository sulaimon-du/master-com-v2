import os
import re

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings
from app.services.storage import KEY_RE, LocalDiskStorage, get_storage, new_key

IMAGE = b"RIFF\x04\x00\x00\x00WEBPfake"


@pytest.fixture
def media_root(tmp_path):
    """The autouse fixture in conftest already points MEDIA_ROOT at tmp_path."""
    return tmp_path / "media"


def test_put_then_get_serves_webp_with_cache_headers(client: TestClient, media_root) -> None:
    key = new_key("p")
    get_storage().put(key, IMAGE, "image/webp")
    response = client.get(f"/media/{key}")
    assert response.status_code == 200
    assert response.content == IMAGE
    assert response.headers["content-type"] == "image/webp"
    assert response.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert get_storage().url(key) == f"/media/{key}"


@pytest.mark.parametrize(
    "key",
    [
        "../x",
        "/etc/passwd",
        "a/zz.webp",
        "p/2026/11/abc.png",
        "a/../../etc/passwd",
        "a/" + "0" * 32 + ".png",
        "p/2026/11/" + "0" * 32 + "/../x.webp",
    ],
)
def test_media_rejects_traversal_and_bad_keys(client: TestClient, key: str) -> None:
    assert client.get(f"/media/{key}").status_code == 404


def test_missing_key_is_404(client: TestClient) -> None:
    assert client.get("/media/a/" + "0" * 32 + ".webp").status_code == 404


def test_delete_missing_is_silent(media_root) -> None:
    get_storage().delete(new_key("a"))
    key = new_key("a")
    get_storage().put(key, IMAGE, "image/webp")
    get_storage().delete(key)
    assert not os.path.exists(media_root / key)


def test_put_leaves_no_temp_files(media_root) -> None:
    LocalDiskStorage(media_root).put(new_key("p"), IMAGE, "image/webp")
    names = [n for _, _, files in os.walk(media_root) for n in files]
    assert len(names) == 1 and names[0].endswith(".webp")


def test_new_keys_match_regex_and_are_unique() -> None:
    keys = [new_key("p") for _ in range(1000)]
    assert len(set(keys)) == 1000
    assert all(KEY_RE.fullmatch(k) for k in keys)
    assert all(KEY_RE.fullmatch(new_key("a")) for _ in range(50))
    assert re.fullmatch(r"a/[0-9a-f]{32}\.webp", new_key("a"))
    base = keys[0]
    assert new_key("p", thumb_of=base) == base.removesuffix(".webp") + "-t.webp"
    assert KEY_RE.fullmatch(new_key("p", thumb_of=base))


def test_uploads_disabled_by_default_on_staging(monkeypatch) -> None:
    for env, expected in [("staging", False), ("production", False), ("development", True)]:
        monkeypatch.setenv("APP_ENV", env)
        monkeypatch.delenv("UPLOADS_ENABLED", raising=False)
        assert Settings(_env_file=None).uploads_enabled is expected  # type: ignore[call-arg]
    monkeypatch.setenv("APP_ENV", "staging")
    monkeypatch.setenv("UPLOADS_ENABLED", "true")
    # Uploads on staging need R2 (otherwise the settings refuse to load, see test_r2_storage).
    monkeypatch.setenv("R2_ENDPOINT", "https://acct.r2.cloudflarestorage.com")
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "id")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "secret")
    monkeypatch.setenv("R2_BUCKET", "bucket")
    monkeypatch.setenv("R2_PUBLIC_URL", "https://pub.r2.dev")
    assert Settings(_env_file=None).uploads_enabled is True  # type: ignore[call-arg]


def test_uploads_enabled_in_test(settings: Settings) -> None:
    assert settings.app_env == "test"
    assert settings.uploads_enabled is True


def test_plan_limit_lookup(settings: Settings) -> None:
    assert settings.limit("photos_per_collection") == 60
    assert settings.limit("collections", "free") == 30
    assert get_settings().plan_limits["free"]["areas"] == 20
