---
repo: uk-due-diligence-mcp
date: 2026-09-09
purpose: Build a tested, transcript-based session review; no hook registration.
touches: hard-boundaries
---

# Task Packet: Transcript-based session review

## Goal

`scripts/session_review.py` turns a Claude Code transcript JSONL into a structured
rollup of failures, denials and token usage, proven against a committed real-session
fixture by a test that goes red when the schema drifts.

## Blocker found before this packet was written — read first

**The fixture cannot be committed as-is.** The candidate transcript contains **600
occurrences of the machine-local home prefix** (`/home/<user>`), one on the `cwd`
field of essentially every record.

`check_invariants.py:check_absolute_paths()` scans **tracked** files for that prefix
and exits 2. Committing the fixture unscrubbed therefore:

- wedges every subsequent agent `Write|Edit|MultiEdit` (the `PostToolUse` hook),
- fails `deploy-staging.yml` on push to main,
- fails `release.yml` before the PyPI publish,
- fails `/verify`.

This is not a reason to abandon the fixture. It is a required, *proposed-not-silent*
redaction (Step 0.3), and it is lossless for this purpose: no assertion in scope reads
`cwd`. **Do not commit the fixture until the redaction below is approved.**

## External boundaries

- Claude Code transcript JSONL at `~/.claude/projects/<project-slug>/<session-id>.jsonl`
  — read-only, local disk, no network. This is an **internal serialisation format, not
  a documented contract** (evidenced by mixed `session_id`/`sessionId` casing). It sits
  below hook payloads on the executability ladder; the drift test is what makes
  depending on it acceptable rather than reckless.

## Assumptions to validate

Phase 1 answers these against the **fixture**, not from memory or docs:

1. The three-class discriminator holds across the whole file, not just the two known
   records: failure = `is_error:true` + `toolUseResult` is `str` + no `toolDenialKind`;
   denial = `is_error:true` + `toolDenialKind` present; success = `is_error:false` +
   `toolUseResult` is `dict`. Report any record matching none, or more than one.
2. Every distinct `toolDenialKind` value present. `"permission-rule"` may not be the only one.
3. The `session_id` / `sessionId` split — which record types carry which, exact counts,
   how often each is absent.
4. A stable grouping key for "same error, repeated". Raw strings carry paths and
   truncation markers, so exact-match under-counts. Propose a normalisation and show it
   against real repeats in the fixture.
5. Which `message.usage` fields are on **every** assistant record vs intermittent. A
   rollup that sums an intermittent field silently under-reports.

## Files to inspect first

1. `docs/plans/TEMPLATE-handoff.md` — the shape contract this packet follows.
2. `.claude/hooks/check_invariants.py` — `check_absolute_paths()` is why the blocker above exists.
3. `tests/test_pre_bash_deploy.py` — the repo's existing pattern for testing against a real artifact.
4. `.claude/hooks/FIELD-NOTES.md` — prior measured claims; do not re-derive.
5. `docs/BACKLOG.md` — three 2026-09-09 entries relate to hook observability.

## Source-of-truth docs

- `.claude/hooks/HOOKS-REF.md` — vendored Hooks reference. Cite **by heading, never by
  line number** (repo `CLAUDE.md` > *Citing the vendored docs*).
- `.claude/hooks/FIELD-NOTES.md` — prior verified runtime claims for this repo.
- `docs/plans/` — prior decisions; `plan-2026-09-09-hook-verification-practice.md` is adjacent.
- The fixture itself — once frozen, it outranks all prose here. Running artifact > docs.

## Evidence to gather first

- The frozen transcript fixture — `tests/fixtures/transcripts/` (**directory does not
  yet exist**; create it).
- Filename: `<date>-<model>-<entrypoint>-mixed-failures.jsonl`. The single-session,
  single-model, single-entrypoint caveat lives in the filename, not in prose that drifts.

### Step 0 — freeze the fixture first. Perishable.

A stated exception to plan-mode-for-committed-files, on grounds of perishability.
Additive and non-destructive. Report exactly what was copied.

Scrub enumeration was run in the originating session. Findings:

| check | result |
|---|---|
| absolute paths / usernames | **600 hits** — redaction required, see Blocker |
| credentials, tokens, API keys | **none.** All candidate hits were false positives: `sk-` matched inside "ta**sk-**specific" / "ta**sk-**packet"; `CH_API_KEY=` and `FLY_API_TOKEN=` were the scan's own command text echoed into the transcript; `FlyV1` / `Bearer` are prose from `CLAUDE.md` deployment notes |
| client-identifying register data | **none.** No Companies House / Land Registry / Charity Commission tool was called in that session |
| `gitBranch` leaking unreleased work | **none.** Single value `main` (592 records) |
| user email address | **2 hits** — redact |

**Re-run this enumeration at freeze time**; the transcript grows and the counts above
are a mid-session prefix.

**Expect self-referential credential hits at freeze time.** The earlier scan's own
command text is now inside the transcript, and the freeze-time scan's will be too. A
`CH_API_KEY=` hit may simply be a `grep` pattern echoed back. Discriminate by checking
whether the enclosing record is a `tool_input.command` containing the scan itself, and
**report those separately from genuine findings** — do not silently fold them into a
"none" line.

**Redaction — APPROVED 2026-09-09.** Substitute the **bare username token**, not
merely the `/home/<user>` prefix, plus the email → `user@example.invalid`.

What the guard actually matches, read from source (not inferred from its name):
`check_invariants.py` sets `HOME_PREFIX = ("/" + "home" + "/<user>").encode()` — built
by concatenation so the file does not match its own scan — and applies it as a plain
**byte-substring containment** (`if HOME_PREFIX in blob`) over `git ls-files` output,
skipping any file with a NUL in its first 8192 bytes. No regex, no word boundary.

So the prefix rule alone satisfies the invariant; the bare-token redaction is a
**privacy measure broader than the guard requires**. Report the delta at freeze time.
Known shapes in the delta: the project-slug form `-home-<user>-dev-...` (in
`transcript_path` and scratchpad paths), `ls -l` owner/group columns, and — note — the
guard's own source line quoted back inside the transcript.

Verify JSON validity by re-parsing every line after substitution.

If the fixture cannot be committed cleanly, **say so and stop**. Everything downstream
depends on it. A synthetic substitute is worthless: its whole value is that both error
classes are real and have known causes.

Note in the commit message that the file is a **mid-session prefix** and which two
events it is known to contain.

## Live probes before starting

none — the transcript is local disk, no live or paid system is touched.

## Out of scope

- **Do not modify `.claude/settings.json`.** No `PostToolUseFailure`, no
  `PermissionDenied`, no `StopFailure`, no `SessionEnd` digest, no `SessionStart` injection.
  The hook-based telemetry logger is **cut as redundant**: the runtime already records
  failures at higher fidelity and the hook channel structurally cannot see denials.
  This is a scope cut on grounds of redundancy — do not write it up as a correction.
- Do not investigate whether `PostToolUseFailure` fires. Unverified and now irrelevant.
  Record as a deliberately-unclosed open unknown in FIELD-NOTES; do not spend a turn on it.
- Do not produce any monetary cost figure. See Constraints.
- Do not touch any other repo in the fleet. This session writes to `uk-due-diligence-mcp` only.
- Do not parse `thinking` block content. Count them; do not analyse them.

## Allowed commands

```bash
uv run --no-sync pytest -q
uv run --no-sync pytest tests/test_session_review.py -q
uv run --no-sync python .claude/hooks/check_invariants.py --standalone
uv run --no-sync ruff check .
git status --porcelain
```

## Do not touch

- `.claude/settings.json` — see Out of scope; a JSON snippet for an existing settings
  file is a *merge* instruction, and applying one literally deletes every unnamed handler.
- `.claude/hooks/pre_bash_deploy.py` and `.claude/hooks/check_invariants.py` — live
  guards. This cycle observes; it does not intervene.
- The committed fixture, once frozen. Phase 3's negative proof mutates a **scratch copy**.

## Acceptance criteria

- [ ] `tests/fixtures/transcripts/<date>-<model>-<entrypoint>-mixed-failures.jsonl` committed, scrubbed, every line parsing as JSON.
- [ ] `scripts/session_review.py` exposes one entry point: transcript path in, `dict` out. No I/O beyond reading that path; writes only to stdout.
- [ ] `tests/test_session_review.py` asserts the known counts against the fixture and names the fields it depends on.
- [ ] `.claude/commands/review-session.md` contains **no** field name, discrimination logic or counting.
- [ ] FIELD-NOTES entry added, in the order specified below.
- [ ] `check_invariants.py --standalone` exits 0 with the fixture **staged, before commit**.

## Evidence of completion

All five pasted back; the cycle is not complete otherwise.

1. `git status --porcelain` showing exactly the intended files and no others.
2. Full scrub enumeration output from Step 0, including an explicit `no findings`
   line for each clean category.
3. `pytest` output showing the schema-drift test passing against the committed
   fixture, with the known counts visible in the assertion. **A manual script run does
   not substitute** — the test exists to catch a future version bump, and a manual run
   proves nothing about future regressions.
4. One deliberate **negative** proof: mutate a field name in a **scratch copy** of the
   fixture, re-run, show the test going red with a message naming the field. An
   instrument that has never produced a true positive in its real execution environment
   is decoration.
5. `check_invariants.py --standalone` exit 0 with the fixture **staged (`git add`) but
   not yet committed** — proving the redaction cleared the absolute-path invariant
   *before* the commit lands. Order matters: the guard scans tracked files, so a missed
   hit committed first would block the very edit needed to fix it. Commit only on exit 0.

## Mutating actions requiring approval

- [ ] Write the scrubbed fixture into `tests/fixtures/transcripts/` — ~1.4 MB of real
      session content entering version control, after the redaction above is approved.
- [ ] `git commit` of the fixture and sources. Nothing is committed without an explicit
      go-ahead in the moment, even though it is listed here.
- [ ] none beyond these. No deploy, no publish, no network call.

## Constraints on the implementation

**Tokens, not money.** Report `input_tokens`, `output_tokens`, `cache_read_input_tokens`
and `cache_creation_input_tokens` separately. These are runtime facts. Do **not**
multiply by a price: the multipliers live outside the transcript, differ per token type,
and change over time. A hard-coded table goes stale silently and launders a wrong number
into a metric. A money figure, if ever wanted, gets its own file with an `as-of` date
and its own staleness test. Not this cycle.

**Logic in the script, not the command file.** Markdown cannot be tested. If a field
name is going into `.claude/commands/review-session.md`, it belongs in the script.

**Read both casings.** `session_id` and `sessionId` both occur and the snake form is
intermittent. Handle both at the read boundary, once — not scattered through the code.

**Never read a redacted field.** The script must not read `cwd`, the username, the
email, or any other redacted value. These are placeholders carrying no information. An
implementation reaching for one means the redaction was not lossless after all, and the
packet needs revisiting rather than the code working around it.

**No network, no live sessions.** The script reads a path it is given. It must never
enumerate the projects directory itself, never read a currently-open session, and never
write anywhere but stdout.

## Phase discipline

- **Phase 1 — investigate.** Plan mode, read only. Answer the five assumptions against
  the fixture. Output a findings note. **Write no code.**
- **Phase 2 — plan.** Module interface; the exact test assertions and why those catch
  schema drift rather than restating the implementation; the command wrapper's contents.
  **Then stop and wait for explicit approval.**
- **Phase 3 — implement.** Only after approval.

## FIELD-NOTES entry — record in this order

1. **The finding:** the runtime records tool failures and guard denials natively in the
   transcript; denials fire no hook event but carry `toolDenialKind`.
2. **The scope cut:** hook-based telemetry removed as redundant, with the reason.
3. **The open unknown:** whether `PostToolUseFailure` fires is unverified and
   deliberately unclosed.
4. **The classification:** transcript is **observation**; hooks are **intervention**.
   Anything that only needs to know what happened does not need a hook.
5. **The caveat:** schema derived from one session, one model, one entrypoint. The
   fixture filename carries this; the drift test is the canary.

State plainly that the transcript is an internal serialisation format, not a documented
contract — the mixed snake/camel casing is the evidence. Anything built on it sits below
hook payloads on the executability ladder, and the drift test is what makes that
acceptable rather than reckless.

## Output format

Report:
- Files changed: [list]
- Tests run: [pass/fail/skipped]
- Evidence: [paths, matching Evidence of completion above]
- Blockers: [any unresolved issues]
