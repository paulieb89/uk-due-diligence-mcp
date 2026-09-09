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

**Where new information goes — provisional placement, 2026-09-08.** This is a
note about the `.claude/` directory's own conventions, not an observation about
the runtime, so by its own routing rule it does not belong in an evidence log.
It sits here because the directory-level doc that should hold it does not exist
yet — checked 2026-09-08, `.claude/` contains `commands/`, `hooks/` and
`settings*.json`, and no README or conventions file. Move this block there when
that doc lands; it is a placeholder with a forwarding address, not a resident.

> A verified, citable claim about platform or repo behaviour → **FIELD-NOTES**
> (evidence required — a citation, a measurement, or both). Something small,
> real and non-blocking → one line in **`docs/BACKLOG.md`**, triaged into a plan
> when picked up. Anything touching multiple files with a genuine design
> tradeoff → a plan in **`docs/plans/`**, using `docs/plans/TEMPLATE-handoff.md`.
> A FIELD-NOTES entry that has stabilised into a permanent constraint, rather
> than a one-off discovery → **promote it into something that actually runs** —
> a hook, a `check_invariants.py` check, a committed test — rather than leaving
> it as prose someone has to remember to reread.

The last clause is the one with no mechanism behind it: nothing currently
detects a note that has earned promotion, so it stays a habit until something
enforces it. Filed in `docs/BACKLOG.md` (2026-09-08) as this file's open
location-and-maturity question.

---

## 1. stderr from a hook that exits 0 never reaches the model

**Believed:** printing to stderr surfaces a warning to Claude even when the hook
does not block, so a hook could "warn without blocking".

**True:** it does not. ~~Exit-0 stderr goes to the debug log only.~~ The
narrow claim survives; the "debug log only" half does not — see AMENDED below.

> "Stderr from a hook that exits 0 goes to the debug log only, never the
> transcript, and Claude never sees it."
> — Claude Code Hooks reference, <https://code.claude.com/docs/en/hooks>,
> accessed 2026-09-07.

### AMENDED 2026-09-08 — where exit-0 stderr actually goes

Ratified amendment, pasted verbatim:

> Exit-0 stderr is not a model-facing channel. Claude never sees it, and the
> transcript never shows it. Contrary to the Hooks reference, it does not appear
> in the debug log — verified absent at both default and
> `CLAUDE_CODE_DEBUG_LOG_LEVEL=verbose` on Claude Code 2.1.263, Linux. It is
> recoverable only from `--output-format stream-json --include-hook-events`,
> where `hook_response` exposes `stdout`, `stderr`, and `output` as separate
> fields. Docs-confirmed ≠ verified; this one was docs-confirmed and observed
> false.

This is the second claim in this section sourced from the reference page and
found false against the runtime. The first was the corollary retracted below.
Note what that costs the citation above: it is still what the docs say, and it
is still wrong in its second clause. A quote is evidence of the documentation,
never of the behaviour.

The `hook_response` recovery path is independently corroborated here — §10, run
D — on the same build.

**Transcript-level evidence** (parallel session, 2026-09-07): `hook_success`
attachments carry the stderr text verbatim in the internal record, but the
`content` delivered to the model is `""` at exit 0. The text exists; it is
simply never handed over. This is why the failure is invisible — the hook looks
like it worked, and the string is right there in the log.

**Live instance in this repo:** `pre_bash_deploy.py` prints its reason to stderr
with the comment "stderr puts it in the transcript either way", then returns 0.
That premise is false; the stderr line is not delivered to anyone. Commit
`47c81a4` ("surface the deploy warning on stderr too") was built on it.

**Fixed 2026-09-08.** Those five lines are gone; the decision JSON is now the
whole message. See §4 and §8.

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

~~So exit 2 is the *only* reliable channel from a hook to the model~~ —
**CORRECTED 2026-09-08.** Exit 2 works, and it works in PostToolUse too, where
it cannot prevent anything but the model does see the text. It is not the only
channel: `hookSpecificOutput.additionalContext` at exit 0 also reaches the model,
now measured rather than assumed (§8). The narrow surviving claim is about
*stderr* at exit 0 reaching nobody — not about exit 2 being the sole route.

`check_invariants.py` still relies on exit 2 and is correct as written.
`post_edit_python.py` now uses both: exit 2 for failures, `additionalContext`
for the success and fail-open paths.

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

## 4. DONE 2026-09-08: pre_bash_deploy is an acknowledge gate

Design decided 2026-09-07, implemented 2026-09-08. The spec below is kept as
written, with the two places implementation contradicted it marked inline.

**One correction the design missed entirely.** The spec said the hook *allows*
the command when `FLY_DEPLOY_ACK=1` is set. That is unsafe, and so is the
obvious repair. `"allow"` skips the permission prompt (HOOKS-REF.md:1744) — and
because a `"deny"` reason is shown to **Claude** (:1745), an agent that reads the
refusal can set the variable itself and deploy with no human involved. Falling
back to plain pass-through (exit 0, no JSON) does not fix it either: in auto mode
the classifier approves Bash calls silently, which is what auto mode is for.

The ACK path therefore returns **`"ask"`** — the only decision documented to
force a prompt even in auto mode (:1755) — and its reason is shown to the user,
which is the right audience for a confirmation. Picking a decision channel is
picking an audience: `deny` reasons are agent-facing, `ask`/`allow` reasons are
human-facing. A gate whose whole point is a human in the loop has to keep its
guarantee in the channel a human actually reads.

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
- ~~**Measured 2026-09-07: 4 firings on inert strings, 0 on real deploys.** A
  guard with no true positives and a 100% false-positive rate is not yet an
  instrument.~~ — **CORRECTED 2026-09-08.** The firing counts are right; the
  diagnosis misread them. Re-measured against the live hook by piping synthetic
  payloads: it returned `ask` for `fly deploy`, `git push && fly deploy` and
  `flyctl deploy --ha=false` as well as for `echo "run fly deploy to ship"` and
  `git commit -m "note: fly deploy is manual"`. **Recall was never the defect —
  precision was.** "0 true positives" recorded that nobody hand-deployed in that
  window, not that the matcher missed real deploys. A guard can be both correct
  and untrustworthy; this one was.

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

**Amended 2026-09-08, two ways.** First, `if` holds exactly one permission rule —
there is no `&&`, `||` or list syntax (HOOKS-REF.md:432) — so `fly` and `flyctl`
need **two handler entries**, not one pattern. Second, `Bash(fly deploy *)` is
the wrong half of the trade. The same table's last row: a pattern specifying more
than the command name "runs the hook anyway on `$()`, backticks, or `$VAR`".
`Bash(fly deploy *)` is such a pattern, so it spawns on essentially every command
containing a shell variable; `Bash(fly *)` is a command-name pattern and does not.
The more specific pattern is the noisier one. Shipped as
`Bash(fly *)` + `Bash(flyctl *)`; `fly logs` and `fly status` spawn and exit
silently, which is cheap.

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

Fail-on-purpose cases. **Amended 2026-09-08** — the two ACK rows read `allow`
as filed, which is precisely the defect the correction above exists to prevent.
A table is the half of a spec that actually gets executed, so leaving it would
have certified the bug:

| case | expected |
|---|---|
| `FLY_DEPLOY_ACK=1 fly deploy` (env-prefix form) | ~~allow~~ **`ask`** |
| `fly deploy` with `FLY_DEPLOY_ACK=1` exported | ~~allow~~ **`ask`** |
| `git push && fly deploy` | **`deny`** |
| `echo "run fly deploy to ship"` | silent — no fire |

The ACK rows must assert `ask` exactly, not "not deny" — a hook returning
`"allow"` passes the weaker assertion while carrying the whole defect.

**Now a standing assertion, not a one-off.** `tests/test_pre_bash_deploy.py`
runs these plus `;`-separated and leading-whitespace forms, `fly logs`/`fly
status`, a non-Bash tool and an unparseable payload — 15 cases. Written before
the rewrite, so its first run caught the real pre-existing defect (`assert 'ask'
== 'deny'`) rather than a staged one.

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

## 5. RESOLVED 2026-09-08: two shapes, both now in use

Marked RESOLVED, not CORRECTED — nothing in this section was false. "Choose at
harvest time" was a deliberate deferral, and the choice has now been made:
**exit 2 for failures, `additionalContext` for informational output.**
`post_edit_python.py` implements both as of 2026-09-08 (§8). The uk-legal
harvest this was filed for is deferred; the decision is not.


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
of treating it as context" (HOOKS-REF.md:1003 — **corrected 2026-09-08**
from :1005, which is the replay note two lines further down). A lint message phrased as an order
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

### Fleet sweep — RUN 2026-09-08

Scope: every fleet repo's `.claude/settings*.json`, every `SKILL.md` and command
frontmatter, and the global `~/.claude/settings.json` (which has no `hooks` key
at all). Only **two** repos carry hooks. This one is anchored; `uk-legal-mcp`'s
`scope` skill already uses `${CLAUDE_PROJECT_DIR:-.}`.

Two files remain, both in `uk-legal-mcp`, both **deferred** with that repo:

- `settings.json` — relative `python3 .claude/hooks/…` on both handlers. The
  §7 defect exactly.
- `settings.example.json` — worse, and in the opposite direction: it invents
  `$PROJECT_ROOT`, which appears **zero** times in the Hooks reference against
  `CLAUDE_PROJECT_DIR`'s 19, so it expands to empty. It also invokes the `.py`
  with no interpreter, and the scripts carry no exec bit. Guessing a variable
  name is the same failure as hard-coding a path: neither enumerated what the
  platform actually ships.

The sweep was smaller than filed. That is worth recording too — "latent in every
copy of the pattern" assumed more copies than exist.

---

## 8. Both delivery channels, measured on 2.1.263

**2026-09-08.** §1 established the dead exit-0 stderr channel from a parallel
session and reasoned the rest by analogy. Analogy is what this file exists to
replace, so all three cases were run here, on `post_edit_python.py`, in one
session.

**Method.** Write a throwaway `_hookprobe.py` at repo root *via the Write tool* —
a Bash heredoc cannot fire this hook, whose matcher is `Write|Edit|MultiEdit`,
which is the same gap that lets a `sed` bypass `check_invariants.py` — then read
the session transcript for the `PostToolUse` attachment.

**Case 1 — the old shape, exit 0 with stderr. Dead.**

```
.attachment.hookName = "PostToolUse:Write"
.attachment.exitCode = 0
.attachment.stderr   = "ruff clean, 137 passed in 17.38s\n"   <- text exists
.attachment.stdout   = ""
.attachment.content  = ""                                      <- delivered
```

This is what distinguishes *dropped* from *never fired*, which silence alone
cannot: ruff and 137 tests demonstrably ran, the summary was captured verbatim,
and the model was handed `""`.

**Case 2 — exit 2. Delivered.** Same file, `import os` left unused so ruff fails.
The full diagnostic arrived immediately as a blocking error. It also returned the
`file_path` **absolute**, which incidentally settles a question for the deferred
uk-legal harvest: that repo's `str(p).startswith("src/")` test gate can never be
true.

**Case 3 — exit 0 with `additionalContext`. Delivered.**

```
.attachment.hookName = "PostToolUse:Write"
.attachment.content  = ["ruff reports no issues and the test suite passes: 152 passed."]
```

Note the shape differs: `content` is a list, and `exitCode`/`stderr` are absent
on the JSON path rather than empty. Same probe, same session as case 1 — a clean
before-and-after rather than two readings taken apart.

**Verdict.** `additionalContext` works and needs no further proof. §2's "exit 2
is the only reliable channel" is corrected there.

§10 extends this to the exit-2 path on live traffic and, more usefully, bounds
the instrument: this section read the *session transcript*, which is not the
same artifact as a `-p` stream-json stream, and neither one is authoritative for
a channel it was not asked to emit.

### The pattern behind both bugs

Two independent files each asserted, in their own prose, that exit-0 stderr
reaches Claude:

- `pre_bash_deploy.py` — "stderr puts it in the transcript either way" (§1).
- `uk-legal-mcp`'s `post_edit_check.py` — "Errors are printed to stderr so Claude
  Code sees them as context."

Neither author read it from the other. A wrong belief that appears independently
in two repos is not two bugs, it is a **fleet-level default** — the shape a hook
takes when written from intuition about how stderr behaves in a terminal, where
it *is* the channel you see. The next hook written from memory will carry it too.

That is also why the failure is so durable: the belief is unfalsifiable from the
outside. A hook whose message reaches nobody looks exactly like a hook with
nothing to say.

## 9. `deny` and `ask` are not equally verifiable

**2026-09-08.** §4 shipped the acknowledge gate and `tests/test_pre_bash_deploy.py`
asserts its logic (15 cases, green). Those tests call `_classify()` and `main()`
directly, so they prove the *decision*, not the *dispatch* — whether
`settings.json`'s `if: "Bash(fly *)"` spawn filter actually routes a real Bash
call to the hook. Presence in the config file is not load.

**The deny path was live-fired end to end.** Command: `fly deploy --help` — a
string the matcher classifies as a deploy (`rest[0] in DEPLOYERS` and `"deploy"
in rest[1:]`) but which, if the hook were *not* wired, merely prints help. That
asymmetry is the whole trick: the probe is deploy-shaped to the gate and inert to
Fly, so a dispatch failure degrades to a no-op instead of shipping prod.

```
$ fly deploy --help
<error> Hand deploys bypass the release pipeline (PyPI publish + CI provenance).
        See CLAUDE.md > Releasing. Deliberate rollback: re-run with FLY_DEPLOY_ACK=1.
```

Verdict: config loads, the spawn filter dispatches, the matcher anchors, and the
deny reason reaches **Claude** — confirming `HOOKS-REF.md:1745` on live traffic,
not by reading. §4's design note can now cite a measurement.

That delivery is also the standing argument for §4's one design change. The agent
receives the text naming `FLY_DEPLOY_ACK=1`; it therefore *holds* the bypass. An
`"allow"` on the ack path would let it exercise what it was just handed, with no
person in the loop. `"ask"` is what keeps the second step in a human channel.

**The ask path cannot be closed the same way, and this is structural.** Tripping
a `deny` is free — the guarded action never runs, so the probe costs nothing.
Approving an `ask` *is* the guarded action. There is no dry-run of a "yes", and
no inert probe exists: any command that reaches the prompt is by construction one
that deploys if confirmed. So the chain splits:

| Link | deny path | ask path |
|---|---|---|
| decision JSON at the hook boundary | verified (tests + piped payload) | verified (piped payload) |
| settings dispatch / spawn filter | verified (live fire above) | shared with deny — verified |
| reason rendered to its audience | verified (agent read it) | **unverified by construction** |

Only the last cell is open, and it is the user-facing render. Staging it would
require deploying prod to find out whether the confirmation text was legible —
paying the exact cost the gate exists to avoid. This is the case the global
prove-it-fires rule carves out for passive closure: **the next time a deliberate
hand deploy happens, read the prompt before answering it and record whether the
reason arrived intact.** Until then the correct claim is "the ask decision is
emitted and dispatched", not "the ask prompt works".

Do not resolve this by deploying to test it. Prod was healthy at `v1.3.0` when
this was written and a hand deploy would have shipped 15 unreleased commits — the
drift the gate exists to prevent. The gap is cheap to hold and expensive to close.

---

## 10. Exit-2 delivery, and what stream-json actually proves

**2026-09-08, Claude Code 2.1.263, Linux.** Four nested `claude -p` runs against
this repo's live `post_edit_python.py`. Probe: Write `_hookprobe.py` containing
`import os`, which ruff fails F401, so the hook takes its exit-2 branch. Write
rather than a heredoc, because the matcher is `Write|Edit|MultiEdit`.

### Exit 2 delivers, measured three times

The model received the diagnostic in all three hooks-enabled runs and quoted it
back. The delivery envelope, verbatim:

```
PostToolUse:Write hook blocking error from command: "uv run --no-sync --project
"$CLAUDE_PROJECT_DIR" python "$CLAUDE_PROJECT_DIR/.claude/hooks/post_edit_python.py"":
[...]:
ruff failed on <repo-root>/_hookprobe.py:

F401 [*] `os` imported but unused
 --> _hookprobe.py:1:8
```

Note the envelope names the hook and its full command line. §2's exit-2 claim was
read from the reference; it is now measured on live traffic.

One elision, disclosed: the run printed an absolute path where `<repo-root>`
stands. `check_invariants.py` rejected this section on its ABSOLUTE PATH rule
when it was first written — a true positive against new prose, caught on the
standalone run rather than the hook path, since a `python3` heredoc does not
match `Write|Edit|MultiEdit`. Everything else in the block is unaltered.

### The instrument correction — check per event type, not per format

Runs A2/B/A3 used `--output-format stream-json --verbose`. In those streams the
`user` tool_result event read, in full:

```
"content": "File created successfully at: .../_hookprobe.py"
```

— while the model demonstrably had the whole ruff diagnostic. The first reading
of that was "stream-json does not carry hook output". **That is wrong, and the
error is instructive.** Those runs omitted `--include-hook-events`, so the events
that carry hook output were never emitted at all. Run D added the flag and got
them (2 per Write, one per configured handler):

```
"subtype": "hook_response", "hook_name": "PostToolUse:Write",
"exit_code": 2, "outcome": "error",
"output":  "\nruff failed on ...F401...",
"stderr":  "\nruff failed on ...F401...",
"stdout":  ""
```

Keys: `hook_id`, `hook_name`, `hook_event`, `output`, `stdout`, `stderr`,
`exit_code`, `outcome`. `check_invariants.py`'s handler appears alongside with
all three text fields `""` and `exit_code: 0` — a silent pass is visible as a
pass, not as an absence.

**The scope, stated precisely.** A stream-json probe proves what it directly
captures and nothing beyond it. `tool_result` captures the *tool's* result;
`hook_response` captures the *hook's*. The earlier UserPromptSubmit probe
generalised safely only because its evidence *was* the `hook_response` event.
Here the evidence was the model's subsequent behaviour, and the JSON field was
silent about something that had plainly happened. Verify per event type; "I read
the stream and saw nothing" is a claim about the flags passed, not about the
runtime.

### Negative control — run, after being attempted wrongly twice

Purpose: distinguish *fired and dropped* from *never fired*.

| attempt | result |
|---|---|
| `--bare` | unusable — skips the credential path (`Not logged in · Please run /login`), and narrows tools to `Bash`/`Edit`/`Read` |
| `--settings '{"hooks":{}}'` | hooks still fired |
| `--settings '{"hooks":{"PostToolUse":[]}}'` | hooks still fired |
| `--settings '{"disableAllHooks": true}'` | **works** — model reports `NONE`, zero `hook_response` events, probe left dirty |

The two middle rows are not findings. HOOKS-REF.md:278 states hook entries
**merge** across settings levels rather than replacing each other, so neither
form could ever have overridden project hooks; :708 names
`--settings '{"disableAllHooks": true}'` as the documented way to turn hooks off
for one run "whatever the project's settings say". Both were on the page already.

**This was briefly written up as "the negative control is structurally
unrunnable."** It is not; it was untried as specified. The failure is the one
§4's order-of-work names first — read the reference end to end before building —
committed while writing notes about a section that says exactly that. Recorded
because "blocked" and "not yet attempted correctly" are different states, and
only one of them is an invitation to stop.

## 11. A config snippet for a file that exists is a *merge* instruction

Measured 2026-09-09, as a near-miss rather than a defect.

A task packet supplied a `.claude/settings.json` body containing exactly one
handler — a new `InstructionsLoaded` logger. The file on disk already carried
four handlers: `check_invariants.py` and `post_edit_python.py` on PostToolUse,
and `pre_bash_deploy.py` twice on PreToolUse. Written literally, the snippet
deletes all four. Silently: JSON has no merge semantics, the result is valid
JSON, and nothing in the pipeline warns that a gate stopped existing.

The trap is that the snippet was *complete and correct in itself*. Nothing about
it reads as partial.

HOOKS-REF.md § *Configuration* says hook entries **merge across settings
levels** — user, project, local. That is a different mechanism, and it offers no
protection at all against one file being overwritten by hand. Reading it as
reassurance here would be a category error.

The check, run on both merges that day:

| | PostToolUse | PreToolUse | InstructionsLoaded | PermissionDenied |
|---|---|---|---|---|
| before | 2 | 2 | — | — |
| after `InstructionsLoaded` | 2 | 2 | 1 | — |
| after `PermissionDenied` | 2 | 2 | 1 | 1 |

Assert per-event handler counts before and after, and fail on any decrease.
Eyeballing the diff is not the check: the deletion shows up as absence, which is
exactly what a diff makes easy to skim past.

## 12. `flyctl` on the ack-gate was already covered — a negative result, recorded

Investigated 2026-09-09 on the premise that the gate's dispatch filter was
`if: "Bash(fly *)"` only, that `flyctl deploy` therefore never reached the hook,
and that the test payloads were all `fly `-prefixed. All three were false:

| layer | state |
|---|---|
| `.claude/settings.json` | two `if` entries — `Bash(fly *)` **and** `Bash(flyctl *)` |
| `pre_bash_deploy.py` | `DEPLOYERS = {"fly", "flyctl"}` |
| `pre_bash_deploy.py` | `FALLBACK = r"\bfly(?:ctl)?\s+deploy\b"` |
| `tests/test_pre_bash_deploy.py` | `"flyctl deploy"` already a parametrized case |

The compound and env-prefixed forms are covered at the `if` layer too, per
HOOKS-REF.md § *Bash `if` matching*: each subcommand is checked, and leading
`VAR=value` assignments are stripped before matching. Measured against the
unmodified gate:

```
flyctl deploy                    -> deny
git push && flyctl deploy        -> deny
FLY_DEPLOY_ACK=1 flyctl deploy   -> ask
```

Two of those three were nonetheless new *test inputs*, and were added as
regression pins (15 passed -> 17 passed, all green on first run). Pins, not
fixes — they exist so a future edit narrowing either layer fails loudly.

Recorded because a discarded negative result is a concern that comes back. The
next person to notice `Bash(fly *)` in isolation will re-derive the same alarm
unless the answer is written down.

## 13. First `path_glob_match` against real config — and what silence means

The 2026-09-08 verification of `log_event.py` staged its lazy load: a throwaway
`.claude/rules/_probe.md` created, fired, and deleted. On 2026-09-09 the repo
gained its first real conditional rule, `.claude/rules/hook-authoring.md`,
scoped to two globs. Opening `.claude/hooks/post_edit_python.py`:

```
load_reason      path_glob_match
globs            ['.claude/hooks', '.claude/settings*.json']
trigger_file_path .../.claude/hooks/post_edit_python.py
```

Two things worth keeping.

**The payload normalises the glob.** The rule's frontmatter says
`".claude/hooks/**"`; the event reports `.claude/hooks`. Matching is unaffected —
a file beneath the directory triggered it — but a check that string-compares the
reported `globs` against the authored frontmatter will disagree for no reason.

**Silence does not mean no match.** Opening `.claude/settings.json` immediately
afterwards, in the same session, produced **no line at all**. The event fires
when a file is *loaded into context*, not on every access that matches its glob;
once loaded, a rule is not re-reported. Confirming the second glob therefore
required a fresh session (`claude -p`, hooks enabled), which produced
`path_glob_match ... trigger=settings.json` as its fifth line after the four
`session_start`/`include` loads.

The instrument answers "did this rule ever load", not "how often did it match".
Reading a missing line as a broken glob is the available mistake here.

## 14. `PermissionDenied` logger — wired 2026-09-09, UNFIRED

Status, not a finding. `log_event.py` was pointed at `PermissionDenied` on
2026-09-09 by a settings merge (§11). It has **not** been observed firing.

It cannot be staged cheaply: the event fires only when auto mode actually denies
a tool call, which needs the classifier to refuse something rather than a
synthetic payload. Per the calibration in the global `CLAUDE.md`, an
informational channel whose staged test is expensive earns a passive check
instead — so this one waits for its first natural firing.

Blast radius if it never fires: a false belief that denials are being recorded.
Nothing blocks, nothing deploys, no data is lost. That is what makes passive
acceptable here, and it is the reason the status is written down rather than
assumed.

**To close:** after a few sessions of ordinary work, grep
`.claude/metrics/*.jsonl` for `"hook_event_name":"PermissionDenied"`. Record the
result here either way — a confirmed absence after real denials is a defect
report, not a non-event.
