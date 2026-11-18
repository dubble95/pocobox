#!/bin/sh
set -eu

BUILD_DIR="/tmp/squeezelite-build"
rm -rf "${BUILD_DIR}"
git clone --depth 1 https://github.com/ralph-irving/squeezelite.git "${BUILD_DIR}"
cd "${BUILD_DIR}"

# Build with ALSA backend (supports BOTH bit-perfect hw:CARD=<USB_DAC> AND -o pulse via alsa-plugins-pulse),
# plus DSD, SoXR resampling, Opus, SSL/HTTPS streaming (for TIDAL), and VISEXPORT (-v) for decoupled AEC3 reference tap
CFLAGS="-O2 -fPIC -fcommon -I/usr/include/opus" OPTS="-DDSD -DRESAMPLE -DOPUS -DSSL -DVISEXPORT" make -j"$(nproc)"
if [ "$(id -u)" -eq 0 ]; then
    install -m 0755 squeezelite /usr/local/bin/squeezelite.new
    mv -f /usr/local/bin/squeezelite.new /usr/local/bin/squeezelite
else
    sudo install -m 0755 squeezelite /usr/local/bin/squeezelite.new
    sudo mv -f /usr/local/bin/squeezelite.new /usr/local/bin/squeezelite
fi
/usr/local/bin/squeezelite -? | head -n 5
rm -rf "${BUILD_DIR}"
