ALTER TABLE family_credit_reservations
    ADD COLUMN profile_id UUID REFERENCES digital_human_profiles(profile_id),
    ADD COLUMN auth_session_id UUID REFERENCES user_sessions(session_id),
    ADD COLUMN conversation_id UUID,
    ADD COLUMN expires_at TIMESTAMPTZ;
ALTER TABLE family_credit_reservations ADD CONSTRAINT call_binding_complete
    CHECK (num_nonnulls(profile_id,auth_session_id,conversation_id,expires_at) IN (0,4));
INSERT INTO schema_migrations(version) VALUES ('047_call_reservation_binding');
