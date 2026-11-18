#!/bin/sh
set -eu

BUILD_DIR="/tmp/squeezelite-build"
rm -rf "${BUILD_DIR}"
git clone --depth 1 https://github.com/ralph-irving/squeezelite.git "${BUILD_DIR}"
cd "${BUILD_DIR}"

# Build with ALSA, PulseAudio, DSD, SoXR resampling, Opus, and SSL/HTTPS streaming (for TIDAL)
OPTS="-DDSD -DRESAMPLE -DOPUS -DSSL -DPULSEAUDIO" make -j"$(nproc)"
sudo install -m 0755 squeezelite /usr/local/bin/squeezelite
/usr/local/bin/squeezelite -? | head -n 5
rm -rf "${BUILD_DIR}"
