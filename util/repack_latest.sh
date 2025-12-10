#!/bin/bash
set -euo pipefail

UPLOAD_RELEASE=""
CHECKSUM_FILE=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --upload-release)
            if [[ $# -lt 2 ]]; then
                echo "Missing value for --upload-release"
                exit 1
            fi
            UPLOAD_RELEASE="$2"
            shift 2
            ;;
        --checksum-file)
            if [[ $# -lt 2 ]]; then
                echo "Missing value for --checksum-file"
                exit 1
            fi
            CHECKSUM_FILE="$2"
            shift 2
            ;;
        --)
            shift
            break
            ;;
        *)
            break
            ;;
    esac
done

if [[ $# -lt 2 ]]; then
    echo "Usage: $0 [--upload-release TAG] [--checksum-file FILE] <output_dir> <input1> [input2 ...]"
    exit 1
fi

RELEASE_DIR="$1"
shift
mkdir -p "$RELEASE_DIR"
RELEASE_DIR="$(cd "$RELEASE_DIR" && pwd)"

if [[ -n "$UPLOAD_RELEASE" && -z "$CHECKSUM_FILE" ]]; then
    CHECKSUM_FILE="$RELEASE_DIR/checksums.sha256"
fi

CHECKSUM_LOCK=""
if [[ -n "$CHECKSUM_FILE" ]]; then
    CHECKSUM_DIR="$(dirname "$CHECKSUM_FILE")"
    mkdir -p "$CHECKSUM_DIR"
    CHECKSUM_FILE="$(cd "$CHECKSUM_DIR" && pwd)/$(basename "$CHECKSUM_FILE")"
    : > "$CHECKSUM_FILE"
    CHECKSUM_LOCK="${CHECKSUM_FILE}.lock"
fi

rm -rf repack_dir
mkdir repack_dir
trap "rm -rf repack_dir" EXIT

while [[ $# -gt 0 ]]; do
    INPUT="$1"
    shift

    (
        set -euo pipefail
        REPACK_DIR="repack_dir/$BASHPID"
        rm -rf "$REPACK_DIR"
        mkdir "$REPACK_DIR"

        if [[ $INPUT == *.zip ]]; then
            unzip "$INPUT" -d "$REPACK_DIR"
        elif [[ $INPUT == *.tar.xz ]]; then
            tar xvaf "$INPUT" -C "$REPACK_DIR"
        else
            echo "Unknown input file type: $INPUT"
            exit 1
        fi

        cd "$REPACK_DIR"

        INAME="$(echo ffmpeg-*)"
        TAGNAME="$(cut -d- -f2 <<<"$INAME")"

        if [[ $TAGNAME == N ]]; then
            TAGNAME="master"
        elif [[ $TAGNAME == n* ]]; then
            TAGNAME="$(sed -re 's/([0-9]+\.[0-9]+).*/\1/' <<<"$TAGNAME")"
        fi

        if [[ "$INAME" =~ -[0-9]+-g ]]; then
            ONAME="ffmpeg-$TAGNAME-latest-$(cut -d- -f5- <<<"$INAME")"
        else
            ONAME="ffmpeg-$TAGNAME-latest-$(cut -d- -f3- <<<"$INAME")"
        fi

        mv "$INAME" "$ONAME"

        OUTPUT_PATH=""
        if [[ $INPUT == *.zip ]]; then
            OUTPUT_PATH="$RELEASE_DIR/$ONAME.zip"
            zip -9 -r "$OUTPUT_PATH" "$ONAME"
        elif [[ $INPUT == *.tar.xz ]]; then
            OUTPUT_PATH="$RELEASE_DIR/$ONAME.tar.xz"
            tar cvJf "$OUTPUT_PATH" "$ONAME"
        fi

        if [[ -n "$UPLOAD_RELEASE" ]]; then
            if [[ -n "$CHECKSUM_FILE" ]]; then
                (
                    flock -x 200
                    sha256sum "$OUTPUT_PATH" >> "$CHECKSUM_FILE"
                ) 200>"$CHECKSUM_LOCK"
            fi
            gh release upload "$UPLOAD_RELEASE" "$OUTPUT_PATH" --clobber
            rm -f "$OUTPUT_PATH"
        fi

        rm -rf "$REPACK_DIR"
    ) &

    while [[ $(jobs | wc -l) -gt 3 ]]; do
        wait %1
    done
done

while [[ $(jobs | wc -l) -gt 0 ]]; do
    wait %1
done

if [[ -n "$UPLOAD_RELEASE" && -n "$CHECKSUM_FILE" ]]; then
    gh release upload "$UPLOAD_RELEASE" "$CHECKSUM_FILE" --clobber
fi

rm -rf repack_dir
