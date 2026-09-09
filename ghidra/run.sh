#!/usr/bin/env bash
# Run Ghidra headless against Croc2.exe inside the container.
#
# The binary is mounted read-only and never enters the image. Analysis results are
# cached in ghidra/project/, so only the first run pays for the full analysis; later
# runs reuse it and just execute the script you ask for.
#
#   ./ghidra/run.sh                          # analyse, then export the decompilation
#   ./ghidra/run.sh SomeOtherScript.java     # run a different script from scripts/
#
# Override the binary location with CROC2_EXE if yours lives elsewhere.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMAGE=croc2-ghidra
PROJECT_NAME=croc2

DEFAULT_EXE="$HOME/Applications/Croc 2.app/Contents/SharedSupport/prefix/drive_c/Program Files (x86)/Fox/Croc 2/Croc2.exe"
EXE="${CROC2_EXE:-$DEFAULT_EXE}"
SCRIPT="${1:-ExportDecompiled.java}"

if [[ ! -f "$EXE" ]]; then
    echo "Croc2.exe not found at:" >&2
    echo "  $EXE" >&2
    echo "Set CROC2_EXE to point at your own copy." >&2
    exit 1
fi

EXE_DIR="$(cd "$(dirname "$EXE")" && pwd)"
EXE_NAME="$(basename "$EXE")"

mkdir -p "$HERE/project" "$HERE/out"

if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
    echo "building $IMAGE (downloads Ghidra, ~570 MB, one time)..."
    docker build --platform linux/amd64 -t "$IMAGE" "$HERE"
fi

# Import and analyse once; afterwards reuse the analysed program.
if [[ -e "$HERE/project/$PROJECT_NAME.gpr" ]]; then
    echo "reusing analysed project; running $SCRIPT"
    MODE=(-process "$EXE_NAME" -noanalysis)
else
    echo "first run: importing and analysing $EXE_NAME (this takes a while)"
    MODE=(-import "/game/$EXE_NAME")
fi

# bash 3.2, which macOS ships, treats an empty array as unbound under `set -u`.
TTY=()
[[ -t 0 && -t 1 ]] && TTY=(-it)

docker run --rm ${TTY[@]+"${TTY[@]}"} \
    --platform linux/amd64 \
    -v "$EXE_DIR":/game:ro \
    -v "$HERE/project":/project \
    -v "$HERE/out":/out \
    -v "$HERE/scripts":/scripts:ro \
    "$IMAGE" \
    /project "$PROJECT_NAME" \
    "${MODE[@]}" \
    -scriptPath /scripts \
    -postScript "$SCRIPT" /out

echo "output in ghidra/out/"
