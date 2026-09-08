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
