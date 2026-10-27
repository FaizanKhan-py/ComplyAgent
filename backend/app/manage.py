"""Account and data administration from the server's command line.

Run from the backend directory with the virtualenv active:

    python -m app.manage create-admin --email you@example.com --name "Your Name"
    python -m app.manage set-role --email someone@example.com --role admin
    python -m app.manage reset-password --email someone@example.com
    python -m app.manage list-users
    python -m app.manage import-customers [--csv path/to/data.csv|.xls|.xlsx] [--replace]
    python -m app.manage import-emails path/to/data_with_emails.xls
    python -m app.manage score-customers       # after running the notebook
    python -m app.manage add-documents ../sample_policies/*_policy.*
    python -m app.manage evaluate-rag
    python -m app.manage run-follow-ups       # the Follow-up Agent: every follow-up now due
    python -m app.manage poll-email           # read the collections mailbox for replies
    python -m app.manage set-supervisor --email lead@example.com [--off]
    python -m app.manage evaluate-agents      # score the agents on sample_policies/agent_eval.json

Sign-up through the web page creates an ordinary user, unless the address is listed in
ADMIN_EMAILS. Otherwise administrators can only be made here, by someone who already has
access to the server.
"""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

from dotenv import load_dotenv

from .core import auth
from .core.config import BACKEND_DIR, Settings
from .core.db import Database, EmailTaken
from .customers.portfolio import DATA_URL, ImportFailed, Portfolio
from .langchain.llm import build_generator
from .rag import evaluation
from .rag.embeddings import build_embedder
from .rag.extraction import ValidationError
from .rag.knowledge_base import DuplicateDocument, KnowledgeBase
from .risk import risk_model


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


def _build_kb(settings: Settings, db: Database) -> KnowledgeBase:
    return KnowledgeBase(settings, db, build_embedder(settings), build_generator(settings))


def _knowledge_base_command(args, kb: KnowledgeBase) -> int:
    if args.command == "add-documents":
        failed = 0
        for path in args.files:
            try:
                doc = kb.register_upload(path.name, path.read_bytes())
            except OSError as exc:
                print(f"{path}: cannot be read ({exc.strerror}).")
                failed += 1
                continue
            except ValidationError as exc:
                print(f"{path}: {exc}")
                failed += 1
                continue
            except DuplicateDocument as exc:
                print(f"{path}: already uploaded as {exc.existing['filename']}, skipped.")
                continue
            kb.ingest(doc["id"])
            doc = kb.db.get_document(doc["id"])
            if doc["status"] == "indexed":
                print(f"{path}: indexed, {doc['chunk_count']} chunks.")
            else:
                print(f"{path}: failed. {doc['error']}")
                failed += 1
        return 1 if failed else 0

    report = evaluation.evaluate(kb, evaluation.load_questions(args.questions))
    for r in report["results"]:
        mark = "ok  " if r["retrieved"] and r["answered"] else "MISS"
        print(f"{mark} {r['question']}")
        if not r["retrieved"]:
            print(f"     expected {r['expected_document']}, top source {r['top_document']}")
        elif not r["answered"]:
            print(f"     answer ({r['answer_mode']}): {r['answer'][:160]}")
    n = report["questions"]
    print(f"\nRetrieval: {report['retrieval_hits']}/{n} questions found the right document first.")
    if report["generator"]:
        print(f"Answers:   {report['correct_answers']}/{n} correct ({report['generator']}).")
    else:
        print("Answers:   not checked, because no answer model is configured.")
    return 0


def main(
    argv: list[str] | None = None,
    db: Database | None = None,
    models_dir: Path | None = None,
    kb: KnowledgeBase | None = None,
) -> int:
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
    lead = commands.add_parser(
        "set-supervisor", help="make an administrator a team lead (the second approval)"
    )
    lead.add_argument("--email", required=True)
    lead.add_argument("--off", action="store_true", help="remove the team lead role")
    reset = commands.add_parser("reset-password", help="set a new password and end all sessions")
    reset.add_argument("--email", required=True)
    commands.add_parser("list-users", help="list every account")
    data = commands.add_parser("import-customers", help="load the UCI credit card default data")
    data.add_argument(
        "--csv",
        type=Path,
        help="a downloaded copy (CSV, .xls or .xlsx); otherwise it is downloaded",
    )
    emails = commands.add_parser(
        "import-emails", help="add the EMAIL column of a dataset copy to the loaded customers"
    )
    emails.add_argument("file", type=Path)
    data.add_argument("--replace", action="store_true", help="delete the loaded data first")
    commands.add_parser(
        "score-customers", help="score every customer with the model the notebook saved"
    )
    docs = commands.add_parser("add-documents", help="upload and index policy documents")
    docs.add_argument("files", nargs="+", type=Path)
    check = commands.add_parser("evaluate-rag", help="ask the test questions and score the answers")
    check.add_argument("--questions", type=Path, default=evaluation.DEFAULT_QUESTIONS)
    commands.add_parser("run-follow-ups", help="carry out the follow-ups that are due")
    commands.add_parser("poll-email", help="read the collections mailbox for customer replies")
    agents = commands.add_parser("evaluate-agents", help="score the agents against test cases")
    agents.add_argument("--cases", type=Path, default=None)
    args = parser.parse_args(argv)

    load_dotenv(BACKEND_DIR / ".env")
    settings = Settings.from_env()
    if db is None:
        db = Database.from_settings(settings)

    try:
        if args.command == "list-users":
            users = db.list_users()
            for user in users:
                print(f"{user['id']:>4}  {user['role']:<5}  {user['email']}  ({user['full_name']})")
            print(f"{len(users)} account(s)")
            return 0
        if args.command == "import-emails":
            result = Portfolio(db).import_emails(args.file)
            print(f"Added email addresses to {result['updated']} customers.")
            if result["unknown_ids"]:
                print(
                    f"Skipped {len(result['unknown_ids'])} rows whose ID is not a loaded customer."
                )
            if result["without_email"]:
                print(f"{result['without_email']} customers still have no email address.")
            return 0
        if args.command == "import-customers":
            count = Portfolio(db).import_data(args.csv, replace=args.replace)
            print(f"Imported {count} customers from {args.csv or DATA_URL}.")
            print("Next, score them with: python -m app.manage score-customers")
            return 0
        if args.command in ("add-documents", "evaluate-rag"):
            return _knowledge_base_command(args, kb or _build_kb(settings, db))
        if args.command == "poll-email":
            from .decisions.conversation import Conversations, poll_mailbox
            from .integrations.mail import build_mailer

            kb = kb or _build_kb(settings, db)
            result = poll_mailbox(
                kb, Portfolio(db), kb.generator, Conversations(db), build_mailer(settings)
            )
            print(
                f"{result['fetched']} new email(s): {len(result['matched'])} matched to a "
                f"conversation, {result['unmatched']} left unread for a person."
            )
            for failure in result["failed"]:
                print(
                    f"Conversation {failure['conversation_id']}: {failure['error']}",
                    file=sys.stderr,
                )
            return 1 if result["failed"] else 0
        if args.command == "evaluate-agents":
            from .decisions import agent_evaluation

            kb = kb or _build_kb(settings, db)
            if kb.generator is None:
                print(
                    "No language model is configured: set the AZURE_AI_* settings.", file=sys.stderr
                )
                return 1
            report = agent_evaluation.run(
                kb, kb.generator, args.cases or agent_evaluation.DEFAULT_CASES
            )
            print(agent_evaluation.format_report(report))
            return 0
        if args.command == "run-follow-ups":
            from .decisions import follow_up
            from .decisions.conversation import Conversations

            kb = kb or _build_kb(settings, db)
            result = follow_up.run_due(kb, Portfolio(db), kb.generator, Conversations(db))
            print(f"{result['due']} follow-up(s) due, {len(result['done'])} done.")
            for failure in result["failed"]:
                print(f"Follow-up {failure['id']} failed: {failure['error']}", file=sys.stderr)
            return 1 if result["failed"] else 0
        if args.command == "score-customers":
            result = risk_model.score_all(db, models_dir or settings.risk_model_dir)
            test = result["test"]
            print(
                f"Scored {result['customers']} customers with {result['model_name']} "
                f"(trained by the notebook on {result['trained_at'][:10]})."
            )
            print(
                f"Test customers: ROC-AUC {test['roc_auc']:.4f}, recall {test['recall']:.4f}, "
                f"precision {test['precision']:.4f}, accuracy {test['accuracy']:.4f}"
            )
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
        elif args.command == "set-supervisor":
            user = _existing(db, email)
            if user["role"] != "admin" and not args.off:
                print(f"{email} must be an administrator first (set-role).", file=sys.stderr)
                return 1
            db.set_supervisor(user["id"], not args.off)
            what = "no longer a team lead" if args.off else "a team lead (second approver)"
            print(f"{email} is now {what}. Their open sessions were ended.")
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
    except (ImportFailed, risk_model.ScoringFailed) as exc:
        raise SystemExit(str(exc)) from exc
    except EmailTaken:
        raise SystemExit("That email address already has an account.") from None
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
