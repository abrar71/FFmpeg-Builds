#!/bin/bash
set -e

git fetch --tags

# Gather all tags that match the `autobuild-*` pattern, sorted descending
# (Adjust if you want different or more advanced filtering)
TAGS=( $(git tag -l "autobuild-*" | sort -r) )

KEEP_LATEST=14
KEEP_MONTHLY=24

LATEST_TAGS=()
MONTHLY_TAGS=()
CUR_MONTH="-1"

# Collect the tags to keep
for TAG in "${TAGS[@]}"; do
    if [[ ${#LATEST_TAGS[@]} -lt ${KEEP_LATEST} ]]; then
        LATEST_TAGS+=( "$TAG" )
    fi

    if [[ ${#MONTHLY_TAGS[@]} -lt ${KEEP_MONTHLY} ]]; then
        TAG_MONTH="$(echo "$TAG" | cut -d- -f3)"
        if [[ ${TAG_MONTH} != ${CUR_MONTH} ]]; then
            CUR_MONTH="${TAG_MONTH}"
            MONTHLY_TAGS+=( "$TAG" )
        fi
    fi
done

# Remove the "keep" tags from the full list
for KEEP_TAG in "${LATEST_TAGS[@]}" "${MONTHLY_TAGS[@]}"; do
    TAGS=( "${TAGS[@]/$KEEP_TAG}" )
done

# At this point, TAGS should be the list you want to delete
for TAG in "${TAGS[@]}"; do
  echo "Checking for release: $TAG"

  # We capture output and return code of `gh release view <TAG>`.
  # That way, if the error is 'release not found', we handle it;
  # if it's something else (permissions, rate-limits), we stop.
  output=""
  ret=0
  output=$(gh release view "$TAG" 2>&1) || ret=$?

  if [[ $ret -eq 0 ]]; then
    # The release *does* exist
    echo "Found GitHub release for tag '$TAG'. Deleting release and tag..."
    if ! gh release delete --cleanup-tag --yes "$TAG"; then
      echo "ERROR: Failed deleting GitHub release for tag '$TAG' (unexpected error)."
      exit 1
    fi
  else
    # A non-zero return. Possibly "release not found" or something else
    if echo "$output" | grep -q "release not found"; then
      # Normal scenario: no release object. Just delete the tag itself.
      echo "No GitHub release found for tag '$TAG'. Deleting local & remote tag only..."
      if ! git push origin :"refs/tags/$TAG"; then
        echo "ERROR: Unable to delete remote tag '$TAG'."
        exit 1
      fi
      if ! git tag -d "$TAG"; then
        echo "ERROR: Unable to delete local tag '$TAG'."
        exit 1
      fi
    else
      # If it's not "release not found," assume some other failure (permissions, rate-limits, etc.)
      echo "ERROR: Unexpected failure checking release '$TAG':"
      echo "$output"
      exit 1
    fi
  fi
done

echo "Done pruning tags and releases."
git push --tags --prune