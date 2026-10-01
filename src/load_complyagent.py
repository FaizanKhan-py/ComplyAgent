"""
Load complyagent_training_10k.csv into MySQL:
    customers -> loans -> ml_training_data
(payments table is left empty on purpose)

pip install pandas pymysql
"""
import pandas as pd
import pymysql

from pathlib import Path
CSV_PATH = Path(__file__).resolve().parent.parent / "data" / "processed" / "complyagent_training_10k.csv"
DB = dict(
    host="localhost",
    user="root",
    password="Heisen21",      # <-- change this
    database="complyagent",
    charset="utf8mb4",
)
CHUNK = 2000


# ---------- 1) Read + clean the CSV ----------
df = pd.read_csv(CSV_PATH)
df["start_date"] = pd.to_datetime(df["start_date"]).dt.date

# integer columns -> real ints (CSV may hold 675.0 etc.)
for c in ["loan_term", "grade_num", "fico_range_low", "days_overdue",
          "hardship_label", "continued_delinquency_label"]:
    df[c] = df[c].astype(int)

assert df["email"].is_unique, "emails must be unique (used to map customer_id)"


def to_rows(frame, cols):
    """DataFrame -> list of tuples with plain Python types and None for NaN."""
    sub = frame[cols].astype(object)
    sub = sub.where(frame[cols].notna(), None)
    return list(sub.itertuples(index=False, name=None))


def insert(cur, table, cols, rows):
    sql = f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})"
    for i in range(0, len(rows), CHUNK):
        cur.executemany(sql, rows[i:i + CHUNK])
    print(f"{table}: inserted {len(rows)} rows")


# ---------- 2) Insert ----------
conn = pymysql.connect(**DB)
try:
    with conn.cursor() as cur:
        # Safety: don't double-load
        for t in ["customers", "loans", "ml_training_data"]:
            cur.execute(f"SELECT COUNT(*) FROM {t}")
            if cur.fetchone()[0] > 0:
                raise SystemExit(f"Table {t} is not empty. Truncate it first or stop here.")

        # --- customers ---
        cust_cols = ["full_name", "email", "phone", "annual_income"]
        insert(cur, "customers", cust_cols, to_rows(df, cust_cols))

        # map email -> customer_id (email is unique)
        cur.execute("SELECT customer_id, email FROM customers")
        email_to_cid = {email: cid for cid, email in cur.fetchall()}
        df["customer_id"] = df["email"].map(email_to_cid)
        assert df["customer_id"].notna().all(), "some customers missing after insert"

        # --- loans (one loan per customer) ---
        loan_cols = ["customer_id", "loan_amount", "installment_amount", "interest_rate",
                     "loan_term", "grade_num", "dti", "fico_range_low", "start_date",
                     "outstanding_balance", "overdue_amount", "days_overdue"]
        insert(cur, "loans", loan_cols, to_rows(df, loan_cols))

        # map customer_id -> loan_id
        cur.execute("SELECT loan_id, customer_id FROM loans")
        cid_to_lid = {cid: lid for lid, cid in cur.fetchall()}
        df["loan_id"] = df["customer_id"].map(cid_to_lid)
        assert df["loan_id"].notna().all(), "some loans missing after insert"

        # --- ml_training_data ---
        ml_cols = ["loan_id", "annual_income", "loan_amount", "installment_amount",
                   "interest_rate", "loan_term", "grade_num", "dti", "fico_range_low",
                   "loan_to_income", "hardship_label", "continued_delinquency_label"]
        insert(cur, "ml_training_data", ml_cols, to_rows(df, ml_cols))

    conn.commit()
    print("Committed.")

    # ---------- 3) Quick checks ----------
    with conn.cursor() as cur:
        for t in ["customers", "loans", "ml_training_data", "payments"]:
            cur.execute(f"SELECT COUNT(*) FROM {t}")
            print(f"{t}: {cur.fetchone()[0]}")
        cur.execute("""SELECT continued_delinquency_label, hardship_label, COUNT(*)
                       FROM ml_training_data GROUP BY 1, 2""")
        print("label counts:", cur.fetchall())

except Exception:
    conn.rollback()
    raise
finally:
    conn.close()