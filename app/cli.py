"""Owner commands until the admin panel exists (week 10).

    uv run python -m app.cli claims list
    uv run python -m app.cli claims approve <id>
    uv run python -m app.cli claims reject <id>

On staging (no shell on Render free) the same decision is one statement in the Neon SQL Editor:
    UPDATE google_claims SET status = 'approved', resolved_at = now()
    WHERE id = <id> AND status = 'pending';
"""

import argparse
import sys

from app.core.db import get_sessionmaker
from app.models.user import User
from app.services import auth


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    groups = parser.add_subparsers(dest="group", required=True)
    claims = groups.add_parser("claims", help="Google requests for unconfirmed accounts (R-019)")
    actions = claims.add_subparsers(dest="action", required=True)
    actions.add_parser("list")
    for name in ("approve", "reject"):
        actions.add_parser(name).add_argument("claim_id", type=int)
    args = parser.parse_args(argv)

    with get_sessionmaker()() as db:
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
