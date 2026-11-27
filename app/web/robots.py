"""`/robots.txt` (spec D5): closed to everyone outside production, open with a few exceptions
in production. Registered before the SEO catch-all routes."""

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

from app.core.config import get_settings
from app.services.seo import absolute

router = APIRouter(include_in_schema=False)

DISALLOW = (
    "/search",
    "/account",
    "/master/",
    "/requests/",
    "/feed",
    "/responses/",
    "/auth/",
    "/signin",
    "/register",
    "/password/",
    "/confirm",
)


def robots_txt(*, production: bool) -> str:
    if not production:
        return "User-agent: *\nDisallow: /\n"
    lines = ["User-agent: *", "Allow: /", *(f"Disallow: {path}" for path in DISALLOW)]
    lines.append(f"Sitemap: {absolute('/sitemap.xml')}")
    return "\n".join(lines) + "\n"


@router.get("/robots.txt", name="robots_txt")
def robots() -> PlainTextResponse:
    return PlainTextResponse(robots_txt(production=get_settings().app_env == "production"))
