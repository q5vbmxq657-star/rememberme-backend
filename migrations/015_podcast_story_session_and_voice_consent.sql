ALTER TABLE podcast_invitations
ADD COLUMN IF NOT EXISTS theme TEXT NOT NULL DEFAULT 'life_story',
ADD COLUMN IF NOT EXISTS prompt_sequence JSONB NOT NULL DEFAULT '[]'::jsonb,
ADD COLUMN IF NOT EXISTS speaker_confirmed_subject BOOLEAN NOT NULL DEFAULT FALSE,
ADD COLUMN IF NOT EXISTS voice_training_consent_granted BOOLEAN NOT NULL DEFAULT FALSE,
ADD COLUMN IF NOT EXISTS voice_training_consented_at TIMESTAMPTZ,
ADD COLUMN IF NOT EXISTS voice_training_used_at TIMESTAMPTZ;

CREATE TABLE IF NOT EXISTS podcast_responses (
    response_id UUID PRIMARY KEY,
    invitation_id UUID NOT NULL
        REFERENCES podcast_invitations(invitation_id)
        ON DELETE CASCADE,
    turn_index INTEGER NOT NULL,
    prompt_id TEXT NOT NULL,
    category TEXT NOT NULL,
    question TEXT NOT NULL,
    audio_asset_id UUID NOT NULL,
    memory_id UUID NOT NULL UNIQUE,
    transcript TEXT NOT NULL,
    summary TEXT NOT NULL,
    memory_payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT podcast_response_turn_check CHECK (turn_index >= 0),
    CONSTRAINT podcast_response_prompt_id_check CHECK (length(prompt_id) BETWEEN 3 AND 80),
    CONSTRAINT podcast_response_category_check CHECK (length(category) BETWEEN 3 AND 40),
    CONSTRAINT podcast_response_transcript_check CHECK (length(btrim(transcript)) >= 2),
    UNIQUE (invitation_id, turn_index)
);

CREATE INDEX IF NOT EXISTS podcast_responses_invitation_index
ON podcast_responses (invitation_id, turn_index);

ALTER TABLE podcast_invitations
DROP CONSTRAINT IF EXISTS podcast_invitation_voice_consent_check;

ALTER TABLE podcast_invitations
ADD CONSTRAINT podcast_invitation_voice_consent_check
CHECK (
    NOT voice_training_consent_granted
    OR (
        speaker_confirmed_subject
        AND voice_training_consented_at IS NOT NULL
    )
);

INSERT INTO schema_migrations (version)
VALUES ('015_podcast_story_session_and_voice_consent');
