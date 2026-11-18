"""Real-time PocoBox Voice, AEC Mic Meter, USB DAC, and LMS Control Dashboard."""

from __future__ import annotations

DASHBOARD_HTML = """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>PocoBox Dashboard</title>
<style>
  :root {
    --bg: #0b0f19;
    --card: #121826;
    --border: rgba(255,255,255,0.1);
    --fg: #f1f5f9;
    --muted: #94a3b8;
    --accent: #38bdf8;
    --ok: #22c55e;
    --warn: #f59e0b;
    --rec: #ef4444;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; padding: 20px;
    background: var(--bg); color: var(--fg);
    font-family: -apple-system, BlinkMacSystemFont, "Noto Sans KR", "Segoe UI", sans-serif;
  }
  .wrap { max-width: 1040px; margin: 0 auto; display: grid; gap: 16px; }
  header {
    display: flex; flex-wrap: wrap; justify-content: space-between; align-items: center; gap: 12px;
  }
  h1 { margin: 0; font-size: 22px; display: flex; align-items: center; gap: 10px; }
  .links { display: flex; gap: 8px; flex-wrap: wrap; }
  .chip {
    display: inline-flex; align-items: center; gap: 6px;
    padding: 7px 12px; border-radius: 999px;
    background: var(--card); border: 1px solid var(--border);
    color: var(--fg); text-decoration: none; font-size: 13px; font-weight: 600;
  }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(310px, 1fr)); gap: 16px; }
  .card {
    background: var(--card); border: 1px solid var(--border);
    border-radius: 16px; padding: 18px;
  }
  .card h2 {
    margin: 0 0 12px 0; font-size: 15px; color: var(--muted);
    display: flex; justify-content: space-between; align-items: center;
  }
  .meter-box { position: relative; height: 28px; border-radius: 999px; background: #1e293b; overflow: hidden; margin: 10px 0 8px 0; }
  .meter-bar { height: 100%; width: 0%; background: linear-gradient(90deg, #22c55e, #38bdf8, #f59e0b, #ef4444); transition: width 0.08s linear; }
  .thresh-line {
    position: absolute; top: 0; bottom: 0; width: 3px; background: #ffffff;
    box-shadow: 0 0 8px #fff; pointer-events: none;
  }
  .kv { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; margin-top: 10px; }
  .stat { background: rgba(255,255,255,0.04); border-radius: 10px; padding: 10px; text-align: center; }
  .stat .label { font-size: 11px; color: var(--muted); }
  .stat .val { font-size: 18px; font-weight: 800; margin-top: 2px; font-variant-numeric: tabular-nums; }
  .row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-top: 10px; }
  input[type="text"] {
    flex: 1; min-width: 180px; padding: 10px 12px; border-radius: 10px;
    border: 1px solid var(--border); background: #0b0f19; color: var(--fg); font-size: 14px;
  }
  input[type="range"] { width: 100%; accent-color: var(--accent); }
  button {
    padding: 9px 14px; border-radius: 10px; border: 1px solid var(--border);
    background: #1e293b; color: var(--fg); font-weight: 700; font-size: 13px; cursor: pointer;
  }
  button.primary { background: var(--accent); color: #0b0f19; border-color: var(--accent); }
  button.danger { background: var(--rec); color: #fff; border-color: var(--rec); }
  button:active { transform: scale(0.97); }
  .np-box { display: flex; gap: 14px; align-items: center; }
  .np-art { width: 76px; height: 76px; border-radius: 12px; object-fit: cover; background: #1e293b; flex-shrink: 0; }
  .np-title { font-size: 18px; font-weight: 800; }
  .np-sub { font-size: 13px; color: var(--muted); margin-top: 3px; }
  .logs {
    height: 240px; overflow-y: auto; font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 12.5px; line-height: 1.5; background: #090d16;
    border: 1px solid var(--border); border-radius: 12px; padding: 12px;
  }
  .log-item { padding: 3px 0; border-bottom: 1px solid rgba(255,255,255,0.05); }
  .badge { padding: 3px 8px; border-radius: 999px; font-size: 11px; font-weight: 700; }
  .b-ok { background: rgba(34,197,94,0.2); color: #86efac; }
  .b-rec { background: rgba(239,68,68,0.25); color: #fca5a5; }
  .b-proc { background: rgba(245,158,11,0.25); color: #fcd34d; }
  .b-muted { background: rgba(148,163,184,0.2); color: #cbd5e1; }
  label.toggle { display: flex; align-items: center; gap: 8px; font-size: 13px; cursor: pointer; margin-top: 8px; }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>🎵 PocoBox Live Dashboard <span id="stateBadge" class="badge b-ok">waiting</span></h1>
    <div class="links">
      <span class="chip" id="outputChip">🔊 출력 확인 중</span>
      <span class="chip" id="dspChip">🧠 DSP 확인 중</span>
      <span class="chip" id="providerChip">Gemini</span>
      <a class="chip" href="/display" target="_blank">📱 키오스크 화면</a>
      <a class="chip" id="lmsLink" href="http://127.0.0.1:9000/material/" target="_blank">🎛 LMS Material</a>
    </div>
  </header>

  <div class="grid">
    <div class="card">
      <h2>🎙 실시간 마이크 & VAD 레벨 <span id="musicFlag" class="badge b-muted">정지</span></h2>
      <div class="meter-box">
        <div id="meterBar" class="meter-bar"></div>
        <div id="threshLine" class="thresh-line" style="left:15%"></div>
      </div>
      <div style="font-size:12px; color:var(--muted)">흰 선이 발화 감지 문턱입니다. 말씀하실 때 막대가 흰 선을 넘으면 녹음이 시작됩니다.</div>
      <div class="kv">
        <div class="stat"><div class="label">현재 마이크 RMS</div><div id="curRms" class="val">0</div></div>
        <div class="stat"><div class="label">배경 소음 RMS</div><div id="ambRms" class="val">0</div></div>
        <div class="stat"><div class="label">감지 문턱값</div><div id="thrVal" class="val">0</div></div>
      </div>
      <div class="row">
        <button class="primary" onclick="triggerListen()">🎙 지금 바로 1회 듣기 (호출어 불필요)</button>
        <button onclick="resetFloor()">배경 기준 리셋</button>
      </div>
    </div>

    <div class="card">
      <h2>⚙️ 실시간 감도 & 호출어 조절</h2>
      <div style="margin-bottom:10px">
        <div style="display:flex; justify-content:space-between; font-size:13px">
          <span>조용할 때 문턱 (quiet)</span><strong id="qVal">220</strong>
        </div>
        <input id="qSlider" type="range" min="40" max="1000" step="10" value="220" oninput="qVal.textContent=this.value" onchange="saveCfg()" />
      </div>
      <div style="margin-bottom:10px">
        <div style="display:flex; justify-content:space-between; font-size:13px">
          <span>음악 재생 중 문턱 (playing)</span><strong id="pVal">280</strong>
        </div>
        <input id="pSlider" type="range" min="40" max="1200" step="10" value="280" oninput="pVal.textContent=this.value" onchange="saveCfg()" />
      </div>
      <div style="margin-bottom:6px">
        <div style="display:flex; justify-content:space-between; font-size:13px">
          <span>배경 대비 배수 (multiplier)</span><strong id="mVal">2.1</strong>
        </div>
        <input id="mSlider" type="range" min="1.3" max="5.0" step="0.1" value="2.1" oninput="mVal.textContent=Number(this.value).toFixed(1)" onchange="saveCfg()" />
      </div>
      <div style="margin-bottom:6px">
        <div style="display:flex; justify-content:space-between; font-size:13px">
          <span>호출 시 자동 볼륨 덕킹 (%)</span><strong id="dVal">18</strong>
        </div>
        <input id="dSlider" type="range" min="5" max="50" step="1" value="18" oninput="dVal.textContent=this.value" onchange="saveCfg()" />
      </div>
      <label class="toggle">
        <input id="duckCheck" type="checkbox" checked onchange="saveCfg()" />
        <span>호출 감지 시 음악 볼륨 자동 낮춤 (Auto-Ducking)</span>
      </label>
      <label class="toggle">
        <input id="wakeCheck" type="checkbox" checked onchange="saveCfg()" />
        <span>상시 대기 시 호출어("포코야") 필수</span>
      </label>
    </div>

    <div class="card">
      <h2>💿 현재 재생 곡 (Now Playing)</h2>
      <div class="np-box">
        <img id="npArt" class="np-art" src="" alt="" />
        <div style="min-width:0">
          <div id="npTitle" class="np-title">재생 중인 곡 없음</div>
          <div id="npArtist" class="np-sub">-</div>
          <div id="npStatus" class="np-sub">stop · VOL 0%</div>
        </div>
      </div>
      <div class="row">
        <button onclick="ctrl('prev')">⏮ 이전</button>
        <button class="primary" onclick="ctrl('toggle')">⏯ 재생/정지</button>
        <button onclick="ctrl('next')">⏭ 다음</button>
        <button onclick="ctrl('vol_down')">🔉 −5</button>
        <button onclick="ctrl('vol_up')">🔊 +5</button>
      </div>
      <div class="row">
        <input id="cmdInput" type="text" placeholder="명령 또는 질문 입력 (예: 아이유 틀어줘 / 지금 몇 시야? / 프랑스 수도는?)" onkeydown="if(event.key==='Enter') sendText()" />
        <button class="primary" onclick="sendText()">실행</button>
      </div>
    </div>
  </div>

  <div class="card">
    <h2>📜 실시간 음성 인식 자막 & 이벤트 로그 <button onclick="clearLogs()" style="padding:4px 10px; font-size:11px">지우기</button></h2>
    <div id="logs" class="logs"></div>
  </div>
</div>

<script>
  document.getElementById("lmsLink").href = "http://" + location.hostname + ":9000/material/";
  let cfgLoaded = false;
  let lastArtKey = "";

  function addLog(tag, text, cls="b-muted") {
    const el = document.getElementById("logs");
    const ts = new Date().toTimeString().slice(0,8);
    const div = document.createElement("div");
    div.className = "log-item";
    div.innerHTML = `<span style="color:#64748b">${ts}</span> <span class="badge ${cls}">${tag}</span> ${text}`;
    el.prepend(div);
    while (el.children.length > 60) el.removeChild(el.lastChild);
  }
  function clearLogs() { document.getElementById("logs").innerHTML = ""; }

  async function pollMetrics() {
    try {
      const r = await fetch("/display/state", {cache: "no-store"});
      const s = await r.json();
      const v = s.voice || {};
      const np = s.now_playing || {};

      document.getElementById("outputChip").textContent = s.audio_output_label || "오디오";
      const dspParts = [];
      if (v.rnnoise_enabled) dspParts.push("RNNoise");
      if (v.bandpass_enabled) dspParts.push("250-3.5kHz");
      if (v.wake_engine === "vosk") dspParts.push("Vosk호출어");
      if (v.duck_enabled) dspParts.push("덕킹 " + (v.duck_volume ?? 18) + "%");
      document.getElementById("dspChip").textContent = "🧠 " + (dspParts.join(" · ") || "기본 VAD");
      document.getElementById("providerChip").textContent = "STT: " + (v.provider || "local");

      const st = v.listening_state || "stopped";
      const sb = document.getElementById("stateBadge");
      sb.textContent = st;
      sb.className = "badge " + (st === "recording" || st === "wake" ? "b-rec" : st === "processing" ? "b-proc" : "b-ok");

      const mf = document.getElementById("musicFlag");
      mf.textContent = v.music_playing ? "음악 재생 중" : "대기/정지";
      mf.className = "badge " + (v.music_playing ? "b-ok" : "b-muted");

      const cur = Number(v.current_rms || 0);
      const amb = Number(v.ambient_rms || 0);
      const thr = Number(v.active_threshold || 30);
      document.getElementById("curRms").textContent = cur.toFixed(1);
      document.getElementById("ambRms").textContent = amb.toFixed(1);
      document.getElementById("thrVal").textContent = thr.toFixed(1);

      const scaleMax = Math.max(250, thr * 2.5, cur * 1.2);
      document.getElementById("meterBar").style.width = Math.min(100, (cur / scaleMax) * 100).toFixed(1) + "%";
      document.getElementById("threshLine").style.left = Math.min(98, (thr / scaleMax) * 100).toFixed(1) + "%";

      if (!cfgLoaded && v.quiet_threshold !== undefined) {
        cfgLoaded = true;
        qSlider.value = v.quiet_threshold; qVal.textContent = v.quiet_threshold;
        pSlider.value = v.playing_threshold; pVal.textContent = v.playing_threshold;
        mSlider.value = v.multiplier; mVal.textContent = Number(v.multiplier).toFixed(1);
        if (v.duck_volume !== undefined) { dSlider.value = v.duck_volume; dVal.textContent = v.duck_volume; }
        if (v.duck_enabled !== undefined) { duckCheck.checked = Boolean(v.duck_enabled); }
        wakeCheck.checked = Boolean(v.require_wake_word);
      }

      document.getElementById("npTitle").textContent = np.title || "재생 중인 곡 없음";
      document.getElementById("npArtist").textContent = [np.artist, np.album].filter(Boolean).join(" — ") || "-";
      document.getElementById("npStatus").textContent = (np.mode || "stop") + " · VOL " + (np.volume ?? 0) + "%";
      const artKey = (np.title || "") + "|" + (np.coverid || "");
      if (np.title && artKey !== lastArtKey) {
        lastArtKey = artKey;
        document.getElementById("npArt").src = "/display/cover?v=" + encodeURIComponent(artKey);
      }
    } catch (_) {}
  }

  async function saveCfg() {
    const body = {
      quiet_threshold: Number(qSlider.value),
      playing_threshold: Number(pSlider.value),
      multiplier: Number(mSlider.value),
      duck_volume: Number(dSlider.value),
      duck_enabled: duckCheck.checked,
      require_wake_word: wakeCheck.checked
    };
    const r = await fetch("/dashboard/config", {
      method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)
    });
    const j = await r.json();
    if (j.ok) addLog("CONFIG", `quiet=${body.quiet_threshold} playing=${body.playing_threshold} mult=${body.multiplier} duck=${body.duck_enabled ? body.duck_volume + '%' : 'off'} wake=${body.require_wake_word}`, "b-ok");
  }

  async function resetFloor() {
    await fetch("/dashboard/config", {
      method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({reset_ambient: true})
    });
    addLog("CONFIG", "배경 소음 기준(ambient_rms)을 리셋했습니다", "b-ok");
  }

  async function ctrl(action) {
    await fetch("/display/control", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({action})});
    setTimeout(pollMetrics, 150);
  }

  async function triggerListen() {
    addLog("LISTEN", "수동 1회 듣기 시작 (호출어 불필요)...", "b-rec");
    const r = await fetch("/voice/listen", {method: "POST", body: "{}"});
    const j = await r.json();
    addLog(j.ok ? "OK" : "ERR", (j.intent && j.intent.raw_text ? `“${j.intent.raw_text}” → ` : "") + (j.message || ""), j.ok ? "b-ok" : "b-rec");
  }

  async function sendText() {
    const inp = document.getElementById("cmdInput");
    const text = inp.value.trim();
    if (!text) return;
    inp.value = "";
    const r = await fetch("/voice/text", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({text})});
    const j = await r.json();
    addLog(j.ok ? "TEXT" : "ERR", `“${text}” → ${j.message}`, j.ok ? "b-ok" : "b-rec");
  }

  function connectSSE() {
    const es = new EventSource("/display/events");
    es.onmessage = (e) => {
      try {
        const ev = JSON.parse(e.data);
        if (ev.type === "state") {
          if (ev.state === "recording") addLog("REC", `음성 감지 시작 (rms=${ev.rms})`, "b-rec");
          else if (ev.state === "processing") addLog("STT", "Gemini 음성 분석 중...", "b-proc");
        } else if (ev.type === "transcript") {
          addLog("자막", `“${ev.text}” (wake=${ev.wake})`, "b-proc");
        } else if (ev.type === "result") {
          if (ev.ignored_no_wake) addLog("무시", ev.text ? `호출어 없음: “${ev.text}”` : "배경 음악/소음 무시됨", "b-muted");
          else addLog(ev.ok ? "실행" : "실패", `${ev.text ? `“${ev.text}” → ` : ""}${ev.message}`, ev.ok ? "b-ok" : "b-rec");
        }
      } catch (_) {}
    };
    es.onerror = () => { es.close(); setTimeout(connectSSE, 2000); };
  }

  connectSSE();
  pollMetrics();
  setInterval(pollMetrics, 200);
</script>
</body>
</html>
"""
