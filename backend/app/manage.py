"""Account administration from the server's command line.

Run from the backend directory with the virtualenv active:

    python -m app.manage create-admin --email you@example.com --name "Your Name"
    python -m app.manage set-role --email someone@example.com --role admin
    python -m app.manage reset-password --email someone@example.com
    python -m app.manage list-users

Sign-up through the web page always creates an ordinary user. Administrators can only be made
here, by someone who already has access to the server.
"""

from __future__ import annotations

import argparse
import getpass
import sys

from dotenv import load_dotenv

from . import auth
from .config import BACKEND_DIR, Settings
from .db import Database, EmailTaken


def _read_password() -> str:
    """Prompted without echo. When input is piped, the first line is the password."""
    if sys.stdin.isatty():
        password = getpass.getpass("Password: ")
        if getpass.getpass("Repeat password: ") != password:
            raise SystemExit("The passwords do not match.")
        return password
    return sys.stdin.readline().rstrip("\r\n")


def _existing(db: Database, email: str) -> dict:
    user = db.get_user_by_email(email)
    if not user:
        raise SystemExit(f"No account with the email {email}.")
    return user


def main(argv: list[str] | None = None, db: Database | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.manage", description=__doc__.split("\n")[0]
    )
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create-admin", help="create a new administrator account")
    create.add_argument("--email", required=True)
    create.add_argument("--name", required=True)
    role = commands.add_parser("set-role", help="promote or demote an existing account")
    role.add_argument("--email", required=True)
    role.add_argument("--role", required=True, choices=auth.ROLES)
    reset = commands.add_parser("reset-password", help="set a new password and end all sessions")
    reset.add_argument("--email", required=True)
    commands.add_parser("list-users", help="list every account")
    args = parser.parse_args(argv)

    if db is None:
        load_dotenv(BACKEND_DIR / ".env")
        db = Database(Settings.from_env().db_path)

    try:
        if args.command == "list-users":
            users = db.list_users()
            for user in users:
                print(f"{user['id']:>4}  {user['role']:<5}  {user['email']}  ({user['full_name']})")
            print(f"{len(users)} account(s)")
            return 0

        email = auth.normalize_email(args.email)
        if args.command == "create-admin":
            name = auth.clean_name(args.name)
            if db.get_user_by_email(email):
                raise SystemExit(
                    f"{email} already has an account. To promote it, run: "
                    f"python -m app.manage set-role --email {email} --role admin"
                )
            password = _read_password()
            auth.check_password_rules(password, email)
            db.create_user(email, name, auth.hash_password(password), "admin")
            print(f"Created administrator {email}.")
        elif args.command == "set-role":
            user = _existing(db, email)
            db.set_role(user["id"], args.role)
            print(f"{email} is now '{args.role}'. Their open sessions were ended.")
        elif args.command == "reset-password":
            user = _existing(db, email)
            password = _read_password()
            auth.check_password_rules(password, email)
            db.set_password_hash(user["id"], auth.hash_password(password), end_sessions=True)
            print(f"Password changed for {email}. Their open sessions were ended.")
    except auth.AuthError as exc:
        raise SystemExit(exc.message) from exc
    except EmailTaken:
        raise SystemExit("That email address already has an account.") from None
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
