BEGIN;

ALTER TABLE digital_human_profiles
    ADD COLUMN IF NOT EXISTS
        identity_verification_status TEXT
        NOT NULL
        DEFAULT 'not_evaluated',

    ADD COLUMN IF NOT EXISTS
        current_identity_verification_receipt_id UUID,

    ADD COLUMN IF NOT EXISTS
        identity_verified_at TIMESTAMPTZ;

ALTER TABLE digital_human_profiles
    DROP CONSTRAINT IF EXISTS
        digital_human_profiles_identity_verification_status_check;

ALTER TABLE digital_human_profiles
    ADD CONSTRAINT
        digital_human_profiles_identity_verification_status_check
    CHECK (
        identity_verification_status IN (
            'not_evaluated',
            'evaluation_required',
            'evaluating',
            'verified',
            'rejected',
            'inconclusive',
            'error'
        )
    );

CREATE TABLE IF NOT EXISTS
    digital_human_identity_verification_receipts (
        receipt_id UUID PRIMARY KEY,

        profile_id UUID NOT NULL
            REFERENCES digital_human_profiles(profile_id)
            ON DELETE CASCADE,

        training_version INTEGER NOT NULL
            CHECK (training_version > 0),

        status TEXT NOT NULL,

        face_status TEXT NOT NULL,
        voice_status TEXT NOT NULL,

        evaluation_version TEXT NOT NULL,

        face_model_version TEXT,
        voice_model_version TEXT,

        face_threshold DOUBLE PRECISION,
        voice_threshold DOUBLE PRECISION,

        face_score DOUBLE PRECISION,
        voice_score DOUBLE PRECISION,

        evidence JSONB NOT NULL DEFAULT '{}'::jsonb,

        evaluated_at TIMESTAMPTZ NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

        CONSTRAINT
            identity_verification_receipts_status_check
        CHECK (
            status IN (
                'evaluating',
                'verified',
                'rejected',
                'inconclusive',
                'error'
            )
        ),

        CONSTRAINT
            identity_verification_receipts_face_status_check
        CHECK (
            face_status IN (
                'not_evaluated',
                'evaluating',
                'verified',
                'rejected',
                'inconclusive',
                'error'
            )
        ),

        CONSTRAINT
            identity_verification_receipts_voice_status_check
        CHECK (
            voice_status IN (
                'not_required',
                'not_evaluated',
                'evaluating',
                'verified',
                'rejected',
                'inconclusive',
                'error'
            )
        ),

        CONSTRAINT
            identity_verification_receipts_face_threshold_check
        CHECK (
            face_threshold IS NULL
            OR (
                face_threshold >= 0
                AND face_threshold <= 1
            )
        ),

        CONSTRAINT
            identity_verification_receipts_voice_threshold_check
        CHECK (
            voice_threshold IS NULL
            OR (
                voice_threshold >= 0
                AND voice_threshold <= 1
            )
        ),

        CONSTRAINT
            identity_verification_receipts_face_score_check
        CHECK (
            face_score IS NULL
            OR (
                face_score >= 0
                AND face_score <= 1
            )
        ),

        CONSTRAINT
            identity_verification_receipts_voice_score_check
        CHECK (
            voice_score IS NULL
            OR (
                voice_score >= 0
                AND voice_score <= 1
            )
        ),

        CONSTRAINT
            identity_verification_receipts_verified_components_check
        CHECK (
            status <> 'verified'
            OR (
                face_status = 'verified'
                AND voice_status IN (
                    'verified',
                    'not_required'
                )
                AND face_model_version IS NOT NULL
                AND face_threshold IS NOT NULL
                AND face_score IS NOT NULL
                AND (
                    voice_status = 'not_required'
                    OR (
                        voice_model_version IS NOT NULL
                        AND voice_threshold IS NOT NULL
                        AND voice_score IS NOT NULL
                    )
                )
            )
        )
    );

CREATE INDEX IF NOT EXISTS
    identity_verification_receipts_profile_index
ON digital_human_identity_verification_receipts (
    profile_id,
    training_version,
    evaluated_at DESC
);

CREATE OR REPLACE FUNCTION
    rememberme_prevent_identity_receipt_update()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION
        'Identity verification receipts are immutable.';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS
    identity_verification_receipts_prevent_update
ON digital_human_identity_verification_receipts;

CREATE TRIGGER
    identity_verification_receipts_prevent_update
BEFORE UPDATE ON
    digital_human_identity_verification_receipts
FOR EACH ROW
EXECUTE FUNCTION
    rememberme_prevent_identity_receipt_update();

ALTER TABLE digital_human_profiles
    DROP CONSTRAINT IF EXISTS
        digital_human_profiles_current_identity_receipt_fk;

ALTER TABLE digital_human_profiles
    ADD CONSTRAINT
        digital_human_profiles_current_identity_receipt_fk
    FOREIGN KEY (
        current_identity_verification_receipt_id
    )
    REFERENCES
        digital_human_identity_verification_receipts(receipt_id)
    ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS
    digital_human_profiles_identity_verification_index
ON digital_human_profiles (
    identity_verification_status
);

INSERT INTO schema_migrations (
    version
)
VALUES (
    '004_identity_verification_receipts'
)
ON CONFLICT (
    version
)
DO NOTHING;

COMMIT;
