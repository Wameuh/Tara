-- Only accounting fields are retained; supporter names, messages and emails are not stored.
CREATE TABLE kofi_payment_events (
    id INTEGER PRIMARY KEY,
    message_id TEXT NOT NULL UNIQUE CHECK(length(message_id) BETWEEN 1 AND 128),
    event_type TEXT NOT NULL CHECK(length(event_type) BETWEEN 1 AND 32),
    amount_micros INTEGER NOT NULL CHECK(amount_micros BETWEEN 0 AND 1000000000000000),
    currency TEXT NOT NULL CHECK(length(currency) = 3 AND currency = upper(currency)),
    occurred_at TEXT NOT NULL,
    received_at TEXT NOT NULL
);
CREATE INDEX idx_kofi_payment_events_month
 ON kofi_payment_events(currency,event_type,occurred_at);
