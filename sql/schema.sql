-- ComplyAgent schema. Loaded by src/load_complyagent.py (payments is left empty).
CREATE DATABASE IF NOT EXISTS complyagent CHARACTER SET utf8mb4;
USE complyagent;

CREATE TABLE IF NOT EXISTS customers (
    customer_id    INT AUTO_INCREMENT PRIMARY KEY,
    full_name      VARCHAR(100)  NOT NULL,
    email          VARCHAR(255)  NOT NULL UNIQUE,
    phone          VARCHAR(30),
    annual_income  DECIMAL(14,2),
    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS loans (
    loan_id              INT AUTO_INCREMENT PRIMARY KEY,
    customer_id          INT NOT NULL,
    loan_amount          DECIMAL(12,2) NOT NULL,
    installment_amount   DECIMAL(10,2) NOT NULL,
    interest_rate        DECIMAL(5,2)  NOT NULL,
    loan_term            INT           NOT NULL,
    grade_num            TINYINT,
    dti                  DECIMAL(7,2),
    fico_range_low       INT,
    start_date           DATE,
    outstanding_balance  DECIMAL(12,2),
    overdue_amount       DECIMAL(12,2),
    days_overdue         INT,
    FOREIGN KEY (customer_id) REFERENCES customers(customer_id)
);

CREATE TABLE IF NOT EXISTS payments (
    payment_id    INT AUTO_INCREMENT PRIMARY KEY,
    loan_id       INT NOT NULL,
    payment_date  DATE NOT NULL,
    amount        DECIMAL(12,2) NOT NULL,
    status        VARCHAR(20),
    FOREIGN KEY (loan_id) REFERENCES loans(loan_id)
);

CREATE TABLE IF NOT EXISTS ml_training_data (
    id                           INT AUTO_INCREMENT PRIMARY KEY,
    loan_id                      INT NOT NULL,
    annual_income                DECIMAL(14,2),
    loan_amount                  DECIMAL(12,2),
    installment_amount           DECIMAL(10,2),
    interest_rate                DECIMAL(5,2),
    loan_term                    INT,
    grade_num                    TINYINT,
    dti                          DECIMAL(7,2),
    fico_range_low               INT,
    loan_to_income               DECIMAL(10,4),
    hardship_label               TINYINT NOT NULL,
    continued_delinquency_label  TINYINT NOT NULL,
    FOREIGN KEY (loan_id) REFERENCES loans(loan_id)
);
