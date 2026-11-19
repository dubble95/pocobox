"""Always-on Fullscreen Now Playing Kiosk HTML/CSS/JS UI with Portrait/Landscape Rotation & Live SSE Subtitles."""

from __future__ import annotations

import json

UI_VERSION = "6"

DISPLAY_MANIFEST = json.dumps(
    {
        "name": "PocoBox Now Playing",
        "short_name": "PocoBox",
        "start_url": "/display",
        "scope": "/",
        "display": "fullscreen",
        "orientation": "any",
        "background_color": "#0b0d12",
        "theme_color": "#0b0d12",
    },
    ensure_ascii=False,
)

DISPLAY_HTML = r"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no, viewport-fit=cover" />
<meta name="theme-color" content="#0b0d12" />
<meta name="apple-mobile-web-app-capable" content="yes" />
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent" />
<meta name="mobile-web-app-capable" content="yes" />
<link rel="manifest" href="/display/manifest.webmanifest" />
<title>PocoBox Now Playing</title>
<style>
  :root {
    --bg: #0b0d12;
    --fg: #f5f7fb;
    --muted: rgba(245, 247, 251, 0.68);
    --accent: #38bdf8;
    --ok: #22c55e;
    --rec: #ef4444;
    --warn: #f59e0b;
    --glass: rgba(18, 22, 32, 0.56);
    --border: rgba(255, 255, 255, 0.14);
  }
  * { box-sizing: border-box; -webkit-tap-highlight-color: transparent; user-select: none; }
  html, body {
    margin: 0; padding: 0;
    width: 100%; height: 100%;
    width: 100vw; height: 100dvh;
    background: var(--bg); color: var(--fg);
    font-family: -apple-system, BlinkMacSystemFont, "Noto Sans CJK KR", "Noto Sans KR", "Segoe UI", sans-serif;
    overflow: hidden;
    overscroll-behavior: none;
    touch-action: manipulation;
  }

  .backdrop {
    position: fixed; inset: -36px;
    background-size: cover; background-position: center;
    filter: blur(48px) brightness(0.38) saturate(1.4);
    transform: scale(1.12);
    transition: background-image 0.8s ease;
    z-index: 0;
  }
  .backdrop-grad {
    position: fixed; inset: 0;
    background: radial-gradient(circle at 50% 30%, rgba(15, 23, 42, 0.16), rgba(8, 10, 16, 0.86) 82%);
    z-index: 1;
  }

  /* Pure edge-to-edge fullscreen stage (no persistent top/bottom status bars) */
  .app {
    position: relative; z-index: 2;
    width: 100%; height: 100%;
    display: flex; align-items: center; justify-content: center;
    padding:
      max(18px, env(safe-area-inset-top))
      max(20px, env(safe-area-inset-right))
      max(18px, env(safe-area-inset-bottom))
      max(20px, env(safe-area-inset-left));
  }

  .stage {
    width: 100%; height: 100%;
    max-width: 640px;
    display: flex; flex-direction: column;
    align-items: center; justify-content: center;
    gap: clamp(16px, 2.8dvh, 30px);
    min-height: 0;
  }

  .art-wrap {
    flex: 0 0 auto;
    width: min(78vw, 46dvh, 420px);
    height: min(78vw, 46dvh, 420px);
    border-radius: clamp(20px, 3.2vmin, 30px);
    overflow: hidden;
    background: rgba(255, 255, 255, 0.06);
    border: 1px solid rgba(255, 255, 255, 0.16);
    box-shadow: 0 28px 64px rgba(0, 0, 0, 0.68);
    display: flex; align-items: center; justify-content: center;
    position: relative;
  }
  .art-wrap img { width: 100%; height: 100%; object-fit: cover; display: block; }
  .art-placeholder { font-size: clamp(56px, 12vmin, 96px); opacity: 0.35; }

  .info-col {
    width: 100%;
    max-width: 560px;
    display: flex; flex-direction: column;
    align-items: center; justify-content: center;
    text-align: center;
    gap: clamp(14px, 2.4dvh, 24px);
    min-width: 0;
  }

  .meta { width: 100%; padding: 0 4px; min-width: 0; }
  .title {
    font-size: clamp(22px, 5.4vw, 34px);
    font-weight: 800; line-height: 1.22;
    letter-spacing: -0.02em; margin: 0 0 8px 0;
    display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical;
    overflow: hidden; word-break: keep-all;
  }
  .artist {
    font-size: clamp(16px, 4.1vw, 22px);
    font-weight: 600; color: var(--accent);
    margin: 0 0 4px 0;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .album {
    font-size: clamp(13px, 3vw, 15px);
    color: var(--muted);
    margin: 0;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .clock-idle {
    font-size: clamp(46px, 12vw, 72px);
    font-weight: 800; letter-spacing: -0.03em; margin-bottom: 6px;
    font-variant-numeric: tabular-nums;
  }

  .progress-wrap { width: 100%; }
  .bar-bg {
    width: 100%; height: 6px; border-radius: 999px;
    background: rgba(255, 255, 255, 0.16); overflow: hidden;
  }
  .bar-fill {
    height: 100%; width: 0%; border-radius: 999px;
    background: linear-gradient(90deg, #38bdf8, #818cf8);
    transition: width 0.45s linear;
  }
  .times {
    display: flex; justify-content: space-between; align-items: center;
    font-size: 13px; color: var(--muted); margin-top: 8px;
    font-variant-numeric: tabular-nums;
  }

  .controls {
    display: flex; align-items: center; justify-content: center;
    gap: clamp(10px, 2.8vw, 16px);
    flex-wrap: nowrap;
  }
  .btn {
    border: 1px solid var(--border); background: var(--glass); color: var(--fg);
    border-radius: 50%;
    width: clamp(44px, 11.5vw, 56px); height: clamp(44px, 11.5vw, 56px);
    font-size: clamp(17px, 4.2vw, 20px); font-weight: 700; cursor: pointer;
    display: inline-flex; align-items: center; justify-content: center;
    backdrop-filter: blur(12px);
    transition: transform 0.12s ease, background 0.15s ease;
    flex-shrink: 0;
  }
  .btn:active { transform: scale(0.92); background: rgba(255, 255, 255, 0.22); }
  .btn.primary {
    width: clamp(58px, 15vw, 72px); height: clamp(58px, 15vw, 72px);
    font-size: clamp(22px, 5.8vw, 28px);
    background: #ffffff; color: #0b0d12; border-color: #ffffff;
  }
  .btn.mic {
    background: rgba(56, 189, 248, 0.22); border-color: rgba(56, 189, 248, 0.55);
  }
  .btn.mic.active {
    background: rgba(239, 68, 68, 0.34); border-color: rgba(239, 68, 68, 0.85);
    box-shadow: 0 0 18px rgba(239, 68, 68, 0.45);
  }

  /* Landscape side-by-side layout (auto via orientation OR manual .layout-landscape) */
  @media (orientation: landscape) and (min-aspect-ratio: 115/100) {
    body:not(.layout-portrait) .app {
      padding:
        max(16px, env(safe-area-inset-top))
        max(28px, env(safe-area-inset-right))
        max(16px, env(safe-area-inset-bottom))
        max(28px, env(safe-area-inset-left));
    }
    body:not(.layout-portrait) .stage {
      max-width: 1280px;
      flex-direction: row;
      gap: clamp(24px, 4.5vw, 68px);
    }
    body:not(.layout-portrait) .art-wrap {
      width: min(76dvh, 42vw, 460px);
      height: min(76dvh, 42vw, 460px);
    }
    body:not(.layout-portrait) .info-col {
      flex: 1 1 0;
      max-width: 580px;
      align-items: flex-start;
      text-align: left;
      gap: clamp(12px, 3dvh, 26px);
    }
    body:not(.layout-portrait) .meta { padding: 0; }
    body:not(.layout-portrait) .title {
      font-size: clamp(20px, 3.6vw, 38px);
    }
    body:not(.layout-portrait) .artist {
      font-size: clamp(15px, 2.4vw, 24px);
    }
    body:not(.layout-portrait) .album {
      font-size: clamp(12px, 1.7vw, 16px);
    }
    body:not(.layout-portrait) .clock-idle {
      font-size: clamp(42px, 11dvh, 72px);
    }
    body:not(.layout-portrait) .controls {
      justify-content: flex-start;
      gap: clamp(10px, 1.8vw, 18px);
    }
    body:not(.layout-portrait) .btn {
      width: clamp(42px, 10.5dvh, 56px);
      height: clamp(42px, 10.5dvh, 56px);
      font-size: clamp(16px, 3.8dvh, 21px);
    }
    body:not(.layout-portrait) .btn.primary {
      width: clamp(54px, 13.5dvh, 72px);
      height: clamp(54px, 13.5dvh, 72px);
      font-size: clamp(21px, 5dvh, 28px);
    }
  }

  /* Compact short landscape screens (e.g. POCO F1 898x432, phone landscape) */
  @media (orientation: landscape) and (max-height: 460px) {
    body:not(.layout-portrait) .stage {
      gap: clamp(18px, 3.5vw, 36px);
    }
    body:not(.layout-portrait) .art-wrap {
      width: min(80dvh, 39vw);
      height: min(80dvh, 39vw);
    }
    body:not(.layout-portrait) .info-col {
      gap: clamp(10px, 2.6dvh, 18px);
    }
    body:not(.layout-portrait) .title {
      font-size: clamp(18px, 5.2dvh, 26px);
      margin-bottom: 5px;
    }
    body:not(.layout-portrait) .artist {
      font-size: clamp(14px, 3.8dvh, 19px);
    }
    body:not(.layout-portrait) .album {
      font-size: clamp(12px, 3dvh, 14px);
    }
  }

  /* Manual landscape override class */
  body.layout-landscape .stage {
    max-width: 1280px;
    flex-direction: row;
    gap: clamp(20px, 4vw, 60px);
  }
  body.layout-landscape .art-wrap {
    width: min(74dvh, 42vw, 460px);
    height: min(74dvh, 42vw, 460px);
  }
  body.layout-landscape .info-col {
    flex: 1 1 0;
    max-width: 580px;
    align-items: flex-start;
    text-align: left;
  }
  body.layout-landscape .controls {
    justify-content: flex-start;
  }

  /* Active-only breathing voice indicator dot (completely hidden while idle) */
  .voice-active-dot {
    position: fixed;
    top: max(14px, env(safe-area-inset-top));
    left: max(14px, env(safe-area-inset-left));
    width: 10px; height: 10px; border-radius: 50%;
    z-index: 15;
    opacity: 0; pointer-events: none;
    transition: opacity 0.2s ease;
  }
  .voice-active-dot.recording {
    opacity: 1; background: var(--rec);
    box-shadow: 0 0 14px var(--rec);
    animation: pulse 0.85s infinite;
  }
  .voice-active-dot.processing {
    opacity: 1; background: var(--warn);
    box-shadow: 0 0 14px var(--warn);
    animation: pulse 0.6s infinite;
  }
  @keyframes pulse { 0%,100% { transform: scale(1); opacity: 1; } 50% { transform: scale(1.45); opacity: 0.6; } }

  /* Auto-hiding tap-to-reveal floating HUD (zero bars visible by default!) */
  .hud {
    position: fixed;
    top: max(14px, env(safe-area-inset-top));
    right: max(14px, env(safe-area-inset-right));
    left: max(14px, env(safe-area-inset-left));
    z-index: 18;
    display: flex; justify-content: space-between; align-items: center; gap: 10px;
    opacity: 0;
    transform: translateY(-8px);
    pointer-events: none;
    transition: opacity 0.25s ease, transform 0.25s ease;
  }
  .hud.visible {
    opacity: 1;
    transform: translateY(0);
    pointer-events: auto;
  }
  .hud-group {
    display: inline-flex; align-items: center; gap: 8px;
    min-width: 0;
  }
  .hud-pill, .hud-btn {
    display: inline-flex; align-items: center; gap: 6px;
    padding: 7px 12px; border-radius: 999px;
    background: rgba(12, 16, 26, 0.78);
    border: 1px solid rgba(255, 255, 255, 0.16);
    color: var(--fg); font-size: 12px; font-weight: 600;
    backdrop-filter: blur(14px);
    text-decoration: none; white-space: nowrap;
  }
  .hud-btn { cursor: pointer; }
  .hud-btn:active { transform: scale(0.94); background: rgba(255, 255, 255, 0.18); }
  .hud-dot {
    width: 8px; height: 8px; border-radius: 50%; background: var(--ok);
    box-shadow: 0 0 8px var(--ok); flex-shrink: 0;
  }
  .hud-dot.recording { background: var(--rec); box-shadow: 0 0 10px var(--rec); }
  .hud-dot.processing { background: var(--warn); box-shadow: 0 0 10px var(--warn); }
  .hud-dot.off { background: #64748b; box-shadow: none; }
  .hud-output { overflow: hidden; text-overflow: ellipsis; max-width: 36vw; }

  /* Active-only quiz score badge */
  .quiz-score-bar {
    position: fixed;
    top: max(14px, env(safe-area-inset-top));
    left: 50%; transform: translateX(-50%);
    z-index: 16;
    display: none;
    align-items: center; justify-content: center; gap: 10px;
    padding: 8px 16px; border-radius: 999px;
    background: rgba(79, 70, 229, 0.42);
    border: 1px solid rgba(129, 140, 248, 0.72);
    font-size: 13px; font-weight: 700; color: #e0e7ff;
    backdrop-filter: blur(14px);
    white-space: nowrap;
  }
  .quiz-score-bar.active { display: inline-flex; }

  /* Live floating subtitle overlay */
  .subtitle-overlay {
    position: fixed;
    left: max(16px, env(safe-area-inset-left));
    right: max(16px, env(safe-area-inset-right));
    bottom: max(22px, env(safe-area-inset-bottom));
    max-width: 720px; margin: 0 auto;
    z-index: 20;
    pointer-events: none;
    opacity: 0;
    transform: translateY(18px) scale(0.97);
    transition: opacity 0.22s ease, transform 0.22s ease;
  }
  .subtitle-overlay.visible {
    opacity: 1;
    transform: translateY(0) scale(1);
  }
  .subtitle-card {
    background: rgba(8, 12, 22, 0.9);
    border: 1.5px solid rgba(56, 189, 248, 0.5);
    border-radius: 22px;
    padding: 14px 20px;
    box-shadow: 0 20px 50px rgba(0, 0, 0, 0.74);
    backdrop-filter: blur(18px);
    text-align: center;
  }
  .subtitle-card.recording { border-color: rgba(239, 68, 68, 0.85); }
  .subtitle-card.processing { border-color: rgba(245, 158, 11, 0.85); }
  .subtitle-card.ok { border-color: rgba(34, 197, 94, 0.8); }
  .subtitle-card.quiz { border-color: rgba(129, 140, 248, 0.9); background: rgba(15, 20, 42, 0.94); }
  .subtitle-card.ambient { border-color: rgba(148, 163, 184, 0.45); background: rgba(10, 14, 24, 0.8); }

  .sub-header {
    display: inline-flex; align-items: center; justify-content: center; gap: 8px;
    font-size: 12px; font-weight: 700; letter-spacing: 0.03em;
    color: var(--accent); margin-bottom: 5px;
  }
  .subtitle-card.recording .sub-header { color: #fca5a5; }
  .subtitle-card.processing .sub-header { color: #fcd34d; }
  .subtitle-card.ok .sub-header { color: #86efac; }
  .subtitle-card.quiz .sub-header { color: #a5b4fc; }
  .subtitle-card.ambient .sub-header { color: #94a3b8; }

  .sub-text {
    font-size: clamp(18px, 4.6vmin, 26px);
    font-weight: 800; line-height: 1.32;
    color: #ffffff;
    word-break: keep-all;
    white-space: pre-line;
  }
  .subtitle-card.ambient .sub-text {
    font-size: clamp(15px, 3.8vmin, 20px);
    font-weight: 600;
    color: rgba(241, 245, 249, 0.85);
  }
  .sub-status {
    margin-top: 6px;
    font-size: 13px; font-weight: 600;
    color: var(--muted);
  }
  .wave {
    display: inline-flex; align-items: center; gap: 3px; height: 14px;
  }
  .wave span {
    width: 3px; height: 6px; border-radius: 3px; background: currentColor;
    animation: eq 0.7s infinite ease-in-out;
  }
  .wave span:nth-child(2) { animation-delay: 0.15s; }
  .wave span:nth-child(3) { animation-delay: 0.3s; }
  .wave span:nth-child(4) { animation-delay: 0.45s; }
  @keyframes eq { 0%,100% { height: 4px; } 50% { height: 14px; } }
</style>
</head>
<body>
  <div id="backdrop" class="backdrop"></div>
  <div class="backdrop-grad"></div>

  <div id="voiceActiveDot" class="voice-active-dot"></div>
  <div id="quizScoreBar" class="quiz-score-bar">🎧 영어 퀴즈 1/5 · ⭐ 0점</div>

  <!-- Hidden by default; briefly shown on background tap/hover -->
  <div id="hud" class="hud" aria-hidden="true">
    <div class="hud-group">
      <div class="hud-pill"><span id="micDot" class="hud-dot"></span><span id="micText">상시 음성 대기</span></div>
      <div class="hud-pill hud-output" id="outputBadge">🔊 오디오</div>
    </div>
    <div class="hud-group">
      <button class="hud-btn" id="rotateBtn" onclick="rotateDisplay(event)" title="화면 회전">↻ 회전</button>
      <button class="hud-btn" id="fsBtn" onclick="toggleFullscreen(event)" title="전체 화면">⛶ 전체화면</button>
      <a class="hud-btn" id="dashLink" href="/dashboard" target="_self">설정</a>
      <a class="hud-btn" id="lmsLink" href="http://127.0.0.1:9000/material/" target="_self">LMS</a>
    </div>
  </div>

  <div id="subtitleOverlay" class="subtitle-overlay">
    <div id="subtitleCard" class="subtitle-card">
      <div id="subHeader" class="sub-header"></div>
      <div id="subText" class="sub-text"></div>
      <div id="subStatus" class="sub-status"></div>
    </div>
  </div>

  <div class="app" id="appRoot">
    <div class="stage">
      <div class="art-wrap" id="artWrap">
        <img id="coverImg" alt="" style="display:none" />
        <div id="artPlaceholder" class="art-placeholder">🎵</div>
      </div>

      <div class="info-col">
        <div class="meta">
          <div id="idleClock" class="clock-idle" style="display:none">00:00</div>
          <h1 id="trackTitle" class="title">PocoBox 연결 중...</h1>
          <p id="trackArtist" class="artist">"포코야 ..." 라고 불러보세요</p>
          <p id="trackAlbum" class="album">TIDAL · Lyrion Music Server</p>
        </div>

        <div class="progress-wrap">
          <div class="bar-bg"><div id="barFill" class="bar-fill"></div></div>
          <div class="times">
            <span id="timeElapsed">0:00</span>
            <span id="volText">VOL --%</span>
            <span id="timeTotal">0:00</span>
          </div>
        </div>

        <div class="controls">
          <button class="btn" id="volDownBtn" onclick="ctrl('vol_down')" aria-label="Volume Down">−</button>
          <button class="btn" onclick="ctrl('prev')" aria-label="Previous">⏮</button>
          <button class="btn primary" id="playBtn" onclick="ctrl('toggle')" aria-label="Play/Pause">▶</button>
          <button class="btn" onclick="ctrl('next')" aria-label="Next">⏭</button>
          <button class="btn" id="volUpBtn" onclick="ctrl('vol_up')" aria-label="Volume Up">+</button>
          <button class="btn mic" id="micBtn" onclick="triggerMic()" aria-label="Voice">🎙</button>
        </div>
      </div>
    </div>
  </div>

<script>
  const CLIENT_UI_VERSION = "__UI_VERSION__";
  const IS_KIOSK = new URLSearchParams(location.search).get("kiosk") === "1";
  let lastCoverKey = "";
  let hideTimer = null;
  let hudTimer = null;
  let refreshBusy = false;
  let quizActive = false;
  let layoutMode = "auto";
  let isDacVolumeFixed = false;
  let playbackState = { mode: "stop", elapsed: 0, duration: 0, updatedAt: Date.now() };

  function fmtTime(sec) {
    sec = Math.max(0, Math.floor(sec || 0));
    const m = Math.floor(sec / 60);
    const s = sec % 60;
    return m + ":" + (s < 10 ? "0" + s : s);
  }

  function updateClock() {
    const d = new Date();
    const hh = String(d.getHours()).padStart(2, "0");
    const mm = String(d.getMinutes()).padStart(2, "0");
    const el = document.getElementById("idleClock");
    if (el) el.textContent = hh + ":" + mm;
  }

  function renderProgress(elapsed, duration) {
    const pct = duration > 0 ? Math.min(100, (elapsed / duration) * 100) : 0;
    document.getElementById("barFill").style.width = pct.toFixed(1) + "%";
    document.getElementById("timeElapsed").textContent = fmtTime(elapsed);
    document.getElementById("timeTotal").textContent = fmtTime(duration);
  }

  function tickLocalProgress() {
    updateClock();
    if (playbackState.mode === "play" && playbackState.duration > 0) {
      const dt = Math.max(0, (Date.now() - playbackState.updatedAt) / 1000);
      const est = Math.min(playbackState.duration, playbackState.elapsed + dt);
      renderProgress(est, playbackState.duration);
    }
  }

  function setMicBadge(ls, continuous) {
    const dot = document.getElementById("micDot");
    const activeDot = document.getElementById("voiceActiveDot");
    const micText = document.getElementById("micText");
    const micBtn = document.getElementById("micBtn");
    const stateCls = ls === "recording" ? "recording" : ls === "processing" ? "processing" : ls === "waiting" ? "" : "off";
    if (dot) dot.className = "hud-dot " + stateCls;
    if (activeDot) activeDot.className = "voice-active-dot" + (ls === "recording" ? " recording" : ls === "processing" ? " processing" : "");
    if (micBtn) micBtn.classList.toggle("active", ls === "recording" || ls === "processing");
    if (micText) {
      if (ls === "recording") micText.textContent = "음성 듣는 중...";
      else if (ls === "processing") micText.textContent = "인식 중...";
      else if (continuous) micText.textContent = "상시 음성 대기";
      else micText.textContent = "음성 대기";
    }
  }

  function showHud(durationMs) {
    const hud = document.getElementById("hud");
    if (!hud) return;
    hud.classList.add("visible");
    hud.setAttribute("aria-hidden", "false");
    updateFullscreenButton();
    if (hudTimer) clearTimeout(hudTimer);
    hudTimer = setTimeout(() => {
      hud.classList.remove("visible");
      hud.setAttribute("aria-hidden", "true");
    }, durationMs || 4000);
  }

  function hideHud() {
    const hud = document.getElementById("hud");
    if (!hud) return;
    if (hudTimer) clearTimeout(hudTimer);
    hud.classList.remove("visible");
    hud.setAttribute("aria-hidden", "true");
  }

  function isFullscreen() {
    return Boolean(
      document.fullscreenElement ||
      document.webkitFullscreenElement ||
      window.matchMedia("(display-mode: fullscreen)").matches ||
      window.navigator.standalone === true
    );
  }

  function updateFullscreenButton() {
    const btn = document.getElementById("fsBtn");
    if (!btn) return;
    const canFs = Boolean(document.documentElement.requestFullscreen || document.documentElement.webkitRequestFullscreen);
    if (IS_KIOSK || !canFs) {
      btn.style.display = "none";
      return;
    }
    btn.style.display = "inline-flex";
    btn.textContent = isFullscreen() ? "✕ 창모드" : "⛶ 전체화면";
  }

  async function toggleFullscreen(ev) {
    if (ev) ev.stopPropagation();
    try {
      if (isFullscreen()) {
        if (document.exitFullscreen) await document.exitFullscreen();
        else if (document.webkitExitFullscreen) document.webkitExitFullscreen();
      } else {
        const el = document.documentElement;
        if (el.requestFullscreen) await el.requestFullscreen({ navigationUI: "hide" });
        else if (el.webkitRequestFullscreen) el.webkitRequestFullscreen();
      }
    } catch (_) {}
    updateFullscreenButton();
    hideHud();
  }

  function applyLayoutClass() {
    document.body.classList.remove("layout-landscape", "layout-portrait");
    if (layoutMode === "landscape") document.body.classList.add("layout-landscape");
    else if (layoutMode === "portrait") document.body.classList.add("layout-portrait");
  }

  async function rotateDisplay(ev) {
    if (ev) ev.stopPropagation();
    if (IS_KIOSK) {
      try {
        await fetch("/display/rotate", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({mode: "next"})
        });
      } catch (_) {}
      hideHud();
      return;
    }
    const currentlyWide = window.innerWidth > window.innerHeight;
    if (layoutMode === "auto") {
      layoutMode = currentlyWide ? "portrait" : "landscape";
    } else if (layoutMode === "landscape") {
      layoutMode = "portrait";
    } else {
      layoutMode = "auto";
    }
    applyLayoutClass();
    try {
      if (screen.orientation && screen.orientation.lock && isFullscreen()) {
        if (layoutMode === "landscape") await screen.orientation.lock("landscape");
        else if (layoutMode === "portrait") await screen.orientation.lock("portrait");
        else if (screen.orientation.unlock) screen.orientation.unlock();
      }
    } catch (_) {}
    showHud(2500);
  }

  function showSubtitle(mode, headerHtml, text, statusText, autoHideMs) {
    const overlay = document.getElementById("subtitleOverlay");
    const card = document.getElementById("subtitleCard");
    card.className = "subtitle-card " + (mode || "");
    document.getElementById("subHeader").innerHTML = headerHtml || "";
    document.getElementById("subText").textContent = text || "";
    document.getElementById("subStatus").textContent = statusText || "";
    overlay.classList.add("visible");

    if (hideTimer) clearTimeout(hideTimer);
    if (autoHideMs && autoHideMs > 0) {
      hideTimer = setTimeout(() => {
        overlay.classList.remove("visible");
      }, autoHideMs);
    }
  }

  function updateQuizBadge(q) {
    const bar = document.getElementById("quizScoreBar");
    quizActive = Boolean(q && q.active);
    if (!bar) return;
    if (quizActive) {
      bar.classList.add("active");
      const streakTxt = q.streak >= 2 ? (" · 🔥 " + q.streak + "연속") : "";
      bar.textContent = "🎧 영어 퀴즈 (" + (q.topic_label || "기초") + ")  " + (q.question_num || 1) + "/" + (q.total || 5) + "  ·  ⭐ " + (q.score || 0) + "점" + streakTxt;
    } else {
      bar.classList.remove("active");
    }
  }

  function handleVoiceEvent(ev) {
    if (!ev || !ev.type) return;

    if (ev.type === "quiz") {
      updateQuizBadge(ev.quiz);
      const isOk = ev.event === "correct" || ev.event === "finished";
      const mode = isOk ? "ok" : "quiz";
      const autoHide = ev.active ? 0 : 14000;
      showSubtitle(mode, ev.header || "🎧 영어 리스닝 퀴즈", ev.text || "", ev.status || "", autoHide);
      return;
    }

    if (ev.type === "quiz_ready") {
      updateQuizBadge(ev.quiz);
      const statusEl = document.getElementById("subStatus");
      if (statusEl) statusEl.textContent = "🔔 띵동! 지금 말씀해 주세요...";
      return;
    }

    if (ev.type === "state") {
      setMicBadge(ev.state, true);
      if (ev.state === "recording") {
        showSubtitle(
          "recording",
          '<span class="wave"><span></span><span></span><span></span><span></span></span> ' + (quizActive ? "🔔 지금 대답해 주세요!" : "음성 감지 중"),
          quizActive ? "정답을 말씀해 주세요..." : "듣고 있습니다...",
          quizActive ? '"다시", "천천히", "힌트", "패스", "그만"' : "말씀을 마치면 자동으로 인식합니다",
          0
        );
      } else if (ev.state === "processing") {
        showSubtitle(
          "processing",
          "✨ 음성 인식 중",
          "분석하고 있습니다...",
          "",
          0
        );
      }
      return;
    }

    if (ev.type === "transcript") {
      const spoken = ev.text || "";
      if (!spoken) return;
      const isWake = Boolean(ev.wake);
      showSubtitle(
        isWake ? "processing" : "ambient",
        isWake ? "🎙 인식된 음성" : "💬 감지된 음성 (호출어 없음)",
        "“" + spoken + "”",
        isWake ? "명령 실행 중..." : "‘포코야’라고 불러주세요",
        isWake ? 0 : 4000
      );
      return;
    }

    if (ev.type === "ack") {
      const ack = ev.text || "";
      if (!ack) return;
      const userText = ev.user_text || "";
      showSubtitle("ok", "💬 포코박스 응답", ack, userText ? ("“" + userText + "”") : "", 0);
      return;
    }

    if (ev.type === "result") {
      const spoken = ev.text || "";
      const msg = ev.message || "";
      if (ev.ignored_no_wake) {
        if (spoken) {
          showSubtitle("ambient", "💬 주변 음성 (호출어 없음)", "“" + spoken + "”", "명령 실행 시 ‘포코야’로 시작해 주세요", 4500);
        } else {
          document.getElementById("subtitleOverlay").classList.remove("visible");
        }
        return;
      }
      if (ev.action === "weather" && ev.ok) {
        showSubtitle("ok", "🌤️ 날씨 안내", msg, spoken ? ("Q. " + spoken) : "", 12000);
        return;
      }
      if (ev.action === "answer_qa" && ev.ok) {
        showSubtitle("ok", "💬 포코박스 답변", msg, spoken ? ("Q. " + spoken) : "", 12000);
        return;
      }
      const mode = ev.ok ? "ok" : "";
      const hdr = ev.ok ? "✅ 음성 명령 완료" : "⚠️ 음성 명령";
      showSubtitle(mode, hdr, spoken ? ("“" + spoken + "”") : msg, spoken ? msg : "", 6500);
      setTimeout(refresh, 350);
    }
  }

  function connectEvents() {
    try {
      const es = new EventSource("/display/events");
      es.onmessage = (e) => {
        try { handleVoiceEvent(JSON.parse(e.data)); } catch (_) {}
      };
      es.onerror = () => {
        es.close();
        setTimeout(connectEvents, 2000);
      };
    } catch (_) {
      setTimeout(connectEvents, 3000);
    }
  }

  async function ctrl(action) {
    if (isDacVolumeFixed && (action === "vol_up" || action === "vol_down")) {
      showSubtitle("ok", "🎧 DAC 볼륨 고정", "DAC 모드에서는 고음질 출력을 위해 볼륨이 100%로 고정되어 있습니다.", "앰프나 스피커에서 볼륨을 조절해 주세요", 4500);
      return;
    }
    try {
      await fetch("/display/control", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({action})
      });
      setTimeout(refresh, 200);
    } catch (_) {}
  }

  async function triggerMic() {
    try {
      const r = await fetch("/voice/listen", {method: "POST", body: "{}"});
      await r.json();
      refresh();
    } catch (_) {}
  }

  function setBackdropUrl(url) {
    const bg = document.getElementById("backdrop");
    if (!bg) return;
    if (!url) {
      bg.style.backgroundImage = "none";
      return;
    }
    const safeUrl = url.replace(/["\\]/g, "\\$&");
    bg.style.backgroundImage = 'url("' + safeUrl + '")';
  }

  async function refresh() {
    if (refreshBusy) return;
    refreshBusy = true;
    updateClock();
    try {
      const r = await fetch("/display/state", {cache: "no-store"});
      if (!r.ok) return;
      const s = await r.json();

      if (s.ui_version && s.ui_version !== CLIENT_UI_VERSION) {
        location.reload();
        return;
      }

      const lmsLink = document.getElementById("lmsLink");
      if (lmsLink) lmsLink.href = "http://" + location.hostname + ":9000/material/";
      if (s.voice) {
        setMicBadge(s.voice.listening_state || "stopped", Boolean(s.voice.continuous_listen));
      }
      const outBadge = document.getElementById("outputBadge");
      if (outBadge) outBadge.textContent = s.audio_output_label || "🔊 오디오";
      updateQuizBadge(s.quiz);

      const np = s.now_playing || {};
      const hasTrack = Boolean(np.title) || np.mode === "play";
      document.getElementById("idleClock").style.display = hasTrack ? "none" : "block";
      document.getElementById("trackTitle").textContent = hasTrack ? (np.title || "재생 중") : "재생 중인 곡 없음";
      document.getElementById("trackArtist").textContent = hasTrack ? (np.artist || "Unknown Artist") : '"포코야 ..." 라고 불러보세요';
      document.getElementById("trackAlbum").textContent = hasTrack ? (np.album || "TIDAL") : "USB DAC 연결 시 자동 이어재생";

      document.getElementById("playBtn").textContent = (np.mode === "play") ? "⏸" : "▶";
      isDacVolumeFixed = Boolean(s.dac_fixed_volume || s.audio_output_mode === "usb_dac" || np.digital_volume_control === 0 || np.use_volume_control === 0);
      const volDownBtn = document.getElementById("volDownBtn");
      const volUpBtn = document.getElementById("volUpBtn");
      const volText = document.getElementById("volText");

      if (isDacVolumeFixed) {
        volText.textContent = "DAC 100% (고정)";
        volText.style.color = "rgba(74, 222, 128, 0.95)";
        if (volDownBtn) {
          volDownBtn.style.opacity = "0.35";
          volDownBtn.setAttribute("title", "DAC 모드에서는 볼륨이 100%로 고정됩니다");
        }
        if (volUpBtn) {
          volUpBtn.style.opacity = "0.35";
          volUpBtn.setAttribute("title", "DAC 모드에서는 볼륨이 100%로 고정됩니다");
        }
      } else {
        const vol = (np.volume !== undefined && np.volume !== null) ? Math.round(Number(np.volume)) : 0;
        volText.textContent = "VOL " + vol + "%";
        volText.style.color = "";
        if (volDownBtn) {
          volDownBtn.style.opacity = "";
          volDownBtn.removeAttribute("title");
        }
        if (volUpBtn) {
          volUpBtn.style.opacity = "";
          volUpBtn.removeAttribute("title");
        }
      }

      const elapsed = Number(np.time || 0);
      const duration = Number(np.duration || 0);
      playbackState = { mode: np.mode || "stop", elapsed, duration, updatedAt: Date.now() };
      renderProgress(elapsed, duration);

      const coverKey = (np.title || "") + "|" + (np.artist || "") + "|" + (np.coverid || "") + "|" + (np.artwork_url || "");
      const img = document.getElementById("coverImg");
      const ph = document.getElementById("artPlaceholder");
      if (hasTrack) {
        if (coverKey !== lastCoverKey) {
          lastCoverKey = coverKey;
          const url = "/display/cover?v=" + encodeURIComponent(coverKey);
          img.onload = () => {
            img.style.display = "block";
            ph.style.display = "none";
            setBackdropUrl(url);
          };
          img.onerror = () => {
            img.style.display = "none";
            ph.style.display = "block";
            setBackdropUrl("");
          };
          img.src = url;
        }
      } else {
        lastCoverKey = "";
        img.style.display = "none";
        ph.style.display = "block";
        setBackdropUrl("");
      }
    } catch (_) {
    } finally {
      refreshBusy = false;
    }
  }

  // Tap empty background to briefly toggle minimal HUD; double-tap cover art for fullscreen
  document.getElementById("appRoot").addEventListener("click", (e) => {
    if (e.target.closest("button, a")) return;
    const hud = document.getElementById("hud");
    if (hud && hud.classList.contains("visible")) hideHud();
    else showHud(4000);
  });

  document.getElementById("artWrap").addEventListener("dblclick", (e) => {
    if (!IS_KIOSK) toggleFullscreen(e);
  });

  document.addEventListener("fullscreenchange", updateFullscreenButton);
  document.addEventListener("webkitfullscreenchange", updateFullscreenButton);
  window.addEventListener("orientationchange", () => {
    if (layoutMode !== "auto") {
      layoutMode = "auto";
      applyLayoutClass();
    }
  });
  window.addEventListener("resize", () => {
    if (layoutMode !== "auto" && IS_KIOSK) {
      layoutMode = "auto";
      applyLayoutClass();
    }
  });

  updateFullscreenButton();
  connectEvents();
  refresh();
  setInterval(refresh, 1500);
  setInterval(tickLocalProgress, 500);
</script>
</body>
</html>
""".replace("__UI_VERSION__", UI_VERSION)
