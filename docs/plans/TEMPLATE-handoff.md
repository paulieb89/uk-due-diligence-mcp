# Handoff / kickoff / task-packet template

Three shapes. Pick based on stakes and risk profile, not habit.

## Shape A — full handoff (investigation, ratified decisions, amendments)

Use when: a real decision got made or reversed, evidence was gathered,
something spans multiple sessions and might need a later correction.

    ---
    status: active | landed | superseded
    repo: <repo-name>
    date: <YYYY-MM-DD>
    supersedes: <prior file, or "none">
    sessions: <which sessions touched this>
    ---

Sections: Purpose and scope · Decisions (mark AMENDED/INVESTIGATED inline
when something changes, don't silently edit) · Open items — numbered,
concrete, ready to hand to the next session · a closing "for a fresh
claude.ai session" paragraph, since these are meant to work without the
originating chat's history.

## Shape B — lightweight kickoff (small, well-scoped, low blast radius)

Use when: the fix is already fully diagnosed, touches one or two files,
and doesn't need investigate-then-plan staging. Applying Shape A's full
ceremony here is itself a miscalibration — see the proportionality
principle.

    ---
    repo: <repo-name>
    date: <YYYY-MM-DD>
    purpose: <one line>
    ---

Sections: what's already known · what to check before implementing, if
anything's genuinely unresolved · the fix · how to verify it, specifically
via a fresh terminal run, not an in-session one.

## Shape C — bounded task packet (live, paid, production, or hard boundaries)

Use when: the task touches a live or paid external system, production
infrastructure, or needs explicit do-not-touch boundaries stated up front
rather than negotiated mid-task. This is not a continuity document like A
and B — it's a scope-and-permission contract, filled in *before* an agent
starts, not written up after the fact. Bounded spec = bounded risk.

Fill every section. Write "none" explicitly rather than omitting a section —
an omitted section reads as forgotten; a stated "none" reads as checked.

    ---
    repo: <repo-name>
    date: <YYYY-MM-DD>
    purpose: <one line>
    touches: live | paid | production | hard-boundaries
    ---

    # Task Packet: [Task Title]

    ## Goal
    <!-- One sentence. The outcome, not the method. -->
    [Concrete outcome when the task is complete.]

    ## External boundaries
    <!-- APIs, MCP tools, webhooks, CLIs, DB views, model outputs this task touches. -->
    - [boundary name] — [what it does in this task]
    <!-- "none" if absent -->

    ## Assumptions to validate
    - [assumption]
    <!-- "none" if absent -->

    ## Files to inspect first
    <!-- The minimum reads needed to understand context. Ordered. -->
    1. `[path/to/primary/doc]` — [why]
    2. `[path/to/key/file]` — [why]

    ## Source-of-truth docs
    <!-- What to treat as authoritative, in this stack's actual precedence order:
         running artifact > installed package/lockfile > docs filtered to the
         installed version > prose > memory. Don't cite a source you haven't
         confirmed exists in this repo. -->
    - `.claude/hooks/HOOKS-REF.md` — vendored Hooks reference, pinned to the CC
      version in FIELD-NOTES; re-check the pinned version before citing a line.
    - `.venv/lib/python*/site-packages/<pkg>/` or `uv pip show <pkg>` — installed
      package behaviour, not the package's public docs site.
    - `.claude/hooks/FIELD-NOTES.md` — prior verified claims about this repo's
      runtime behaviour; don't re-derive something already measured there.
    - `docs/plans/` — prior decisions this task might build on or conflict with.
    - [any other source] — confirm it exists here before listing it; an inherited
      reference to a doc this repo doesn't have is the same mistake as an
      invented environment variable.

    ## Evidence to gather first
    <!-- Fixtures, probes, or captured responses needed BEFORE implementation
         starts — distinct from "Evidence of completion" below, which proves the
         work afterward. Don't conflate the two. -->
    - [what to capture] — [where to save it]
    <!-- "none" if absent -->

    ## Live probes before starting
    <!-- Read-only curl/API/CLI calls against a live or paid system, run to
         gather the evidence above. Needs explicit approval before running. -->
    - [ ] `[command]` — [why this counts as live/paid, and that it's read-only]
    <!-- "none" if absent -->

    ## Out of scope
    - Do not modify [X]
    - Do not touch [Y]
    - Do not start [Z] until [precondition]

    ## Allowed commands
    <!-- Explicit list prevents unwanted side effects. This stack's usual set: -->
    ```bash
    uv run --no-sync pytest -q
    uv run --no-sync python .claude/hooks/check_invariants.py --standalone
    uv run --no-sync ruff check .
    ```

    ## Do not touch
    <!-- Files or systems that must not be modified, beyond "Out of scope" above —
         this is for things where touching them is actively dangerous, not just
         off-topic. -->
    - `[critical file or system]`

    ## Acceptance criteria
    <!-- Verifiable, not aspirational. Checkable without running the system. -->
    - [ ] [Specific observable outcome 1]
    - [ ] [Specific observable outcome 2]

    ## Evidence of completion
    <!-- What proves each acceptance criterion above is actually met — distinct
         from "Evidence to gather first". -->
    - File diff showing [specific change]
    - `[command]` output showing [expected result]
    - Test output: `[test name]` passed

    ## Mutating actions requiring approval
    <!-- Writes, deploys, publishes — anything with a real-world side effect,
         run during or after implementation. Distinct from the read-only probes
         above. Never exercised without an explicit go-ahead in the moment, even
         if listed here in advance. -->
    - [ ] `[command]` — [why it needs approval, what it would actually do]
    - [ ] Deploy to [environment]
    <!-- "none" if absent -->

    ## Output format
    Report:
    - Files changed: [list]
    - Tests run: [pass/fail/skipped]
    - Evidence: [paths, matching "Evidence of completion" above]
    - Blockers: [any unresolved issues]

## All three shapes, always

- Self-contained: a fresh claude.ai session or fresh local session should be
  able to work from the file alone.
- Point at canonical docs/files rather than restating their contents — and for
  Shape C specifically, confirm a cited source actually exists in *this* repo
  before listing it; don't inherit a reference from a different stack.
- Close the loop explicitly when done — a one-line "Closed <date>" note at
  the top, not a silent deletion or an assumption someone remembers.
- **Tick checkboxes the moment the thing they describe becomes true, not at
  session end.** A step being done and a step being marked done are different
  states — same principle as "a recommendation is not a change," applied to
  acceptance criteria instead of backlog text. An end-of-session sweep is fine
  as an *audit* for anything genuinely missed; it is not the primary
  mechanism, and reconstructing ticks from memory at the end of a session is
  a small version of the exact backfill problem this file's own history
  already warned against.
- For Shape C: approval gates are stated before the task starts, not
  negotiated mid-task. If something not listed under a "requires approval"
  section turns out to need one, stop and ask — don't expand the packet's own
  authorization retroactively.

## Routing — which shape

- A verified, citable claim about platform or repo behaviour → FIELD-NOTES,
  not any of these three.
- Something small, real, non-blocking → one line in `docs/BACKLOG.md`.
- A real decision, investigation, or multi-session thread → Shape A.
- A small, fully-diagnosed, low-blast-radius fix → Shape B.
- Touches a live, paid, or production system, or needs explicit do-not-touch
  boundaries → Shape C, filled in before the agent starts.