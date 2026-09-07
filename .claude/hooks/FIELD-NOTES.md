# Hooks field notes

Observed behaviour of the Claude Code hook runtime, recorded where the hooks
live. Each entry states what we believed, what is actually true, and the
evidence — so a future reader re-checks rather than re-learns.

Sources cited with access date. The runtime is the authority; docs second.

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

**Corollary the docs do not state:** under an auto-granting permission mode the
model is the only actor present. There is no human to read a `permissionDecision:
"ask"` reason, and exit-0 stderr does not reach the model. Therefore
**warn-without-blocking is unimplementable in PreToolUse.** A PreToolUse hook has
exactly two honest options: block (deny/exit 2), or stay silent. Anything in
between is a warning nobody receives.

**Live instance in this repo:** `pre_bash_deploy.py` prints its reason to stderr
with the comment "stderr puts it in the transcript either way", then returns 0.
That premise is false; the warning is not delivered. Commit `47c81a4`
("surface the deploy warning on stderr too") was built on it. See §4.

---

## 2. Exit code 2 — what it does per event

> PreToolUse: "Blocks the tool call" — the message shown comes from the JSON
> decision reason if provided, otherwise stderr.
> PostToolUse: "Shows stderr to Claude; the tool already ran."
> — same reference, accessed 2026-09-07.

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

`"ask"` defers to the normal permission flow — which an auto-granting mode
satisfies without ever displaying the reason. See the corollary in §1: `"ask"`
is not a warning mechanism.

---

## 4. Filed, not yet done: pre_bash_deploy becomes an acknowledge gate

Design decided 2026-09-07; implementation is a separate task.

- Switch `permissionDecision` from `"ask"` to `"deny"`, and have the reason name
  `FLY_DEPLOY_ACK=1` as the explicit escape. The hook allows the command when
  that variable is set. This converts an undeliverable warning (§1) into a real
  gate with a documented rollback path — the legitimate hand-deploy cases
  (rollback, wedged machine) stay possible, but become deliberate.
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

---

## 5. Filed for the uk-legal harvest

`uk-legal-mcp`'s `post_edit_check.py` is built on the premise "stderr so Claude
sees it, exit 0 always". Per §1 and §2 that is falsified in both halves: exit-0
stderr reaches nobody, and PostToolUse exit 2 *does* show stderr to the model
even though the tool already ran. Its fix is mechanical — **exit 0 → exit 2 on
the failure paths**. Nothing else about it needs to change.

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
