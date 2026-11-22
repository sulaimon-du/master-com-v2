import asyncio
import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.core.body_limit import DEFAULT_CAP, UPLOAD_REQUEST_CAP, BodyLimitMiddleware
from tests.support import csrf_token, make_user, sign_in

pytestmark = pytest.mark.usefixtures("clean_auth")


def test_oversized_content_length_on_avatar_is_413_without_auth(client: TestClient) -> None:
    r = client.post(
        "/master/avatar", content=b"x" * (UPLOAD_REQUEST_CAP + 1), headers={"content-type": "x/y"}
    )
    assert r.status_code == 413


def test_oversized_content_length_on_avatar_413_when_uploads_disabled(
    client: TestClient, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    from app.core.config import get_settings

    monkeypatch.setenv("UPLOADS_ENABLED", "false")
    get_settings.cache_clear()
    r = client.post("/master/avatar", content=b"x" * (UPLOAD_REQUEST_CAP + 1))
    assert r.status_code == 413


def test_other_paths_are_capped_at_64k(client: TestClient) -> None:
    r = client.post("/signin", content=b"a=" + b"x" * DEFAULT_CAP)
    assert r.status_code == 413
    # Just under the cap is read normally (and refused by CSRF, not by the cap).
    r = client.post("/signin", content=b"a=" + b"x" * 1000)
    assert r.status_code == 403


def test_chunked_body_over_cap_is_rejected(client: TestClient) -> None:
    def chunks():  # type: ignore[no-untyped-def]
        for _ in range(DEFAULT_CAP // 1024 + 10):
            yield b"x" * 1024

    r = client.post(
        "/signin",
        content=chunks(),
        headers={"content-type": "application/x-www-form-urlencoded"},
    )
    # The cap cuts the body; the app sees a disconnect and answers 400 (or we answer 413).
    assert r.status_code in (400, 413)


def test_lying_content_length_is_cut_off_at_the_cap() -> None:
    reached = {"read": 0}

    async def app(scope, receive, send):  # type: ignore[no-untyped-def]
        while True:
            message = await receive()
            if message["type"] != "http.request":
                raise RuntimeError("client gone")
            reached["read"] += len(message["body"])

    sent: list[dict] = []
    chunks = [{"type": "http.request", "body": b"x" * 8192, "more_body": True}] * 1000

    async def receive():  # type: ignore[no-untyped-def]
        return chunks.pop(0)

    async def send(message):  # type: ignore[no-untyped-def]
        sent.append(message)

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/signin",
        "headers": [(b"content-length", b"10")],
    }
    asyncio.run(BodyLimitMiddleware(app)(scope, receive, send))
    assert sent[0]["status"] == 413
    assert reached["read"] <= DEFAULT_CAP + 8192
    assert len(chunks) > 900  # most of the body was never read


def test_normal_avatar_upload_still_works(client: TestClient) -> None:
    make_user()
    sign_in(client)
    client.post(
        "/master/onboarding/basics",
        data={"display_name": "Али", "slug": "ali-body", "csrf_token": csrf_token(client)},
    )
    buf = io.BytesIO()
    Image.new("RGB", (800, 600), (10, 20, 30)).save(buf, "PNG")
    r = client.post(
        "/master/avatar",
        data={"csrf_token": csrf_token(client)},
        files={"upload": ("a.png", buf.getvalue(), "image/png")},
    )
    assert r.status_code == 303


@pytest.mark.parametrize(
    "path",
    ["/master/portfolio/collections/1/photos", "/master/onboarding/collection", "/master/avatar"],
)
def test_upload_paths_get_the_small_cap_when_uploads_are_off(
    client: TestClient, monkeypatch, path: str
) -> None:  # type: ignore[no-untyped-def]
    from app.core.config import get_settings

    monkeypatch.setenv("UPLOADS_ENABLED", "false")
    get_settings.cache_clear()
    assert client.post(path, content=b"x" * (DEFAULT_CAP + 1)).status_code == 413
    # Under the small cap the body is read and the route answers (CSRF or 404), not 413.
    assert client.post(path, content=b"x" * 1000).status_code != 413


@pytest.mark.parametrize(
    "path",
    [
        "/master/portfolio/collections/1/photos",
        "/master/onboarding/collection",
        "/master/avatar",
    ],
)
def test_upload_paths_keep_the_big_cap_when_uploads_are_on(
    client: TestClient, monkeypatch, path: str
) -> None:  # type: ignore[no-untyped-def]
    from app.core.config import get_settings

    monkeypatch.setenv("UPLOADS_ENABLED", "true")
    get_settings.cache_clear()
    assert client.post(path, content=b"x" * (DEFAULT_CAP + 1)).status_code != 413


@pytest.mark.parametrize(
    "path",
    [
        "/master/portfolio/collections/١/photos",  # Arabic-Indic digit
        "/master/portfolio/collections/1/photos\n",
        "/master/onboarding/collection\n",
    ],
)
def test_lookalike_paths_get_the_default_cap(monkeypatch, path: str) -> None:  # type: ignore[no-untyped-def]
    from app.core.body_limit import request_cap
    from app.core.config import get_settings

    monkeypatch.setenv("UPLOADS_ENABLED", "true")
    get_settings.cache_clear()
    assert request_cap(path) == DEFAULT_CAP
