# Production Readiness Audit: 2026-09-05

## Scope and Release Decision

This audit inspected backend API, provider, persistence, upload and migration
contracts, plus selected iOS voice/runtime code and existing client quality gates.
It is not a certification that every app screen or production integration is
bug-free. No production deployment or database migration was performed.

Existing uncommitted work was preserved. The changes below are this audit's work,
not a claim of authorship over all modifications in the working tree.

## Confirmed Defects Corrected

| Priority | Defect | Correction |
| --- | --- | --- |
| P1 | Provider status route called a nonexistent ownership method. | Resolve job ownership through the durable training/preview repository; reject unknown, wrong-type and foreign-profile jobs before provider access. |
| P1 | Successful preview IDs use `tavus:video:`, but the status dispatcher did not recognize that prefix. | Dispatch the canonical identifier to preview status, preserving the existing legacy branch. |
| P1 | HTTP 429/503, transport errors and malformed status responses could fail or corrupt training state. | Return a sanitized retryable 503 without overwriting persisted training state; retain the existing iOS retry path. |
| P1 | An older job's poll/webhook could overwrite a newer avatar on the same profile. | Fence the profile UPDATE atomically against the currently assigned provider job ID. Historical job state may still update independently. |
| P1 | Synchronous transcription blocked the async server event loop. | Use AsyncOpenAI with explicit timeout/retry settings and close the client on success, error or cancellation. |
| P1 | Synchronous database work in avatar training-status polling blocked other requests. | Move authorization, ownership lookup, training lookup and status persistence off the event loop. |
| P1 | New migration 016 had an outer transaction rejected by the migration runner. | Let the existing runner own its transaction; preserve historical migrations and hashes; update expected migration catalogs. |
| P2 | Transcription always supplied German as the input language. | Remove the hardcoded hint so mixed/English calls are not forced through the German setting. |
| P2 | Voice uploads were fully read before size/count limits were checked. | Bound transcription reads and enforce the existing voice-clone sample/count/total limits before materialization; return 413 for oversized audio. |
| P2 | Realtime health used an unresolved `Any` annotation. | Import the type so API/schema introspection can resolve the contract. |

## Verification

- Full backend suite: **279 passed** with Python 3.12 and the project's complete
  `requirements-python312.lock.txt` in a fresh temporary environment.
- Focused new/adjacent regression group: **49 passed**.
- Migration runner tests: **19 passed**.
- Existing iOS release-candidate readiness script: **passed**.
- Existing Swift 6 concurrency-boundary script: **passed**.
- `git diff --check`: **passed** before this documentation addition.
- Isolated pre-fix reproduction: a simulated 120 ms synchronous STT call delayed
  an independent 10 ms timer to 143 ms. This is a blocking-behavior demonstration,
  not a production latency measurement.

The initial project virtual environment stalled while importing dependencies.
Tests were rerun in `/private/tmp/stay-production-audit-venv`; no project dependency
versions were changed. Provider behavior is mocked in contract tests. SQL fencing
is covered at the repository/query-contract level, not by a live production DB test.

## Open Release Gates

1. **Realtime memory filtering:** `app/routes/realtime.py` still accepts a raw
   client `memory_context` and passes it to `OpenAIRealtimeService`. Profile access
   is checked, but that alone does not prove that all submitted content is current,
   relevant and non-excluded. Audit server-selected evidence and exclusion updates
   across every call transport before certifying the user's memory-sharing policy.
   This review did not demonstrate an actual cross-profile disclosure.
2. **Real-device audio performance:** measure speech-end to first audible audio,
   first-turn capture, interruption and reconnect behavior on iPhone speakers,
   Bluetooth and constrained networks for generic and trained voices. No numeric
   production latency improvement or resolution of every missed turn is claimed.
3. **End-to-end deployment:** deploy the code and apply migration 016 through the
   existing runner, then verify provider callbacks, training and media lifecycle
   with authorized test profiles. This audit did not alter production state.
4. **Broader client audit:** source checks do not replace Instruments, rendering,
   memory-pressure, accessibility and UI interaction tests. The legacy
   `RememberAudioPlaybackService` has cancellation/delegate state risks, but no
   current call site was found; it was not represented as a confirmed cause of
   the active voice-call failures and was left unchanged.

## Reproduce the Backend Check

```sh
OPENAI_API_KEY=test-only PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  /private/tmp/stay-production-audit-venv/bin/python -B \
  -X pycache_prefix=/private/tmp/stay-audit-no-bytecode \
  -m pytest tests -q -p no:cacheprovider
```

Run from the backend repository. The temporary environment can be recreated from
the checked-in Python 3.12 lockfile. This command is a local test command, not a
production deployment or migration command.
