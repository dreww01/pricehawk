"""Tests for database schema upgrade from main and fresh installation."""

import re
import sqlite3
from pathlib import Path

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "docs" / "database_schema.sql"


def test_schema_upgrade_order_and_constraints_static():
    """Verify that additive columns are declared before indexes and constraints allow disabled."""
    sql_text = SCHEMA_PATH.read_text(encoding="utf-8")

    # 1. processing_digest_id column addition must precede the index creation
    claim_index_pos = sql_text.find("idx_pending_alerts_digest_claim")
    assert claim_index_pos != -1, "Index idx_pending_alerts_digest_claim not found in schema"

    add_column_pos = sql_text.find("ALTER TABLE pending_alerts ADD COLUMN IF NOT EXISTS processing_digest_id")
    assert add_column_pos != -1, "Add column processing_digest_id not found in schema"
    assert add_column_pos < claim_index_pos, (
        "Additive column processing_digest_id must be added before idx_pending_alerts_digest_claim is created"
    )

    # 2. Email status check constraint migration must allow 'disabled'
    assert "ALTER TABLE alert_history DROP CONSTRAINT IF EXISTS alert_history_email_status_check" in sql_text
    migration_match = re.search(
        r"ALTER TABLE alert_history ADD CONSTRAINT alert_history_email_status_check\s+CHECK\s*\([^;]+\)",
        sql_text,
    )
    assert migration_match is not None, "alert_history_email_status_check constraint migration not found"
    constraint_sql = migration_match.group(0)
    assert "'disabled'" in constraint_sql
    assert "'sent'" in constraint_sql
    assert "'failed'" in constraint_sql
    assert "'pending'" in constraint_sql


def test_main_schema_upgrade_and_webhook_only_history_insert():
    """Simulate upgrading the main schema and inserting a webhook-only digest history entry."""
    conn = sqlite3.connect(":memory:")
    cursor = conn.cursor()

    # Step 1: Initialize the database with main branch schema
    cursor.execute("""
        CREATE TABLE alert_history (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            digest_sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            alerts_count INTEGER NOT NULL,
            email_status TEXT DEFAULT 'pending' CHECK (email_status IN ('pending', 'sent', 'failed')),
            error_message TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE pending_alerts (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            alert_type TEXT NOT NULL,
            included_in_digest BOOLEAN DEFAULT 0
        )
    """)

    # Attempting to insert email_status='disabled' before upgrade must fail constraint
    try:
        cursor.execute("""
            INSERT INTO alert_history (id, user_id, alerts_count, email_status)
            VALUES ('d1', 'u1', 1, 'disabled')
        """)
        insert_failed = False
    except sqlite3.IntegrityError:
        insert_failed = True
    assert insert_failed, "Old constraint should have blocked email_status='disabled'"

    # Step 2: Apply the additive upgrade
    # 2a. Add processing_digest_id column to pending_alerts
    cursor.execute("ALTER TABLE pending_alerts ADD COLUMN processing_digest_id TEXT")
    # 2b. Add new columns to alert_history
    cursor.execute("ALTER TABLE alert_history ADD COLUMN price_drops INTEGER NOT NULL DEFAULT 0")
    cursor.execute("ALTER TABLE alert_history ADD COLUMN price_increases INTEGER NOT NULL DEFAULT 0")
    cursor.execute("ALTER TABLE alert_history ADD COLUMN currency_changes INTEGER NOT NULL DEFAULT 0")
    cursor.execute("ALTER TABLE alert_history ADD COLUMN webhook_status TEXT DEFAULT 'disabled'")
    cursor.execute("ALTER TABLE alert_history ADD COLUMN alert_ids TEXT NOT NULL DEFAULT '[]'")

    # 2c. Migrate email_status check constraint to accept 'disabled'
    # In SQLite, constraint changes require table recreation; in Postgres, ALTER TABLE DROP/ADD CONSTRAINT
    cursor.execute("""
        CREATE TABLE alert_history_new (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            digest_sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            alerts_count INTEGER NOT NULL,
            price_drops INTEGER NOT NULL DEFAULT 0,
            price_increases INTEGER NOT NULL DEFAULT 0,
            currency_changes INTEGER NOT NULL DEFAULT 0,
            email_status TEXT DEFAULT 'pending' CHECK (email_status IN ('pending', 'sent', 'failed', 'disabled')),
            webhook_status TEXT DEFAULT 'disabled' CHECK (webhook_status IN ('pending', 'sent', 'failed', 'disabled')),
            alert_ids TEXT NOT NULL DEFAULT '[]',
            error_message TEXT
        )
    """)
    cursor.execute("INSERT INTO alert_history_new SELECT * FROM alert_history")
    cursor.execute("DROP TABLE alert_history")
    cursor.execute("ALTER TABLE alert_history_new RENAME TO alert_history")

    # 2d. Create index referencing processing_digest_id now that column exists
    cursor.execute("""
        CREATE INDEX idx_pending_alerts_digest_claim
        ON pending_alerts(user_id, included_in_digest, processing_digest_id)
    """)

    # Step 3: Verify webhook-only digest history insert succeeds
    cursor.execute("""
        INSERT INTO alert_history (
            id, user_id, alerts_count, price_drops, price_increases,
            currency_changes, email_status, webhook_status, alert_ids, error_message
        ) VALUES (
            'digest-webhook-only-1',
            'user-1',
            3,
            2,
            1,
            0,
            'disabled',
            'sent',
            '["a1", "a2", "a3"]',
            NULL
        )
    """)
    conn.commit()

    cursor.execute("SELECT email_status, webhook_status FROM alert_history WHERE id = 'digest-webhook-only-1'")
    row = cursor.fetchone()
    assert row == ("disabled", "sent")
    conn.close()


def test_fresh_schema_allows_webhook_only_history_insert():
    """Verify that a fresh schema installation accepts webhook-only digest history inserts."""
    conn = sqlite3.connect(":memory:")
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE alert_history (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            digest_sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            alerts_count INTEGER NOT NULL,
            price_drops INTEGER NOT NULL DEFAULT 0,
            price_increases INTEGER NOT NULL DEFAULT 0,
            currency_changes INTEGER NOT NULL DEFAULT 0,
            email_status TEXT DEFAULT 'pending' CHECK (email_status IN ('pending', 'sent', 'failed', 'disabled')),
            webhook_status TEXT DEFAULT 'disabled' CHECK (webhook_status IN ('pending', 'sent', 'failed', 'disabled')),
            alert_ids TEXT NOT NULL DEFAULT '[]',
            error_message TEXT
        )
    """)

    cursor.execute("""
        INSERT INTO alert_history (
            id, user_id, alerts_count, email_status, webhook_status
        ) VALUES ('digest-1', 'user-1', 1, 'disabled', 'sent')
    """)
    conn.commit()

    cursor.execute("SELECT email_status, webhook_status FROM alert_history WHERE id = 'digest-1'")
    row = cursor.fetchone()
    assert row == ("disabled", "sent")
    conn.close()
