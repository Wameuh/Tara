ALTER TABLE upload_sessions ADD COLUMN language TEXT NOT NULL DEFAULT 'fr' CHECK(length(language) BETWEEN 2 AND 16);
ALTER TABLE upload_sessions ADD COLUMN context_text TEXT NOT NULL DEFAULT '' CHECK(length(context_text) <= 200000);
ALTER TABLE upload_sessions ADD COLUMN previous_summaries_text TEXT NOT NULL DEFAULT '' CHECK(length(previous_summaries_text) <= 2000000);
