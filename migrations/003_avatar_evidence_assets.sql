BEGIN;

CREATE TABLE IF NOT EXISTS avatar_evidence_assets (
    asset_id UUID PRIMARY KEY,

    profile_id UUID NOT NULL
        REFERENCES digital_human_profiles(profile_id)
        ON DELETE CASCADE,

    asset_type TEXT NOT NULL,
    evidence_kind TEXT NOT NULL,

    title TEXT NOT NULL,
    filename TEXT NOT NULL,
    content_type TEXT NOT NULL,
    size_bytes BIGINT NOT NULL
        CHECK (size_bytes >= 0),

    storage_backend TEXT NOT NULL DEFAULT 'local_private',
    storage_key TEXT NOT NULL,
    storage_path TEXT,

    processing_status TEXT NOT NULL DEFAULT 'uploaded',
    selection_status TEXT NOT NULL DEFAULT 'not_selected',

    is_included_in_avatar BOOLEAN NOT NULL DEFAULT FALSE,
    included_in_avatar_at TIMESTAMPTZ,
    archived_at TIMESTAMPTZ,

    duration_seconds DOUBLE PRECISION,

    quality_score DOUBLE PRECISION NOT NULL DEFAULT 0
        CHECK (
            quality_score >= 0
            AND quality_score <= 1
        ),

    has_face BOOLEAN NOT NULL DEFAULT FALSE,
    has_frontal_face BOOLEAN NOT NULL DEFAULT FALSE,
    has_clear_lighting BOOLEAN NOT NULL DEFAULT FALSE,
    has_voice BOOLEAN NOT NULL DEFAULT FALSE,

    voice_usable BOOLEAN NOT NULL DEFAULT FALSE,
    motion_usable BOOLEAN NOT NULL DEFAULT FALSE,

    emotional_presence_score DOUBLE PRECISION NOT NULL DEFAULT 0
        CHECK (
            emotional_presence_score >= 0
            AND emotional_presence_score <= 1
        ),

    identity_consistency_score DOUBLE PRECISION NOT NULL DEFAULT 0
        CHECK (
            identity_consistency_score >= 0
            AND identity_consistency_score <= 1
        ),

    motion_quality_score DOUBLE PRECISION NOT NULL DEFAULT 0
        CHECK (
            motion_quality_score >= 0
            AND motion_quality_score <= 1
        ),

    expression_range_score DOUBLE PRECISION NOT NULL DEFAULT 0
        CHECK (
            expression_range_score >= 0
            AND expression_range_score <= 1
        ),

    lip_visibility_score DOUBLE PRECISION NOT NULL DEFAULT 0
        CHECK (
            lip_visibility_score >= 0
            AND lip_visibility_score <= 1
        ),

    head_pose_stability_score DOUBLE PRECISION NOT NULL DEFAULT 0
        CHECK (
            head_pose_stability_score >= 0
            AND head_pose_stability_score <= 1
        ),

    recommended_for_avatar BOOLEAN NOT NULL DEFAULT FALSE,

    rejection_code TEXT,
    rejection_reason TEXT,

    analysis_version TEXT NOT NULL DEFAULT 'legacy-v1',
    analysis_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    source_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT avatar_evidence_assets_asset_type_check
        CHECK (
            asset_type IN (
                'image',
                'video',
                'voice',
                'audio',
                'reference',
                'training_sample',
                'generated_preview',
                'trained_replica'
            )
        ),

    CONSTRAINT avatar_evidence_assets_kind_check
        CHECK (
            evidence_kind IN (
                'identity_photo',
                'motion_video',
                'voice_sample',
                'generated_preview',
                'trained_replica'
            )
        ),

    CONSTRAINT avatar_evidence_assets_processing_status_check
        CHECK (
            processing_status IN (
                'uploaded',
                'analyzing',
                'ready',
                'rejected',
                'training',
                'failed',
                'archived'
            )
        ),

    CONSTRAINT avatar_evidence_assets_selection_status_check
        CHECK (
            selection_status IN (
                'not_selected',
                'selected',
                'primary',
                'removed'
            )
        ),

    CONSTRAINT avatar_evidence_assets_inclusion_consistency_check
        CHECK (
            (
                is_included_in_avatar = TRUE
                AND included_in_avatar_at IS NOT NULL
            )
            OR
            (
                is_included_in_avatar = FALSE
                AND included_in_avatar_at IS NULL
            )
        )
);

CREATE UNIQUE INDEX IF NOT EXISTS
    avatar_evidence_assets_storage_key_unique
ON avatar_evidence_assets (
    storage_backend,
    storage_key
);


CREATE UNIQUE INDEX IF NOT EXISTS
    avatar_evidence_assets_single_primary_unique
ON avatar_evidence_assets (
    profile_id,
    evidence_kind
)
WHERE
    selection_status = 'primary'
    AND archived_at IS NULL;

CREATE INDEX IF NOT EXISTS
    avatar_evidence_assets_profile_index
ON avatar_evidence_assets (
    profile_id,
    evidence_kind,
    processing_status
);

CREATE INDEX IF NOT EXISTS
    avatar_evidence_assets_active_selection_index
ON avatar_evidence_assets (
    profile_id,
    evidence_kind,
    is_included_in_avatar,
    quality_score DESC,
    updated_at DESC
)
WHERE
    archived_at IS NULL;

CREATE INDEX IF NOT EXISTS
    avatar_evidence_assets_visual_index
ON avatar_evidence_assets (
    profile_id,
    has_face,
    has_frontal_face,
    has_clear_lighting,
    quality_score DESC
)
WHERE
    evidence_kind IN (
        'identity_photo',
        'motion_video'
    )
    AND archived_at IS NULL;

CREATE OR REPLACE FUNCTION
    rememberme_avatar_evidence_set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS
    avatar_evidence_assets_set_updated_at
ON avatar_evidence_assets;

CREATE TRIGGER
    avatar_evidence_assets_set_updated_at
BEFORE UPDATE ON avatar_evidence_assets
FOR EACH ROW
EXECUTE FUNCTION
    rememberme_avatar_evidence_set_updated_at();

INSERT INTO schema_migrations (
    version
)
VALUES (
    '003_avatar_evidence_assets'
)
ON CONFLICT (
    version
)
DO NOTHING;

COMMIT;
