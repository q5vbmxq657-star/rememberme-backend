ALTER TABLE family_credit_reservations DROP CONSTRAINT family_credit_reservations_profile_id_fkey;
ALTER TABLE family_credit_reservations ADD CONSTRAINT family_credit_reservations_profile_id_fkey
    FOREIGN KEY (profile_id) REFERENCES digital_human_profiles(profile_id) ON DELETE CASCADE;
ALTER TABLE family_credit_reservations DROP CONSTRAINT family_credit_reservations_auth_session_id_fkey;
ALTER TABLE family_credit_reservations ADD CONSTRAINT family_credit_reservations_auth_session_id_fkey
    FOREIGN KEY (auth_session_id) REFERENCES user_sessions(session_id) ON DELETE CASCADE;
INSERT INTO schema_migrations(version) VALUES ('048_call_binding_erasure');
