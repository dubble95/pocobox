#!/bin/sh
set -eu

BUILD_DIR="/tmp/squeezelite-build"
rm -rf "${BUILD_DIR}"
git clone --depth 1 https://github.com/ralph-irving/squeezelite.git "${BUILD_DIR}"
cd "${BUILD_DIR}"

# Build with ALSA backend (supports BOTH bit-perfect hw:CARD=<USB_DAC> AND -o pulse via alsa-plugins-pulse),
# plus DSD, SoXR resampling, Opus, and SSL/HTTPS streaming (for TIDAL)
CFLAGS="-O2 -fPIC -fcommon -I/usr/include/opus" OPTS="-DDSD -DRESAMPLE -DOPUS -DSSL" make -j"$(nproc)"
sudo install -m 0755 squeezelite /usr/local/bin/squeezelite
/usr/local/bin/squeezelite -? | head -n 5
rm -rf "${BUILD_DIR}"
