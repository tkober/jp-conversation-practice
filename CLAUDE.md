# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Language convention

Conversation with the user happens in **German**. Everything in the repository —
identifiers, comments, commit messages, documentation — is **English**.

The exception is user-facing UI copy, which is German because the learner is a
German speaker: Angular templates, the German lead-in on error messages, and the
parts of the prompts in `backend/app/prompts.py` that instruct the model to
write feedback in German. Backend `HTTPException` details stay English and the
frontend prefixes them with a German sentence.

## Working in this repository

Every change goes on a feature branch and reaches `main` through a pull
request. Do not merge locally and push `main` — a branch that is already an
ancestor of `main` cannot be turned into a PR afterwards, and the review never
happens.

```bash
git checkout -b feature/<topic>
# ... commit ...
git push -u origin feature/<topic>
# then open a PR against main and leave the merge to the repository owner
```

Push the branch as soon as there is something to look at; do not push to `main`
directly. If a PR was already merged, its branch is gone — start a new branch
rather than pushing to the old name, which would recreate it outside any PR and
leave the commits invisible.

## Commands

```bash
docker compose up --build   # whole stack incl. Postgres on :8085
./dev.sh                    # dev servers; frontend proxies /api and /ws to :8000

cd backend
uv sync                     # install
uv run uvicorn app.main:app --reload --port 8000
uv run pytest               # needs Docker: testcontainers starts a Postgres
uv run pytest tests/test_pricing.py::test_cached_tokens_are_billed_at_the_cached_rate

cd frontend
npm start                   # ng serve on :4200
npx ng build                # AOT + template type-check
```

The frontend currently has no spec files, so `ng test` fails with "No tests
found" — the vitest runner is configured but unused. `ng build` is the
correctness gate: it type-checks templates, so run it after touching any
component. Add specs as `src/**/*.spec.ts` and `ng test` picks them up
(`--filter "<regex>"` by test name, `--include <path>` by file).

## Persistence

Postgres on the shared `postgres-core` instance, using the same two-role split
as the other stacks: an **owner** role runs DDL and seeding in `init_db()` at
startup, an **app** role serves every request. The app role's access comes from
server-side `ALTER DEFAULT PRIVILEGES` (bootstrap SQL in
`deploy/jp_conversation_practice/bootstrap/`), so no GRANT is issued from code.
`migrate_schema()` is the hook for column additions — `create_all` only creates
missing *tables*, so anything else has to go there, idempotent and append-only.

`RuntimeConfig` (`runtime_config.py`) is what services take, not raw settings:
environment defaults with the `app_settings` row layered on top. A NULL column
means "not set here", so clearing a field in the Settings UI falls back to the
environment rather than blanking it. It is loaded per request — the table has
one row, and a stale API key after a settings change would be worse than the
lookup.

Secrets never leave the backend in full: `/api/settings` returns only whether
one is set, a masked hint, and whether it came from the environment (which the
UI needs in order to explain why an env key cannot be cleared).

Tests run against a throwaway Postgres via testcontainers, reproducing the
owner/app split, so a stray DDL statement in a request path fails there rather
than at deploy time. HTTP tests use `httpx.ASGITransport` rather than
`TestClient`: the latter runs the app on its own event loop in a worker thread,
which the shared SQLAlchemy engine cannot be used from.

## Architecture

Three-stage flow, with the backend as the only holder of the API key:

```
setup -> live conversation -> review
```

**Relay** (`backend/app/realtime.py`). One browser WebSocket maps to one OpenAI
Realtime WebSocket. `RealtimeSession.run()` waits for an `app.session.start`
handshake carrying scenario and JLPT level, builds the tutor instructions from
`prompts.py`, sends `session.update`, then pumps both directions concurrently.
Everything the relay adds itself is namespaced `app.*` (`app.cost.update`,
`app.transcript.turn`, `app.session.ended`, `app.error`); raw upstream events
pass through unchanged so the frontend can react to VAD events directly.

**Analysis** (`backend/app/analysis.py`) runs after the session: Chat Completions
with Structured Outputs against the `SessionAnalysis` schema. `_strict_schema()`
rewrites Pydantic's JSON schema for OpenAI's `strict` mode (every object needs
all properties in `required` plus `additionalProperties: false`).

**Frontend state** lives in `RealtimeSessionService` as signals; components read
them directly rather than passing data down. The app is zoneless, so anything
the UI must react to has to be a signal.

## Invariants worth preserving

**The client event allow-list.** `ALLOWED_CLIENT_EVENTS` in `realtime.py` is a
security boundary, not a convenience filter: without it the browser could send
`session.update` and rewrite the tutor instructions. Add to it deliberately.

**Audio crosses as binary frames, not base64.** The microphone worklet posts
PCM16, the browser sends raw frames, and the relay base64-encodes them into
`input_audio_buffer.append`. Downstream, audio deltas are decoded in the relay
and forwarded as binary. Keeping base64 off the browser's hot path is the point.

**The AudioContext runs at 24 kHz** so the browser resamples the microphone and
the worklet only converts float to int16. Changing the rate means changing it in
`config.py`, the worklet and `realtime-session.service.ts` together.

**Playback is scheduled, not played on arrival.** The API delivers audio faster
than real time, so `AudioPlayer` queues each chunk where the previous one ends.
Barge-in therefore requires *two* things: the server stops generating
(`interrupt_response: true`) and the client drops its queue on
`input_audio_buffer.speech_started`. Removing either breaks interrupting.

**Cost comes from the API, never from wall-clock time.** `CostTracker` folds the
`usage` object of every `response.done`, bills each modality at its own rate and
subtracts cached input tokens before applying the uncached rate. `MODEL_RATES`
in `pricing.py` is hard-coded and must be updated when OpenAI changes pricing;
an unknown model falls back to the mini rates and sets `rates_known: false`.

Only the realtime session is counted. The analysis call, the scenario
assistant and the TTS previews are real spending that no counter reports, so
the session and history totals are exact for what they measure and lower than
the actual OpenAI bill.

**AnkiConnect is called from the backend, not the browser.** AnkiConnect checks
the `Origin` header against its `webCorsOriginList` and rejects
`http://localhost:4200` by default. A server-side request sends no Origin and is
allowed through.

**WaniKani filtering happens twice** — once as an exclusion list in the prompt,
once over the model's response in `filter_known_cards()`, because models do not
reliably honour exclusion lists. A WaniKani outage degrades to an unfiltered
analysis rather than failing the request.

## Prompt design

### The scenario is one block, not the whole prompt

`build_realtime_instructions()` in `prompts.py` builds the tutor's entire system
prompt and drops the scenario into a fixed frame that every session gets,
whatever the scenario says:

```
role line             a warm conversation partner running a spoken role-play
# Scenario            the scenario text verbatim, plus the reminder that it is
                      a role and a setting and not a list of steps
# Learner level       JLPT_GUIDANCE[level] — vocabulary, grammar, speaking pace
# Language policy     Japanese only, no romaji, 1-3 sentences, ONE question/turn
# Stay coherent       the anti-nonsense rules (below)
# Scaffolding policy  four escalation steps, German/English only when asked for
# Correction policy   recast silently, never lecture; feedback comes afterwards
# Tone                patient, interested, short affirmations
```

So a scenario cannot switch the language, lift the level cap or turn the tutor
into a grammar drill — it fills in who the model is and where it is, and nothing
else. When a conversation goes wrong, the frame is usually not the suspect.

Around the prompt, the harness is deliberately thin: no tools are declared (the
tutor can only talk), the level comes from the setup screen, and the only other
levers are `REALTIME_MODEL`, the voice, the speaking rate and the semantic VAD's
eagerness (see below).

### Where the scenario text comes from

`backend/scenarios/*.md` — YAML front matter (`slug`, German `title` and
`summary`) plus an English body that is the model-facing prompt — seeds the
`scenarios` table at startup. After that the database is the source of truth:
`seed_scenarios()` refreshes untouched rows from the files but leaves anything
flagged `is_customized`, so an edit made in the UI survives a redeploy. The
setup screen sends the picked row's `prompt`, or the free-text field which
overrides it, as `scenario` in the `app.session.start` handshake. The relay
never treats that text as instructions of its own — it only ever reaches the
model interpolated into the frame above.

### Roles generalise, checklists fossilise

Scenario prompts describe a role and a setting, never a list of things to ask.
This is load-bearing: an early version of the konbini preset spelled out the
steps of a checkout, and the model executed that list literally — offering to
heat an iced coffee and handing out chopsticks with a drink, in the same order
every session. If conversations start feeling canned or nonsensical, look for
imperative sequences that crept into a scenario before blaming the model. The
scenario editor's writing assistant (`scenario_assistant.py`) is built around
the same warning and is told to call such sequences out in a user's draft.

The `# Stay coherent` block in `prompts.py` backs this up: one question per
turn, only ask what applies to the current situation, never contradict yourself,
admit incomprehension instead of inventing something, and never break character
with meta-commentary about the exercise. Each of those rules corresponds to an
observed failure, so removing one is likely to bring that failure back.

`gpt-realtime-2.1-mini` is the default for cost reasons and is the weakest link
in coherence. Switching `REALTIME_MODEL` to `gpt-realtime` is the first thing to
try when the tutor's reasoning, not its wording, is the problem — it costs
roughly 3x more per audio token.

## Voice, speaking rate and turn taking

The voice is fixed by the Realtime API once a session produces audio, so it is
chosen at setup time and travels in the `app.session.start` handshake. The
speaking rate and the VAD eagerness are not fixed, so the session screen changes
both live.

Those two live changes are the only places the browser influences
`session.update`, and they deliberately do *not* go through the allow-list: the
client sends `app.session.speed` / `app.session.eagerness`, and
`RealtimeSession` translates each into a `session.update` carrying nothing but
`audio.output.speed` / `audio.input.turn_detection`. Keep it that way —
allow-listing `session.update` itself would hand the browser the instructions
field. Both values are validated server-side (`_clamp_speed`,
`normalise_eagerness`, `is_valid_voice`) rather than trusted from the client;
`voices.py` validates the voice id before it is used in a filesystem path for
the preview cache.

**Eagerness decides how long a pause may last before the tutor answers**
(`turn_detection.py`). The default is `low`, the most patient setting, because a
learner assembling a sentence pauses where a native speaker would not; the value
is exposed because it stops fitting as the learner improves. `_turn_detection()`
always sends the whole block, never just the changed field: a partial
`turn_detection` drops `interrupt_response` and silently breaks barge-in.

Voice previews are rendered through the TTS endpoint on first request and cached
in `backend/.voice-samples/` (gitignored). `VOICES` lists only voices available
to *both* the Realtime and TTS APIs, so a preview is representative.

## Choosing models

The Settings screen offers a dropdown per slot, built by `model_catalog.py`
from two sources that answer different questions.

`GET /v1/models` answers *what this key may call*. It is authoritative and
current, but each entry carries only `id`, `created`, `owned_by` and
`shutdown_date` — **no price, and no capability or modality**. There is no
pricing API at all; the Costs API under the Admin key reports what was already
billed, aggregated per day, which is a reconciliation tool and not a rate
table. `MODEL_RATES` therefore stays hand-maintained whatever else changes.

The `SLOTS` table answers *what is worth picking and what it costs*. It carries
the German descriptions, the curated order, and — for the one cost-tracked slot
— the price the app will actually bill. Curated entries come first, live extras
follow, and the merge means a model released after the last deploy is still
selectable.

**The ids are not self-describing, so the filters are load-bearing.**
`gpt-realtime-whisper` and `gpt-realtime-translate` both match "realtime"
without holding a conversation, and `gpt-4o-transcribe-diarize` splits speakers
apart when the realtime input stream is one. Each entry in `excludes` is a
model that would otherwise be offered for a job it cannot do. Dated snapshot
ids (`-YYYY-MM-DD`) are dropped as well — they pin an alias that is already
listed, so they would double every dropdown without adding a capability.

**A model id reaches the filesystem.** `VoiceSampleService` caches previews
under `.voice-samples/<tts_model>/`, so the settings PUT validates the shape of
every model field against `MODEL_ID_PATTERN`, for the same reason `voices.py`
validates voice ids.

The dropdown keeps a free-text escape hatch ("Anderes Modell …") because trying
a model the day it ships is the point of a PoC. It is also where a configured
model that has since left the list resurfaces: a `<select>` renders an unknown
value as blank, so the component falls back to the text box instead of
swallowing it. Picking a realtime model that `MODEL_RATES` does not know says
so right there, rather than leaving it for the session screen to discover.

## Session export

`app.session.started` echoes the tutor's full instructions, voice, speed and
VAD eagerness back to the browser so the review screen's JSON export can include
them; the same values are stored on the session row for the history export. That export is the intended
way to hand a bad conversation to another agent for analysis: the transcript
shows the symptom, the prompt usually contains the cause.

## Startup

`init_db()` waits for the database before touching it, because postgres-core
lives in a *different* compose stack: `depends_on` cannot order this one after
it, so on a host reboot both come up at once. Retrying is right for a database
that is merely not up yet.

It is wrong for one that will never let us in, so `_wait_for_database()` splits
the two: SQLSTATEs 28P01 (bad password), 28000 (missing role) and 3D000
(missing database) raise immediately with a sentence naming what to change,
`raise ... from None` so the driver traceback does not bury it. Everything else
retries. Add a new fatal case to `FATAL_SQLSTATES` rather than broadening the
retry.

## Deployment

Two GHCR images, built by GitHub Actions on push to `main`. Only the frontend
publishes a port (8085); its nginx serves the SPA and reverse-proxies `/api`
and `/ws` to the backend over the internal network, which is why no CORS is
involved and the backend port stays unpublished.

**The `/ws/` location is not a copy of `/api/`.** It carries the `Upgrade`
handshake and sets `proxy_read_timeout 3600s` with `proxy_buffering off` — a
learner can listen for minutes without sending anything, and nginx's default
60s read timeout would tear the conversation down mid-sentence. If realtime
sessions start dying after about a minute in the deployment, look here first.

**The UI needs a secure context.** `navigator.mediaDevices` and
`BaseAudioContext.audioWorklet` are `[SecureContext]`, so over plain HTTP to a
LAN address (`http://<host>:8085/`) they are undefined rather than merely
denied and no session can start — the relay itself is fine. Serve the stack
behind TLS, or reach it through `localhost` (an SSH tunnel counts). The setup
screen names this via `microphoneBlockedReason()` instead of failing silently.

Database bootstrap is manual, like the other projects: `dbeaver/` holds the SQL
to create the roles and database, run by hand against postgres-core with the
`${...}` password placeholders substituted. `dbeaver/verify.sql` exists because
the failure mode is silent — Postgres answers a missing role with the same
`28P01` it uses for a wrong password, so "authentication failed" does not tell
you which of the two happened.

The stack directory (`deploy/jp_conversation_practice/`) is meant to be copied
into the `compose-stacks-unraid` repo. `compose.yaml` at the repo root is the
local mirror of it, down to creating both Postgres roles via `dev/initdb`.

## Known gaps

- **`conversation.item.truncate` is not sent on barge-in.** The server knows
  where it stopped generating but not how much the browser actually played, so
  an interrupted response stays in the model's context in full and the tutor may
  refer to sentences the learner never heard. `AudioPlayer.bufferedSeconds`
  exists to supply the played position when this gets implemented.
- Session state is in-memory; no persistence, no auth, one upstream socket per
  browser connection.
- The WaniKani vocabulary list is cached in-process for 15 minutes.
