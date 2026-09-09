# Backlog

Non-blocking follow-ups surfaced during other work. Not a plan, not evidence —
write it down and move on. Triage into `docs/plans/` when picked up. Strike
through, don't delete, when done or superseded.

Entries are one physical line each — `/prime` heads this file with a `grep`, so
a wrapped entry shows up there truncated mid-sentence. If it needs more than a
line to explain, it is plan-sized, not backlog-sized: write it in `docs/plans/`.

Append new entries at the bottom, oldest first. `/prime` heads the top three
live ones, so what it surfaces is what has been ignored longest — which is the
point. Struck entries are skipped there automatically.

Deliberately *not* held to `.claude/hooks/FIELD-NOTES.md`'s evidence standard;
that file is for measured claims about the runtime, this one is for reminders.

---

- 2026-09-08: `actions/checkout@v4` + `astral-sh/setup-uv@v5` (both workflows) target Node 20, forced onto Node 24 — non-fatal today, bump before it is.
- ~~2026-09-08: `/prime`'s Open-TODOs fallback is dead — `grep ... | head -8 || echo none` never fires, since `head` exits 0 even when grep matched nothing.~~
- ~~2026-09-08: `/prime`'s "Open TODOs" label overclaims — it scans only `*.py` for case-sensitive `TODO`/`FIXME`, so a marker in any of the 22 tracked `.md`, or in `.sh`/`.yml`/`.toml`, is invisible; widen the scan or narrow the label.~~
- 2026-09-08: Decide if marker-hunting is grep's job or a VS Code Todo-Tree-style extension's — grep is text-only and cannot tell a real `# TODO` from the word in a docstring or a `KEY=xxx` placeholder (3 such false positives in `fly.toml`/`fly.staging.toml` today), whereas a comment-parsing extension can.
- 2026-09-08: Whichever TODO convention wins, write it down — marker vocabulary and where it is scanned — in this repo's `CLAUDE.md` or the global one, since an unrecorded convention binds nobody and drifts silently.
- 2026-09-08: `.claude/hooks/FIELD-NOTES.md` is in a poor location and has no maturity model — it sits with the hooks but records runtime facts that outlive them, and a note that has stabilised into a permanent constraint should graduate into a hook, a `check_invariants.py` check or a committed test rather than staying prose someone must remember to reread; decide the destination and the promotion trigger.
- 2026-09-08: Explore Claude Code subagents (isolated context and tool set per agent) for this repo's workflows — enumerate the real surface against the docs map and the installed runtime before designing anything, since this is exactly the enumerate-before-you-build trap FIELD-NOTES §4 records three misses of.
- 2026-09-09: Port the InstructionsLoaded/PermissionDenied logger to `property-shared` (6 `paths:`-scoped rules, no `settings.json` yet); when porting, note `.claude/rules/mcp-server.md`'s `mcp_server/**` glob matches zero tracked files (verified 2026-09-09 against `32caf95` — no such directory exists) while the other five match 3–128 of 549.
- 2026-09-09: `PermissionDenied` does not fire on a `PreToolUse` hook-issued deny — measured 2026-09-09: a denied `fly deploy` produced `Hook denied tool use for Bash` / `Bash tool permission denied` in the runtime debug log while `.claude/metrics/` gained no record, so `a5bb8fc`'s logger has now had its first real opportunity and missed it; log the denial from inside `pre_bash_deploy.py`, or find the event that actually covers hook denials.
- 2026-09-09: `check_invariants.py:check_version_triad` fails open on a regex miss — a participant the regex cannot parse drops silently out of `found` instead of being reported, so `len(set(...)) <= 1` still holds; measured 2026-09-09: `server.py` at `"version" : "9.9.9"` (one space before the colon) exits 0, while `"version": "9.9.9"` exits 2 — identical drift, opposite verdict. An unparseable participant should be a problem, not an absence.
- 2026-09-09: The `PostToolUse` `Write|Edit|MultiEdit` matcher has no path predicate, so an edit to a file in any repo fires `check_invariants.py`, whose `ROOT` is pinned to this one — measured 2026-09-09: editing `PROBE.md` in the sibling `mcpfleet-obs` repo re-asserted *this* repo's triad and exited 0, a green light sourced from a repo the edit never touched.
- 2026-09-09: Fixture size policy for fleet-wide rollout — the `dd` session-review fixture is one ~1.7 MB transcript (`tests/fixtures/transcripts/`, full session, deliberately not curated to a subset this cycle); decide the per-repo policy before extending the workbench beyond `dd`, since twelve repos at that size is ~20 MB of session content in git for a schema-drift canary.
