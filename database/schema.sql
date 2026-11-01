-- ComplyAgent: simple MySQL schema (7 tables)
-- Run in MySQL Workbench: File > Open SQL Script > Execute (lightning icon)

CREATE DATABASE IF NOT EXISTS complyagentdb;
USE complyagentdb;

-- 1) customers
CREATE TABLE customers (
    customer_id        INT PRIMARY KEY,
    full_name          VARCHAR(100),
    gender             VARCHAR(10),
    age                INT,
    marital_status     VARCHAR(20),
    education_level    VARCHAR(30),
    phone              VARCHAR(30),
    email              VARCHAR(100),
    preferred_channel  VARCHAR(10)          -- sms / email / call
);

-- 2) accounts
CREATE TABLE accounts (
    account_id      INT PRIMARY KEY,
    customer_id     INT NOT NULL,
    credit_limit    DECIMAL(12,2),
    open_date       DATE,
    account_status  VARCHAR(20),            -- active / overdue / closed
    FOREIGN KEY (customer_id) REFERENCES customers(customer_id)
);

-- 3) monthly_records (one row per account per month)
CREATE TABLE monthly_records (
    record_id     INT PRIMARY KEY,
    account_id    INT NOT NULL,
    month         DATE,                     -- first day of the month
    bill_amount   DECIMAL(12,2),
    amount_paid   DECIMAL(12,2),
    pay_status    INT,                      -- same meaning as PAY_0..PAY_6 (-2 to 8)
    days_late     INT,
    FOREIGN KEY (account_id) REFERENCES accounts(account_id)
);

-- 4) cases (overdue account + ML prediction)
CREATE TABLE cases (
    case_id              INT PRIMARY KEY,
    account_id           INT NOT NULL,
    days_overdue         INT,
    amount_overdue       DECIMAL(12,2),
    default_probability  DECIMAL(5,4),      -- model output, 0 to 1
    predicted_default    TINYINT,           -- 0 or 1
    risk_segment         VARCHAR(10),       -- low / medium / high
    case_status          VARCHAR(20),       -- open / in_review / resolved / escalated
    FOREIGN KEY (account_id) REFERENCES accounts(account_id)
);

-- 5) messages (the conversation, one row per message)
CREATE TABLE messages (
    message_id        INT PRIMARY KEY,
    case_id           INT NOT NULL,
    sender_type       VARCHAR(10),          -- customer / system / officer
    channel           VARCHAR(10),          -- sms / email / call
    message_text      TEXT,
    message_time      DATETIME,
    detected_intent   VARCHAR(50),
    hardship_flag     TINYINT DEFAULT 0,
    promised_amount   DECIMAL(12,2),        -- filled only if customer made a promise
    promise_date      DATE,
    promise_kept      TINYINT,              -- NULL until the promise date passes
    FOREIGN KEY (case_id) REFERENCES cases(case_id)
);

-- 6) policies
CREATE TABLE policies (
    policy_id       INT PRIMARY KEY,
    title           VARCHAR(100),
    category        VARCHAR(50),            -- contact / hardship / escalation ...
    content         TEXT,
    effective_date  DATE
);

-- 7) agent_actions (workflow: recommend > compliance > human > follow-up)
CREATE TABLE agent_actions (
    action_id           INT PRIMARY KEY,
    case_id             INT NOT NULL,
    recommended_action  VARCHAR(100),
    channel             VARCHAR(10),
    reasoning           TEXT,
    generated_message   TEXT,
    policy_id           INT,
    compliance_result   VARCHAR(10),        -- approved / rejected
    violation_type      VARCHAR(50),
    human_decision      VARCHAR(10),        -- approve / reject / modify
    human_comment       TEXT,
    escalated           TINYINT DEFAULT 0,
    followup_date       DATE,
    created_at          DATETIME,
    FOREIGN KEY (case_id)   REFERENCES cases(case_id),
    FOREIGN KEY (policy_id) REFERENCES policies(policy_id)
);
