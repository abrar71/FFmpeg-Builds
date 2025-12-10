#!/bin/bash
set -euo pipefail

command -v jq >/dev/null || { echo "jq is required" >&2; exit 1; }
command -v gh >/dev/null || { echo "gh is required" >&2; exit 1; }

: "${GITHUB_RUN_ID:?GITHUB_RUN_ID is required}"
: "${GITHUB_REPOSITORY:?GITHUB_REPOSITORY is required}"
: "${GITHUB_TOKEN:?GITHUB_TOKEN is required}"

export GH_TOKEN="${GITHUB_TOKEN}"

REL_DATE="$(date +'%Y-%m-%d %H:%M')"
TAG_NAME="autobuild-$(date +'%Y-%m-%d-%H-%M')"
RELEASE_TITLE="Auto-Build ${REL_DATE}"
LATEST_TITLE="Latest Auto-Build (${REL_DATE})"
LATEST_TAG="latest"

ARTIFACTS_DIR="artifacts"
LATEST_DIR="latest_artifacts"
MAIN_CHECKSUM="${ARTIFACTS_DIR}/checksums.sha256"
LATEST_CHECKSUM="${LATEST_DIR}/checksums.sha256"

rm -rf "${ARTIFACTS_DIR}" "${LATEST_DIR}"
mkdir -p "${ARTIFACTS_DIR}" "${LATEST_DIR}"
: >"${MAIN_CHECKSUM}"
: >"${LATEST_CHECKSUM}"

if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
    {
        echo "tag_name=${TAG_NAME}"
        echo "rel_date=${REL_DATE}"
    } >>"${GITHUB_OUTPUT}"
fi

append_checksum() {
    local src="$1"
    local dest="$2"
    local lock="${dest}.lock"
    flock "${lock}" -c "sha256sum \"${src}\" >> \"${dest}\""
}

repack_and_upload_latest() {
    local input="$1"
    local repack_dir
    repack_dir="$(mktemp -d "repack.XXXXXX")"

    if [[ "${input}" == *.zip ]]; then
        unzip -q "${input}" -d "${repack_dir}"
    elif [[ "${input}" == *.tar.xz ]]; then
        tar xf "${input}" -C "${repack_dir}"
    else
        echo "Unknown input file type: ${input}" >&2
        rm -rf "${repack_dir}"
        return 1
    fi

    pushd "${repack_dir}" >/dev/null
    shopt -s nullglob
    local inames=(ffmpeg-*)
    shopt -u nullglob

    if [[ "${#inames[@]}" -ne 1 ]]; then
        echo "Unexpected repack input layout for ${input}" >&2
        popd >/dev/null
        rm -rf "${repack_dir}"
        return 1
    fi

    local iname="${inames[0]}"
    local tagname
    tagname="$(cut -d- -f2 <<<"${iname}")"

    if [[ "${tagname}" == "N" ]]; then
        tagname="master"
    elif [[ "${tagname}" == n* ]]; then
        tagname="$(sed -re 's/([0-9]+\.[0-9]+).*/\1/' <<<"${tagname}")"
    fi

    local oname
    if [[ "${iname}" =~ -[0-9]+-g ]]; then
        oname="ffmpeg-${tagname}-latest-$(cut -d- -f5- <<<"${iname}")"
    else
        oname="ffmpeg-${tagname}-latest-$(cut -d- -f3- <<<"${iname}")"
    fi

    mv "${iname}" "${oname}"

    local output_path
    if [[ "${input}" == *.zip ]]; then
        output_path="${repack_dir}/${oname}.zip"
        zip -9 -r "${output_path}" "${oname}" >/dev/null
    else
        output_path="${repack_dir}/${oname}.tar.xz"
        tar cJf "${output_path}" "${oname}"
    fi

    gh release upload "${LATEST_TAG}" "${output_path}" --clobber
    append_checksum "${output_path}" "${LATEST_CHECKSUM}"

    popd >/dev/null
    rm -rf "${repack_dir}"
}

process_artifact() {
    local name="$1"
    local workdir
    workdir="$(mktemp -d "artifact.${name}.XXXXXX")"

    gh run download "${GITHUB_RUN_ID}" -n "${name}" --dir "${workdir}"

    mapfile -t archives < <(find "${workdir}" -type f \( -name '*.zip' -o -name '*.tar.xz' \))
    mapfile -t info_files < <(find "${workdir}" -type f -name '*.txt')

    if [[ "${#archives[@]}" -eq 0 ]]; then
        echo "No archives found for artifact ${name}" >&2
        rm -rf "${workdir}"
        return 1
    fi

    mkdir -p "${ARTIFACTS_DIR}"
    for info in "${info_files[@]}"; do
        cp "${info}" "${ARTIFACTS_DIR}/"
    done

    for archive in "${archives[@]}"; do
        gh release upload "${TAG_NAME}" "${archive}" --clobber
        append_checksum "${archive}" "${MAIN_CHECKSUM}"
        repack_and_upload_latest "${archive}"
    done

    rm -rf "${workdir}"
}

list_artifacts() {
    local page=1
    while :; do
        local resp
        resp="$(gh api -H "Accept: application/vnd.github+json" "/repos/${GITHUB_REPOSITORY}/actions/runs/${GITHUB_RUN_ID}/artifacts?per_page=100&page=${page}")"
        local count
        count="$(jq '.artifacts | length' <<<"${resp}")"
        jq -r '.artifacts[].name | select(startswith("ffmpeg-"))' <<<"${resp}"
        if [[ "${count}" -lt 100 ]]; then
            break
        fi
        page=$((page + 1))
    done
}

gh release create "${TAG_NAME}" --target "master" --title "${RELEASE_TITLE}" --draft
gh release delete --cleanup-tag --yes "${LATEST_TAG}" || true
sleep 15
gh release create "${LATEST_TAG}" --target "master" --title "${LATEST_TITLE}" --draft

artifacts="$(list_artifacts)"
if [[ -z "${artifacts// }" ]]; then
    echo "No artifacts to publish" >&2
    exit 1
fi

running=0
failed=0
while IFS= read -r artifact; do
    [[ -z "${artifact}" ]] && continue
    process_artifact "${artifact}" &
    running=$((running + 1))
    if [[ "${running}" -ge 3 ]]; then
        if ! wait -n; then
            failed=1
        fi
        running=$((running - 1))
    fi
done <<<"${artifacts}"

while [[ "${running}" -gt 0 ]]; do
    if ! wait -n; then
        failed=1
    fi
    running=$((running - 1))
done

if [[ "${failed}" -ne 0 ]]; then
    exit 1
fi

gh release upload "${TAG_NAME}" "${MAIN_CHECKSUM}" --clobber
gh release upload "${LATEST_TAG}" "${LATEST_CHECKSUM}" --clobber
gh release edit "${TAG_NAME}" --draft=false
gh release edit "${LATEST_TAG}" --draft=false
