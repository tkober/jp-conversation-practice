# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Language convention

Conversation with the user happens in **German**. Everything in the repository —
identifiers, comments, commit messages, documentation, user-facing UI copy and
the feedback the model generates — is **English**: Angular templates, the
lead-in on error messages, and the parts of the prompts in
`backend/app/prompts.py` that instruct the model to write feedback in English.
Backend `HTTPException` details are English and the frontend's lead-in matches.
Japanese content (the tutor's speech, scenario prompts, furigana) stays
Japanese throughout.

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
docker compose -f compose.sqlite.yaml up --build   # same, but SQLite in a volume
./dev.sh                    # dev servers; frontend proxies /api and /ws to :8000

cd backend
uv sync                     # install
uv run uvicorn app.main:app --reload --port 8000
uv run pytest               # needs Docker: testcontainers starts a Postgres
TEST_DB=sqlite uv run pytest   # same suite against a temp SQLite file, no Docker
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

### SQLite, for a machine with no Postgres

A `sqlite://` `DB_URL` runs the same schema out of a local file
(`compose.sqlite.yaml` is that stack). It is a second backend, not a second
code path: everything that differs is collected in `db.py`, and nothing above
that module knows which one is in use. What differs:

- **The roles collapse.** `_role_url()` returns the same file for both, because
  the owner/app split is a Postgres privilege boundary and SQLite has nothing
  to enforce it with. The Postgres test run still exercises the split for real.
- **`JSONColumn`** is `JSON` with a `JSONB` variant for Postgres — declared
  that way round so the Postgres DDL is byte-for-byte what it already was.
- **`UtcDateTime`** exists because SQLite has no timestamp type:
  `DateTime(timezone=True)` reads a *naive* datetime back, FastAPI serialises
  it without a zone, and the browser reads it as local time. The type attaches
  UTC on the way out.
- **Boolean `server_default`s must be `false()`, not `"false"`.** SQLAlchemy
  quotes a string default as a literal, so SQLite stores the *text* `'false'`
  and reads it back as `True` — every seeded scenario would look customised and
  would never be refreshed from its file again.
- **`migrate_schema()` reflects instead of `ADD COLUMN IF NOT EXISTS`**, which
  SQLite does not have. Add to `ADDED_COLUMNS`, not to the SQL.
- **`_upsert()`** picks the dialect's `insert`; both offer `on_conflict_*` with
  the same arguments but neither accepts the other's construct.
- **Three PRAGMAs on every connection** (`_configure_sqlite_connection`):
  `foreign_keys` (off by default, and `ON DELETE SET NULL` is silently ignored
  without it), `journal_mode=WAL` and `busy_timeout`.
- **`_wait_for_database()` does not wait.** A file is openable now or never, so
  it fails immediately with the path named instead of retrying for a minute.

`TEST_DB=sqlite uv run pytest` points the whole suite at a temporary file —
that, rather than `test_sqlite.py`, is what actually covers the backend;
`test_sqlite.py` holds the cases that must run on a Postgres run too.

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
`app.transcript.event`, `app.session.ended`, `app.error`); raw upstream events
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

`backend/scenarios/*.md` — YAML front matter (`slug`, `title` and
`summary`, all English) plus an English body that is the model-facing prompt — seeds the
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
in coherence. Switching `REALTIME_MODEL` to `gpt-realtime-2.1` is the first
thing to try when the tutor's reasoning, not its wording, is the problem — it
costs exactly 3.2x more per audio token ($32/$64 against $10/$20 per 1M).

Reach for the *versioned* id. `gpt-realtime` bills the same for audio and is
what this file used to recommend, but it is an unversioned alias and OpenAI has
it shutting down on 2027-01-20; the same goes for `gpt-realtime-mini` against
`gpt-realtime-2.1-mini`. The versioned ids carry no shutdown date.

## Voice, speaking rate and turn taking

The voice is fixed by the Realtime API once a session produces audio, so it is
chosen at setup time and travels in the `app.session.start` handshake. The
speaking rate and the VAD eagerness are not fixed, so the session screen changes
both live.

Those two live changes are two of the three places the browser influences
`session.update` (handing over context material is the third), and they
deliberately do *not* go through the allow-list: the client sends
`app.session.speed` / `app.session.eagerness`, and `RealtimeSession` translates
each into a `session.update` carrying nothing but `audio.output.speed` /
`audio.input.turn_detection`. Keep it that way — allow-listing `session.update`
itself would hand the browser the instructions field. Both values are validated
server-side (`_clamp_speed`, `normalise_eagerness`, `is_valid_voice`) rather
than trusted from the client; `voices.py` validates the voice id before it is
used in a filesystem path for the preview cache.

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
the English descriptions, the curated order, and — for the one cost-tracked slot
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

The dropdown keeps a free-text escape hatch ("Other model…") because trying
a model the day it ships is the point of a PoC. It is also where a configured
model that has since left the list resurfaces: a `<select>` renders an unknown
value as blank, so the component falls back to the text box instead of
swallowing it. Picking a realtime model that `MODEL_RATES` does not know says
so right there, rather than leaving it for the session screen to discover.

## The わからない button

A teacher sees when a learner is out of their depth and eases off without being
asked. The model cannot see that, and "ask for help in Japanese" is precisely
what a stuck learner cannot do — so the session screen has a button that says
it for them.

A press sends `app.session.help`. The relay answers it with **one**
`response.create` whose `response.instructions` is
`build_help_instructions()` — the full session prompt plus a block describing
how to help. Per-response `instructions` *replace* the session prompt rather
than extending it, which is why that function rebuilds the whole frame; sending
only the help block would drop the scenario, the level and the language policy
for exactly the turn where the learner is struggling most.

**The help turn is slowed down, and that is what lets the wording work.** No
stage may ask the model to "say it again more slowly": it cannot change its own
delivery, so that instruction reliably produced a near-verbatim repeat — the one
response guaranteed not to help, since those exact words are what the learner
just failed to parse. The rate is handled mechanically instead
(`realtime_help_speed_factor`). It is a factor on `self.speed` — whatever the
session is running at *now*, so it follows the live tempo slider — and not a
rate of its own; the Settings screen shows the resulting tempo next to it,
because a bare "0.80×" is written exactly like the tempo slider's own value and
reads as an absolute rate. The Realtime API has no per-response speed, so it
goes through the same narrow `session.update` the slider uses and is put back on
the following `response.done`.

**Every stage is subject to "smaller than the turn they did not understand"** —
fewer words, one sentence, at most one question, nothing new. Without that rule
the model pads the sentence out with explanation instead of cutting it down, and
the help arrives longer than the thing it was meant to clarify.

`HELP_STAGES` in `prompts.py` is the escalation, one entry per press: two
Japanese-only stages, a third that assumes nothing landed, and English as the
last resort. The stage advances with every press and resets to 0 as soon as the
learner says something (`app.help.stage` carries both directions, so the button
never has to guess).

**The reset needs a turn that actually carried words.** The semantic VAD commits
background noise as a user turn too, and those transcribe to nothing; resetting
on one left a learner who sat silent and kept pressing stuck on stage 1 forever
— invisibly, because an empty turn never reaches the transcript either. So
`_emit_turn` resets only after `_record_speech` kept the turn.

**A press is an event in the transcript** (`HelpEvent`), and the reply it
produced carries `SpeechEvent.help_stage` on top. The two are not redundant:
the event says the learner asked, the marker says which reply actually carried
the help — and a press cancels a running response that may still emit its
partial transcript, so something can come between them. Both are stored,
because an export is how a bad conversation gets analysed and telling a help
turn from an ordinary reply is the first thing you need there. Each stage offers *several* tactics and tells the model to
pick one that fits and not to repeat the previous one — a tutor that answers
the same signal with the same move teaches the learner the pattern instead of
the language, which is the "roles generalise, checklists fossilise" rule
applied to helping.

The button usually gets pressed while the tutor is still talking, so a running
response is cancelled first and the help request rides on the `response.done`
that the cancellation produces; the browser drops its playback queue the same
way it does on barge-in. If the cancel errors instead, the pending request is
sent from the `error` branch — otherwise a failed cancel would leave the button
dead for the rest of the session.

The model is never told a button exists: it is told the learner signalled they
are stuck, and to stay in character.

The English stage has to say it **overrides** the "speak ONLY Japanese" rule
sitting above it in the same prompt. Appending a permission is not enough; the
earlier absolute wins, and the escalation just never arrives at English.

## Context material

A scenario says who the tutor is and where. What it cannot say is what is
lying on the table — and without that, a learner cannot practise the sentences
they will actually need, because これ, その赤いの and この先 have nothing to
point at. Context material fills that in: images or text attached to a
scenario, shown to the learner and described to the tutor.

**The learner sees it. That is half the feature, not decoration.** Deixis works
in both directions or not at all: a menu only the tutor knows about is a menu
nobody can point at. So the session screen renders every attachment
(`ContextPanel`) alongside the transcript, and the tutor's prompt is told
explicitly that the learner is looking at it and must not have it read out.

**The material is evaluated once, not sent to the realtime model.** The ticket
asked for it to be worked up in advance, and that is the right way round here for three
reasons: the default `gpt-realtime-2.1-mini` is already the weakest link in
coherence and reading a photographed menu mid-conversation is exactly the load
it fails under; a description written once is identical in every session, is
what the export shows, and can be corrected by hand when a price is misread;
and it keeps the prompt text-only, so `build_help_instructions()` picks the
material up for free. `context_material.py` makes that call against
`scenario_assistant_model` — the same slot that already writes English prose
for a scenario's prompt, only from a photo instead of a draft. It has to be a
model that can read images.

**A menu is a list, and this project already knows what a list in the prompt
does.** Both the evaluation prompt and the `# Context material` block that
consumes it say the same thing twice over: this describes a thing that EXISTS,
it is not a plan for the conversation. Without that sentence the model works
through the menu from the top, in the same order every session — the konbini
checklist failure with different words. `CONTEXT_RULES` in `prompts.py` carries
the rest: never invent an item the learner cannot see on their screen, use the
names and prices exactly as written, and say so when the description calls
something unreadable.

**The description is an ordinary editable field.** The evaluation is a first
draft, not an oracle, and a failed one keeps the upload rather than losing the
file: `analysis_error` reports why, the attachment stays, and the text can be
retried or simply written. An attachment with an empty description is left out
of the prompt entirely — announcing a menu and then saying nothing about it is
worse than not mentioning it.

**Material belongs to nobody.** The scenario is the role, and the role is the
part that repeats: what varies between two runs of the same konbini is what is
on the shelf. So `attachments` is a library, picked per run on the setup
screen, and the same shelf photo is reusable in the supermarket scenario. A
scenario may *pre-select* entries (`scenario_material`), which decides only
what gets ticked when you pick it — never what is available. Both sides of
that link CASCADE, because the row records a preference about two things and
means nothing once either is gone.

An early version had material owned by the scenario and managed in the
scenario editor. That reads plausibly and is wrong: it makes the material the
fixed part and the role the variable one, which is backwards, and it means
practising the same setting with different goods is impossible without editing
the scenario. Do not put it back.

The scenario currently picked still travels with an upload and a
re-evaluation, but only to *frame* the description — a shelf reads differently
in a konbini than in a supermarket. It does not file the material anywhere.

The bytes live in the database (`attachments.data`) rather than on disk, which
keeps the SQLite deployment a single file and the Postgres one inside the
existing backup; the cap is `ATTACHMENT_MAX_BYTES`, and **nginx's
`client_max_body_size` has to be at least as large** or the proxy rejects a
phone photo with its own 413 before the backend's message about the real limit
can be shown.

**"At the start or during the exercise"** is `available_from_start`: material
either sits in the prompt from the first turn or waits until the learner hands
it over from the session screen. It is the item's own default — a shelf is
simply there, a menu gets brought to the table — and the setup screen
overrides it per run. A handover sends
`app.session.context` with nothing but an id; the relay reads the row and
rebuilds the *whole* instructions into a `session.update`. Sending a
conversation item instead would be lighter and wrong: `response.instructions`
for a わからない turn rebuilds the frame from scratch, so the one turn where
the learner is most stuck would be the one that had forgotten the menu they
are holding. It also does not ask for a reply — the learner clicked because
they want to look at the thing and then speak, and a tutor turn fired at that
moment talks over them.

**The library is managed where it is used**, on the setup screen
(`MaterialPicker`): ticking what comes along, the from-the-start toggle, the
star that pre-selects for the current scenario, and behind the expander the
description, a re-evaluation and delete. One screen, because picking material
and fixing a misread price are the same moment — you notice the wrong price
while deciding whether to bring it.

The material travels with the session record (`sessions.context_items`) and
with the analysis request, both for the same reason: これを二つください is
unreadable — as history and as feedback — without the menu これ pointed at.
The stored `instructions` cannot stand in for it, since they were built before
anything handed over mid-session arrived.

## The transcript is a stream of events

`sessions.transcript` is not a list of utterances. A learner who pressed
わからない three times at one spot and then got handed the menu had a session
that a list of utterances describes badly — and badly in exactly the place you
go looking when the conversation went wrong. So the transcript carries three
kinds of event (`models.py`), ordered by when they happened:

| | what it records |
|---|---|
| `SpeechEvent` | something that was said, plus its furigana and the help stage it answered |
| `HelpEvent` | a わからない press, at the stage it escalated to |
| `ContextEvent` | a piece of material handed over mid-conversation |

`_emit_event` in the relay is the single place the transcript grows, so the
browser's copy and the one that gets stored are built from the same list in the
same order.

**A press used to leave a trace only on the reply it produced**, so a press
whose reply never arrived — cancelled, errored, answered with silence — left no
record at all. That is the case the whole button exists for, and it was the one
the transcript could not show. It also means a session of nothing but presses
is now worth storing, and `Practice.storeSession` does; the *analysis* still
needs speech, and gates on that separately.

**Non-speech events reach the analysis as bracketed stage directions**
(`format_transcript`), never as dialogue lines. The brackets are load-bearing:
the analysis is told to quote the learner verbatim, so a line it mistook for an
utterance would come back as a grammar note about something nobody said. The
system prompt says what the brackets are and tells it to use them — repeated
presses at one spot are the clearest signal it has about what to cover.

**Old rows are upgraded on read, not rewritten** (`parse_event`): an entry with
no `type` is speech. Existing databases carry real practice history, and this
is the same trade the furigana makes. Only the read path is forgiving — nothing
writes that shape any more, so `POST /api/sessions` rejects it.

**Anything that counts "turns" counts speech**, in the history summary
and on the review screen. A press is not a turn, and a number inflated by
presses is worse than no number.

## Furigana

The transcript carries its readings. `annotate()` in `furigana.py` cuts a line
into segments and puts the reading over the kanji **only** — 食べる becomes
食[た]べる, not 食べる[たべる], because the okurigana is the part the learner can
already read. Where the kana of a word do not line up with its reading, the
whole word gets one ruby rather than a plausible-looking wrong split.

The readings come from a local morphological analysis (MeCab via `fugashi`,
dictionary `unidic-lite`), not from a per-kanji table: only the surrounding
word decides that 今日 is キョウ and not イマヒ. It costs no API call and about
0.1 ms per sentence, so the relay annotates every turn as it arrives. The
dictionary is the price — roughly 250 MB in the backend image, memory-mapped,
so the resident footprint stays small. If it cannot be loaded, `annotate()`
returns None and the UI shows plain text, the same degradation WaniKani has.

**Furigana is derived, never stored.** The session row keeps the plain speech and `/api/sessions/{id}` annotates on the way out, so sessions
recorded before this feature have readings too, and both JSON exports strip
them again (`withoutFurigana()`) — an export is meant to be read, and segment
arrays bury the conversation in it.

`_SURFACE_READINGS` is the one hand-maintained exception: unidic reads 私 as
ワタクシ, which is defensible and not what a textbook teaches. Keep that table
short — it is for readings that are *misleading*, not merely surprising.

The toggle (`FuriganaService`) is one setting for the whole app, kept in
localStorage and offered wherever a transcript appears: session, review,
history.

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

**`/api/` raises two nginx defaults.** `client_max_body_size` (1 MB by
default) has to cover `ATTACHMENT_MAX_BYTES`, or a material upload dies at the
proxy; `proxy_read_timeout` has to cover the vision call that answers inside
that upload, and inside a voice preview's first render.

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
