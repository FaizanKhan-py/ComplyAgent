"""
ComplyGent - load the UCI credit card default data into MySQL.
Run:  python load_data.py
"""
import os
import pandas as pd
import mysql.connector
from ucimlrepo import fetch_ucirepo
from sklearn.model_selection import train_test_split

# ---------------- CONFIG ----------------
DB_PASSWORD = os.getenv("DB_PASSWORD")
if not DB_PASSWORD:
    raise SystemExit("Set the DB_PASSWORD environment variable first (see database/README.md).")

DB = dict(host="localhost", user="root",
          password=DB_PASSWORD,
          database="complygent_db")
# ----------------------------------------

# 1. Fetch raw data from UCI
dataset = fetch_ucirepo(id=350)
df = pd.concat([dataset.data.features, dataset.data.targets], axis=1)
assert df.shape == (30000, 24), f"Unexpected shape: {df.shape}"

# 2. Name the columns
df.columns = [
    "LIMIT_BAL", "SEX", "EDUCATION", "MARRIAGE", "AGE",
    "PAY_0", "PAY_2", "PAY_3", "PAY_4", "PAY_5", "PAY_6",
    "BILL_AMT1", "BILL_AMT2", "BILL_AMT3", "BILL_AMT4", "BILL_AMT5", "BILL_AMT6",
    "PAY_AMT1", "PAY_AMT2", "PAY_AMT3", "PAY_AMT4", "PAY_AMT5", "PAY_AMT6",
    "default_next_month",
]
df.insert(0, "ID", range(1, len(df) + 1))

# 3. Cleaning
df["EDUCATION"] = df["EDUCATION"].replace({0: 4, 5: 4, 6: 4})
df["MARRIAGE"]  = df["MARRIAGE"].replace({0: 3})

# 4. Split 70/15/15 (same as notebook)
y = df["default_next_month"]
temp_idx, test_idx = train_test_split(df.index, test_size=0.15, random_state=42, stratify=y)
train_idx, val_idx = train_test_split(temp_idx, test_size=0.17647, random_state=42, stratify=y.loc[temp_idx])
df["data_split"] = None
df.loc[train_idx, "data_split"] = "train"
df.loc[val_idx,   "data_split"] = "val"
df.loc[test_idx,  "data_split"] = "test"

# 5. Build rows
customers = [
    (int(r.ID), int(r.LIMIT_BAL), int(r.SEX), int(r.EDUCATION), int(r.MARRIAGE),
     int(r.AGE), int(r.default_next_month), r.data_split)
    for r in df.itertuples()
]
pay_cols = ["PAY_0", "PAY_2", "PAY_3", "PAY_4", "PAY_5", "PAY_6"]
history = []
for r in df.to_dict("records"):
    for m in range(1, 7):
        history.append((int(r["ID"]), m, int(r[pay_cols[m-1]]),
                        int(r[f"BILL_AMT{m}"]), int(r[f"PAY_AMT{m}"])))

# 6. Insert in SMALL batches
def insert_in_batches(conn, cur, sql, rows, size=1000):
    for i in range(0, len(rows), size):
        cur.executemany(sql, rows[i:i + size])
        conn.commit()
        print(f"  inserted {min(i + size, len(rows))}/{len(rows)}")

conn = mysql.connector.connect(**DB)
cur = conn.cursor()
cur.execute("DELETE FROM monthly_history")
cur.execute("DELETE FROM customers")
conn.commit()

print("Inserting customers...")
insert_in_batches(conn, cur, """INSERT INTO customers
    (customer_id, limit_bal, sex_id, education_id, marriage_id, age, default_next_month, data_split)
    VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""", customers)

print("Inserting monthly_history...")
insert_in_batches(conn, cur, """INSERT INTO monthly_history
    (customer_id, month_no, repayment_status, bill_amt, pay_amt)
    VALUES (%s,%s,%s,%s,%s)""", history)

print(f"Done: {len(customers)} customers and {len(history)} monthly rows.")
cur.close(); conn.close()