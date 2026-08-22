# Japanese AI Conversation & Anki Generator (PoC)

Practice spoken Japanese with an adaptive AI tutor over the OpenAI Realtime API,
watch the exact API cost tick up live, and turn the session into Anki cards for
the words you did not know yet.

```
Angular (24 kHz PCM16)  <--WebSocket-->  FastAPI relay  <--WebSocket-->  OpenAI Realtime API
                                              |
                                              +-- Chat Completions (structured analysis)
                                              +-- WaniKani API (known-vocabulary filter)
                                              +-- AnkiConnect (localhost:8765)
```

The browser never sees the OpenAI API key: all traffic is relayed through the
backend.

## Requirements

- Docker (the quickest way to run everything, and how it is deployed)
- For working on the code: [uv](https://docs.astral.sh/uv/) with Python 3.12+, Node.js 20+
- An OpenAI API key with access to `gpt-realtime-2.1-mini`
- Optional: a WaniKani personal access token
- Optional: Anki desktop with the [AnkiConnect](https://ankiweb.net/shared/info/2055492159) add-on

## Run the whole stack

```bash
OPENAI_API_KEY=sk-... docker compose up --build
```

Opens on <http://localhost:8085>. This brings up Postgres, the backend and the
nginx-served frontend, creating the two database roles on first start — the
same owner/app split the deployment uses. The API key can also be left out here
and entered in the app's Settings screen instead.

## Run for development

```bash
cp backend/.env.example backend/.env   # fill in DB_* and OPENAI_API_KEY
cd backend && uv sync && cd ..
cd frontend && npm install && cd ..
./dev.sh
```

Backend on <http://localhost:8000> (docs at `/docs`), frontend on
<http://localhost:4200>. The Angular dev server proxies `/api` and `/ws` to the
backend, so both origins are the same from the browser's point of view.

This needs a Postgres with the two roles. The quickest way is to borrow the
compose one:

```bash
docker compose up -d postgres
# then in backend/.env:
#   DB_URL=postgresql://localhost:5432/jp_conversation
#   DB_USER=jp_conversation_app       DB_PASSWORD=jp_conversation
#   DB_OWNER_USER=jp_conversation_owner  DB_OWNER_PASSWORD=jp_conversation
```

## Data

Postgres holds three things, all of which survive an image update:

| Table | Contents |
|---|---|
| `app_settings` | One row. API keys and model choices from the Settings screen. Every column is nullable — a NULL falls back to the environment variable, so the app boots from its `.env` alone. |
| `scenarios` | Seeded on first start from `backend/scenarios/*.md`. Editing one in the UI marks it `is_customized`, which stops the next boot from seeding the file version back over it. |
| `sessions` | Finished conversations: transcript, exact cost, the analysis, and the prompt the tutor actually ran with. |

Scenarios ship as Markdown with YAML front matter so they can be reviewed and
diffed as prose:

```markdown
---
slug: konbini
title: Einkaufen im Kombini
summary: Abendschicht an der Kasse ...
---

You are the clerk at a Japanese convenience store ...
```

The body is the model-facing prompt and is English; `title` and `summary` are
user-facing and German.

## How it works

### 1. Setup

Pick one of the preset scenarios or describe your own, choose your JLPT level
(N5–N2), and hit start. The level drives both vocabulary complexity and speaking
pace in the tutor's system prompt.

### 2. Voice and speaking rate

Ten voices are selectable on the setup screen, each with a spoken preview. The
first preview per voice is rendered through the TTS endpoint and cached on disk
(`backend/.voice-samples/`), so it costs a fraction of a cent once and nothing
afterwards — roughly 1.1 s to render, 3 ms from cache.

The voice must be picked *before* the session starts: the Realtime API fixes it
once the first audio is produced. The speaking rate is different — it can be
changed mid-session with the tempo slider, and takes effect from the tutor's
next reply. For a beginner, dropping to ~0.8x makes a large difference.

### 3. Live conversation

The microphone is captured through an `AudioWorklet` at 24 kHz and sent to the
backend as raw PCM16 binary frames; the backend base64-encodes them into
`input_audio_buffer.append` events. Audio coming back is forwarded as binary
frames and scheduled gap-free in the browser, so the browser never base64-decodes
on the hot path.

Turn taking uses the Realtime API's `semantic_vad`. The default eagerness is
`low` — learners need thinking time, and being cut off mid-sentence is the
fastest way to kill a practice session — but the right value changes as you
improve, so it is a setting (`REALTIME_VAD_EAGERNESS`, overridable in Settings)
and the session screen can change it live, next to the tempo slider. Waiting for
a tutor that already knows you have finished is its own kind of annoying.

**Scenario design.** A scenario preset describes *who you are and where you
are*, never a sequence of things to ask. The first version of the convenience
store preset listed the steps of a checkout ("ask whether to heat it up, whether
they need a bag and chopsticks, ...") and the model worked through that list
regardless of what the learner bought — offering to heat an iced coffee, then
handing out chopsticks with a drink. Roles and goals generalise; checklists
fossilise. The system prompt reinforces this with explicit coherence rules: one
question per turn, only ask what applies to the current situation, never
contradict an earlier statement, and admit incomprehension rather than inventing
a plausible-sounding reply.

**Scaffolding policy.** When the learner hesitates or stalls, the tutor escalates
help *inside Japanese first*: repeat slower → rephrase simpler → offer a yes/no
question → model an example answer. It only switches to German or English when
the learner explicitly asks. Grammar is never corrected mid-conversation; that
happens in the review step.

**The わからない button.** A teacher notices when you are out of your depth and
eases off unasked; the model cannot, and asking for help *in Japanese* is
exactly what someone who is stuck cannot do. So the session screen has a button
that says it for you. Each press without saying anything in between escalates
one step — stay in Japanese and make the sentence easier to understand, then
make it easier to *answer*, then assume nothing landed at all, and only as a
last resort explain it in German before switching straight back. The tutor is
never told that a button exists, only that you signalled you are stuck, so it
helps in character instead of breaking into teacher mode. Saying something
resets the escalation.

### 4. Live cost tracking

The relay listens for `response.done` and reads the exact `usage` object:

```
input_token_details  → text_tokens, audio_tokens, cached_tokens_details
output_token_details → text_tokens, audio_tokens
```

Each modality is billed at its own rate from `backend/app/pricing.py`, cached
input tokens at the (much cheaper) cached rate. The running total is pushed to
the frontend as `app.cost.update` and shown as e.g. `$0.0124 USD` with a timer
and a $/minute estimate. Nothing is estimated from wall-clock time — the numbers
come straight from the API's own accounting.

Rates for `gpt-realtime-2.1-mini` (USD per 1M tokens):

| | text | audio |
|---|---|---|
| input | 0.60 | 10.00 |
| cached input | 0.06 | 0.30 |
| output | 2.40 | 20.00 |

If you point `REALTIME_MODEL` at a model that has no entry in the table, the UI
says so and falls back to the mini rates.

### 5. Post-session analysis

Ending the session posts the transcript to `/api/analysis`, which:

1. Optionally loads every WaniKani vocabulary item at **Guru or above**
   (SRS stage ≥ 5) and passes it to the model as an exclusion list.
2. Calls the analysis model with **Structured Outputs** (`strict: true`), so the
   response always matches the `SessionAnalysis` schema.
3. Filters the returned cards against the WaniKani list a second time — models
   do not always honour exclusion lists — and deduplicates them.

A WaniKani outage degrades to an unfiltered analysis instead of failing.

### 6. Scenario editor

Scenarios are edited in the app, with a writing assistant beside the editor.
It answers in German, proposes complete English prompts as one-click
replacements, and its system prompt encodes the role-not-checklist rule — so it
argues against the failure mode described above rather than helping you
reproduce it. It runs on its own model (`SCENARIO_ASSISTANT_MODEL`), separate
from the live tutor, because it writes prose rather than driving a conversation.

Editing a built-in scenario marks it as customised; a redeploy will not
overwrite it, and "Auf Original zurücksetzen" restores the Markdown version.

### 7. Session export

The review screen offers the session as JSON, either to the clipboard or as a
download. It contains the transcript, exact usage and cost, the analysis result
and — importantly — the **system prompt the tutor actually ran with**. A
transcript alone rarely explains why a conversation went sideways; the prompt
usually does, which makes the export directly useful as input for a coding
agent working on the prompts.

### 8. Anki export

Tick the cards you want and hit export. `/api/anki/export` talks to AnkiConnect
on `localhost:8765`, creates the deck and a four-field note type
(`Expression`, `Reading`, `Meaning`, `ContextSentence`) on first use, and reports
how many notes were added and how many Anki rejected as duplicates.

## Deployment

Two images are published to GHCR by GitHub Actions on every push to `main`:
`jp-conversation-practice-backend` and `-frontend`. The stack itself lives in
`deploy/jp_conversation_practice/` — copy that directory into the
`compose-stacks-unraid` repo.

The backend joins the external `postgres-core-net` and reaches the shared
Postgres by container name. Only the frontend publishes a port (**8085**);
nginx serves the SPA and reverse-proxies `/api` and `/ws` internally, so the
browser talks to a single origin and no CORS is involved.

First-time setup, once per deployment. The SQL lives in `dbeaver/` and is run
by hand against the shared Postgres — in DBeaver, or piped through `psql`:

```bash
# 1. Fill in the passwords
cp env/jp-conversation-practice-backend/.env.example \
   env/jp-conversation-practice-backend/.env

# 2. Substitute the ${...} placeholders in the SQL with those passwords,
#    WITHOUT the surrounding quotes, then run both files:
docker exec -i postgres-core psql -U postgres < dbeaver/create_users_and_db.sql
docker exec -i postgres-core psql -U postgres -d jp_conversation \
  < dbeaver/grant_privileges.sql

# 3. Confirm it took effect — both roles and the database must be listed
docker exec -i postgres-core psql -U postgres < dbeaver/verify.sql
```

If a value in the `.env` is written as `DB_PASSWORD="…"`, paste it into the SQL
**without** the quotes: Docker Compose strips them before the container sees
the value, so a role created with them can never be logged into.

The tables are created by the backend on startup as the owner role; the app
role never runs DDL and gets its access from `ALTER DEFAULT PRIVILEGES`.

**AnkiConnect note:** Anki runs on your desktop, not on the server, so the
default `localhost:8765` cannot work from inside the container. Point
`ANKICONNECT_URL` at the machine running Anki and add that origin to
AnkiConnect's `webCorsOriginList`.

## Configuration

Everything below can be set in `backend/.env` **and** in the app's Settings
screen. The database value wins; clearing it in the UI falls back to the
environment. Infrastructure settings (`DB_*`, `CORS_ORIGINS`, `HOST`, `PORT`)
are environment-only.



| Variable | Default | Purpose |
|---|---|---|
| `DB_URL` | `postgresql://localhost:5432/jp_conversation` | **Required.** Host/port/database only. |
| `DB_USER` / `DB_PASSWORD` | `jp_conversation_app` | **Required.** Serves requests. |
| `DB_OWNER_USER` / `DB_OWNER_PASSWORD` | `jp_conversation_owner` | **Required.** Runs DDL at startup. |
| `OPENAI_API_KEY` | — | Required unless set in Settings. |
| `REALTIME_MODEL` | `gpt-realtime-2.1-mini` | Live conversation model. |
| `ANALYSIS_MODEL` | `gpt-4o-mini` | Post-session analysis model. |
| `SCENARIO_ASSISTANT_MODEL` | `gpt-4o` | Writing assistant in the scenario editor. |
| `SCENARIOS_DIR` | `scenarios` | Markdown scenarios seeded on first start. |
| `REALTIME_VOICE` | `marin` | Default voice. |
| `REALTIME_SPEED` | `1.0` | Default speaking rate. |
| `REALTIME_SPEED_MIN` / `_MAX` | `0.6` / `1.4` | Slider bounds. |
| `REALTIME_VAD_EAGERNESS` | `low` | How soon a pause ends your turn: `low`/`medium`/`high`/`auto`. |
| `TTS_MODEL` | `gpt-4o-mini-tts` | Renders the voice previews. |
| `TRANSCRIPTION_MODEL` | `gpt-4o-mini-transcribe` | Input transcription. |
| `REALTIME_BETA_HEADER` | `false` | Set `true` for pre-GA realtime models. |
| `WANIKANI_API_TOKEN` | — | Empty disables the dedup filter. |
| `ANKICONNECT_URL` | `http://localhost:8765` | AnkiConnect endpoint. |
| `ANKI_DECK_NAME` | `Japanese::AI Conversation` | Target deck. |
| `CORS_ORIGINS` | `http://localhost:4200` | Comma-separated. |

## Tests

```bash
cd backend && uv run pytest
```

The suite covers the cost maths (including cached-token pricing and malformed
usage payloads), the WaniKani filtering and, most importantly, the relay itself:
a fake Realtime API server verifies the session configuration, header handling,
binary-to-base64 audio conversion, event allow-listing, cost accounting and
transcript normalisation end to end.


## Limitations

- **Barge-in does not truncate the model's context.** Interrupting stops playback
  and generation, but the full response stays in the conversation history, so the
  tutor may refer to sentences you never heard. Fixing this means sending
  `conversation.item.truncate` with the actually-played position.
- Single user: there is no authentication, and `app_settings` is one row.
- The relay holds one upstream socket per browser connection — fine for one
  person, not for many concurrent users.
- Pricing is hard-coded and must be updated when OpenAI changes its rates.
- The WaniKani vocabulary list is cached in process for 15 minutes.
