"""Serves uploaded images from local disk. A plain route (not StaticFiles) so the key is validated
first. With R2 on, images are served from the public bucket URL and this route answers 404."""

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.core.config import get_settings
from app.services.storage import KEY_RE, LocalDiskStorage

router = APIRouter(include_in_schema=False)

HEADERS = {
    "Cache-Control": "public, max-age=31536000, immutable",
    "X-Content-Type-Options": "nosniff",
}


@router.get("/media/{key:path}", name="media")
def media(key: str) -> FileResponse:
    settings = get_settings()
    if settings.r2_enabled or not KEY_RE.fullmatch(key):
        raise HTTPException(status_code=404)
    try:
        path = LocalDiskStorage(settings.media_root).path(key)
    except ValueError:
        raise HTTPException(status_code=404) from None
    if not path.is_file():
        raise HTTPException(status_code=404)
    return FileResponse(path, media_type="image/webp", headers=HEADERS)
