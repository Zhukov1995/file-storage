#!/usr/bin/env bash
#
# Cut a new release tag and push it in one shot.
#
# Tags look like  vX.Y.Z  (plain semver). The script:
#   1. fetches tags from origin (so the number accounts for releases cut elsewhere),
#   2. bumps the PATCH of the highest existing vX.Y.Z tag by one,
#   3. tags the current HEAD and pushes the branch + the tag — the tag triggers
#      .github/workflows/build.yml, which builds & pushes the image to ghcr.io.
#
# The image version comes from the git-tag name (v1.2.3 -> image :1.2.3), so
# there is nothing else to keep in sync.
#
# Usage:
#   npm run release              # bump PATCH on the latest tag (v0.0.1 -> v0.0.2)
#   npm run release -- 0.1.0     # start a new BASE at v0.1.0 (minor/major bump)
#   DRY_RUN=1 npm run release    # show what would happen, change nothing
#
set -euo pipefail

DEFAULT_BASE="0.0.1"   # used only when no vX.Y.Z tag exists yet

# --- locate repo root so the script works from any cwd ----------------------
ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"

# --- make sure we know about tags created from other machines ---------------
git fetch --tags --quiet origin 2>/dev/null || true

# --- determine the next tag -------------------------------------------------
# An explicit version ($1) starts a new BASE verbatim. Otherwise bump the PATCH
# of the most recent vX.Y.Z tag; if none exists, start at DEFAULT_BASE.
if [[ -n "${1:-}" ]]; then
  NEW_VERSION="${1#v}"                                   # tolerate a leading "v"
else
  LATEST="$(git tag --list 'v[0-9]*.[0-9]*.[0-9]*' | sort -V | tail -n1)"
  if [[ -n "$LATEST" ]]; then
    IFS='.' read -r MAJOR MINOR PATCH <<<"${LATEST#v}"
    NEW_VERSION="${MAJOR}.${MINOR}.$((PATCH + 1))"
  else
    NEW_VERSION="$DEFAULT_BASE"
  fi
fi

NEW_TAG="v${NEW_VERSION}"

echo "Latest tag : ${LATEST:-<none>}"
echo "New tag     : $NEW_TAG"

# --- safety checks ----------------------------------------------------------
if git rev-parse -q --verify "refs/tags/${NEW_TAG}" >/dev/null; then
  echo "ERROR: tag ${NEW_TAG} already exists locally." >&2
  exit 1
fi

if git ls-remote --exit-code --tags origin "refs/tags/${NEW_TAG}" >/dev/null 2>&1; then
  echo "ERROR: tag ${NEW_TAG} already exists on origin." >&2
  exit 1
fi

if [[ -n "$(git status --porcelain)" ]]; then
  echo "ERROR: working tree is dirty. Commit or stash changes before releasing." >&2
  git status --short >&2
  exit 1
fi

if [[ "${DRY_RUN:-0}" == "1" ]]; then
  echo "[dry-run] would tag HEAD as ${NEW_TAG}"
  echo "[dry-run] would push the current branch and the tag to origin."
  exit 0
fi

# --- tag and push -----------------------------------------------------------
# Push HEAD before the tag so the tag never points at a commit missing from the
# branch on origin.
git tag "$NEW_TAG"
git push origin HEAD
git push origin "$NEW_TAG"

echo "Done. Tagged and pushed ${NEW_TAG}."
echo "Watch the build: https://github.com/TechPeopleOrg/file-storage/actions"
