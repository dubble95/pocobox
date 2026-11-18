"""Always-on Now Playing Kiosk HTML/CSS/JS UI for the POCO F1 screen."""

from __future__ import annotations

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
    --glass: rgba(18, 22, 32, 0.56);
    --border: rgba(255, 255, 255, 0.12);
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
    /* Extra top padding to clear POCO F1 camera notch cleanly */
    padding: 46px 24px 28px 24px;
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
  .dot.recording { background: var(--rec); box-shadow: 0 0 14px var(--rec); animation: pulse 0.9s infinite; }
  .dot.processing { background: #f59e0b; box-shadow: 0 0 14px #f59e0b; animation: pulse 0.6s infinite; }
  .dot.off { background: #64748b; box-shadow: none; }
  @keyframes pulse { 0%,100% { transform: scale(1); opacity: 1; } 50% { transform: scale(1.4); opacity: 0.65; } }

  .center {
    flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: center;
    text-align: center; gap: 22px; min-height: 0;
  }
  .art-wrap {
    width: min(76vw, 42vh, 360px); height: min(76vw, 42vh, 360px);
    border-radius: 26px; overflow: hidden;
    background: rgba(255,255,255,0.06);
    border: 1px solid rgba(255,255,255,0.16);
    box-shadow: 0 28px 60px rgba(0,0,0,0.65);
    display: flex; align-items: center; justify-content: center;
    position: relative;
  }
  .art-wrap img {
    width: 100%; height: 100%; object-fit: cover; display: block;
  }
  .art-placeholder {
    font-size: 68px; opacity: 0.35;
  }
  .meta { width: 100%; padding: 0 6px; }
  .title {
    font-size: clamp(24px, 5.5vw, 34px); font-weight: 800; line-height: 1.22;
    letter-spacing: -0.02em; margin: 0 0 8px 0;
    display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
  }
  .artist {
    font-size: clamp(17px, 4vw, 22px); font-weight: 600; color: var(--accent);
    margin: 0 0 4px 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .album {
    font-size: 14px; color: var(--muted);
    margin: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .clock-idle {
    font-size: 56px; font-weight: 800; letter-spacing: -0.03em; margin-bottom: 4px;
  }

  .progress-wrap { width: 100%; }
  .bar-bg {
    width: 100%; height: 7px; border-radius: 999px;
    background: rgba(255,255,255,0.16); overflow: hidden;
  }
  .bar-fill {
    height: 100%; width: 0%; border-radius: 999px;
    background: linear-gradient(90deg, #38bdf8, #818cf8);
    transition: width 0.5s linear;
  }
  .times {
    display: flex; justify-content: space-between;
    font-size: 13px; color: var(--muted); margin-top: 8px; font-variant-numeric: tabular-nums;
  }

  .controls {
    display: flex; align-items: center; justify-content: center; gap: 18px;
    margin-top: 12px;
  }
  .btn {
    border: 1px solid var(--border); background: var(--glass); color: var(--fg);
    border-radius: 50%; width: 58px; height: 58px;
    font-size: 22px; font-weight: 700; cursor: pointer;
    display: inline-flex; align-items: center; justify-content: center;
    backdrop-filter: blur(12px);
  }
  .btn:active { transform: scale(0.92); background: rgba(255,255,255,0.2); }
  .btn.primary {
    width: 74px; height: 74px; font-size: 28px;
    background: #ffffff; color: #0b0d12; border-color: #ffffff;
  }
  .btn.mic {
    background: rgba(56, 189, 248, 0.22); border-color: rgba(56, 189, 248, 0.55);
  }

  .footer {
    display: flex; justify-content: space-between; align-items: center; gap: 10px;
    margin-top: 16px;
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
      <div id="voiceBanner" class="voice-banner">음성 명령 대기 중 ("포코야 아이유 노래 틀어줘")</div>
      <a class="link-btn" id="lmsLink" href="http://127.0.0.1:9000/material/" target="_self">LMS</a>
    </div>
  </div>

<script>
  let lastCoverKey = "";

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
    document.getElementById("voiceBanner").textContent = "🎙 듣고 있습니다... 말씀해 주세요";
    try {
      const r = await fetch("/voice/listen", {method: "POST", body: "{}"});
      const j = await r.json();
      if (j.message) document.getElementById("voiceBanner").textContent = "🎙 " + j.message;
      refresh();
    } catch (e) {}
  }

  async function refresh() {
    updateClock();
    try {
      const r = await fetch("/display/state", {cache: "no-store"});
      const s = await r.json();

      // Update LMS Material Skin link dynamically to current host
      document.getElementById("lmsLink").href = "http://" + location.hostname + ":9000/material/";

      // Mic / Always-on indicator
      const dot = document.getElementById("micDot");
      const micText = document.getElementById("micText");
      const ls = s.voice.listening_state || "stopped";
      dot.className = "dot " + (ls === "recording" ? "recording" : ls === "processing" ? "processing" : ls === "waiting" ? "" : "off");
      if (ls === "recording") micText.textContent = "음성 듣는 중...";
      else if (ls === "processing") micText.textContent = "명령 처리 중...";
      else if (s.voice.continuous_listen) micText.textContent = "상시 음성 대기";
      else micText.textContent = "음성 대기 (버튼)";

      // Output badge (USB DAC vs Speaker)
      document.getElementById("outputBadge").textContent = s.audio_output_label || "🔊 오디오";

      // Track info
      const np = s.now_playing || {};
      const hasTrack = Boolean(np.title);
      document.getElementById("idleClock").style.display = hasTrack ? "none" : "block";
      document.getElementById("trackTitle").textContent = hasTrack ? np.title : "재생 중인 곡 없음";
      document.getElementById("trackArtist").textContent = hasTrack ? (np.artist || "Unknown Artist") : '"포코야 ..." 라고 불러보세요';
      document.getElementById("trackAlbum").textContent = hasTrack ? (np.album || "TIDAL") : "USB DAC 연결 시 자동 이어재생";

      const isPlaying = np.mode === "play";
      document.getElementById("playBtn").textContent = isPlaying ? "⏸" : "▶";
      document.getElementById("volText").textContent = "VOL " + (np.volume ?? 0) + "%";

      const elapsed = Number(np.time || 0);
      const duration = Number(np.duration || 0);
      const pct = duration > 0 ? Math.min(100, (elapsed / duration) * 100) : 0;
      document.getElementById("barFill").style.width = pct.toFixed(1) + "%";
      document.getElementById("timeElapsed").textContent = fmtTime(elapsed);
      document.getElementById("timeTotal").textContent = fmtTime(duration);

      // Artwork refresh only when track changes
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

      // Last voice command banner
      if (s.voice.last_result && s.voice.last_result.message) {
        const raw = (s.voice.last_result.intent && s.voice.last_result.intent.raw_text) ? ("“" + s.voice.last_result.intent.raw_text + "” → ") : "";
        document.getElementById("voiceBanner").textContent = "🎙 " + raw + s.voice.last_result.message;
      }
    } catch (e) {}
  }

  refresh();
  setInterval(refresh, 1000);
</script>
</body>
</html>
"""
