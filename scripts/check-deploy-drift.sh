#!/usr/bin/env bash
# Verify the running Fly image was built by CI from the latest release.
#
# Why the image labels and not `flyctl releases`: CI authenticates with a token
# scoped to the owner's account, so the releases USER column reads identically
# for a pipeline deploy and a hand-run one. The GH_* labels are stamped from the
# Actions environment, so a `flyctl deploy` from a working tree carries none.
#
# The invariant is "prod == last release", not "prod == origin/main". Deploys are
# GitHub-release-triggered (release.yml), so main legitimately runs ahead of prod
# between releases; comparing against origin/main reported DRIFT as the steady
# state of a perfectly healthy repo. Distance from main is reported, never fatal.
#
# Exit: 0 in sync | 1 SHA drift | 2 no CI provenance | 3 lookup failed

# --- helpers; sourced by tests/test_check_deploy_drift.py ---

# Reduce a git remote URL to owner/repo. Two steps on purpose: POSIX ERE has no
# lazy quantifier, so the `[^/]+?` this replaced matched greedily and never
# stripped the .git suffix — every clone using the .git remote form got a false
# "built from X but this working tree is X.git" and exit 3.
normalise_repo_url() {
  printf '%s' "$1" | sed -E 's#/+$##; s#\.git$##' | sed -E 's#^.*[:/]([^/]+/[^/]+)$#\1#'
}

# Newest v* tag in version order. `sed -n 1p` rather than `head -1`: head closes
# the pipe as soon as it has its line, and under `set -o pipefail` the resulting
# SIGPIPE on `git tag` would abort the script.
latest_release_tag() {
  git tag -l 'v*' --sort=-v:refname | sed -n '1p'
}

# Tests source this file to exercise the helpers above; only run the checks when
# executed. Must precede `set -u`, which would fire on the positional reads.
if [ "${BASH_SOURCE[0]}" != "${0}" ]; then return 0; fi

set -euo pipefail

APP="${1:-$(sed -n 's/^app *= *"\(.*\)"/\1/p' fly.toml)}"

# Tags drive the comparison now, so they must be fetched before REF is resolved.
git fetch --quiet --tags origin 2>/dev/null || true
REF="${2:-$(latest_release_tag)}"
if [ -z "$REF" ]; then
  echo "FAIL  no v* release tag found — pass a ref explicitly as the 2nd argument." >&2
  exit 3
fi

labels=$(flyctl image show -a "$APP" --json 2>/dev/null \
  | python3 -c 'import json,sys
d=json.load(sys.stdin)
rec=d[0] if isinstance(d,list) else d
lab=rec.get("Labels") or "{}"
print(json.dumps(json.loads(lab) if isinstance(lab,str) else lab))' 2>/dev/null) || {
  echo "FAIL  could not read deployed image for '$APP'" >&2; exit 3; }

deployed_sha=$(printf '%s' "$labels" | python3 -c 'import json,sys;print(json.load(sys.stdin).get("GH_SHA",""))')
event=$(printf '%s' "$labels" | python3 -c 'import json,sys;print(json.load(sys.stdin).get("GH_EVENT_NAME",""))')
gh_repo=$(printf '%s' "$labels" | python3 -c 'import json,sys;print(json.load(sys.stdin).get("GH_REPO",""))')

# Guard: the deployed image must come from the repo this script is running in,
# otherwise every comparison against the local tags is meaningless.
local_repo=$(normalise_repo_url "$(git remote get-url origin 2>/dev/null)")
if [ -n "$gh_repo" ] && [ -n "$local_repo" ] && [ "$gh_repo" != "$local_repo" ]; then
  echo "FAIL  '$APP' was built from $gh_repo but this working tree is $local_repo." >&2
  echo "      Run this from the repo that builds the app." >&2
  exit 3
fi

if [ -z "$deployed_sha" ]; then
  echo "UNTRACKED  $APP: image carries no GH_SHA label."
  echo "           Deployed outside CI — most likely a local 'flyctl deploy' from a working tree."
  echo "           PyPI and the git tags may not reflect what is running."
  exit 2
fi

expected=$(git rev-parse "${REF}^{commit}" 2>/dev/null) || { echo "FAIL  cannot resolve $REF" >&2; exit 3; }

if [ "$deployed_sha" = "$expected" ]; then
  echo "OK  $APP is running ${deployed_sha:0:7} (via $event), matching $REF."
  # Informational only. main running ahead of the last release is the normal
  # state between releases; it must never change the exit code.
  if main_sha=$(git rev-parse origin/main 2>/dev/null) && [ "$main_sha" != "$expected" ] \
     && git merge-base --is-ancestor "$expected" "$main_sha" 2>/dev/null; then
    echo "    origin/main is $(git rev-list --count "$expected".."$main_sha") commit(s) ahead of $REF, unreleased."
  fi
  exit 0
fi

echo "DRIFT  $APP is running ${deployed_sha:0:7} (via $event)"
echo "       but $REF is at ${expected:0:7}."
if git merge-base --is-ancestor "$deployed_sha" "$expected" 2>/dev/null; then
  echo "       Deployed commit is an ancestor: production is behind by \
$(git rev-list --count "$deployed_sha".."$expected") commit(s)."
else
  echo "       Deployed commit is NOT on $REF — built from an unpushed or abandoned branch."
fi
exit 1
