---
repo: uk-due-diligence-mcp
date: 2026-09-09
purpose: Close the still-open cells in hook-verification practice — live dispatch, matcher semantics, and provenance — using dd's acknowledge gate as the specimen.
touches: live | production | hard-boundaries
---

# Task Packet: Hook verification practice — closing the unproven cells

**Subject.** This packet is about *Claude Code practice*: how hooks dispatch, how
matchers actually evaluate, how an escape-hatch environment variable is handled
across the command string and the process environment, and what counts as having
verified any of it. `uk-due-diligence-mcp` is the specimen, not the point — its
`pre_bash_deploy.py` acknowledge gate happens to be the most instrumented hook in
the fleet, which is why the practice questions are being answered here.

Every criterion below is tagged **[dd]** (specific to this repo — the Fly app, the
release pipeline, this gate's classifier) or **[fleet]** (a finding about Claude
Code itself, portable to every other repo and to the user-global skill). A fresh
session should be able to lift the **[fleet]** items into any other repo unchanged.

Shape C was chosen over A or B because one criterion involves typing a real `fly`
command against a live production app. The do-not-touch boundaries have to be
stated before an agent starts, not negotiated once it is holding the CLI.

## Goal

Resolve five open questions in hook-verification practice — ack-path live
dispatch, matcher anchoring semantics, testing provenance, the auto-memory
premise, and dd's deploy convention — each closed with a recorded observation
rather than an assertion, and with no production deploy performed.

## External boundaries

- **Fly.io CLI (`fly` / `flyctl`) against `uk-due-diligence-mcp`** — the gate's
  target. Only ever invoked in the inert `--help` form described below. **[dd]**
- **Claude Code hook dispatch** — `settings.json` `if:` spawn filter and
  `matcher:` evaluation, i.e. the thing actually under test. **[fleet]**
- **`~/.claude/skills/hook-verification/SKILL.md`** — user-global skill, outside
  this repo; edits there affect every project on this machine. **[fleet]**
- **Claude Code session transcripts and shell history** — read-only corpus for
  the deploy-convention criterion. **[dd]** as data, **[fleet]** as method.

## Assumptions to validate

Three of the five originating items rest on premises that were checked on
2026-09-09 and did not hold. They are recorded here as corrections, not as work.

1. **"All ack-gate testing was synthetic" — false as stated.** FIELD-NOTES §9
   (2026-09-08) records a live end-to-end fire: `fly deploy --help` typed in a
   session, intercepted, deny reason delivered to the agent. Config load, spawn
   filter, dispatch and the deny render are all already verified on real traffic.
   What remains open is narrower and stated in criterion 1. Re-read §9 before
   writing anything about provenance. **[dd]**
2. **The `split-payload-from-execution*` auto-memory does not exist.** All 74
   `memory/` directories under `~/.claude` were searched on 2026-09-09; no file
   matching `*split-payload*` exists anywhere, and this repo's own memory
   directory is empty (created 2026-09-05, never written to). There is nothing to
   build on or duplicate — this is the "a recommendation is not a change" case,
   with the memory recommended in conversation and never written. **[fleet]**
3. **The hook-verification skill has no matcher-semantics table.** The installed
   `SKILL.md` (5133 bytes, mtime 2026-09-08 20:09) contains no such table and no
   "full-match regex" claim; its only matcher references are three rows in the
   *failure-modes* table and prose in steps 2–3. The erroneous claim was never
   written to disk, so criterion 4 is an *addition*, not a correction. **[fleet]**
4. **Unvalidated, and the crux of criterion 1:** that `FLY_DEPLOY_ACK=1 fly deploy
   --help` classifies as `ask` *and* is inert to Fly if confirmed. Piped-payload
   evidence says the first half holds; the second half is `--help` semantics.
5. **Unvalidated:** FIELD-NOTES §9 asserts "no inert probe exists" for the ask
   path, because "approving an `ask` *is* the guarded action". §9's own deny probe
   contradicts this — `fly deploy --help` is deploy-shaped to the gate and inert
   to Fly, and prefixing it with the ack variable keeps both properties. If that
   holds, §9's structural claim is too strong and its table's last cell is
   cheaply closable. Validate before rewriting §9. **[fleet]**
6. **Version drift:** FIELD-NOTES pins its claims to Claude Code **2.1.263**;
   `claude --version` reports **2.1.265** as of 2026-09-09. Re-pin before citing
   any FIELD-NOTES line as current.

## Files to inspect first

1. `.claude/hooks/FIELD-NOTES.md` §9 — the deny/ask verifiability split and the
   standing "do not resolve this by deploying" decision. Sets the boundary for
   criterion 1.
2. `.claude/hooks/FIELD-NOTES.md` §6 — the house technique ("arrange the
   experiment so the thing under test prevents the damage"), which is what makes
   an inert ack probe thinkable.
3. `.claude/hooks/pre_bash_deploy.py` — `_classify()` and the two ack channels
   (command-string env prefix vs `os.environ`).
4. `.claude/settings.json` — the two `if:` handlers and the `matcher: "Bash"`.
5. `.claude/hooks/HOOKS-REF.md` § **Matcher patterns** — how a matcher string is
   evaluated (exact-string path vs unanchored-regex path), and § **Match MCP
   tools** for the consequence that bites hardest.
6. `~/.claude/skills/hook-verification/SKILL.md` — the target of criterion 4.
7. `docs/plans/plan-2026-09-08-hook-channel-corrections.md` — the decisions this
   packet builds on.

## Source-of-truth docs

- `.claude/hooks/HOOKS-REF.md` — vendored Hooks reference, pinned to the CC
  version in FIELD-NOTES; re-check the pinned version before citing it. The
  evaluation table and the `RegExp.prototype.test` anchoring rule are both under
  § **Matcher patterns**; the MCP naming consequence is under § **Match MCP
  tools**.

  **Cite vendored docs by heading, never by line number. [fleet]** A line number
  is the one part of a citation that goes stale silently: re-vendor the doc, take
  an upstream edit anywhere above, and `:295` still resolves — to the wrong
  sentence, with nothing to signal the drift. A heading survives edits above it,
  and if it is renamed the citation fails loudly instead of lying. Same failure
  shape as "config only counts if it loads": a citation only counts if it still
  points at what it claims to. This applies to every vendored reference in every
  repo, not just this one.
- `.claude/hooks/FIELD-NOTES.md` — prior verified claims about this repo's
  runtime behaviour; §1–§10 exist, so a new entry is §11.
- `docs/plans/` — prior decisions; the 2026-09-08 hook-channel-corrections plan
  is the direct predecessor.
- `.claude/settings.json` and `tests/test_pre_bash_deploy.py` — the running
  configuration and its 15-case assertion, which outrank any prose about them.
- `~/.claude/skills/hook-verification/SKILL.md` — the user-global skill as
  installed; read it rather than recalling what it was meant to say.
- `claude --version` — the running artifact, which outranks the pinned version
  in FIELD-NOTES when they disagree.

## Evidence to gather first

- Running Claude Code version, captured verbatim — to re-pin or confirm
  FIELD-NOTES' 2.1.263 claims before citing them.
- The historical `fly`/`flyctl` invocation corpus for criterion 5: shell history
  and Claude Code transcripts under `~/.claude/projects/*/`, filtered to this
  repo. Save the extracted invocation list to the scratchpad, not the repo.
- Session count for the ack-gate development thread, to make the §11 provenance
  claim a counted number rather than a remembered one — derive it from git log
  and transcript files, never from recall.

## Live probes before starting

- [ ] `claude --version` — read-only, local, no external system.
- [ ] `fly deploy --help` typed bare in a live session — read-only against Fly
  (prints CLI help and exits; ships nothing). Deploy-shaped to the gate, inert to
  Fly, so a dispatch failure degrades to printed help rather than a deploy. This
  is a *repeat* of §9's probe; run it only if re-confirming dispatch on 2.1.265.
- [ ] `grep`/`jq` over `~/.claude/projects/*/` transcripts and shell history for
  `fly deploy` invocations — read-only, local files only.

## Out of scope

- Do not modify `pre_bash_deploy.py`'s classifier. The `echo $(fly deploy)`
  substitution gap found on 2026-09-09 is real but is a separate change; file it
  in `docs/BACKLOG.md` if it is not there already.
- Do not write the CLAUDE.md deploy-convention section (criterion 5) until
  criterion 1 has produced a recorded result. A convention written ahead of the
  evidence is the hand-written guess this packet exists to avoid.
- Do not add a standing instruction to `CLAUDE.md` for criterion 2 — provenance
  is a FIELD-NOTES fact, not a rule that binds future behaviour.
- Do not create the `split-payload-from-execution` memory file to make premise 2
  true retroactively. If a memory is worth having, write it on its own merits.

## Allowed commands

```bash
uv run --no-sync pytest -q
uv run --no-sync python .claude/hooks/check_invariants.py --standalone
uv run --no-sync ruff check .
claude --version
fly deploy --help
```

Plus ordinary in-repo edits to the files criteria 2, 4 and 5 name:
`.claude/hooks/FIELD-NOTES.md`, `CLAUDE.md`, `docs/BACKLOG.md`, and this packet.

**Where the line falls, and why:** a write that lands in this repo's `git diff`
is ordinary work — reviewable before it goes anywhere, revertible with one
command. A write that does *not* show up there is gated below, whatever its size.
That is why editing a user-global `SKILL.md` needs approval and editing
`FIELD-NOTES.md` does not, even though the skill edit is four lines and the
FIELD-NOTES entry is a section. Blast radius and reversibility set the bar, not
file count.

## Do not touch

- The `uk-due-diligence-mcp` Fly app, its machines, secrets, or volumes.
- `.github/workflows/release.yml` and `deploy-staging.yml`.
- The version triad (`pyproject.toml`, `server.py`, `server.json`) — a bump here
  would couple an unrelated release to a verification exercise.
- `.claude/settings.json` in *this* repo for the matcher probe (criterion 4).
  Use the throwaway project instead; a stray regex matcher left in dd's real
  config is a live misconfiguration, not a test.

## Acceptance criteria

**1. Ack-path live dispatch — `[dd]` specimen, `[fleet]` finding.**
- [ ] `FLY_DEPLOY_ACK=1 fly deploy --help` typed in a live session returns an
  `ask` permission prompt rather than executing unprompted.
- [ ] The prompt's rendered text is read *before* answering, and recorded
  verbatim — this is the user-facing channel that FIELD-NOTES §9 lists as the
  one unverified cell.
- [ ] The prompt is answered **no**, or answered yes and observed to print Fly's
  help text and exit without deploying. Either outcome closes the render cell.
- [ ] `fly.dev` prod is confirmed unchanged afterwards via
  `scripts/check-deploy-drift.sh`.
- [ ] If the probe turns out *not* to be inert (i.e. `--help` is not honoured
  after the ack prefix for any reason), the criterion is abandoned, not forced —
  §9's "do not resolve this by deploying" stands.

**2. FIELD-NOTES §11 provenance entry — `[dd]` content, `[fleet]` discipline.**
- [ ] A new `## 11.` section records what was and was not exercised for real,
  with the deny/ask asymmetry stated accurately — **not** as "all testing was
  synthetic", which §9 falsifies.
- [ ] The session count is a derived number with its derivation named, not a
  recalled one.
- [ ] §9's table is amended in place (marked, not silently edited) if criterion 1
  closes its last cell, and its "no inert probe exists" claim is corrected if
  assumption 5 holds.
- [ ] Nothing from this criterion lands in `CLAUDE.md`.

**3. Auto-memory check — `[fleet]`.**
- [ ] Done, 2026-09-09: no `split-payload-from-execution*` file exists under
  `~/.claude`; this repo's memory directory is empty. Recorded here as assumption
  2. No further action unless a memory is independently worth writing.

**4. hook-verification skill — matcher semantics — `[fleet]`.**
- [ ] A discriminating probe is run in a throwaway project (`/tmp/hookprobe`,
  already used for this purpose by a prior session — a `-tmp-hookprobe` entry
  exists under `~/.claude/projects/`), never in dd's own settings.
- [ ] The probe uses the skill's own recipe: raw-stdin `fired.log`, cleared
  first, asserted by counted tool names, disarmed after.
- [ ] Matcher `ea.` (regex path — it contains `.`, per § **Matcher patterns**)
  is tested against tool `Read`. A hit proves matching is unanchored as that
  section documents; a miss proves the docs are stale and the full-match
  write-up was right. Record whichever it is.
- [ ] A true negative is interleaved — a tool the pattern must exclude (e.g.
  `Bash`) — so the result distinguishes "unanchored" from "fires on everything".
- [ ] `SKILL.md` gains a matcher-semantics section stating the *measured*
  behaviour, with the CC version it was measured on. Note in the write-up that
  dd's own `matcher: "Bash"` is on the **exact-string** path, not the regex path,
  so this finding does not change dd's dispatch either way.
- [ ] The write-up carries the consequence from § **Match MCP tools**, which is
  where the exact-vs-regex split actually costs people something: `mcp__memory`
  contains only exact-match characters, so it is compared as an exact string and
  matches **no tool at all** — silently. `mcp__memory__.*` is required. That is
  the failure mode the skill's "matcher never matches" row is describing without
  naming.

**5. CLAUDE.md deploy convention — `[dd]`.**
- [ ] Blocked until criterion 1 is resolved. Do not start early.
- [ ] The section describes what the extracted history actually shows about
  `fly deploy` usage, with counts and dates, not a procedure written from
  intuition.
- [ ] If the history shows zero real hand deploys, that is the finding and the
  section says so plainly rather than inventing a convention for an event that
  has never occurred.

## Evidence of completion

- Verbatim transcript excerpt of the `ask` prompt from criterion 1, including the
  rendered `ASK_REASON` text, plus the answer given.
- `scripts/check-deploy-drift.sh` output showing prod unchanged before and after.
- Diff of `.claude/hooks/FIELD-NOTES.md` showing the new §11 and any §9
  amendment marked rather than silently rewritten.
- `fired.log` contents from criterion 4 — line count and extracted tool names —
  pasted into the write-up, and confirmation the log and its appending line were
  both removed.
- Diff of `~/.claude/skills/hook-verification/SKILL.md`.
- The extracted `fly deploy` invocation list (scratchpad path) backing criterion 5.
- `uv run --no-sync pytest -q` and `check_invariants.py --standalone` both green
  after any edit.

## Mutating actions requiring approval

- [ ] `FLY_DEPLOY_ACK=1 fly deploy --help` — sets the documented escape variable
  and reaches a real permission prompt. Believed inert (prints help, ships
  nothing), but it is the one command in this packet that deliberately arms the
  bypass, so it is never run without an explicit go-ahead in the moment.
- [ ] Answering **yes** at that prompt — a separate approval from running the
  command. Answering no is sufficient to close the criterion.
- [ ] Editing `~/.claude/skills/hook-verification/SKILL.md` — a user-global file
  affecting every project on this machine, not just this repo.
- [ ] Writing to `.claude/settings.json` in `/tmp/hookprobe` and reverting it.
- [ ] Any `fly` command other than `fly deploy --help` — **not** pre-authorized
  by this packet; stop and ask.
- [ ] Any write outside this repository not already listed above — it leaves no
  reviewable diff here, so it does not inherit the Allowed-commands latitude.

## Output format

Report:
- Files changed: [list]
- Tests run: [pass/fail/skipped]
- Evidence: [paths, matching "Evidence of completion" above]
- Blockers: [any unresolved issues]
- Which criteria are **[fleet]** findings ready to port to other repos, and which
  are **[dd]**-only.
