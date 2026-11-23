#!/bin/sh
set -eu

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SRC="${SCRIPT_DIR}/pocobox_cage_touch.c"
OUT="/usr/local/lib/libpocobox_cage_touch.so"

if [ ! -f "${SRC}" ]; then
    echo "ERROR: missing ${SRC}" >&2
    exit 1
fi

mkdir -p /usr/local/lib
echo "==> Building ${OUT} from ${SRC}..."
gcc -O2 -Wall -shared -fPIC -ldl "${SRC}" -o "${OUT}"
chmod 0755 "${OUT}"
echo "==> Successfully built ${OUT}"
