ALTER TABLE family_content ADD COLUMN allows_family_edits BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE family_content ADD COLUMN last_editor_id UUID REFERENCES users(user_id) ON DELETE SET NULL;
INSERT INTO schema_migrations(version) VALUES ('036_family_collaboration');
