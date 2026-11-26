"""`/sitemap.xml` (spec D6). Registered before the SEO catch-all routes."""

from fastapi import APIRouter, Response

from app.core.db import DbSession
from app.services import sitemap

router = APIRouter(include_in_schema=False)


@router.get("/sitemap.xml", name="sitemap_xml")
def sitemap_xml(db: DbSession) -> Response:
    return Response(sitemap.cached(db), media_type="application/xml; charset=utf-8")
