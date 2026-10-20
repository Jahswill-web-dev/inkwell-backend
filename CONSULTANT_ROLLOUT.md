# Consultant experience rollout

The consultant flow is additive. Existing accounts and articles default to marketing.

## Run locally or deploy

1. Deploy the backend and run `uv run alembic upgrade head`.
2. Configure `OPENAI_API_KEY`, `CONSULTANT_MODEL=gpt-5.6-sol`, and the existing live voice settings.
3. Start a separate durable worker: `uv run python -m app.workers.consultant`.
4. Deploy the frontend. Consultant signup is exposed by default.
5. Run a real voice conversation and validate its draft with a consultant before promoting broadly.

No new packages or broker are required. Jobs live in PostgreSQL. Worker leases renew every 30 seconds;
expired leases are reclaimable after 180 seconds. Transient provider failures retry up to three attempts.
Only one active generation per article is accepted. Subsequent generations are proposals; they do not
replace manual edits. Worker logs contain IDs/error types, never transcript bodies.

The consultant flow uses OpenAI live audio independently of the marketing provider choice. It reuses
the shared WebRTC hook and OpenAI live transport. The marketing ElevenLabs integration is unchanged.
Raw audio is not stored by this implementation. Transcript corrections are kept separately from the
original turns. Generation snapshots are immutable. Writing style is inferred from the current
interview and is not carried across articles in a reusable profile.

## Rollback

Set `CONSULTANT_ENABLED=false` to stop new consultant accounts. Existing consultant workspaces remain
readable, editable and resumable. Keep the worker running for existing jobs. Do not downgrade a database
containing consultant articles; the downgrade intentionally refuses that destructive operation.

## Verification

- `uv run pytest app/tests/unit/test_consultant.py app/tests/integration/test_consultant.py`
- Frontend: `npm run typecheck`, `npm test`, `npx playwright test e2e/consultant.spec.ts`
- Integration tests use the isolated `TEST_DATABASE_URL` only.
- Evaluate the fixed cases in `app/tests/fixtures/consultant_voice_eval.json` with the configured model.
  Have the source consultant rate recognisability and readability (1–5). Target an average >=4 for each,
  with no serious invented claims. Model self-checks are useful but do not establish human voice quality.

## Operational signals

Watch queued-job age, generation failures, stage latency, attempts, token usage, completed interviews,
and accepted draft revisions. Generation records retain model ID, prompt version, snapshots, stage,
usage, candidates, and errors for diagnosis without transcript-bearing analytics. Investigate jobs
remaining queued longer than expected: the separate worker may not be running.

## Local verification (2026-10-17)

Backend unit tests: 154 passed. Production Python type checks and Ruff passed. The new
migration compiled to PostgreSQL SQL from revision 20261006_0015; it has not been applied
to a running database here. Six consultant Playwright cases passed across desktop and
mobile using mocked APIs. Frontend TypeScript and targeted ESLint checks passed.

Database integration tests require a running isolated PostgreSQL instance. Docker Desktop
was unavailable on this machine. Live microphone/provider calls and human voice evaluation
have not been run. Keep the feature disabled until those release checks pass.
