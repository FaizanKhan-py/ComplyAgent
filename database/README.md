# ComplyAgent – Database

MySQL database for **ComplyAgent**, an AI-powered collections decision-support system.
It stores the customer data the system works with and is the base the later
agent stages (policies, actions, messages, escalation) will build on.

**Data source:** [UCI – Default of Credit Card Clients](https://archive.ics.uci.edu/dataset/350/default)
(30,000 customers, 6 months of payment history each, target = default next month)

---

## How the database fits into the system

1. **Customer data is stored** in `customers` and `monthly_history`.
2. **Features are built in SQL.** The view `v_model_features` turns the raw history into the
   7 features the trained model expects, using the same formulas as the notebook.
3. **The trained model scores customers.** The model is already trained in the notebook
   (on the UCI data) and is not retrained here. It reads features from `v_model_features`.
4. **Predictions are saved** in `predictions` (default probability + predicted class).
5. **The agent uses the predictions** to retrieve a policy, choose an action, write a message
   and escalate to a human when needed. These agent tables will be added in a later stage.

> Status: steps 1–2 are done. Steps 3–4 (scoring and saving predictions) and step 5 (agent tables) are next.

---

## Files

| File | Purpose |
|---|---|
| `complygent_schema.sql` | Creates the database `complygent_db`: tables, constraints and views |
| `load_data.py` | Downloads the UCI dataset, cleans it, splits it 70/15/15 and loads it into MySQL |

## Tables

| Table | What it holds |
|---|---|
| `customers` | One row per customer: credit limit, sex, education, marriage, age, target (`default_next_month`), train/val/test split |
| `monthly_history` | One row per customer per month: repayment status, bill amount, payment amount |
| `sex_types`, `education_levels`, `marriage_status` | Small lookup tables (code → label) |
| `model_runs`, `model_metrics` | Model tracking: one row per trained model and its scores |
| `predictions` | Default probability for each customer from a model run |

## Views

| View | What it gives |
|---|---|
| `v_model_features` | The ML dataset: `limit_bal`, `age`, `sex_male`, `education_id`, `on_time_payment_rate`, `avg_payment_delay`, `avg_credit_utilization`, plus the target and the split |
| `v_default_rate_by_group` | Default rate by education, sex and marriage |
| `v_model_comparison` | All models side by side |
| `v_customer_risk_report` | Customers with their prediction and a Low / Medium / High risk level |

The 3 engineered features are **not stored**; they are calculated by `v_model_features`,
so they always match the raw data.

---

## Setup

**Requirements:** MySQL 8 + MySQL Workbench, Python 3.10+

```bash
pip install pandas ucimlrepo mysql-connector-python scikit-learn
```

**1. Create the database** – open `complygent_schema.sql` in MySQL Workbench and run it.
(It starts with `DROP DATABASE IF EXISTS`, so running it again wipes the data.)

**2. Set your MySQL password** (never commit it):

```powershell
# Windows PowerShell
$env:DB_PASSWORD = "your_mysql_password"
```
```bash
# Mac / Linux
export DB_PASSWORD="your_mysql_password"
```

**3. Load the data**

```bash
python load_data.py
```

Expected end of output: `Done: 30000 customers and 180000 monthly rows.`

**4. Check it in Workbench**

```sql
USE complygent_db;
SELECT COUNT(*) FROM customers;        -- 30000
SELECT COUNT(*) FROM monthly_history;  -- 180000
SELECT * FROM v_model_features LIMIT 5;
```

---

## Data cleaning applied while loading

- Education codes 0, 5 and 6 → 4 (Other/Unknown)
- Marriage code 0 → 3 (Other)
- Wide monthly columns (`PAY_0..6`, `BILL_AMT1..6`, `PAY_AMT1..6`) → one row per month in `monthly_history`
- Train/val/test split: 70/15/15, stratified, `random_state=42` (same as the notebook)

The raw data has no missing values or duplicates.

---

## Notes

- The UCI data is a 2005 sample with no names, contact details or current overdue amounts.
  The agent stage will need sample contact/overdue fields added to `customers`.
- The risk levels in `v_customer_risk_report` (High ≥ 0.7, Medium ≥ 0.4) are placeholder cutoffs.

## Next steps

- [ ] Save the trained model (`joblib.dump`) and score customers from `v_model_features`
- [ ] Write the scores into `predictions`
- [ ] Add agent tables: `policies`, `collection_actions`, `agent_decisions`, `communications`, `escalations`
