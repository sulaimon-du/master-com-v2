"""Background jobs (spec D6).

For now `enqueue` is a no-op placeholder so the business code already has its single call site
per event; the real queue (jobs table, worker, retries) replaces it in Task 7.
"""

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session


def enqueue(
    db: Session,
    kind: str,
    payload: dict[str, Any],
    *,
    dedupe_key: str | None = None,
    run_at: datetime | None = None,
) -> None:
    """Queue `kind` with `payload` (ids only) in the caller's transaction. No-op until Task 7."""
    del db, kind, payload, dedupe_key, run_at
