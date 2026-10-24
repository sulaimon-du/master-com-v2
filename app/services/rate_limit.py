from datetime import timedelta

from sqlalchemy import case, func
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.security import hash_token
from app.models.auth import RateLimit


def allow(db: Session, scope: str, identity: str, *, limit: int, window: timedelta) -> bool:
    """Count one attempt in a fixed window. True while the count is within `limit`.

    Rejected attempts are counted too, so a flood keeps the block in place. The key is hashed,
    so emails and addresses are not stored as plain text.
    """
    key = hash_token(f"{scope}:{identity}")
    window_over = RateLimit.window_start <= func.now() - window
    stmt = (
        insert(RateLimit)
        .values(key_hash=key, window_start=func.now(), hits=1)
        .on_conflict_do_update(
            index_elements=[RateLimit.key_hash],
            set_={
                "window_start": case((window_over, func.now()), else_=RateLimit.window_start),
                "hits": case((window_over, 1), else_=RateLimit.hits + 1),
            },
        )
        .returning(RateLimit.hits)
    )
    hits = db.execute(stmt).scalar_one()
    return hits <= limit
