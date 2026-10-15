# Civic Record Ledger (working name: Receipts)

Monitors local governing bodies, captures every agenda and attachment with a
verifiable hash chain, detects changes, and (in later phases) scores each
agenda item for how little coverage it has received relative to its
activity on the record. It does not issue truth verdicts -- all judgment
stays in a human review lane. See the build prompt for the full product
spec; this file only covers how to run what's built so far.

Pilot: one outlet, three Los Angeles bodies -- City Council, County Board of
Supervisors, Board of Police Commissioners. The body list is `bodies.yaml`;
adding a fourth body that fits an existing adapter is a config change, not a
code change.

## Non-negotiables, enforced where possible rather than just documented

- Every fetched file gets a SHA256, a UTC capture timestamp, and an
  immutable storage path (content-addressed: `{sha[:2]}/{sha}.{ext}`).
- `document_versions` is append-only. This is enforced by a Postgres
  trigger (`forbid_document_version_mutation`), not just convention -- an
  UPDATE or DELETE against that table raises rather than silently
  succeeding.
- A new capture of an unchanged document produces zero new rows: dedup is
  on `(document_id, sha256)`, unique at the database level.
- Secrets come from environment variables only (`src/envload.py`, ported
  from record-scanner).

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Fill in `.env`:
- `SUPABASE_URL` is already set to this pilot's project
  (`agenda-ledger`, ref `nvwllgwujqdwailnkels`).
- `SUPABASE_SERVICE_ROLE_KEY` -- from the Supabase dashboard, Project
  Settings > API > service_role secret. Not retrievable through any tool
  this session had access to, and shouldn't be handled through one anyway;
  copy it yourself. Every table has RLS enabled with no public policies, so
  this backend needs the service role to write at all.

```bash
python3 -m src.check_env   # confirms required credentials are present
```

## Phase 1 -- sources and hashing

```bash
python3 -m src.scan --dry-run   # fetches and hashes live sources, writes nothing
python3 -m src.scan             # same, plus writes bodies/meetings/documents/document_versions
python3 -m pytest -q            # 23 tests, all against recorded fixtures, no live calls
```

Running `python3 -m src.scan` twice in a row against unchanged sources
should report `new_versions=0` on the second run -- that's Phase 1's
definition of done, and `tests/test_scan_idempotency.py` proves the
underlying dedup logic offline (with an in-memory fake standing in for
Postgres, verified by mutation -- breaking the dedup check fails that test).
The live half of the same claim (Supabase's own unique constraint on
`(document_id, sha256)`, not just this repo's in-memory logic) still needs
one real run against Supabase once `SUPABASE_SERVICE_ROLE_KEY` is filled
in -- that's the one step of Phase 1 not yet exercised live, see the report
below.

**Live `--dry-run` result, 2026-08-30, against the real three pilot bodies:**
49 meetings, 315 documents, 0 fetch errors. LA City Council (PrimeGov):
9 meetings, 275 documents (agenda pages + item attachments, several
committees). LA County BOS (pdf_directory): 8 meetings, 8 documents. LA
Police Commission (pdf_directory): 32 meetings, 32 documents, correctly
including cancellation notices as dated agenda-type postings. Every
document hashed cleanly; the assumptions above about PrimeGov's agenda
artifact and the pdf_directory date-parsing held up against real data.

### Source adapters (`src/adapters/`)

Four adapter types, one interface (`SourceAdapter.discover()` yields
`MeetingRecord`s, each carrying its `DocumentRecord`s). Add a body that fits
one of these to `bodies.yaml`; write a new adapter only for a genuinely new
platform.

| Type | Used by (pilot) | Notes |
|---|---|---|
| `primegov` | LA City Council | JSON API; agenda = the meeting's portal page (HTML), plus individual item attachments parsed from that same page's server-rendered HTML. No confirmed single "compiled agenda PDF" endpoint -- see `src/adapters/primegov.py`'s docstring. |
| `pdf_directory` | LA County BOS, LA Police Commission | Generic: crawls an index page, extracts a 6- or 8-digit date run from each linked PDF's filename, parses it per a configured `strptime` format. Same adapter, different `bodies.yaml` entry. |
| `legistar` | none in this pilot | None of the three pilot bodies run Legistar (verified live, ruled out). Built per spec for the many cities that do; its test fixture is representative of Legistar's documented schema, not a live capture -- revalidate against a real client before pointing it at one. |
| `granicus` | none in this pilot | LA County's Granicus feed turned out to be video/minutes only, not agendas (their agendas are `pdf_directory` instead). Built and tested (RSS live-recorded fixture, iCal fixture hand-built to RFC 5545) for the Granicus clients whose feeds do carry an agenda link. |

**Content-Type headers are not trusted, on purpose.** Verified live
2026-08-30: PrimeGov's attachment endpoint
(`/api/compilemeetingattachmenthistory/historyattachment/`) serves genuine
PDFs -- confirmed by the `%PDF-` magic bytes -- labeled
`Content-Type: application/json`. Trusting that header would have silently
extracted zero text from every PrimeGov attachment and stored them with the
wrong extension. `src/text_extract.sniff_kind()` reads the actual bytes
first and only falls back to the declared header when sniffing can't tell;
`src/scan.py`'s extension guessing and the Supabase Storage upload's
content-type both go through the same function, so there's one place this
is decided rather than three that could disagree. Regression-tested against
the real recorded response (`tests/fixtures/text_extract/pdf_mislabeled_as_json.pdf`),
and the fix was mutation-verified (reverting to header-trust-first fails
the test, confirming it's not vacuous).

## What's assumed, not yet fully verified live

- **PrimeGov's compiled agenda PDF endpoint.** Several plausible URL
  patterns were probed live and none resolved. The adapter uses the
  meeting's portal page (real agenda content, HTML) instead, which
  satisfies "capture every agenda" but isn't as clean an artifact as a
  compiled PDF would be. Worth one more look before this is considered
  fully closed.
- **Legistar and Granicus adapters are unverified against a live body in
  this pilot's context** -- built to spec and to documented/standard
  shapes, tested against representative (Legistar) or real-but-secondary
  (Granicus RSS) fixtures, but no pilot body exercises them live.
- **LAPD's yearly archive page** (`police-commission-agenda-archives-2026`)
  will need its `bodies.yaml` entry updated (or a second row added) at each
  new calendar year -- there's no code path that derives the year.

## Phase 2 -- diffs and receipt pages

Built: `src/diffing.py` (unified diff + a deterministic plain-language
`diff_summary`, wired into `scan.py` so every new version after the first
gets one automatically) and `web/`, a small Vite app that renders a receipt
page at `/r/<document_version_id>` -- current text, the diff from the prior
version, SHA-256, capture time in UTC, and a download link for the original
file.

```bash
cd web
npm install
cp .env.example .env.local   # already points at this pilot's project + its public anon key
npm run dev                  # http://localhost:5173/r/<id>
npm run build                # -> dist/, what Vercel deploys
```

**diff_summary is deterministic, not an LLM call.** `document_versions` has
no columns to record a model name, prompt version, or input hash for it
(only `agenda_items` does, for Phase 3), and the non-negotiable is that
every LLM output carries that provenance. Rather than add untracked LLM
output to an append-only table, it's computed the same way `sha256` and
`text_extracted` are: deterministically from the two stored texts. The raw
unified diff itself is never stored either, for the same reason it doesn't
need columns -- it's fully reproducible from two already-stored,
already-immutable texts, so the receipt page recomputes it client-side
(`web/src/main.js`, using the `diff` npm package rather than a hand-rolled
diff algorithm).

**Verified live against the real project** (`nvwllgwujqdwailnkels`), via
direct SQL rather than the app's own write path (no service-role key was
available yet): inserted a two-version document chain, confirmed
`prev_version_id`/`diff_summary` linked correctly, then confirmed the
append-only trigger actually blocks both UPDATE and DELETE on
`document_versions`, and that the `(document_id, sha256)` unique constraint
actually rejects a duplicate. All three passed. One side effect worth
knowing: that trigger blocking DELETE means the test rows are now stuck in
the table permanently, by design -- there's no cleanup path once a
`document_versions` row exists, on purpose. They're harmless (named "Test
Body" / "Test Jurisdiction", won't collide with or be matched by any real
scan), but they're there.

**Receipt pages are public read, cited documents only -- applied
2026-09-03.** A receipt page exists to let a reader verify a claim in a
published piece, not to mirror the corpus, so public read was never meant
to be "everything captured" -- it's scoped to exactly what's been cited.
`supabase/migrations/004_public_read_for_receipts.sql` adds
`document_versions.publicly_released boolean not null default false` and
SELECT policies for the `anon` role on `document_versions`, `documents`,
`meetings`, `bodies`, and `storage.objects` (the `document-snapshots`
bucket, object-scoped rather than a bucket-wide public flip), each gated
back to a released version via `EXISTS`. The column defaults false, so
applying the migration changed zero live exposure by itself -- an early
draft used `using (true)` (every row, anon-enumerable) and a fully public
storage bucket (irrevocable per object); both were rejected on review
before anything was applied, for exactly that reason.

**The release process is manual and per-version, on purpose.** A row only
becomes readable when I set `publicly_released = true` on that specific
`document_versions` row, which I do at the point a piece actually cites it
-- reading that version's `text_extracted` for personal information first,
every time. No batch release, no default-on by `doc_type`. **Minutes carry
a higher bar than agendas.** Agenda header boilerplate routinely names
public staff (a legislative assistant, a direct office line) -- public
officials' work contact details on a document the city already publishes
itself, not a blocker. Minutes are different: they routinely record public
commenters' names, and sometimes addresses, read into the record during
public comment, which is the real PII exposure in this corpus. Scoping
release to cited-only means minutes only ever get released at the point
they're already being read closely for the citation, but that reading has
to actually be done, not assumed, before flipping the flag on a minutes
version specifically.

**Not yet deployed to Vercel.** The app builds cleanly (`npm run build`,
verified) and is ready to deploy with `deploy_to_vercel`, but publishing a
new public site is a separate explicit-permission action. Say the word and
I'll ship it -- the RLS mechanism is live, but nothing is released yet, so
today it would deploy to a page with nothing to show.

**Known rough edges:**
- `web`'s `npm audit` reports a moderate/high advisory in `esbuild`
  (via `vite`) that affects only the local dev server, not the production
  build Vercel deploys. Fixing it needs a major Vite version bump (5 -> 7/8);
  left alone for now rather than force an untested breaking change.
- The client-side diff renderer occasionally shows one extra
  removed/added line pair around a real change, when the old and new text's
  last lines differ and both lack a trailing newline (an artifact of how
  the `diff` library's patch format handles "no newline at end of file").
  Cosmetic only -- the actual change is still shown correctly.

## Phase 3 -- extraction and salience

Built: `src/extraction.py` (LLM extraction of agenda items, with a
mandatory verification pass), `src/mentions.py` (GDELT + outlet RSS
mention fetching), `src/salience.py` (the ratio), and
`src/extract_and_score.py` (the phase entrypoint tying them together),
plus `supabase/migrations/005_least_covered_items_view.sql` (a Postgres
view for the ranked report -- applied and live).

```bash
python3 -m src.extract_and_score            # extract + score every un-extracted document_version
python3 -m src.extract_and_score --dry-run  # extract + verify, print, write nothing
python3 -m src.extract_and_score --report   # print the ranked least-covered-items report
```

**Extraction is billed to the Claude subscription, not a metered API key.**
`extraction.call_llm_subscription_cli` (the default) shells out to
`claude -p --output-format json --allowedTools ""`, the same pattern
record-scanner's own `draft_assist.py` already uses, so a pilot-scale nightly
run costs nothing beyond the subscription already paid for.
`call_llm_anthropic` (direct API, metered) still exists as an alternative.
Verified live 2026-08-31, including that `--bare` mode must NOT be used --
it forces API-key auth and never reads subscription credentials, which
would defeat the point. `extract_and_score.py` caps calls per run
(`MAX_EXTRACTIONS_PER_RUN`, default 20) the same way record-scanner caps its
own subscription-billed drafts per day: to protect rate/session limits, not
dollars. A capped run says so and reports how many documents were held
back for next time, rather than truncating silently.

**Verification is mandatory, not optional, and it has now caught real
hallucinations in real live output, not just a hand-built test fixture.**
Every extracted item's dollar amounts and named entities are checked
against the source text before `extract_agenda_items()` returns anything --
a claim that doesn't verify is dropped and logged as a warning, not passed
through. Run live end-to-end against the real LA County supplemental
agenda PDF (15 real agenda items extracted, real model, real verification),
it correctly stripped a fabricated entity ("Director of Aging and
Disabilities" -- the real text reads "the Directors of DCBA, and Aging and
Disabilities," a shared construction, not that standalone title) while
correctly keeping everything genuinely present. Also proven with a
hand-written response fixture carrying three deliberate fabrications (a
dollar amount, an address, an entity name) for a repeatable, offline test.

That same live run also surfaced a real bug in the verifier itself:
"Director of Consumer and Business Affairs (DCBA)" -- genuinely,
verbatim present in the source text -- was wrongly dropped, because the
phrase ends in ")" and `\b...\b` anchoring finds no word-boundary between
")" and the "," that follows it in the real text (both non-word
characters, so no transition exists for `\b` to match). Fixed in the new
`src/textmatch.py` (shared by `extraction.py` and `mentions.py`, which had
the identical bug): a boundary is now only asserted on a side where the
phrase's own edge character is itself a word character, closing the false
positive without reopening the "Acme" -inside-"AcmeCorp" false-positive
this project already guards against. Re-run live after the fix: DCBA
survived verification correctly, and the genuine paraphrase above was
still (correctly) caught -- the fix narrowed the bug, not the guard.

**Input hash without a new column.** The non-negotiable is that every LLM
output carries its model, prompt version, and input hash. `agenda_items`
has `extraction_model` and `extraction_prompt_version` but no
`extraction_input_hash` column -- not an oversight, a design choice:
`document_version_id` already references an immutable `document_versions`
row whose `sha256` is the exact input this extraction ran against, so
`(extraction_prompt_version, document_versions.sha256)` via that one join
*is* the audit trail. Adding a column would just be a stored copy of data
one join away, the same reasoning that kept Phase 2's diff out of a column.

**record_activity = 1 per item, an explicit assumption.** The build spec
names no formula for "record activity," only "record activity divided by
coverage count, with a defined floor." Rather than invent a weighting
scheme (by dollar amount, by entity count) with no spec to justify it, this
first version treats every agenda item as one unit of activity, which
makes "least covered" exactly equivalent to "lowest coverage_count" --
transparent and easy to audit. A richer formula is a reasonable future
refinement, not required by Phase 3's definition of done.

**GDELT confirmed live, and confirmed flaky.** `src/mentions.py`'s GDELT
client was built against a real, live probe (not documentation): the
`doc/artlist` endpoint, its article shape, and the `startdatetime`/
`enddatetime` window parameters were all verified working. In the same
session, a second GDELT query issued minutes after a successful first one
failed outright (SSL connect error) -- matching record-scanner's own
documented experience that GDELT penalizes an IP regardless of pacing. A
failed GDELT measurement is therefore unmeasured, never zero (mutation
verified: a version of the code that wrote a zero-coverage score anyway on
total measurement failure was confirmed to fail the test suite).

**The pilot outlet is LAist.** `OUTLET_RSS_FEED_URL` is set to
`https://laist.com/rss/latest-news.rss`, found and verified live 2026-08-31
(LAist runs on Brightspot CMS; the feed URL isn't the obvious guess --
`/feed`, `/rss.xml`, and `index.rdf` all 404 or are stale, the real one is
`/rss/latest-news.rss`). Confirmed end-to-end against the real feed, not
just a fixture: querying for "NPR" against the live feed correctly found
the one real matching story. A recorded 5-item fixture
(`tests/fixtures/mentions/laist_latest_news.xml`) backs the offline tests.
It's a rolling "latest stories" feed (12 items), not a deep archive --
fine for the recency-windowed search this does, not a source of historical
coverage.

**The DoD's ten-extraction spot check is done, run live against ten real
documents** (not the Postgres pipeline yet -- fetched directly from the
live pilot sites, the same URLs Phase 1's dry run already found): the LA
County supplemental agenda above, three more real LA County BOS agendas
(one a full FY2026-27 budget adoption packet), three LAPD Police Commission
agendas (one a genuine cancellation notice, correctly extracting zero
items), and three PrimeGov artifacts (two City Council/committee meeting
pages, one a real individual attachment PDF -- confirming the
mislabeled-Content-Type sniffing fix works on the full live path, not just
in a test fixture). 103 items total. Beyond the automated verification
guard, every dollar amount across the LA County and LAPD documents was
independently cross-checked by hand against its source line, confirming
correct *attribution* to the right item -- not just "this text exists
somewhere in the document," which the automated guard alone can't
distinguish from a misattributed-but-present figure. All matched exactly.

That batch surfaced three more real issues, all fixed:

- **JSON parsing was too strict.** The same prompt against the same short
  input (the LAPD cancellation notice) returned clean JSON on one run and,
  on another, valid JSON followed by trailing content that made strict
  `json.loads()` reject the entire response -- and LAPD cancels roughly a
  third of its posted meetings, so this is routine, not rare. Fixed with
  `json.JSONDecoder().raw_decode()` from the first `{`, which parses the
  first complete JSON value and ignores whatever follows -- tolerant of
  surrounding model chatter without weakening what counts as valid.
- **The CLI timeout was too tight for a large real document.** A genuine
  ~35k-character, 38-item committee agenda timed out at the original 120s.
  Raised to 300s. Re-run afterward: all 38 items extracted correctly.
- **A third `textmatch.py` bug**, same family as the DCBA one: a real
  agenda used "Governor's" with a Unicode right single quote while the
  model's own output used a plain ASCII apostrophe, so the genuinely
  correct entity was wrongly dropped. Fixed by normalizing common
  quote/apostrophe variants before comparing (`normalize_text`, renamed
  from `normalize_whitespace` since it now does more than whitespace).

Every fix above was mutation-verified the same way as everything else in
this repo: broken, confirmed the relevant test(s) fail, reverted, confirmed
the suite is clean again.

## Phase 3, continued -- the real production run

Once the service-role key was in and Phase 1's real write path was live,
`extract_and_score.py` ran for real against actually-captured pilot data
for the first time, in several rounds. That surfaced two more real bugs,
both more consequential than anything in the spot-check batch above,
because they silently affected every run rather than one document.

**GDELT is currently unusable from this network.** Not flaky -- every
single query failed, across 130+ distinct real entities, both connection
timeouts and empty responses. This matches record-scanner's own documented
history (GDELT can rate-limit an IP hard, regardless of pacing), just at
much greater scale than one bad request. The "unmeasured, not zero" design
held up correctly under this: zero false coverage data got written, every
failure was logged and skipped cleanly. But with GDELT fully down and no
second source, `--report` was producing almost nothing -- which is what
led to setting up the outlet feed (above) as a GDELT-independent source.

**The outlet feed had zero effect for several runs after being set, for a
separate, more fundamental reason.** `OUTLET_RSS_FEED_URL` and its sibling
config values were plain module-level assignments, evaluated once at
import time -- before `main()` ever calls `load_dotenv()`. Running via
`python3 -m src.extract_and_score` therefore always saw
`OUTLET_RSS_FEED_URL` as unset, silently, no matter what `.env` said.
Setting the outlet feed had done nothing until this was found and fixed
(`_load_config()`, called once at import for direct/test use and again in
`main()` after `load_dotenv()` actually runs).

**A second, related bug in the scoring logic** would have undercut the fix
even after the config-loading bug was closed: the "was this entity
measured" check used `stats["unmeasured_entities"]`, a running total
across every item in a whole document_version, not the current item --
so one bad entity anywhere earlier in a multi-item document (real
documents commonly have dozens of items) wrongly skipped every later
item's score too, even ones whose own entities measured cleanly. It also
conflated "had an error" with "was unmeasured": an entity where GDELT
failed but the outlet feed *succeeded* with a genuine zero was still
treated as fully unmeasured, discarding a real (if partial) measurement.
Fixed together: `any_entity_measured` now tracks, per item, whether at
least one configured source returned an answer for at least one entity --
a successful empty result counts; only every source failing for every
entity is truly unmeasured.

Re-run after both fixes: **40 of 40 items extracted in that run got a real
salience_score**, up from 2 total across every prior run combined. All
currently tie at the top (coverage 0, ratio 1.0) -- expected, not a bug:
GDELT is still down and LAist's narrow 12-item "latest stories" feed
doesn't happen to mention any of these department/committee names, so
every entity is genuinely, correctly measured as zero right now. `--report`
now produces real output for the first time:

```
BODY                                     ITEM     COVERAGE  RATIO    TITLE
Los Angeles County Board of Supervisors  1-P      0         1.00     Whittier Narrows Recreation Area Regional Splash Pad
  receipt: (RECEIPTS_BASE_URL not set) /r/8aa15cf7-1831-4e72-93d7-344145bb7f27
...
```

135 tests passing, every fix above mutation-verified.

**What's still blocked:** receipt links show `(RECEIPTS_BASE_URL not set)`
until Phase 2's Vercel deployment is live, which still needs the RLS
approval and your go-ahead to publish. 327 document_versions remain queued
behind `MAX_EXTRACTIONS_PER_RUN`; future runs will pick them up.

## Phase 4

Not built yet.
