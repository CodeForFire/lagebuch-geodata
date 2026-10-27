#!/usr/bin/env bash
# Commit the manifest changes of a build to a fresh branch through the GraphQL
# createCommitOnBranch mutation. GitHub signs commits made that way, so the
# pull request passes the signed-commits rule, which a plain `git push` from the
# runner would not. Env: GH_TOKEN, GITHUB_REPOSITORY, BRANCH, TAG.
set -euo pipefail

: "${GH_TOKEN:?}" "${GITHUB_REPOSITORY:?}" "${BRANCH:?}" "${TAG:?}"
FILES=(packs.json pack-sources.json "boundaries/${SLUG:?}.geojson")

# Base the branch on the commit the manifest was computed from, never on
# today's main. The files below are whole copies of that checkout plus this
# pack; laid over a main that has moved on, they would silently undo every
# manifest PR merged meanwhile (ffb-v2 dropped aic-v1 that way). On the old
# base, a concurrent change shows up as a merge conflict instead.
BASE_SHA="$(git rev-parse HEAD)"
if gh api "repos/$GITHUB_REPOSITORY/git/ref/heads/$BRANCH" >/dev/null 2>&1; then
  # A retry after a partial failure recomputes the same branch; it only ever
  # holds this one commit, so resetting it to the base is safe.
  gh api -X PATCH "repos/$GITHUB_REPOSITORY/git/refs/heads/$BRANCH" -f sha="$BASE_SHA" -F force=true >/dev/null
else
  gh api "repos/$GITHUB_REPOSITORY/git/refs" -f ref="refs/heads/$BRANCH" -f sha="$BASE_SHA" >/dev/null
fi

python3 - "$GITHUB_REPOSITORY" "$BRANCH" "$BASE_SHA" "$TAG" "${FILES[@]}" > build/commit.json <<'PY'
import base64, json, sys
repo, branch, base, tag, *files = sys.argv[1:]
additions = [{"path": p, "contents": base64.b64encode(open(p, "rb").read()).decode()} for p in files]
message = {
    "headline": f"chore: publish {tag}",
    "body": "Signed-off-by: github-actions[bot] <41898282+github-actions[bot]@users.noreply.github.com>",
}
query = """
mutation ($input: CreateCommitOnBranchInput!) {
  createCommitOnBranch(input: $input) { commit { oid } }
}"""
print(json.dumps({"query": query, "variables": {"input": {
    "branch": {"repositoryNameWithOwner": repo, "branchName": branch},
    "expectedHeadOid": base,
    "message": message,
    "fileChanges": {"additions": additions},
}}}))
PY

SHA="$(gh api graphql --input build/commit.json --jq .data.createCommitOnBranch.commit.oid)"
echo "Committed $SHA"
if [ -n "${GITHUB_OUTPUT:-}" ]; then
  echo "sha=$SHA" >> "$GITHUB_OUTPUT"
fi
