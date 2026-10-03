# Self-hosted voice: implementation and release gates

Status: backend lifecycle and iOS WAV/language integration implemented locally;
NOT an approved production voice provider. No live GPU deployment or listening acceptance.

## Verification on 2026-09-24

- 295 selected backend tests passed, including actual isolated PostgreSQL transactions,
  encrypted-reference round trips, authorized revocation, deletion during inference and
  retry after a simulated inference outage. GPU responses in integration tests are test doubles.
- 99 iOS AvatarCallTrainingTests passed on the iPhone 17 Pro simulator (iOS 26.5).
- Actual PyAV noise-reduction and tempo filters were executed in tests.
- No container build, live CUDA benchmark, human listening review or production activation.
- No Runpod credentials were present in the active process environment. Account access
  was requested; it is not replaced by simulated deployment evidence.

## Implemented

- Private authenticated `/v1/synthesize` and `/health/ready` endpoints.
- Versioned strict request contract: profile, voice version, consent revision,
  model revision and request ID. Reference is mandatory; no default voice substitution.
- Bounded request, reference duration, utterance length and output duration/size.
- One admitted synthesis per GPU process; capacity rejection instead of an unbounded queue.
- Chatterbox Multilingual V3 adapter using a local checksummed model bundle, CUDA only.
- Mutable model conditioning cleared after success and failure, with exclusive ownership.
- Temporary reference files removed after inference. Mount `/tmp` as a private tmpfs.
- Backend transport requires HTTPS, rejects redirects, verifies response identity and WAV.
- Model code pinned to commit 5de7a54aa4e5e2baadb0182dde554908b48b85c2.
- Contract tests use an injected test engine; these are NOT neural inference acceptance tests.
- The existing PostgreSQL activation transaction handles `elevenlabs` and `stay_voice`
  jobs together: latest selection wins across providers, failed/pending replacements retain
  the active voice, and consent revisions are checked atomically before activation.
- The ElevenLabs synthesis path rejects a selected self-hosted voice instead of silently
  substituting its default voice. A deliberately pinned generic call remains generic.
- The existing clone endpoint dispatches preparation to the configured provider. Own-voice
  preparation normalizes the supplied recordings to a bounded 16 kHz PCM reference and
  requires a successful real inference response before activation. There is no per-user
  model fine-tuning: the shared model is conditioned on a private reference.
- References are AES-256-GCM encrypted in PostgreSQL, bound to profile, job, consent revision
  and model revision. Key IDs support retaining old decryption keys during key rotation.
- The existing TTS route dispatches version-bound own voices and returns WAV with the correct
  content type. iOS sends a locally recognized output language. The current model contract
  supports English and German, maximum 600 characters per inference request.
- Longer API responses are split into at most 240-character inference segments, with
  authorization rechecked between segments. WAV segments are combined without MP3 relabeling.
  The gateway enforces a 75-second overall deadline; this remains buffered, not streaming.
- Existing upload noise reduction uses FFmpeg `afftdn`. Energy maps to the model's
  exaggeration control; speed uses pitch-preserving `atempo`, with bounded inter-segment pauses.
  These mappings require real listening calibration, not just numerical tests.
- Delete and profile-erasure paths cover own references and jobs. Consent removal deletes
  own references in the consent transaction. Late inference results are discarded.
- Transient preparation failures can retry the same job without creating a second identity.

## Infrastructure selection

Runpod Secure Cloud is the initial benchmark target. Compare L4 and RTX 4090 on
measured cost per generated audio minute and p95 response latency; hourly GPU price alone
does not establish the cheapest usable option. See https://www.runpod.io/pricing.
No GPU has been provisioned and no account access has been confirmed. Production capacity,
region, storage, spending limits and credentials must be configured before live tests.
Do not use spot eviction or scale-to-zero in an active conversation without measured recovery.

## Build and infrastructure

Build from repository root with `docker build -f docker/voice-runtime.Dockerfile .`.
Requires NVIDIA container runtime and a CUDA-capable GPU. Do not deploy to the existing
CPU web service. Run one process per replica behind private TLS ingress. Restrict network
access to the authorized STAY gateway, disable request-body logging, use a read-only root
filesystem, and mount writable private tmpfs at `/tmp`. Set ingress body and request timeouts.

Required secrets/configuration:

- `STAY_VOICE_RUNTIME_TOKEN`: independently generated secret, at least 32 characters.
- `STAY_VOICE_MODEL_DIRECTORY`: read-only local directory of approved model artifacts.
- `STAY_VOICE_MODEL_REVISION`: SHA-256 of the exact `manifest.json` bytes.
- Gateway only: `STAY_VOICE_RUNTIME_URL`, private HTTPS origin of the worker.
- Gateway only: `STAY_VOICE_REFERENCE_KEYS`, JSON object of key IDs to base64-encoded
  32-byte AES keys; `STAY_VOICE_REFERENCE_ACTIVE_KEY` selects the encryption key.
- Gateway only: `STAY_VOICE_TRAINING_PROVIDER=stay_voice` selects own voices for new
  training requests. Default remains `elevenlabs`. Existing voice versions keep their provider.
- Apply migration `038_self_hosted_voice_references.sql` before deploying this gateway.

The manifest is a JSON object mapping these filenames to their SHA-256 hashes:
`ve.pt`, `s3gen.pt`, `t3_mtl23ls_v3.safetensors`,
`grapheme_mtl_merged_expanded_v1.json`, `Cangjie5_TC.json`.
Obtain and approve artifacts separately; do not download models on a live request.
An unexpected built-in `conds.pt` is rejected. Do not use untrusted checkpoints.
Dependency resolution and the container base still require a verified build, locked
transitive dependencies, image digest and vulnerability scan before publication.

## Remaining release gates

The GPU service trusts the gateway; signed-in user authorization remains in the existing
public routes and is checked before and after synthesis. No user authentication tokens or
invented conversation identifiers are sent to the worker.

Before production activation, verify together:

1. Real CUDA inference, deployment, dependency lock and image scan.
2. Full device call behavior, first playable audio, interruptions, voice identity and delivery.
3. Backup/restore deletion guarantees for the newly encrypted reference table, key rotation
   and deletion of encryption keys when retention expires.
4. Existing session credit accounting under retries and interrupted own-voice calls.
5. Long-response continuity and the effect of the audio filters on speaker identity.
6. Generic voice and Tavus remain on their existing paths; this adapter replaces personalized
   voice inference only. Do not remove old credentials while old voice versions need them.

## Quality and latency gates

The upstream `generate` method returns a complete waveform. This adapter does not pretend
to offer token/audio streaming. GPU measurements must determine whether bounded utterances
can satisfy STAY's first-audio and uninterrupted-playback targets. Do not label buffered
responses as realtime streaming. Evaluate another model if they cannot.

Measure first playable audio, real-time factor, p50/p95 latency, memory, concurrent demand,
interruption behavior, identity similarity and German/English pronunciation using authorized
references. Test worker failure during a call. Include model watermark and license review.
Voice samples must not become shared-model training data by default.

Run `python -m voice_runtime.benchmark --cases <private-json> --output <new-private-dir>
--max-p95-seconds <release-target>` on the CUDA worker. It requires English and German
authorized cases, writes audible WAV evidence, and reports median/p95 and real-time factors.
It does not certify listening quality or network/device latency. Do not commit its inputs
or audio outputs. This benchmark has not been executed against a GPU yet.

No production activation until all integration gates, real inference tests, device tests,
rollback exercise and total-cost benchmark pass. Existing ElevenLabs/Tavus behavior remains
unchanged while these gates are open.

## Local verification follow-up: 2026-10-03

- Restored local file availability after iCloud eviction. Existing work was preserved.
- Added a 30-second total GPU request deadline, in addition to HTTP phase timeouts.
  Tests verify response-stream closure on timeout and caller cancellation.
- Applied the canonical migrations through 038 to a new isolated PostgreSQL cluster.
  The voice, ElevenLabs, purpose-consent and erasure selection passed: 297 tests,
  no skips. The test server was stopped afterward. Production was not touched.
- Subsequently added rejection of entirely silent PCM output and regression cases
  for invalid and truncated WAV responses. The runtime/audio/benchmark selection
  passed 36 tests. A subsequent database-backed run including the realtime call
  lifecycle and durable OpenAI session tests passed 348 tests with no skips.
  The isolated database was stopped afterward; production remained unchanged.
- These are local contract tests, not GPU inference or audible device acceptance.
  They do not establish the cause or resolution of historical generic-voice silence.
- Added engine cleanup tests with an explicit fake model/encoder for generation,
  silent/nonfinite/empty output and encoding failures. Each case runs twice and
  checks cleared conditioning, removed temporary references and a released lock.
  Runtime/audio/benchmark selection: 41 passed. No CUDA inference was performed.
- Full backend suite with isolated PostgreSQL: 1456 passed, no skips (12.74 s).
  Updated the two explicit migration inventories to include 038, retaining the
  historical hash checks. Replaced the outdated immediate-upgrade-invalidation
  assertion with checks that the active verified avatar survives preparation,
  that candidate activation clears runtime verification, and that the previous
  snapshot cannot verify the replacement. This is repository-level evidence,
  not a live Tavus call or device continuity test. The test server was stopped.
- Rebuilt iOS and reran AvatarCallTrainingTests on iPhone 17 Pro simulator,
  iOS 26.5: 99 passed, 0 failed, 0 skipped (xcresult summary verified).
  Evidence: /private/tmp/STAY-Voice-20261003/Logs/Test/Test-RemembermeAI-2026.10.03_19-04-20-+0200.xcresult.
  The pre-existing project build-number change to 22 was preserved. This was
  a simulator test run, not a TestFlight upload or physical-device audio check.
- Readiness now returns authenticated 503 with Retry-After while the single GPU
  slot is occupied, and recovers to 200 after completion. Tests cover this during
  actual concurrent requests against the fake engine (41 targeted tests passed).
  Use this endpoint for traffic readiness only, not as a liveness restart probe:
  ordinary in-flight synthesis must not trigger worker restarts. A separate
  inference watchdog/process recovery policy remains a deployment gate.
- Native iOS generic synthesis now has a 30-second deadline and cancellation
  completion. A checked-Sendable Mutex owner serializes AVSpeech file callbacks,
  completes the continuation once, and removes partial audio on failure. Late
  callbacks cannot recreate cancelled files. Each call retains its own synthesizer.
  Three new regression tests cover cancellation before continuation registration,
  partial-file cleanup with late callbacks, and successful exactly-once completion.
  AvatarCallTrainingTests: 102 passed, no failures/skips; evidence:
  /private/tmp/STAY-Voice-20261003/Logs/Test/Test-RemembermeAI-2026.10.03_19-44-25-+0200.xcresult.
  This closes the identified unbounded native continuation path, not the remaining
  end-to-end audible-call acceptance requirement.

## Process recovery, language and first-phrase follow-up

The production runtime factory now loads Chatterbox in a spawned child process
owned by `SupervisedVoiceEngine`. Startup is bounded to 120 seconds and each
inference to 20 seconds. Failure or timeout terminates and, if necessary, kills
the old child before starting a replacement. The failed utterance is never
automatically replayed. Readiness stays unavailable while the HTTP request owns
capacity, including recovery. Recovery failure remains unavailable, not success.
Parent-owned temporary storage is removed after the child exits. Mount temporary
storage as private tmpfs in deployment. Real CUDA context release and restart
timing still require GPU acceptance; local tests use real spawned processes with
a deliberately non-neural fixture for hang, crash, exception and revision mismatch.

The language contract now follows the 23-language list of pinned Chatterbox commit
5de7a54aa4e5e2baadb0182dde554908b48b85c2 (`src/chatterbox/mtl_tts.py`). Locale
suffixes and Norwegian nb/nn aliases are normalized centrally. Unsupported
languages still fail explicitly. iOS uses the full original response for language
selection across phrases, including native fallback. This does not prove accent,
identity or pronunciation quality in those languages.

The existing voice-only phrase/prefetch path is retained. The first phrase now
targets 80 characters with a 120-character cap; subsequent phrases retain their
existing targets. The hard-boundary off-by-one was corrected. This reduces the
first synthesis workload, but is not neural audio streaming or a measured latency
guarantee. Video and standalone full-text synthesis still need separate acceptance.

Verification after these changes: 1466 backend tests passed against isolated
PostgreSQL, no skips; 183 iOS AvatarCallTrainingTests/MemoryJourneyDomainTests
passed, no skips. Simulator evidence:
/private/tmp/STAY-Voice-20261003/Logs/Test/Test-RemembermeAI-2026.10.03_20-00-33-+0200.xcresult.
No physical device was found by devicectl. Docker/Podman/Runpod/Modal CLIs are not
available in the current PATH. Cloud access, spending limits and real test-device
acceptance are still required. No production release was performed.

Idle-worker recovery follow-up: the HTTP lifespan now checks the supervised
process every two seconds without waiting for user traffic. Recovery uses the
same exclusive owner as inference; readiness stays false until the replacement
has completed model initialization. A real-process test kills an idle fixture
worker and observes authenticated readiness recover without synthesis traffic.
Runtime/supervisor selection: 41 passed. This prevents a readiness-based load
balancer from stranding a dead idle worker; CUDA recovery remains unverified.

Expanded regression after idle recovery: all 1467 backend tests passed against
isolated PostgreSQL (24.13 s), and all 295 tests in the RemembermeAITests iOS
target passed on iPhone 17 Pro/iOS 26.5 simulator, with no skips or failures.
Evidence: /private/tmp/STAY-Voice-20261003/Logs/Test/Test-RemembermeAI-2026.10.03_20-12-16-+0200.xcresult.
The database was stopped after the run. UI-test targets, physical-device audio,
GPU inference and live provider acceptance are not included in these totals.

## Ordered deployment and rollback

1. Build and scan the GPU container on Linux/CUDA infrastructure. Record the image
   digest, pinned model manifest hash and approved license evidence. This has not
   been completed locally; no Docker engine or paid GPU was provisioned.
2. Mount the verified model read-only and private temporary storage as tmpfs.
   Keep a single HTTP process per GPU. Configure the runtime token through the
   platform secret manager; never put it in an image, repository or app bundle.
3. Start the runtime and run authorized inference, identity/listening, concurrency,
   cancellation and recovery benchmarks. The authenticated readiness response
   alone does not establish quality, cost or first-audio latency acceptance.
4. Configure backend STAY_VOICE_RUNTIME_URL, STAY_VOICE_RUNTIME_TOKEN,
   STAY_VOICE_MODEL_REVISION, STAY_VOICE_REFERENCE_KEYS and
   STAY_VOICE_REFERENCE_ACTIVE_KEY through the deployment secret manager.
   Keep STAY_VOICE_TRAINING_PROVIDER=elevenlabs until acceptance is complete.
5. Deploy migrations and backend together through scripts/run_predeploy.py.
   The canonical order is migrations, memory runtime verification, then voice
   release preflight. Preflight checks retained key IDs and activated model
   revisions from PostgreSQL, and verifies the bounded authenticated runtime
   contract whenever self-hosted training or stored references require it.
   It fails closed on mismatches. A busy runtime can fail preflight; rerun when
   capacity is available rather than bypassing the check.
6. After real acceptance, enable STAY_VOICE_TRAINING_PROVIDER=stay_voice and
   repeat preflight. Retain ElevenLabs credentials for existing voices. Deploy
   the matching iOS build to TestFlight and complete real-device first-input,
   voice-switch, interruption, Bluetooth and network-transition acceptance.
7. Roll back new training by selecting elevenlabs, not by removing keys or
   stopping the own-voice runtime. Already activated own voices still need the
   compatible runtime and their original encryption keys. Do not downgrade the
   backend below migration 038 support while those voices exist. A model update
   incompatible with retained activated references must not pass preflight.

App Store/Family billing, video-provider identity and voice interoperability,
backup-restore deletion replay, UI/accessibility acceptance and production
monitoring remain separate release gates. This runbook does not certify them.
