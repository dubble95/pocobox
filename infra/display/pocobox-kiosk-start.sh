#!/bin/sh
set -eu

[ -f /etc/pocobox/display.env ] && . /etc/pocobox/display.env
URL="${DISPLAY_URL:-http://127.0.0.1:8765/display}"
SCALE="${FIREFOX_SCALE:-2.5}"
PROFILE_DIR="${HOME}/.pocobox-firefox"

mkdir -p "${PROFILE_DIR}"
cat > "${PROFILE_DIR}/user.js" <<EOF
user_pref("layout.css.devPixelsPerPx", "${SCALE}");
user_pref("browser.sessionstore.resume_from_crash", false);
user_pref("browser.shell.checkDefaultBrowser", false);
user_pref("toolkit.telemetry.enabled", false);
user_pref("datareporting.healthreport.uploadEnabled", false);
user_pref("browser.tabs.warnOnClose", false);
user_pref("browser.startup.homepage_override.mstone", "ignore");
user_pref("dom.disable_beforeunload", true);
user_pref("media.autoplay.default", 0);
EOF

# Wait briefly for pocobox-voice HTTP display server to be ready
for i in $(seq 1 25); do
  if curl -sf --max-time 1 http://127.0.0.1:8765/health >/dev/null 2>&1; then
    break
  fi
  sleep 0.5
done

export MOZ_ENABLE_WAYLAND=1
export XDG_CURRENT_DESKTOP=cage
export WLR_LIBINPUT_NO_DEVICES=1

exec cage -s -- firefox-esr --kiosk --no-remote --profile "${PROFILE_DIR}" "${URL}"
