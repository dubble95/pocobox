"""Real-time PocoBox Voice, AEC Mic Meter, USB DAC, and LMS Control Dashboard."""

from __future__ import annotations

DASHBOARD_HTML = r"""<!doctype html>
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
  input[type="text"], select {
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
      <div class="kv" style="margin-top:8px">
        <div class="stat"><div class="label">Raw 마이크 피크</div><div id="rawPeak" class="val">0</div></div>
        <div class="stat"><div class="label">AEC3 에코 감쇠</div><div id="aecDb" class="val">-</div></div>
        <div class="stat"><div class="label">클리핑 상태</div><div id="clipState" class="val" style="font-size:14px">정상</div></div>
      </div>
      <div class="row">
        <button class="primary" onclick="triggerListen()">🎙 지금 바로 1회 듣기 (호출어 불필요)</button>
        <button onclick="resetFloor()">배경 기준 리셋</button>
      </div>
    </div>

    <div class="card">
      <h2>⚙️ 실시간 감도 & 호출어 조절 <button onclick="resetCfg()" style="padding:4px 10px; font-size:11px">기본값 리셋</button></h2>
      <div style="margin-bottom:10px">
        <div style="display:flex; justify-content:space-between; font-size:13px">
          <span>조용할 때 문턱 (quiet)</span><strong id="qVal">40</strong>
        </div>
        <input id="qSlider" type="range" min="10" max="500" step="10" value="40" oninput="qVal.textContent=this.value" onchange="saveCfg()" />
      </div>
      <div style="margin-bottom:10px">
        <div style="display:flex; justify-content:space-between; font-size:13px">
          <span>음악 재생 중 문턱 (playing)</span><strong id="pVal">180</strong>
        </div>
        <input id="pSlider" type="range" min="20" max="600" step="10" value="180" oninput="pVal.textContent=this.value" onchange="saveCfg()" />
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
      <label class="toggle">
        <input id="musicWakeOnlyCheck" type="checkbox" checked onchange="saveCfg()" />
        <span>음악 재생 중 RMS 오발동 차단 (로컬 호출어 전용)</span>
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

    <div class="card">
      <h2>🔊 내장 스피커 & 목소리 설정 <button onclick="resetSpeakerCfg()" style="padding:4px 10px; font-size:11px">기본값 리셋</button></h2>
      <div style="margin-bottom:10px">
        <div style="display:flex; justify-content:space-between; font-size:13px">
          <span>내장 스피커 음량 (%)</span><strong id="spkVolVal">100%</strong>
        </div>
        <input id="spkVolSlider" type="range" min="0" max="150" step="5" value="100" oninput="spkVolVal.textContent=this.value+'%'" onchange="saveSpeakerCfg()" />
      </div>
      <div style="margin-bottom:10px">
        <div style="display:flex; justify-content:space-between; font-size:13px">
          <span>호출 차임음 크기 (%)</span><strong id="chimeVolVal">25%</strong>
        </div>
        <input id="chimeVolSlider" type="range" min="5" max="100" step="5" value="25" oninput="chimeVolVal.textContent=this.value+'%'" onchange="saveSpeakerCfg()" />
      </div>
      <div style="margin-bottom:10px">
        <div style="font-size:13px; margin-bottom:4px">대답 목소리 (TTS)</div>
        <select id="voiceSelect" style="width:100%" onchange="saveSpeakerCfg()">
          <option value="ko-KR-SunHiNeural">선희 (한국어 여성 · 추천)</option>
          <option value="ko-KR-InJoonNeural">인준 (한국어 남성 · 차분함)</option>
          <option value="ko-KR-HyunsuMultilingualNeural">현수 (한/영 다국어 남성)</option>
          <option value="en-US-JennyNeural">Jenny (English Female)</option>
          <option value="en-US-GuyNeural">Guy (English Male)</option>
          <option value="google">Google 기본 TTS (한국어 여성)</option>
        </select>
      </div>
      <div style="margin-bottom:8px">
        <div style="display:flex; justify-content:space-between; font-size:13px">
          <span>말하기 속도</span><strong id="rateVal">+0%</strong>
        </div>
        <input id="rateSlider" type="range" min="-30" max="30" step="5" value="0" oninput="rateVal.textContent=(Number(this.value)>=0?'+':'')+this.value+'%'" onchange="saveSpeakerCfg()" />
      </div>
      <div style="margin-bottom:8px">
        <div style="display:flex; justify-content:space-between; font-size:13px">
          <span>목소리 음높이 (Pitch)</span><strong id="pitchVal">+0Hz</strong>
        </div>
        <input id="pitchSlider" type="range" min="-15" max="15" step="1" value="0" oninput="pitchVal.textContent=(Number(this.value)>=0?'+':'')+this.value+'Hz'" onchange="saveSpeakerCfg()" />
      </div>
      <label class="toggle">
        <input id="chimeCheck" type="checkbox" checked onchange="saveSpeakerCfg()" />
        <span>호출 응답 차임음 켜기</span>
      </label>
      <label class="toggle">
        <input id="ttsCheck" type="checkbox" checked onchange="saveSpeakerCfg()" />
        <span>질문·곡 안내 목소리 대답(TTS) 켜기</span>
      </label>
      <label class="toggle">
        <input id="cmdAckCheck" type="checkbox" checked onchange="saveSpeakerCfg()" />
        <span>명령 실행 전 음성 응답 켜기</span>
      </label>
      <div class="row">
        <button class="primary" onclick="testSpeaker()">🔊 내장 스피커 목소리 테스트</button>
      </div>
    </div>
  </div>

  <div class="card">
    <h2>🎧 어린이 영어 리스닝 퀴즈 <span id="quizBadge" class="badge b-muted">대기 중</span></h2>
    <div class="row" style="margin-top:0">
      <select id="quizTopic" style="min-width:140px">
        <option value="all">종합 듣기 퀴즈 (2~3문장)</option>
        <option value="animals">동물</option>
        <option value="food">음식과 과일</option>
        <option value="colors_numbers">자연·계절·숫자</option>
        <option value="family_school">장소·학교·직업</option>
        <option value="daily">일상 스토리</option>
        <option value="riddles">사물 수수께끼</option>
      </select>
      <select id="quizCount" style="min-width:95px; flex:0">
        <option value="3" selected>3문제</option>
        <option value="5">5문제</option>
        <option value="7">7문제</option>
        <option value="10">10문제</option>
      </select>
      <button class="primary" onclick="startQuiz(true)">🎙 음성 대화 시작</button>
      <button onclick="startQuiz(false)">💬 텍스트 시작</button>
      <button class="danger" onclick="stopQuiz()">종료</button>
    </div>
    <div class="kv">
      <div class="stat"><div class="label">진행</div><div id="quizProg" class="val">-</div></div>
      <div class="stat"><div class="label">정답 점수</div><div id="quizScore" class="val" style="color:var(--ok)">0</div></div>
      <div class="stat"><div class="label">연속 정답</div><div id="quizStreak" class="val" style="color:var(--accent)">0</div></div>
    </div>
    <div id="quizStatusText" style="margin-top:8px; font-size:12.5px; color:var(--muted)">“포코야 영어 퀴즈 시작해줘”라고 말하거나 위 버튼으로 시작하세요.</div>
    <div class="row">
      <input id="quizAnsInput" type="text" placeholder="퀴즈 답변 또는 명령 입력 (예: 코끼리 / 다시 들려줘 / 힌트 / 패스 / 그만)" onkeydown="if(event.key==='Enter') sendQuizAnswer()" />
      <button class="primary" onclick="sendQuizAnswer()">답변</button>
      <button onclick="sendQuizQuick('다시 들려줘')">🔁 다시</button>
      <button onclick="sendQuizQuick('힌트')">💡 힌트</button>
      <button onclick="sendQuizQuick('패스')">⏭ 패스</button>
    </div>
  </div>

  <div class="card">
    <h2>📊 호출어 정밀도 & 오탐 통계 (최근 24시간) <button onclick="pollTelemetry()" style="padding:4px 10px; font-size:11px">새로고침</button></h2>
    <div class="kv" style="grid-template-columns: repeat(4, 1fr)">
      <div class="stat"><div class="label">정탐 (TP)</div><div id="statTp" class="val" style="color:var(--ok)">0</div></div>
      <div class="stat"><div class="label">오탐 (FP)</div><div id="statFp" class="val" style="color:var(--rec)">0</div></div>
      <div class="stat"><div class="label">게이트 차단</div><div id="statRej" class="val" style="color:var(--accent)">0</div></div>
      <div class="stat"><div class="label">정밀도</div><div id="statPrec" class="val">-</div></div>
    </div>
    <div id="statRec" style="margin-top:10px; font-size:12.5px; color:var(--muted)">통계 데이터 수집 대기 중...</div>
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
      const qz = s.quiz || {};
      const qb = document.getElementById("quizBadge");
      if (qb) {
        qb.textContent = qz.active ? ("진행 중 · " + (qz.topic_label || "기초")) : "대기 중";
        qb.className = "badge " + (qz.active ? "b-ok" : "b-muted");
        document.getElementById("quizProg").textContent = qz.total ? ((qz.question_num || 0) + " / " + qz.total) : "-";
        document.getElementById("quizScore").textContent = qz.score ?? 0;
        document.getElementById("quizStreak").textContent = qz.streak ?? 0;
        if (qz.last_feedback) document.getElementById("quizStatusText").textContent = qz.last_feedback;
      }

      document.getElementById("outputChip").textContent = s.audio_output_label || "오디오";
      const dspParts = [];
      if (v.aec3_enabled) {
        if (v.aec3_active) dspParts.push("AEC3 -" + Number(v.aec3_reduction_db || 0).toFixed(1) + "dB");
        else dspParts.push(v.vis_connected ? "AEC3 대기" : "AEC3(VIS대기)");
      }
      if (v.rnnoise_enabled) dspParts.push("RNNoise");
      if (v.bandpass_enabled) dspParts.push(v.bandpass_range || "150-6000Hz");
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
      document.getElementById("rawPeak").textContent = Number(v.raw_peak || 0);
      document.getElementById("aecDb").textContent = v.aec3_active ? ("-" + Number(v.aec3_reduction_db || 0).toFixed(1) + "dB") : (v.aec3_enabled ? "대기" : "OFF");
      const clipEl = document.getElementById("clipState");
      if (v.clip_warning) {
        clipEl.textContent = "⚠️ 포화(CLIP)";
        clipEl.style.color = "#ef4444";
      } else {
        clipEl.textContent = "정상";
        clipEl.style.color = "#22c55e";
      }

      const scaleMax = Math.max(250, thr * 2.5, cur * 1.2);
      document.getElementById("meterBar").style.width = Math.min(100, (cur / scaleMax) * 100).toFixed(1) + "%";
      document.getElementById("threshLine").style.left = Math.min(98, (thr / scaleMax) * 100).toFixed(1) + "%";

      if (!cfgLoaded && v.quiet_threshold !== undefined) {
        cfgLoaded = true;
        applyVoiceCfgToUI(v);
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

  function applyVoiceCfgToUI(v) {
    if (!v) return;
    if (v.quiet_threshold !== undefined) { qSlider.value = v.quiet_threshold; qVal.textContent = v.quiet_threshold; }
    if (v.playing_threshold !== undefined) { pSlider.value = v.playing_threshold; pVal.textContent = v.playing_threshold; }
    if (v.multiplier !== undefined) { mSlider.value = v.multiplier; mVal.textContent = Number(v.multiplier).toFixed(1); }
    if (v.duck_volume !== undefined) { dSlider.value = v.duck_volume; dVal.textContent = v.duck_volume; }
    if (v.duck_enabled !== undefined) { duckCheck.checked = Boolean(v.duck_enabled); }
    if (v.require_wake_word !== undefined) { wakeCheck.checked = Boolean(v.require_wake_word); }
    if (v.rms_fallback_during_music !== undefined) {
      musicWakeOnlyCheck.checked = !Boolean(v.rms_fallback_during_music);
    }
    if (v.speaker_volume !== undefined) {
      spkVolSlider.value = v.speaker_volume; spkVolVal.textContent = v.speaker_volume + "%";
    }
    if (v.chime_volume_pct !== undefined) {
      chimeVolSlider.value = v.chime_volume_pct; chimeVolVal.textContent = v.chime_volume_pct + "%";
    }
    if (v.available_voices && Array.isArray(v.available_voices) && voiceSelect.dataset.populated !== "1") {
      voiceSelect.innerHTML = "";
      for (const item of v.available_voices) {
        const opt = document.createElement("option");
        opt.value = item.id;
        opt.textContent = item.label;
        voiceSelect.appendChild(opt);
      }
      voiceSelect.dataset.populated = "1";
    }
    if (v.tts_voice) { voiceSelect.value = v.tts_voice; }
    if (v.tts_rate_pct !== undefined) {
      const rv = Number(v.tts_rate_pct);
      rateSlider.value = rv; rateVal.textContent = (rv >= 0 ? "+" : "") + rv + "%";
    }
    if (v.tts_pitch_hz !== undefined) {
      const pv = Number(v.tts_pitch_hz);
      pitchSlider.value = pv; pitchVal.textContent = (pv >= 0 ? "+" : "") + pv + "Hz";
    }
    if (v.enable_chime !== undefined) { chimeCheck.checked = Boolean(v.enable_chime); }
    if (v.enable_tts !== undefined) { ttsCheck.checked = Boolean(v.enable_tts); }
    if (v.enable_command_ack !== undefined) { cmdAckCheck.checked = Boolean(v.enable_command_ack); }
  }

  async function saveSpeakerCfg() {
    const body = {
      speaker_volume: Number(spkVolSlider.value),
      chime_volume_pct: Number(chimeVolSlider.value),
      tts_voice: voiceSelect.value,
      tts_rate_pct: Number(rateSlider.value),
      tts_pitch_hz: Number(pitchSlider.value),
      enable_chime: chimeCheck.checked,
      enable_tts: ttsCheck.checked,
      enable_command_ack: cmdAckCheck.checked
    };
    const r = await fetch("/dashboard/config", {
      method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)
    });
    const j = await r.json();
    if (j.ok) addLog("SPEAKER", `내장스피커=${body.speaker_volume}% 차임=${body.enable_chime ? body.chime_volume_pct + '%' : 'off'} 목소리=${body.tts_voice} 속도=${body.tts_rate_pct}% 음높이=${body.tts_pitch_hz}Hz`, "b-ok");
  }

  async function resetSpeakerCfg() {
    const r = await fetch("/dashboard/config", {
      method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({reset_speaker_defaults: true})
    });
    const j = await r.json();
    if (j.ok && j.voice) {
      applyVoiceCfgToUI(j.voice);
      addLog("SPEAKER", `내장 스피커·목소리 설정을 기본값으로 리셋했습니다 (음량 ${j.voice.speaker_volume}%, ${j.voice.tts_voice})`, "b-ok");
    }
  }

  async function testSpeaker() {
    addLog("SPEAKER", "내장 스피커 차임음·목소리 테스트 재생 중...", "b-proc");
    const r = await fetch("/voice/test_speaker", {method: "POST", headers: {"Content-Type": "application/json"}, body: "{}"});
    const j = await r.json();
    addLog(j.ok ? "SPEAKER" : "ERR", `테스트 완료 (${j.voice}, 음량 ${j.speaker_volume}%)`, j.ok ? "b-ok" : "b-rec");
  }

  async function saveCfg() {
    const body = {
      quiet_threshold: Number(qSlider.value),
      playing_threshold: Number(pSlider.value),
      multiplier: Number(mSlider.value),
      duck_volume: Number(dSlider.value),
      duck_enabled: duckCheck.checked,
      require_wake_word: wakeCheck.checked,
      rms_fallback_during_music: !musicWakeOnlyCheck.checked
    };
    const r = await fetch("/dashboard/config", {
      method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)
    });
    const j = await r.json();
    if (j.ok) addLog("CONFIG", `quiet=${body.quiet_threshold} playing=${body.playing_threshold} mult=${body.multiplier} duck=${body.duck_enabled ? body.duck_volume + '%' : 'off'} wake=${body.require_wake_word}`, "b-ok");
  }

  async function resetCfg() {
    const r = await fetch("/dashboard/config", {
      method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({reset_defaults: true})
    });
    const j = await r.json();
    if (j.ok && j.voice) {
      applyVoiceCfgToUI(j.voice);
      addLog("CONFIG", `감도·호출어 설정을 기본값으로 리셋했습니다 (quiet=${j.voice.quiet_threshold} playing=${j.voice.playing_threshold} mult=${j.voice.multiplier} duck=${j.voice.duck_volume}%)`, "b-ok");
    }
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

  async function startQuiz(voiceLoop) {
    const topic = document.getElementById("quizTopic").value;
    const count = Number(document.getElementById("quizCount").value || 3);
    const r = await fetch("/quiz/start", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({topic, count, voice_loop: Boolean(voiceLoop)})
    });
    const j = await r.json();
    addLog(j.ok ? "QUIZ" : "ERR", j.message || "영어 퀴즈 시작", j.ok ? "b-ok" : "b-rec");
  }

  async function stopQuiz() {
    const r = await fetch("/quiz/stop", {method: "POST", headers: {"Content-Type": "application/json"}, body: "{}"});
    const j = await r.json();
    addLog("QUIZ", j.message || "영어 퀴즈 종료", "b-muted");
  }

  async function sendQuizQuick(text) {
    const r = await fetch("/quiz/answer", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({text})
    });
    const j = await r.json();
    addLog(j.ok ? "QUIZ" : "ERR", `“${text}” → ${j.message}`, j.ok ? "b-ok" : "b-rec");
  }

  async function sendQuizAnswer() {
    const inp = document.getElementById("quizAnsInput");
    const text = inp.value.trim();
    if (!text) return;
    inp.value = "";
    await sendQuizQuick(text);
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
        } else if (ev.type === "ack") {
          addLog("응답", `${ev.text}`, "b-ok");
        } else if (ev.type === "quiz") {
          addLog("QUIZ", `${ev.header || "영어 퀴즈"}: ${(ev.text || "").replace(/\n+/g, " / ")}`, ev.event === "correct" || ev.event === "finished" ? "b-ok" : "b-proc");
        } else if (ev.type === "result") {
          if (ev.ignored_no_wake) addLog("무시", ev.text ? `호출어 없음: “${ev.text}”` : "배경 음악/소음 무시됨", "b-muted");
          else addLog(ev.ok ? "실행" : "실패", `${ev.text ? `“${ev.text}” → ` : ""}${ev.message}`, ev.ok ? "b-ok" : "b-rec");
        }
      } catch (_) {}
    };
    es.onerror = () => { es.close(); setTimeout(connectSSE, 2000); };
  }

  async function pollTelemetry() {
    try {
      const r = await fetch("/telemetry/stats?hours=24", {cache: "no-store"});
      const t = await r.json();
      document.getElementById("statTp").textContent = t.wake_true_positives ?? 0;
      document.getElementById("statFp").textContent = t.wake_false_alarms ?? 0;
      document.getElementById("statRej").textContent = t.wake_gates_rejected ?? 0;
      document.getElementById("statPrec").textContent =
        t.wake_precision !== null && t.wake_precision !== undefined
          ? (Number(t.wake_precision) * 100).toFixed(1) + "%"
          : "-";
      const recs = (t.recommendations || []).join(" · ");
      document.getElementById("statRec").textContent = recs || "특이 사항 없음";
    } catch (_) {}
  }

  connectSSE();
  pollMetrics();
  pollTelemetry();
  setInterval(pollMetrics, 200);
  setInterval(pollTelemetry, 10000);
</script>
</body>
</html>
"""
