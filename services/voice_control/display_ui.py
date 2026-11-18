"""Always-on Now Playing Kiosk HTML/CSS/JS UI with real-time SSE voice subtitles."""

from __future__ import annotations

UI_VERSION = "2"

DISPLAY_HTML = """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no" />
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
    --glass: rgba(18, 22, 32, 0.58);
    --border: rgba(255, 255, 255, 0.14);
  }
  * { box-sizing: border-box; -webkit-tap-highlight-color: transparent; user-select: none; }
  html, body {
    margin: 0; padding: 0; width: 100%; height: 100%;
    background: var(--bg); color: var(--fg);
    font-family: -apple-system, BlinkMacSystemFont, "Noto Sans CJK KR", "Noto Sans KR", "Segoe UI", sans-serif;
    overflow: hidden;
  }
  .backdrop {
    position: fixed; inset: -32px;
    background-size: cover; background-position: center;
    filter: blur(42px) brightness(0.36) saturate(1.35);
    transform: scale(1.1);
    transition: background-image 0.8s ease;
    z-index: 0;
  }
  .backdrop-grad {
    position: fixed; inset: 0;
    background: radial-gradient(circle at 50% 25%, rgba(15,23,42,0.15), rgba(8,10,16,0.88) 80%);
    z-index: 1;
  }
  .app {
    position: relative; z-index: 2;
    display: flex; flex-direction: column; justify-content: space-between;
    height: 100%; width: 100%;
    padding: 46px 22px 24px 22px;
    max-width: 720px; margin: 0 auto;
  }
  .topbar {
    display: flex; justify-content: space-between; align-items: center; gap: 10px;
  }
  .pill {
    display: inline-flex; align-items: center; gap: 8px;
    padding: 8px 14px; border-radius: 999px;
    background: var(--glass); border: 1px solid var(--border);
    font-size: 13px; font-weight: 600; backdrop-filter: blur(12px);
    white-space: nowrap;
  }
  .dot {
    width: 9px; height: 9px; border-radius: 50%; background: var(--ok);
    box-shadow: 0 0 10px var(--ok);
  }
  .dot.recording { background: var(--rec); box-shadow: 0 0 14px var(--rec); animation: pulse 0.85s infinite; }
  .dot.processing { background: var(--warn); box-shadow: 0 0 14px var(--warn); animation: pulse 0.6s infinite; }
  .dot.off { background: #64748b; box-shadow: none; }
  @keyframes pulse { 0%,100% { transform: scale(1); opacity: 1; } 50% { transform: scale(1.45); opacity: 0.6; } }

  .center {
    flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: center;
    text-align: center; gap: 18px; min-height: 0;
  }
  .art-wrap {
    width: min(74vw, 39vh, 340px); height: min(74vw, 39vh, 340px);
    border-radius: 26px; overflow: hidden;
    background: rgba(255,255,255,0.06);
    border: 1px solid rgba(255,255,255,0.16);
    box-shadow: 0 28px 60px rgba(0,0,0,0.65);
    display: flex; align-items: center; justify-content: center;
    position: relative;
  }
  .art-wrap img { width: 100%; height: 100%; object-fit: cover; display: block; }
  .art-placeholder { font-size: 64px; opacity: 0.35; }

  .meta { width: 100%; padding: 0 6px; }
  .title {
    font-size: clamp(23px, 5.4vw, 32px); font-weight: 800; line-height: 1.22;
    letter-spacing: -0.02em; margin: 0 0 6px 0;
    display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
  }
  .artist {
    font-size: clamp(16px, 4vw, 21px); font-weight: 600; color: var(--accent);
    margin: 0 0 4px 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .album {
    font-size: 13px; color: var(--muted);
    margin: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .clock-idle { font-size: 52px; font-weight: 800; letter-spacing: -0.03em; margin-bottom: 4px; }

  .progress-wrap { width: 100%; }
  .bar-bg {
    width: 100%; height: 6px; border-radius: 999px;
    background: rgba(255,255,255,0.16); overflow: hidden;
  }
  .bar-fill {
    height: 100%; width: 0%; border-radius: 999px;
    background: linear-gradient(90deg, #38bdf8, #818cf8);
    transition: width 0.5s linear;
  }
  .times {
    display: flex; justify-content: space-between;
    font-size: 13px; color: var(--muted); margin-top: 7px; font-variant-numeric: tabular-nums;
  }

  .controls {
    display: flex; align-items: center; justify-content: center; gap: 14px;
    margin-top: 6px;
  }
  .btn {
    border: 1px solid var(--border); background: var(--glass); color: var(--fg);
    border-radius: 50%; width: 54px; height: 54px;
    font-size: 20px; font-weight: 700; cursor: pointer;
    display: inline-flex; align-items: center; justify-content: center;
    backdrop-filter: blur(12px);
  }
  .btn:active { transform: scale(0.92); background: rgba(255,255,255,0.2); }
  .btn.primary {
    width: 68px; height: 68px; font-size: 26px;
    background: #ffffff; color: #0b0d12; border-color: #ffffff;
  }
  .btn.mic {
    background: rgba(56, 189, 248, 0.22); border-color: rgba(56, 189, 248, 0.55);
  }

  /* Live floating subtitle box */
  .subtitle-overlay {
    position: fixed;
    left: 18px; right: 18px; bottom: 82px;
    max-width: 680px; margin: 0 auto;
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
    background: rgba(8, 12, 22, 0.88);
    border: 1.5px solid rgba(56, 189, 248, 0.5);
    border-radius: 22px;
    padding: 16px 20px;
    box-shadow: 0 20px 50px rgba(0, 0, 0, 0.72);
    backdrop-filter: blur(18px);
    text-align: center;
  }
  .subtitle-card.recording { border-color: rgba(239, 68, 68, 0.85); }
  .subtitle-card.processing { border-color: rgba(245, 158, 11, 0.85); }
  .subtitle-card.ok { border-color: rgba(34, 197, 94, 0.8); }
  .subtitle-card.ambient { border-color: rgba(148, 163, 184, 0.45); background: rgba(10, 14, 24, 0.76); }

  .sub-header {
    display: inline-flex; align-items: center; justify-content: center; gap: 8px;
    font-size: 12px; font-weight: 700; letter-spacing: 0.03em;
    color: var(--accent); margin-bottom: 6px;
  }
  .subtitle-card.recording .sub-header { color: #fca5a5; }
  .subtitle-card.processing .sub-header { color: #fcd34d; }
  .subtitle-card.ok .sub-header { color: #86efac; }
  .subtitle-card.ambient .sub-header { color: #94a3b8; }

  .sub-text {
    font-size: clamp(20px, 5.2vw, 28px);
    font-weight: 800; line-height: 1.32;
    color: #ffffff;
    word-break: keep-all;
  }
  .subtitle-card.ambient .sub-text {
    font-size: clamp(16px, 4.2vw, 21px);
    font-weight: 600;
    color: rgba(241, 245, 249, 0.85);
  }
  .sub-status {
    margin-top: 8px;
    font-size: 14px; font-weight: 600;
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

  .footer {
    display: flex; justify-content: space-between; align-items: center; gap: 10px;
    margin-top: 10px;
  }
  .voice-banner {
    flex: 1; padding: 10px 14px; border-radius: 14px;
    background: var(--glass); border: 1px solid var(--border);
    font-size: 13px; color: var(--muted);
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .link-btn {
    padding: 10px 14px; border-radius: 14px; text-decoration: none;
    background: var(--glass); border: 1px solid var(--border);
    color: var(--fg); font-size: 13px; font-weight: 600; white-space: nowrap;
  }
</style>
</head>
<body>
  <div id="backdrop" class="backdrop"></div>
  <div class="backdrop-grad"></div>

  <div id="subtitleOverlay" class="subtitle-overlay">
    <div id="subtitleCard" class="subtitle-card">
      <div id="subHeader" class="sub-header"></div>
      <div id="subText" class="sub-text"></div>
      <div id="subStatus" class="sub-status"></div>
    </div>
  </div>

  <div class="app">
    <div class="topbar">
      <div class="pill"><span id="micDot" class="dot"></span><span id="micText">상시 음성 대기</span></div>
      <div class="pill" id="outputBadge">🔊 출력 확인 중</div>
    </div>

    <div class="center">
      <div class="art-wrap">
        <img id="coverImg" alt="" style="display:none" />
        <div id="artPlaceholder" class="art-placeholder">🎵</div>
      </div>

      <div class="meta">
        <div id="idleClock" class="clock-idle" style="display:none">00:00</div>
        <h1 id="trackTitle" class="title">PocoBox 대기 중</h1>
        <p id="trackArtist" class="artist">"포코야 ..." 라고 불러보세요</p>
        <p id="trackAlbum" class="album">TIDAL · Lyrion Music Server</p>
      </div>

      <div class="progress-wrap">
        <div class="bar-bg"><div id="barFill" class="bar-fill"></div></div>
        <div class="times">
          <span id="timeElapsed">0:00</span>
          <span id="volText">VOL 50%</span>
          <span id="timeTotal">0:00</span>
        </div>
      </div>

      <div class="controls">
        <button class="btn" onclick="ctrl('vol_down')" aria-label="Volume Down">−</button>
        <button class="btn" onclick="ctrl('prev')" aria-label="Previous">⏮</button>
        <button class="btn primary" id="playBtn" onclick="ctrl('toggle')" aria-label="Play/Pause">▶</button>
        <button class="btn" onclick="ctrl('next')" aria-label="Next">⏭</button>
        <button class="btn" onclick="ctrl('vol_up')" aria-label="Volume Up">+</button>
        <button class="btn mic" onclick="triggerMic()" aria-label="Voice">🎙</button>
      </div>
    </div>

    <div class="footer">
      <div id="voiceBanner" class="voice-banner">🎙 "포코야 아이유 밤편지 틀어줘"</div>
      <a class="link-btn" id="lmsLink" href="http://127.0.0.1:9000/material/" target="_self">LMS</a>
    </div>
  </div>

<script>
  const CLIENT_UI_VERSION = "__UI_VERSION__";
  let lastCoverKey = "";
  let hideTimer = null;

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
    document.getElementById("idleClock").textContent = hh + ":" + mm;
  }

  function setMicBadge(ls, continuous) {
    const dot = document.getElementById("micDot");
    const micText = document.getElementById("micText");
    dot.className = "dot " + (ls === "recording" ? "recording" : ls === "processing" ? "processing" : ls === "waiting" ? "" : "off");
    if (ls === "recording") micText.textContent = "음성 듣는 중...";
    else if (ls === "processing") micText.textContent = "인식 중...";
    else if (continuous) micText.textContent = "상시 음성 대기";
    else micText.textContent = "음성 대기";
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

  function handleVoiceEvent(ev) {
    if (!ev || !ev.type) return;

    if (ev.type === "state") {
      setMicBadge(ev.state, true);
      if (ev.state === "recording") {
        showSubtitle(
          "recording",
          '<span class="wave"><span></span><span></span><span></span><span></span></span> 음성 감지 중',
          "듣고 있습니다...",
          "말씀을 마치면 자동으로 인식합니다",
          0
        );
      } else if (ev.state === "processing") {
        showSubtitle(
          "processing",
          "✨ Gemini 음성 인식 중",
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
      document.getElementById("voiceBanner").textContent = "🎙 “" + spoken + "”";
      return;
    }

    if (ev.type === "result") {
      const spoken = ev.text || "";
      const msg = ev.message || "";
      if (ev.ignored_no_wake) {
        if (spoken) {
          showSubtitle("ambient", "💬 주변 음성 (호출어 없음)", "“" + spoken + "”", "명령 실행 시 ‘포코야’로 시작해 주세요", 4500);
          document.getElementById("voiceBanner").textContent = "💬 “" + spoken + "”";
        } else {
          document.getElementById("subtitleOverlay").classList.remove("visible");
        }
        return;
      }
      const mode = ev.ok ? "ok" : "";
      const hdr = ev.ok ? "✅ 음성 명령 완료" : "⚠️ 음성 명령";
      showSubtitle(mode, hdr, spoken ? ("“" + spoken + "”") : msg, spoken ? msg : "", 6500);
      document.getElementById("voiceBanner").textContent = "🎙 " + (spoken ? ("“" + spoken + "” → ") : "") + msg;
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
    try {
      await fetch("/display/control", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({action})
      });
      setTimeout(refresh, 200);
    } catch (e) {}
  }

  async function triggerMic() {
    try {
      const r = await fetch("/voice/listen", {method: "POST", body: "{}"});
      await r.json();
      refresh();
    } catch (e) {}
  }

  async function refresh() {
    updateClock();
    try {
      const r = await fetch("/display/state", {cache: "no-store"});
      const s = await r.json();

      if (s.ui_version && s.ui_version !== CLIENT_UI_VERSION) {
        location.reload();
        return;
      }

      document.getElementById("lmsLink").href = "http://" + location.hostname + ":9000/material/";
      setMicBadge(s.voice.listening_state || "stopped", Boolean(s.voice.continuous_listen));
      document.getElementById("outputBadge").textContent = s.audio_output_label || "🔊 오디오";

      const np = s.now_playing || {};
      const hasTrack = Boolean(np.title);
      document.getElementById("idleClock").style.display = hasTrack ? "none" : "block";
      document.getElementById("trackTitle").textContent = hasTrack ? np.title : "재생 중인 곡 없음";
      document.getElementById("trackArtist").textContent = hasTrack ? (np.artist || "Unknown Artist") : '"포코야 ..." 라고 불러보세요';
      document.getElementById("trackAlbum").textContent = hasTrack ? (np.album || "TIDAL") : "USB DAC 연결 시 자동 이어재생";

      document.getElementById("playBtn").textContent = (np.mode === "play") ? "⏸" : "▶";
      document.getElementById("volText").textContent = "VOL " + (np.volume ?? 0) + "%";

      const elapsed = Number(np.time || 0);
      const duration = Number(np.duration || 0);
      const pct = duration > 0 ? Math.min(100, (elapsed / duration) * 100) : 0;
      document.getElementById("barFill").style.width = pct.toFixed(1) + "%";
      document.getElementById("timeElapsed").textContent = fmtTime(elapsed);
      document.getElementById("timeTotal").textContent = fmtTime(duration);

      const coverKey = (np.title || "") + "|" + (np.artist || "") + "|" + (np.coverid || "");
      const img = document.getElementById("coverImg");
      const ph = document.getElementById("artPlaceholder");
      const bg = document.getElementById("backdrop");
      if (hasTrack) {
        if (coverKey !== lastCoverKey) {
          lastCoverKey = coverKey;
          const url = "/display/cover?v=" + encodeURIComponent(coverKey);
          img.onload = () => { img.style.display = "block"; ph.style.display = "none"; bg.style.backgroundImage = "url('" + url + "')"; };
          img.onerror = () => { img.style.display = "none"; ph.style.display = "block"; bg.style.backgroundImage = "none"; };
          img.src = url;
        }
      } else {
        lastCoverKey = "";
        img.style.display = "none";
        ph.style.display = "block";
        bg.style.backgroundImage = "none";
      }
    } catch (e) {}
  }

  connectEvents();
  refresh();
  setInterval(refresh, 1000);
</script>
</body>
</html>
""".replace("__UI_VERSION__", UI_VERSION)
