#!/bin/sh
set -eu

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SRC="${SCRIPT_DIR}/pocobox_aec3.cpp"
OUT="/usr/local/lib/libpocobox_aec3.so"

if [ ! -f "${SRC}" ]; then
    echo "ERROR: missing ${SRC}" >&2
    exit 1
fi

# Detect or install WebRTC AudioProcessing headers on Alpine / postmarketOS
PKG=""
for candidate in webrtc-audio-processing-1 webrtc-audio-processing-2; do
    if pkg-config --exists "${candidate}" 2>/dev/null; then
        PKG="${candidate}"
        break
    fi
done

if [ -z "${PKG}" ] && command -v apk >/dev/null 2>&1; then
    echo "==> Installing webrtc-audio-processing-1-dev and build tools (Alpine/postmarketOS)..."
    apk add --no-interactive --no-cache g++ pkgconf webrtc-audio-processing-1-dev </dev/null || \
        apk add --no-interactive --no-cache g++ pkgconf webrtc-audio-processing-2-dev </dev/null || \
        yes | apk add --no-cache g++ pkgconf webrtc-audio-processing-1-dev
    for candidate in webrtc-audio-processing-1 webrtc-audio-processing-2; do
        if pkg-config --exists "${candidate}" 2>/dev/null; then
            PKG="${candidate}"
            break
        fi
    done
elif [ -z "${PKG}" ] && command -v apt-get >/dev/null 2>&1; then
    echo "==> Installing webrtc-audio-processing headers and build tools (Debian/Ubuntu/Raspberry Pi OS)..."
    apt-get update && apt-get install -y libwebrtc-audio-processing-dev pkg-config g++ || true
    for candidate in webrtc-audio-processing-1 webrtc-audio-processing-2 webrtc-audio-processing; do
        if pkg-config --exists "${candidate}" 2>/dev/null; then
            PKG="${candidate}"
            break
        fi
    done
fi

if [ -z "${PKG}" ]; then
    echo "ERROR: neither webrtc-audio-processing-1 nor webrtc-audio-processing-2 found via pkg-config." >&2
    exit 1
fi

echo "==> Building ${OUT} against ${PKG}..."
CFLAGS="$(pkg-config --cflags "${PKG}")"
LIBS="$(pkg-config --libs "${PKG}")"

# shellcheck disable=SC2086
g++ -std=c++17 -O2 -fPIC -shared -Wall -Wextra \
    ${CFLAGS} "${SRC}" -o "${OUT}.new" ${LIBS} -lpthread -lrt

chmod 0755 "${OUT}.new"
mv -f "${OUT}.new" "${OUT}"
echo "==> Installed ${OUT}"
nm -D "${OUT}" | grep -E "pocobox_(aec3|vis)_" || true
