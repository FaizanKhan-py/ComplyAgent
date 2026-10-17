-- =====================================================================
--  ComplyGent - Credit Card Default Risk Database  (MySQL 8 / Workbench)
--  Source: UCI "Default of Credit Card Clients" (30,000 customers)
--  Matches the notebook: Data_pipeline_and_Modeling.ipynb
--
--  HOW TO USE
--   1. Open this file in MySQL Workbench and run it (lightning bolt).
--   2. Run load_data.py to fill the tables from the .xls file.
--   3. Query the views at the bottom, or use v_model_features for ML.
-- =====================================================================

DROP DATABASE IF EXISTS complygent_db;
CREATE DATABASE complygent_db CHARACTER SET utf8mb4;
USE complygent_db;

-- ---------------------------------------------------------------------
-- 1. LOOKUP TABLES  (small code -> label tables)
-- ---------------------------------------------------------------------
CREATE TABLE sex_types (
    sex_id  TINYINT     PRIMARY KEY,
    label   VARCHAR(10) NOT NULL
);
INSERT INTO sex_types VALUES (1,'Male'), (2,'Female');

-- Notebook merges education codes 0, 5, 6 into 4 (Other/Unknown)
CREATE TABLE education_levels (
    education_id TINYINT     PRIMARY KEY,
    label        VARCHAR(30) NOT NULL
);
INSERT INTO education_levels VALUES
    (1,'Graduate School'), (2,'University'), (3,'High School'), (4,'Other/Unknown');

-- Marriage code 0 (undocumented) is merged into 3 (Other)
CREATE TABLE marriage_status (
    marriage_id TINYINT     PRIMARY KEY,
    label       VARCHAR(10) NOT NULL
);
INSERT INTO marriage_status VALUES (1,'Married'), (2,'Single'), (3,'Other');

-- ---------------------------------------------------------------------
-- 2. CUSTOMERS  (one row per client + the target label + data split)
-- ---------------------------------------------------------------------
CREATE TABLE customers (
    customer_id        INT          PRIMARY KEY,           -- original ID column
    limit_bal          INT          NOT NULL CHECK (limit_bal > 0),  -- credit limit
    sex_id             TINYINT      NOT NULL,
    education_id       TINYINT      NOT NULL,
    marriage_id        TINYINT      NOT NULL,
    age                TINYINT UNSIGNED NOT NULL,
    default_next_month TINYINT      NOT NULL CHECK (default_next_month IN (0,1)),  -- target (Y)
    data_split         ENUM('train','val','test') NULL,    -- 70 / 15 / 15 stratified split
    FOREIGN KEY (sex_id)       REFERENCES sex_types(sex_id),
    FOREIGN KEY (education_id) REFERENCES education_levels(education_id),
    FOREIGN KEY (marriage_id)  REFERENCES marriage_status(marriage_id),
    INDEX idx_split   (data_split),
    INDEX idx_default (default_next_month)
);

-- ---------------------------------------------------------------------
-- 3. MONTHLY_HISTORY  (one row per customer per month)
--    Replaces the 18 columns PAY_0..PAY_6, BILL_AMT1..6, PAY_AMT1..6.
--    month_no 1 = most recent month (Sept 2005), 2 = Aug, ... 6 = Apr.
--    No upper limit on month_no -> works for N months of history,
--    which is exactly the "time-agnostic" idea in the notebook.
-- ---------------------------------------------------------------------
CREATE TABLE monthly_history (
    customer_id      INT      NOT NULL,
    month_no         TINYINT  NOT NULL CHECK (month_no >= 1),
    repayment_status TINYINT  NOT NULL CHECK (repayment_status BETWEEN -2 AND 9),
        -- -2 no consumption, -1 paid duly, 0 revolving credit, 1..9 months delayed
    bill_amt         INT      NOT NULL,   -- bill statement amount (can be negative)
    pay_amt          INT      NOT NULL CHECK (pay_amt >= 0),  -- amount paid
    PRIMARY KEY (customer_id, month_no),
    FOREIGN KEY (customer_id) REFERENCES customers(customer_id) ON DELETE CASCADE
);

-- ---------------------------------------------------------------------
-- 4. MODEL TRACKING  (every model you train + its scores)
-- ---------------------------------------------------------------------
CREATE TABLE model_runs (
    run_id          INT AUTO_INCREMENT PRIMARY KEY,
    model_name      VARCHAR(50) NOT NULL,   -- Logistic Regression, Random Forest, XGBoost, SVM
    is_tuned        BOOLEAN     NOT NULL DEFAULT FALSE,  -- TRUE after GridSearchCV
    hyperparameters JSON        NULL,       -- e.g. {"max_depth":5,"learning_rate":0.1}
    notes           VARCHAR(255) NULL,
    trained_at      TIMESTAMP   NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE model_metrics (
    metric_id  INT AUTO_INCREMENT PRIMARY KEY,
    run_id     INT NOT NULL,
    eval_split ENUM('val','test') NOT NULL,
    accuracy   DECIMAL(6,4),
    prec       DECIMAL(6,4),     -- precision
    recall     DECIMAL(6,4),
    f1_score   DECIMAL(6,4),
    roc_auc    DECIMAL(6,4),
    tn INT, fp INT, fn INT, tp INT,   -- confusion matrix
    UNIQUE (run_id, eval_split),
    FOREIGN KEY (run_id) REFERENCES model_runs(run_id) ON DELETE CASCADE
);

-- ---------------------------------------------------------------------
-- 5. PREDICTIONS  (risk score for each customer from a model run)
-- ---------------------------------------------------------------------
CREATE TABLE predictions (
    prediction_id       BIGINT AUTO_INCREMENT PRIMARY KEY,
    customer_id         INT          NOT NULL,
    run_id              INT          NOT NULL,
    default_probability DECIMAL(6,5) NOT NULL CHECK (default_probability BETWEEN 0 AND 1),
    predicted_default   TINYINT      NOT NULL CHECK (predicted_default IN (0,1)),
    predicted_at        TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (customer_id, run_id),
    FOREIGN KEY (customer_id) REFERENCES customers(customer_id) ON DELETE CASCADE,
    FOREIGN KEY (run_id)      REFERENCES model_runs(run_id)     ON DELETE CASCADE
);

-- =====================================================================
--  VIEWS
-- =====================================================================

-- A) ML-ready feature table: the SAME 3 engineered features as the notebook,
--    calculated live in SQL (so they are always in sync with the raw data).
--    Final model columns: limit_bal, age, sex_male, education_id,
--    on_time_payment_rate, avg_payment_delay, avg_credit_utilization.
CREATE VIEW v_model_features AS
SELECT
    c.customer_id,
    c.limit_bal,
    c.age,
    (c.sex_id = 1) AS sex_male,
    c.education_id,
    -- on-time months / months that were "valid" (not -2, not 0); 1 if none valid
    COALESCE(
        SUM(h.repayment_status = -1) /
        NULLIF(SUM(h.repayment_status NOT IN (-2, 0)), 0),
    1)                                              AS on_time_payment_rate,
    -- average delay, early payments counted as 0
    AVG(GREATEST(h.repayment_status, 0))            AS avg_payment_delay,
    -- average of (bill / credit limit)
    AVG(h.bill_amt / c.limit_bal)                   AS avg_credit_utilization,
    c.default_next_month,
    c.data_split
FROM customers c
JOIN monthly_history h ON h.customer_id = c.customer_id
GROUP BY c.customer_id, c.limit_bal, c.age, c.sex_id, c.education_id,
         c.default_next_month, c.data_split;

-- B) Default rate by education / sex / marriage (EDA charts from the notebook)
CREATE VIEW v_default_rate_by_group AS
SELECT 'education' AS group_type, e.label AS group_name,
       COUNT(*) AS customers, ROUND(AVG(c.default_next_month)*100,2) AS default_rate_pct
FROM customers c JOIN education_levels e ON e.education_id = c.education_id GROUP BY e.label
UNION ALL
SELECT 'sex', s.label, COUNT(*), ROUND(AVG(c.default_next_month)*100,2)
FROM customers c JOIN sex_types s ON s.sex_id = c.sex_id GROUP BY s.label
UNION ALL
SELECT 'marriage', m.label, COUNT(*), ROUND(AVG(c.default_next_month)*100,2)
FROM customers c JOIN marriage_status m ON m.marriage_id = c.marriage_id GROUP BY m.label;

-- C) Compare all models side by side (best recall first)
CREATE VIEW v_model_comparison AS
SELECT r.run_id, r.model_name, r.is_tuned, m.eval_split,
       m.accuracy, m.prec, m.recall, m.f1_score, m.roc_auc, r.trained_at
FROM model_runs r JOIN model_metrics m ON m.run_id = r.run_id
ORDER BY m.eval_split, m.recall DESC;

-- D) Risk report: customers with their prediction, label and readable info
CREATE VIEW v_customer_risk_report AS
SELECT p.run_id, c.customer_id, c.limit_bal, c.age,
       s.label AS sex, e.label AS education, m.label AS marriage,
       p.default_probability, p.predicted_default, c.default_next_month AS actual_default,
       CASE WHEN p.default_probability >= 0.7 THEN 'High'
            WHEN p.default_probability >= 0.4 THEN 'Medium'
            ELSE 'Low' END AS risk_level
FROM predictions p
JOIN customers c        ON c.customer_id  = p.customer_id
JOIN sex_types s        ON s.sex_id       = c.sex_id
JOIN education_levels e ON e.education_id = c.education_id
JOIN marriage_status m  ON m.marriage_id  = c.marriage_id;
