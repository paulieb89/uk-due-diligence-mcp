# Handoff / kickoff template

Two shapes. Pick based on stakes, not habit.

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

## Both shapes, always
- Self-contained: a fresh claude.ai session or fresh local session should be
  able to work from the file alone.
- Point at canonical docs/files rather than restating their contents.
- Close the loop explicitly when done — a one-line "Closed <date>" note at
  the top, not a silent deletion or an assumption someone remembers.
