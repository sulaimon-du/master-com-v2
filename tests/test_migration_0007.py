"""Migration 0007 on rows written under the 0006 schema (restores head afterwards)."""

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models import City, JobRequest
from tests.conftest import ALEMBIC_INI
from tests.test_constraints import NOW, master, user


def test_legacy_rows_are_backfilled_or_block_the_upgrade(migrated_engine) -> None:
    cfg = Config(ALEMBIC_INI)
    with Session(migrated_engine) as s:
        city = City(slug="t-mig", name_ru="М", name_tg="М", region="dushanbe", kind="city")
        client, owner = user(), user()
        s.add_all([city, client, owner])
        s.flush()
        m = master(owner)
        s.add(m)
        s.flush()
        base = {"client_id": client.id, "city_id": city.id, "description": "Tile it"}
        closed = JobRequest(
            **base,
            timing="urgent",
            status="closed",
            closed_at=NOW,
            close_reason="client_closed",
            chosen_master_id=m.id,
            master_chosen_at=NOW,
        )
        chosen = JobRequest(
            **base,
            timing="urgent",
            status="master_chosen",
            chosen_master_id=m.id,
            master_chosen_at=NOW,
            client_phone="+992901234567",
        )
        s.add_all([closed, chosen])
        s.commit()
        closed_id, chosen_id = closed.id, chosen.id
        ids = (client.id, owner.id, m.id, city.id)

    try:
        command.downgrade(cfg, "0006")
        with pytest.raises(
            RuntimeError, match="master_chosen or completed and have no client_phone"
        ):
            command.upgrade(cfg, "head")
        with migrated_engine.begin() as c:
            c.execute(
                text("UPDATE requests SET status = 'open', chosen_master_id = NULL WHERE id = :i"),
                {"i": chosen_id},
            )
        command.upgrade(cfg, "head")
        with migrated_engine.connect() as c:
            got = c.execute(
                text("SELECT master_chosen_at, closed_at FROM requests WHERE id = :i"),
                {"i": closed_id},
            ).one()
        assert got.master_chosen_at == got.closed_at
    finally:
        try:
            command.upgrade(cfg, "head")
        finally:
            with migrated_engine.begin() as c:
                c.execute(
                    text("DELETE FROM requests WHERE id = ANY(:r)"), {"r": [closed_id, chosen_id]}
                )
                c.execute(text("DELETE FROM master_profiles WHERE id = :m"), {"m": ids[2]})
                c.execute(text("DELETE FROM users WHERE id = ANY(:u)"), {"u": [ids[0], ids[1]]})
                c.execute(text("DELETE FROM cities WHERE id = :c"), {"c": ids[3]})
