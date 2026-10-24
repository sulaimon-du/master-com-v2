from fastapi.testclient import TestClient


def test_home_renders_with_static_assets(client: TestClient) -> None:
    r = client.get("/")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    html = r.text
    assert '<html lang="ru">' in html
    assert "/static/css/app.css" in html
    assert "/static/js/htmx.min.js" in html
    assert "/static/js/alpine-csp.min.js" in html
    # No third-party origins on the page.
    assert "https://" not in html.split("<body", 1)[0]
