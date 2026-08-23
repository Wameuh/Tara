ALTER TABLE kofi_payment_events
 ADD COLUMN is_test_transaction INTEGER NOT NULL DEFAULT 0
 CHECK(is_test_transaction IN (0,1));

-- Aggregate page views by UTC day and coarse route. No visitor identifier is stored.
CREATE TABLE page_view_counts (
    day TEXT NOT NULL CHECK(length(day) = 10),
    page TEXT NOT NULL CHECK(page IN ('new_job','help','upload_session','job')),
    view_count INTEGER NOT NULL CHECK(view_count BETWEEN 1 AND 1000000000),
    PRIMARY KEY(day,page)
);

-- Signed operator corrections are append-only so changes remain auditable.
CREATE TABLE funding_consumption_adjustments (
    id INTEGER PRIMARY KEY,
    amount_micro_eur INTEGER NOT NULL
        CHECK(amount_micro_eur BETWEEN -1000000000000000 AND 1000000000000000),
    note TEXT NOT NULL CHECK(length(note) BETWEEN 1 AND 200),
    created_at TEXT NOT NULL
);
CREATE INDEX idx_funding_consumption_adjustments_created
 ON funding_consumption_adjustments(created_at);
