---
paths:
  - ".claude/hooks/**"
  - ".claude/settings*.json"
---

# Hook authoring

Loads only when editing hooks or settings. Each line is a directive; the measurements
behind it are in `.claude/hooks/FIELD-NOTES.md`, cited by heading.

- **Exit 2 is the only code that blocks.** Exit 1 is a non-blocking error — the action
  proceeds anyway. (FIELD-NOTES *§2 Exit code 2 — what it does per event*, *§10 Exit-2
  delivery*)
- **Exit-0 stderr reaches nobody.** It is absent from the debug log; recoverable only via
  `--output-format stream-json --include-hook-events`. A warning printed there is not a
  warning delivered. (*§1 stderr from a hook that exits 0*)
- **`additionalContext` is the inform-without-blocking channel.** (*§8 Both delivery
  channels*)
- **Anchor every path to `$CLAUDE_PROJECT_DIR`**, read from the environment — never
  inferred from cwd. Hooks run in the current directory, which is not the project root.
  (*§7 Hooks run in the current directory*)
- **`if` is only evaluated on tool events**: PreToolUse, PostToolUse, PostToolUseFailure,
  PermissionRequest, PermissionDenied. A handler with `if` set never runs on any other
  event, so adding one out of habit silently disables the hook.
  (HOOKS-REF § *Command hook fields*)
- **A JSON snippet for a settings file that already exists is a _merge_ instruction.**
  Applying it literally deletes every hook not named in it — silently, with no error.
  Assert every pre-existing handler survives any settings edit before moving on.

Deliberately not repeated here: *deny reasons are agent-facing, ask/allow are
human-facing* and *a guard's acceptance criteria live with the guard*. Both live in global
`~/.claude/CLAUDE.md`, which loads in all twelve fleet repos; narrowing them to this rule
would remove them from the other eleven.
