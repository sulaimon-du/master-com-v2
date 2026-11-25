import subprocess
from pathlib import Path

ENTRYPOINT = Path(__file__).resolve().parent.parent / "deploy" / "entrypoint.sh"


def _run(tmp_path: Path, env: dict[str, str], *args: str) -> list[str]:
    """Run the entrypoint with stub python, alembic and uvicorn that only record their calls."""
    log = tmp_path / "calls.log"
    for name in ("python", "alembic", "uvicorn"):
        stub = tmp_path / name
        stub.write_text(f'#!/bin/sh\necho "{name} $*" >> "{log}"\n')
        stub.chmod(0o755)
    subprocess.run(  # noqa: S603 - fixed script path, stubbed tools
        ["/bin/sh", str(ENTRYPOINT), *args],
        env={"PATH": f"{tmp_path}:/usr/bin:/bin", **env},
        check=True,
    )
    return log.read_text().splitlines() if log.exists() else []


def test_site_start_rebuilds_the_search_index_after_the_seed(tmp_path: Path) -> None:
    calls = _run(tmp_path, {"RUN_MIGRATIONS": "1", "RUN_SEED": "1"})
    assert calls[:3] == [
        "alembic upgrade head",
        "python -m app.seed",
        "python -m app.cli search rebuild",
    ]
    assert calls[3].startswith("uvicorn --factory app.main:create_app")


def test_bot_start_skips_migrations_seed_and_rebuild(tmp_path: Path) -> None:
    calls = _run(tmp_path, {"RUN_MIGRATIONS": "1", "RUN_SEED": "1"}, "python", "-m", "app.bot")
    assert calls == ["python -m app.bot"]


def test_no_seed_means_no_rebuild(tmp_path: Path) -> None:
    calls = _run(tmp_path, {})
    assert len(calls) == 1 and calls[0].startswith("uvicorn")
