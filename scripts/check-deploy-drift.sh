#!/usr/bin/env bash
# Verify the running Fly image was built by CI from a commit that is on origin/main.
#
# Why the image labels and not `flyctl releases`: CI authenticates with a token
# scoped to the owner's account, so the releases USER column reads identically
# for a pipeline deploy and a hand-run one. The GH_* labels are stamped from the
# Actions environment, so a `flyctl deploy` from a working tree carries none.
#
# Exit: 0 in sync | 1 SHA drift | 2 no CI provenance | 3 lookup failed
set -euo pipefail

APP="${1:-$(sed -n 's/^app *= *"\(.*\)"/\1/p' fly.toml)}"
REF="${2:-origin/main}"

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
# otherwise every comparison against the local origin/main is meaningless.
local_repo=$(git remote get-url origin 2>/dev/null \
  | sed -E 's#^.*[:/]([^/]+/[^/]+?)(\.git)?$#\1#')
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

git fetch --quiet origin 2>/dev/null || true
expected=$(git rev-parse "$REF" 2>/dev/null) || { echo "FAIL  cannot resolve $REF" >&2; exit 3; }

if [ "$deployed_sha" = "$expected" ]; then
  echo "OK  $APP is running ${deployed_sha:0:7} (via $event), matching $REF."
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
