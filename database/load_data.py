"""
ComplyAgent: load the CSV files into the MySQL database.

Setup (once):
    pip install mysql-connector-python pandas python-dotenv

Usage:
    python load_data.py            -> loads the data (tables must be empty)
    python load_data.py --reset    -> empties the tables first, then loads
"""
import os
import sys
from pathlib import Path

import mysql.connector
import pandas as pd
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent   # the "database" folder
load_dotenv(BASE_DIR / ".env")               # .env sits next to this script

# ---------------- CONFIG (from .env) ----------------
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = int(os.getenv("DB_PORT", "3306"))
DB_USER = os.getenv("DB_USER", "root")
DB_PASSWORD = os.environ["DB_PASSWORD"]      # required, no default on purpose
DB_NAME = os.getenv("DB_NAME", "complyagentdb")
CSV_FOLDER = BASE_DIR / os.getenv("CSV_FOLDER", "Datasets")
BATCH_SIZE = 5000
# ----------------------------------------------------

# Parents first, children after (satisfies the foreign keys).
# agent_actions stays empty until the agent is built.
LOAD_ORDER = ["customers", "accounts", "monthly_records", "cases", "messages", "policies"]


def read_table(name: str) -> pd.DataFrame:
    path = CSV_FOLDER / f"{name}.csv"
    # 'NULL' and blanks in the CSV become real NULLs
    df = pd.read_csv(path, keep_default_na=False, na_values=["NULL", ""])
    # NaN -> None so MySQL stores NULL; numpy numbers -> plain Python numbers
    return df.astype(object).where(df.notna(), None)


def load_table(cur, name: str) -> int:
    df = read_table(name)
    cols = ", ".join(f"`{c}`" for c in df.columns)
    marks = ", ".join(["%s"] * len(df.columns))
    sql = f"INSERT INTO `{name}` ({cols}) VALUES ({marks})"
    rows = [tuple(r) for r in df.itertuples(index=False, name=None)]
    for i in range(0, len(rows), BATCH_SIZE):
        cur.executemany(sql, rows[i:i + BATCH_SIZE])
    return len(rows)


def main() -> None:
    reset = "--reset" in sys.argv
    conn = mysql.connector.connect(
        host=DB_HOST, port=DB_PORT, user=DB_USER, password=DB_PASSWORD, database=DB_NAME
    )
    cur = conn.cursor()
    try:
        if reset:
            cur.execute("SET FOREIGN_KEY_CHECKS = 0")
            for name in ["agent_actions"] + LOAD_ORDER[::-1]:
                cur.execute(f"TRUNCATE TABLE `{name}`")
            cur.execute("SET FOREIGN_KEY_CHECKS = 1")
            print("Tables emptied.")

        for name in LOAD_ORDER:
            n = load_table(cur, name)
            print(f"{name:18s} {n:>8,} rows loaded")
        conn.commit()

        print("\nRow counts in MySQL:")
        for name in LOAD_ORDER + ["agent_actions"]:
            cur.execute(f"SELECT COUNT(*) FROM `{name}`")
            print(f"{name:18s} {cur.fetchone()[0]:>8,}")
    except Exception:
        conn.rollback()
        print("\nLoad failed, nothing was saved. If the tables already hold data, run with --reset.")
        raise
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()