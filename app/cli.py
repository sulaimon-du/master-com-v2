"""Owner commands until the admin panel exists (week 10).

    uv run python -m app.cli claims list
    uv run python -m app.cli claims approve <id>
    uv run python -m app.cli claims reject <id>
    uv run python -m app.cli search rebuild
    uv run python -m app.cli demo seed [--count 10]
    uv run python -m app.cli demo remove

Demo accounts (R-027) on staging: run from a laptop with the staging settings in the
git-ignored `.env.staging`: `uv run --env-file .env.staging python -m app.cli demo seed`.

On staging (no shell on Render free) the same decision is one statement in the Neon SQL Editor:
    UPDATE google_claims SET status = 'approved', resolved_at = now()
    WHERE id = <id> AND status = 'pending';
"""

import argparse
import sys

from app.core.config import get_settings
from app.core.db import get_sessionmaker
from app.models.user import User
from app.seed import demo
from app.services import auth, search_index
from app.services.storage import get_storage


def run_demo(args: argparse.Namespace) -> int:
    """Seed or remove the demo accounts. Prints counts only, never the password."""
    settings = get_settings()
    if settings.app_env == "production":
        print("demo accounts are never created or removed in production", file=sys.stderr)
        return 2
    if settings.is_production_like and not settings.r2_enabled:
        # Local disk on a laptop would hold the photos while the staging site points at R2.
        print("staging needs the R2 settings for demo photos; nothing was done", file=sys.stderr)
        return 2
    storage = get_storage()
    print(f"target: APP_ENV={settings.app_env}, storage={'R2' if settings.r2_enabled else 'disk'}")
    with get_sessionmaker()() as db:
        if args.action == "seed":
            written: list[str] = []
            try:
                stats = demo.seed(
                    db,
                    storage,
                    count=args.count,
                    accounts_file=demo.ACCOUNTS_FILE,
                    written=written,
                )
                db.commit()
            except BaseException as exc:
                db.rollback()
                demo.delete_files(storage, written)  # nothing points at them any more
                if isinstance(exc, demo.DemoError):
                    print(str(exc), file=sys.stderr)
                    return 1
                raise
            print(demo.describe(stats))
            print(f"sign-in details: {demo.ACCOUNTS_FILE.relative_to(demo.ROOT)}")
            return 0
        try:
            removed = demo.remove(db)
        except demo.DemoError as exc:
            db.rollback()
            print(str(exc), file=sys.stderr)
            return 1
        db.commit()
        failed = demo.delete_files(storage, removed.keys)
        print(
            f"removed {removed.users} demo users, {removed.masters} masters, "
            f"{removed.photos} portfolio photos, {removed.requests} requests; "
            f"{len(removed.keys) - failed} files deleted, {failed} failed"
        )
        return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    groups = parser.add_subparsers(dest="group", required=True)
    claims = groups.add_parser("claims", help="Google requests for unconfirmed accounts (R-019)")
    actions = claims.add_subparsers(dest="action", required=True)
    actions.add_parser("list")
    for name in ("approve", "reject"):
        actions.add_parser(name).add_argument("claim_id", type=int)
    search = groups.add_parser("search", help="The master search index (week 7)")
    search.add_subparsers(dest="action", required=True).add_parser("rebuild")
    demo_cmd = groups.add_parser("demo", help="Demo master accounts for staging (R-027)")
    demo_actions = demo_cmd.add_subparsers(dest="action", required=True)
    demo_actions.add_parser("seed").add_argument("--count", type=int, default=len(demo.MASTERS))
    demo_actions.add_parser("remove")
    args = parser.parse_args(argv)
    if args.group == "demo":
        return run_demo(args)

    with get_sessionmaker()() as db:
        if args.group == "search":
            count = search_index.rebuild_all(db)
            db.commit()
            print(f"{count} search rows rebuilt")
            return 0
        if args.action == "list":
            for claim in auth.pending_google_claims(db):
                created = f"{claim.created_at:%Y-%m-%d %H:%M}"
                user = db.get(User, claim.user_id)
                # Approving these would do nothing: the owner proved the account meanwhile.
                note = "" if user and auth.claimable(user) else "\t(account no longer claimable)"
                print(f"{claim.id}\t{created}\t{claim.email}\t{claim.google_name or ''}{note}")
            return 0
        outcome, claim = auth.resolve_google_claim(
            db, args.claim_id, approve=args.action == "approve"
        )
        if outcome is not auth.Outcome.OK or claim is None:
            print(f"claim {args.claim_id} not found or already decided", file=sys.stderr)
            return 1
        db.commit()
        print(f"claim {claim.id} for {claim.email}: {claim.status}")
        if claim.status == "approved":
            print("It takes effect at the next Google sign-in, if the account is still unproven.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
