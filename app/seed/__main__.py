"""`python -m app.seed`: load reference data into the database in one transaction."""

import logging

from app.core.db import get_sessionmaker
from app.seed import apply

log = logging.getLogger("app.seed")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    with get_sessionmaker()() as session, session.begin():
        stats = apply(session)
    log.info("seed done: %s", stats)


if __name__ == "__main__":
    main()
