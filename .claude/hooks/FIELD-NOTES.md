# Hooks field notes

Observed behaviour of the Claude Code hook runtime, recorded where the hooks
live. Each entry states what we believed, what is actually true, and the
evidence — so a future reader re-checks rather than re-learns.

Sources cited with access date. The runtime is the authority; docs second.

**Reconciled 2026-09-08** against the full Hooks reference saved as `HOOKS-REF.md`
in this directory, on Claude Code **2.1.263** (`claude --version`). The earlier
entries were written from a model-summarised fetch of the same page; three claims
did not survive reading the page itself. Corrections are inline and marked.

**Standard for this file, set 2026-09-08.** §1's retraction is the model:

1. Every behavioural claim carries the **version** it was observed on and the
   **date**. A behaviour without a version is not a fact, it is an anecdote — all
   three falsified claims were version-gated, and both fixes had already shipped
   before the observation was written down.
2. Corrections are written **as corrections**. Struck text stays, marked
   RETRACTED or CORRECTED with what replaced it. Silently editing a wrong note
   destroys the only thing this file is for: the record of what was believed and
   why it was wrong.

---

## 1. stderr from a hook that exits 0 never reaches the model

**Believed:** printing to stderr surfaces a warning to Claude even when the hook
does not block, so a hook could "warn without blocking".

**True:** it does not. Exit-0 stderr goes to the debug log only.

> "Stderr from a hook that exits 0 goes to the debug log only, never the
> transcript, and Claude never sees it."
> — Claude Code Hooks reference, <https://code.claude.com/docs/en/hooks>,
> accessed 2026-09-07.

**Transcript-level evidence** (parallel session, 2026-09-07): `hook_success`
attachments carry the stderr text verbatim in the internal record, but the
`content` delivered to the model is `""` at exit 0. The text exists; it is
simply never handed over. This is why the failure is invisible — the hook looks
like it worked, and the string is right there in the log.

**Live instance in this repo:** `pre_bash_deploy.py` prints its reason to stderr
with the comment "stderr puts it in the transcript either way", then returns 0.
That premise is false; the stderr line is not delivered to anyone. Commit
`47c81a4` ("surface the deploy warning on stderr too") was built on it.

### ~~Corollary: warn-without-blocking is unimplementable~~ — RETRACTED 2026-09-08

The earlier draft argued that under an auto-granting mode nobody reads an `"ask"`
reason and exit-0 stderr reaches no one, so a PreToolUse hook could only block or
stay silent. **Both halves of that are wrong on 2.1.263.**

1. `"ask"` is not silently granted. HOOKS-REF.md:1755 —

   > "A hook's `"ask"` also forces a permission prompt in auto mode: the
   > classifier can still deny the tool call, but it can't approve the call
   > silently. Before v2.1.211, the classifier could approve a Bash command
   > running outside the sandbox without showing the prompt the hook requested."

   The observation the retraction was built on describes pre-v2.1.211 behaviour.
   We run 2.1.263, so a hook `"ask"` **does** gate.

2. Warn-without-blocking has a supported implementation: `"async": true` with
   `"asyncRewake": true`. Per HOOKS-REF.md:459, such a hook "runs in the
   background and wakes Claude on exit code 2. The hook's stderr, or stdout if
   stderr is empty, is shown to Claude as a system reminder". The tool call is
   not blocked, because an async hook cannot block — and Claude still sees the
   text.

The narrow claim survives intact: **exit-0 stderr reaches nobody.** Everything
inferred beyond it did not. The lesson is the one this file exists for — a
version-gated behaviour observed once is not a permanent property.

---

## 2. Exit code 2 — what it does per event

> `PreToolUse` | Yes | "Blocks the tool call"
> `PostToolUse` | No | "Shows stderr to Claude; the tool already ran"
> `PostToolUseFailure` | No | "Shows stderr to Claude; the tool already failed"
> — HOOKS-REF.md:854, 865, 866.

`PostToolUseFailure` was missing from the first draft; it carries the same exit-2
semantics as `PostToolUse`. Also worth knowing: a hook that exits 2 while printing
JSON that fails schema validation **still blocks** — stderr becomes the blocking
reason and the validation failure goes to the debug log (HOOKS-REF.md:802). Before
v2.1.214 that combination was treated as non-blocking; we are past that.

So exit 2 is the *only* reliable channel from a hook to the model, and it works
in PostToolUse too — where it cannot prevent anything, but the model does see
the text. `check_invariants.py` and `post_edit_python.py` both rely on this and
are correct as written.

---

## 3. Permission-decision JSON (PreToolUse)

```json
{"hookSpecificOutput": {
   "hookEventName": "PreToolUse",
   "permissionDecision": "allow" | "deny" | "ask",
   "permissionDecisionReason": "shown to the user when blocking"}}
```

`"ask"` defers to the normal permission flow. **Corrected 2026-09-08:** the first
draft said an auto-granting mode satisfies this without displaying the reason.
It does not — on 2.1.263 a hook's `"ask"` forces the prompt (HOOKS-REF.md:1755).
`"ask"` is a real gate, not a warning that evaporates.

---

## 4. Filed, not yet done: pre_bash_deploy becomes an acknowledge gate

Design decided 2026-09-07; implementation is a separate task.

- Switch `permissionDecision` from `"ask"` to `"deny"`, and have the reason name
  `FLY_DEPLOY_ACK=1` as the explicit escape. The hook allows the command when
  that variable is set. **Justification revised 2026-09-08:** this was framed as
  converting an undeliverable warning into a real gate. That was wrong — the
  existing `"ask"` already gates on 2.1.263 (§1, §3). `deny` + ACK is therefore a
  deliberate *tightening*, not a bug fix: it turns a prompt a human can click
  through into an escape a human has to type. Still defensible for an
  irreversible prod action, but it is now a policy choice and should be decided
  as one, not adopted because the current hook is broken. It is not.

  **Doctrine (decided 2026-09-08):** *deny + typed escape for irreversible
  externalities (PyPI publish, prod deploy); `"ask"` is legitimate and sufficient
  for reversible actions.* Decided on merits, not on the retracted premise:
  click-through fatigue is real; the `"ask"` prompt renders identically for a
  trivial action and an irreversible one, so the interface carries no signal
  about which is which; and a typed `FLY_DEPLOY_ACK=1` leaves an audit trail in
  shell history that a clicked prompt does not.
- Anchor the matcher to command position. Today it is
  `re.compile(r"\bfly(?:ctl)?\s+deploy\b")` — unanchored, so it matches the
  string anywhere, including inside an `echo`, a heredoc, a commit message or a
  file being written. Target: `^\s*(fly|flyctl)\s+deploy`, also matching after
  `&&`, `;` and `|`.
- **Measured 2026-09-07: 4 firings on inert strings, 0 on real deploys.** A guard
  with no true positives and a 100% false-positive rate is not yet an
  instrument. Fix the matcher before trusting the gate.

### Spec detail (added 2026-09-07)

The escape must be honoured in **both** places it can legitimately live:

1. as an env-prefix inside the command string — `FLY_DEPLOY_ACK=1 fly deploy`;
2. in the hook's own environment — `os.environ`, i.e. exported by the caller.

Only reading `os.environ` misses form 1 entirely, because a `VAR=val cmd` prefix
is part of the command text the hook is handed, not of the hook's environment.
Only parsing the command string misses form 2. Both are how a person actually
types this, so both allow.

Consequently the anchored matcher must still match a deploy preceded by
`VAR=val` assignments — anchoring to `^\s*(fly|flyctl)` alone would stop matching
the very form the escape hatch uses, and the gate would silently stop firing.

**Enumerate before hand-rolling this regex.** The platform ships a filter that
already does most of it: the per-handler `if` field, which takes permission-rule
syntax and is evaluated on `PreToolUse` (HOOKS-REF.md:427). Its documented Bash
matching (HOOKS-REF.md:438) states that **leading `VAR=value` assignments are
stripped before matching** — the exact requirement above — and that **each
subcommand is checked**, so `npm test && git push` matches `Bash(git *)`. With
`"if": "Bash(fly deploy *)"`, the measured false positive `echo "run fly deploy
to ship"` does not match any subcommand, so the hook never spawns.

Two caveats that keep the in-hook check necessary:

- The docs are explicit that this is best-effort: *"When Claude Code can't
  determine which commands the Bash input runs, it runs your hook regardless of
  the pattern. Because the `if` filter is best-effort, use the permission system
  rather than a hook to enforce a hard allow or deny."*
- A pattern that specifies more than the command name runs the hook anyway on
  `$()`, backticks, or `$VAR` — so `Bash(fly deploy *)` still spawns on commands
  containing a substitution.

So `if` is a spawn filter that removes most of the noise for free; the hook keeps
its own matcher as the actual decision. Write the regex for what `if` lets
through, not from scratch.

Fail-on-purpose cases, all four required before the gate is trusted:

| case | expected |
|---|---|
| `FLY_DEPLOY_ACK=1 fly deploy` (env-prefix form) | allow |
| `fly deploy` with `FLY_DEPLOY_ACK=1` exported | allow |
| `git push && fly deploy` | **block** |
| `echo "run fly deploy to ship"` | silent — no fire |

The last two are the ones that matter: the third is the real deploy the current
matcher has never caught, the fourth is the inert string it fires on four times
out of four.

### Order of work

1. **Read `HOOKS-REF.md` end to end first.** Three enumerate-before-you-build
   misses in two days (the hook itself, the release gate, this matcher) all came
   from using a fraction of the surface. Read before writing, not after.
2. `"if": "Bash(fly deploy *)"` as the **spawn filter**; the in-hook regex as the
   **decision layer** for what best-effort lets through. Two layers, different
   jobs — the docs are explicit that `if` is not an enforcement boundary.
3. Both escape forms honoured (command-string env-prefix, and the hook's own
   environment).
4. Run the four-case table **in the real harness, with the working directory
   varied** — §7's lesson. A hook verified only from the repo root is verified
   for one cwd out of many.

---

## 5. Filed for the uk-legal harvest

`uk-legal-mcp`'s `post_edit_check.py` is built on the premise "stderr so Claude
sees it, exit 0 always". Per §1 and §2 that is falsified in both halves: exit-0
stderr reaches nobody, and PostToolUse exit 2 *does* show stderr to the model
even though the tool already ran.

**Two sanctioned shapes; choose at harvest time.**

| shape | mechanism | when |
|---|---|---|
| exit 2 | stderr goes to Claude (HOOKS-REF.md:865) | a failure Claude must act on |
| exit 0 + `hookSpecificOutput.additionalContext` | "String added to Claude's context alongside the tool result" (HOOKS-REF.md:1969) | softer framing, informational |

A third exists if needed: top-level `decision: "block"` with `reason`, which
"adds the `reason` next to the tool result" while Claude still sees the original
output (HOOKS-REF.md:1967).

Caveat on the softer shape, worth knowing before choosing it: `additionalContext`
must read as **factual statements, not imperative instructions** — the docs warn
that text framed as out-of-band system commands "can trigger Claude's
prompt-injection defenses, which causes Claude to surface the text to you instead
of treating it as context" (HOOKS-REF.md:1005). A lint message phrased as an order
can therefore be delivered as a warning *about* the hook rather than as context.
Also: on `--continue`/`--resume`, PostToolUse `additionalContext` is replayed from
the transcript rather than re-run, so embedded timestamps or SHAs go stale.

---

## 6. Release gate: first fire, real environment

**2026-09-07.** The `--expect <tag>` anchor in `release.yml` had been verified
only locally; the `${{ github.event.release.tag_name }}` wiring had never run
where it lives. Exercised deliberately.

**Method — arrange the experiment so the thing under test prevents the damage.**
Cut `v1.3.1-rc1` as a pre-release with `pyproject.toml` deliberately left at
1.3.0, so the gate *must* reject it. This mattered: `release.yml` subscribes to
`release: types: [published]`, which fires for pre-releases too, and the `pypi`
environment has no protection rules — so a *passing* gate would have chained
straight to a PyPI upload and a prod deploy. The rejection is what made the
rehearsal safe, not caution.

**Result — run 34164749257, job `Publish to PyPI`, failed at step 4:**

```
Run uv run --no-project --python 3.12 python .claude/hooks/check_invariants.py --standalone --expect "v1.3.1-rc1"

Repo invariant violation:

  - RELEASE TAG MISMATCH — tag 'v1.3.1-rc1' normalises to 1.3.1-rc1, but pyproject.toml declares 1.3.0.
    The version triad is self-consistent and anchored to the wrong version. Bump the three files, or retag.

Fix before continuing. These are enforced, not advisory.
##[error]Process completed with exit code 2.
```

| step | outcome |
|---|---|
| 4. Repo invariants and release tag | **failure** |
| 5. `uv build` | skipped |
| 6. `pypa/gh-action-pypi-publish` | skipped |
| job `Deploy to Fly` | **skipped** via `needs: publish` |

**Proven where it matters:** the `tag_name` expression resolves and reaches the
script — the tag appears verbatim in the message, which also distinguishes a real
resolution from an empty expression, since an empty tag would fail too but say
so differently; exit 2 propagates out through `uv run` to the runner; and a
failed gate actually blocks publish *and* deploy rather than merely reporting.

**Clean afterwards:** PyPI has no 1.3.1rc1 (latest remains 1.3.0), release and
tag deleted, prod still `8dd311f (via release), matching v1.3.0`.

**Residual, accepted:** this exercises the *reject* path. The accept path
(`--expect v1.3.0` → exit 0) stays verified only locally and first runs in CI on
the next real release. That is the right way round — "does it block" is the
property worth proving.


---

## 7. Hooks run in the current directory — anchor every path

**2026-09-08, found by this reconciliation pass wedging itself.**

`settings.json` invoked the hooks with a relative script path
(`python .claude/hooks/check_invariants.py`). HOOKS-REF.md:416: *"Handlers run in
the current directory with Claude Code's environment."* A `cd` into
`.claude/hooks` during the session therefore resolved the path to
`.claude/hooks/.claude/hooks/...`, and the PreToolUse hook failed on **every Bash
call** — the tool was completely unusable until the config was fixed through
`Edit`, which routes through a different matcher.

Two things worth keeping:

- **A broken PreToolUse hook is not a degraded session, it is a stopped one.** The
  failure blocked the tool rather than being skipped. Recovery had to come from a
  tool the broken matcher did not cover.
- **The relative path had been wrong since the hooks were written.** It survived
  because nothing had changed the working directory before. A latent config bug
  in a guard is invisible until the day it isn't.

Fixed by anchoring both the script and uv's project resolution:

```
uv run --no-sync --project "$CLAUDE_PROJECT_DIR" python "$CLAUDE_PROJECT_DIR/.claude/hooks/<name>.py"
```

Verified by running all three hooks with the working directory set to
`.claude/hooks` — the condition that broke them — all exit 0. The new config took
effect without restarting the session.

Note the JSON: `$CLAUDE_PROJECT_DIR` must be written with escaped quotes
(`\"$CLAUDE_PROJECT_DIR\"`) inside a settings string. An unescaped quote produced
`Invalid JSON: Expected '}'` and the edit was refused — the settings validator
caught it before anything was written, which is the one guard in this whole
exercise that worked first time without being asked.

### Fleet sweep, not yet run

This is a bug in a *pattern*, so it is latent in every copy of the pattern.
Harvest task: grep every fleet repo's `.claude/settings*.json` for hook commands
with a relative script path, and anchor each to `$CLAUDE_PROJECT_DIR`. Any repo
whose hooks have only ever run from its own root is carrying the same defect,
undetected for the same reason this one was.
