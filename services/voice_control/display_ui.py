"""Always-on Fullscreen Now Playing Kiosk HTML/CSS/JS UI with Portrait/Landscape Rotation & Live SSE Subtitles."""

from __future__ import annotations

import json

UI_VERSION = "12"

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
    cursor: pointer;
    touch-action: none;
    user-select: none;
    -webkit-user-select: none;
    transition: transform 0.12s ease;
  }
  .art-wrap:active { transform: scale(0.97); }
  .art-wrap img { width: 100%; height: 100%; object-fit: cover; display: block; }
  .art-placeholder { font-size: clamp(56px, 12vmin, 96px); opacity: 0.35; }
  .art-tap-badge {
    position: absolute; inset: 0;
    display: flex; align-items: center; justify-content: center;
    font-size: clamp(52px, 12vmin, 82px);
    color: #ffffff;
    background: rgba(11, 13, 18, 0.52);
    border-radius: inherit;
    opacity: 0; pointer-events: none;
    transform: scale(0.68);
    transition: opacity 0.22s cubic-bezier(0.16, 1, 0.3, 1), transform 0.22s cubic-bezier(0.16, 1, 0.3, 1);
    backdrop-filter: blur(8px);
    z-index: 5;
  }
  .art-tap-badge.show { opacity: 1; transform: scale(1); }

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
    width: clamp(48px, 12vw, 58px); height: clamp(48px, 12vw, 58px);
    font-size: clamp(18px, 4.4vw, 22px); font-weight: 700; cursor: pointer;
    display: inline-flex; align-items: center; justify-content: center;
    backdrop-filter: blur(12px);
    transition: transform 0.12s ease, background 0.15s ease;
    flex-shrink: 0;
    touch-action: manipulation;
    position: relative;
    z-index: 1;
  }
  /* Expand vertical touch area without overlapping neighboring buttons horizontally */
  .btn::after {
    content: "";
    position: absolute;
    top: -12px; bottom: -12px; left: 0; right: 0;
    border-radius: 50%;
  }
  .btn:active { transform: scale(0.92); background: rgba(255, 255, 255, 0.22); }
  .btn.primary {
    width: clamp(62px, 15.5vw, 78px); height: clamp(62px, 15.5vw, 78px);
    font-size: clamp(24px, 5.8vw, 30px);
    background: #ffffff; color: #0b0d12; border-color: #ffffff;
    margin: 0 clamp(8px, 1.8vw, 16px);
    z-index: 5;
  }
  .btn.primary::after {
    top: -10px; bottom: -10px; left: -6px; right: -6px;
  }
  .btn.mic {
    width: clamp(52px, 13vw, 64px);
    height: clamp(52px, 13vw, 64px);
    font-size: clamp(21px, 5.2vw, 27px);
    background: rgba(56, 189, 248, 0.22); border-color: rgba(56, 189, 248, 0.55);
    margin-left: clamp(4px, 1.2vw, 12px);
    z-index: 4;
  }
  .btn.mic::after {
    top: -14px; bottom: -14px; left: -8px; right: -24px;
    border-radius: 50%;
  }
  .btn.mic.active {
    background: rgba(239, 68, 68, 0.42); border-color: rgba(239, 68, 68, 0.95);
    box-shadow: 0 0 22px rgba(239, 68, 68, 0.65);
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
      width: clamp(46px, 11dvh, 58px);
      height: clamp(46px, 11dvh, 58px);
      font-size: clamp(17px, 4.2dvh, 22px);
    }
    body:not(.layout-portrait) .btn.primary {
      width: clamp(60px, 15dvh, 76px);
      height: clamp(60px, 15dvh, 76px);
      font-size: clamp(23px, 5.8dvh, 30px);
      margin: 0 clamp(8px, 1.5vw, 16px);
    }
    body:not(.layout-portrait) .btn.mic {
      width: clamp(52px, 13dvh, 66px);
      height: clamp(52px, 13dvh, 66px);
      font-size: clamp(21px, 5.2dvh, 27px);
      margin-left: clamp(4px, 1.2vw, 12px);
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

  /* Interactive touch ripple feedback effect */
  .touch-ripple {
    position: fixed;
    width: 68px;
    height: 68px;
    margin-left: -34px;
    margin-top: -34px;
    border-radius: 50%;
    pointer-events: none;
    z-index: 9999;
    will-change: transform, opacity;
    background: radial-gradient(circle, rgba(255, 255, 255, 0.75) 0%, rgba(56, 189, 248, 0.42) 45%, rgba(56, 189, 248, 0) 75%);
    border: 1.5px solid rgba(125, 211, 252, 0.7);
    box-shadow: 0 0 16px rgba(56, 189, 248, 0.5);
    animation: touchRippleAnim 0.42s cubic-bezier(0.12, 0.8, 0.32, 1) forwards;
  }
  @keyframes touchRippleAnim {
    0% {
      opacity: 0.95;
      transform: scale(0.25);
    }
    100% {
      opacity: 0;
      transform: scale(2.2);
    }
  }

  /* Tactile touch-active visual feedback for buttons and interactive elements */
  .btn.touch-active, .btn:active {
    transform: scale(0.90) !important;
    background: rgba(255, 255, 255, 0.26) !important;
    box-shadow: 0 0 14px rgba(56, 189, 248, 0.45);
  }
  .btn.primary.touch-active, .btn.primary:active {
    transform: scale(0.92) !important;
    background: #e2e8f0 !important;
    box-shadow: 0 0 22px rgba(255, 255, 255, 0.65);
  }
  .btn.mic.touch-active, .btn.mic:active {
    transform: scale(0.92) !important;
    background: rgba(239, 68, 68, 0.48) !important;
    box-shadow: 0 0 24px rgba(239, 68, 68, 0.75);
  }
  .art-wrap.touch-active {
    transform: scale(0.985);
    transition: transform 0.12s ease;
  }

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

  /* Fullscreen YouTube & K-Pop MV Overlay */
  .yt-overlay {
    position: fixed; inset: 0; z-index: 100;
    background: #000000;
    display: flex; flex-direction: column; align-items: center; justify-content: center;
    overflow: hidden;
    user-select: none; -webkit-user-select: none;
  }
  .yt-header-bar {
    position: absolute; top: 0; left: 0; right: 0; z-index: 110;
    display: flex; justify-content: space-between; align-items: center;
    padding: max(12px, env(safe-area-inset-top)) max(18px, env(safe-area-inset-right)) 14px max(18px, env(safe-area-inset-left));
    background: linear-gradient(180deg, rgba(0, 0, 0, 0.88) 0%, rgba(0, 0, 0, 0) 100%);
    transition: opacity 0.3s cubic-bezier(0.16, 1, 0.3, 1), transform 0.3s cubic-bezier(0.16, 1, 0.3, 1);
  }
  .yt-title-group {
    display: flex; align-items: center; gap: 12px; min-width: 0;
  }
  .yt-badge {
    font-size: 11px; font-weight: 800; padding: 4px 10px; border-radius: 999px;
    background: #ef4444; color: #ffffff; letter-spacing: 0.05em; white-space: nowrap;
    box-shadow: 0 2px 8px rgba(239, 68, 68, 0.5);
  }
  .yt-meta-col {
    display: flex; flex-direction: column; min-width: 0;
  }
  .yt-main-title {
    font-size: clamp(14px, 3.2vw, 19px); font-weight: 800; color: #ffffff;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: clamp(200px, 45vw, 480px);
  }
  .yt-sub-title {
    font-size: clamp(11px, 2.2vw, 13px); font-weight: 600; color: rgba(255, 255, 255, 0.72);
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: clamp(200px, 45vw, 480px);
  }
  .yt-actions {
    display: flex; align-items: center; gap: 8px; flex-shrink: 0;
  }
  .yt-category-tabs {
    display: flex; align-items: center; gap: 6px;
  }
  .yt-tab-pill {
    background: rgba(255, 255, 255, 0.12); color: rgba(255, 255, 255, 0.88);
    border: 1px solid rgba(255, 255, 255, 0.16); padding: 5px 12px; border-radius: 999px;
    font-size: 12px; font-weight: 700; cursor: pointer; transition: background 0.15s ease;
  }
  .yt-tab-pill.active {
    background: var(--accent); color: #0b0d12; border-color: var(--accent);
  }
  .yt-close-btn {
    background: rgba(255, 255, 255, 0.16); color: #ffffff;
    border: 1px solid rgba(255, 255, 255, 0.22); padding: 6px 14px; border-radius: 999px;
    font-size: 13px; font-weight: 800; cursor: pointer; display: flex; align-items: center; justify-content: center;
    transition: background 0.15s ease, transform 0.12s ease; min-height: 34px;
  }
  .yt-close-btn:active { transform: scale(0.95); }
  .yt-stage {
    position: relative; width: 100%; height: 100%;
    display: flex; align-items: center; justify-content: center;
    cursor: pointer;
  }
  .yt-video-wrap {
    width: 100%; height: 100%; max-width: 100vw; max-height: 100vh;
    aspect-ratio: 16 / 9; display: flex; align-items: center; justify-content: center;
    background: #000000;
  }
  .yt-video-wrap iframe, #ytPlayer {
    width: 100% !important; height: 100% !important; border: 0 !important;
  }
  .yt-tap-badge {
    position: absolute; inset: 0; margin: auto;
    width: 72px; height: 72px; border-radius: 50%;
    display: flex; align-items: center; justify-content: center;
    font-size: 32px; color: #ffffff; background: rgba(0, 0, 0, 0.65);
    backdrop-filter: blur(8px); opacity: 0; pointer-events: none;
    transform: scale(0.68);
    transition: opacity 0.2s cubic-bezier(0.16, 1, 0.3, 1), transform 0.2s cubic-bezier(0.16, 1, 0.3, 1);
    z-index: 105;
  }
  .yt-tap-badge.show { opacity: 1; transform: scale(1); }
  .yt-bottom-bar {
    position: absolute; bottom: 0; left: 0; right: 0; z-index: 110;
    display: flex; justify-content: space-between; align-items: center;
    padding: 16px max(24px, env(safe-area-inset-right)) max(18px, env(safe-area-inset-bottom)) max(24px, env(safe-area-inset-left));
    background: linear-gradient(0deg, rgba(0, 0, 0, 0.9) 0%, rgba(0, 0, 0, 0) 100%);
    transition: opacity 0.3s cubic-bezier(0.16, 1, 0.3, 1), transform 0.3s cubic-bezier(0.16, 1, 0.3, 1);
  }
  .yt-btn-group {
    display: flex; align-items: center; gap: 14px;
  }
  .yt-btn {
    width: 48px; height: 48px; border-radius: 50%;
    background: rgba(255, 255, 255, 0.16); border: 1px solid rgba(255, 255, 255, 0.22);
    color: #ffffff; font-size: 20px; display: flex; align-items: center; justify-content: center;
    cursor: pointer; transition: transform 0.12s ease, background 0.15s ease;
  }
  .yt-btn:active { transform: scale(0.92); }
  .yt-btn.primary {
    width: 58px; height: 58px; font-size: 26px; background: #ffffff; color: #0b0d12;
    border: none; box-shadow: 0 4px 16px rgba(255, 255, 255, 0.35);
  }
  .yt-queue-badge {
    font-size: 13px; font-weight: 700; color: rgba(255, 255, 255, 0.85);
    background: rgba(255, 255, 255, 0.12); border: 1px solid rgba(255, 255, 255, 0.16);
    padding: 6px 14px; border-radius: 999px;
  }
  .yt-ui-hidden .yt-header-bar { opacity: 0; transform: translateY(-12px); pointer-events: none; }
  .yt-ui-hidden .yt-bottom-bar { opacity: 0; transform: translateY(12px); pointer-events: none; }
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
      <button class="hud-btn" id="ytMvBtn" onclick="startKpopMv('all')" title="K-POP 뮤직비디오">📺 K-POP MV</button>
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

  <div id="ytOverlay" class="yt-overlay" aria-hidden="true" style="display: none;">
    <div class="yt-header-bar" id="ytHeaderBar">
      <div class="yt-title-group">
        <span class="yt-badge" id="ytBadgeText">📺 K-POP MV</span>
        <div class="yt-meta-col">
          <div class="yt-main-title" id="ytMainTitle">K-POP 뮤직비디오</div>
          <div class="yt-sub-title" id="ytSubTitle">연속 재생 중</div>
        </div>
      </div>
      <div class="yt-actions">
        <div class="yt-category-tabs" id="ytCategoryTabs">
          <button class="yt-tab-pill active" id="ytTabAll" onclick="loadMvCategory('all')">전체</button>
          <button class="yt-tab-pill" id="ytTabGirl" onclick="loadMvCategory('girlgroup')">걸그룹</button>
          <button class="yt-tab-pill" id="ytTabBoy" onclick="loadMvCategory('boygroup')">보이그룹</button>
          <button class="yt-tab-pill" id="ytTabSolo" onclick="loadMvCategory('solo')">솔로</button>
          <button class="yt-tab-pill" id="ytTabClassic" onclick="loadMvCategory('classic')">클래식</button>
        </div>
        <button class="yt-close-btn" onclick="closeYouTube(event)" aria-label="Close YouTube">✕ 닫기</button>
      </div>
    </div>

    <div class="yt-stage" id="ytStage" onclick="handleYtStageClick(event)">
      <div class="yt-video-wrap" id="ytVideoWrap">
        <div id="ytPlayer"></div>
      </div>
      <div id="ytTapBadge" class="yt-tap-badge">▶</div>
    </div>

    <div class="yt-bottom-bar" id="ytBottomBar">
      <div class="yt-btn-group">
        <button class="yt-btn" onclick="ytPrev(event)" aria-label="이전 영상">⏮</button>
        <button class="yt-btn primary" id="ytPlayBtn" onclick="ytToggle(event)" aria-label="재생/일시정지">⏸</button>
        <button class="yt-btn" onclick="ytNext(event)" aria-label="다음 영상">⏭</button>
      </div>
      <div class="yt-queue-badge" id="ytQueueBadge">1 / 40</div>
    </div>
  </div>

  <div class="app" id="appRoot">
    <div class="stage">
      <div class="art-wrap" id="artWrap" onclick="handleCoverClick(event)" title="재생 / 일시정지 (좌우 탭 또는 스와이프: 이전곡/다음곡)">
        <img id="coverImg" alt="" style="display:none" />
        <div id="artPlaceholder" class="art-placeholder">🎵</div>
        <div id="artTapBadge" class="art-tap-badge">▶</div>
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
          <button class="btn mic" id="micBtn" onclick="triggerMic(event)" aria-label="Voice">🎙</button>
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

  /* YouTube & Continuous K-Pop MV Player State */
  let ytPlayer = null;
  let ytApiLoaded = false;
  let ytActive = false;
  let ytQueue = [];
  let ytIndex = 0;
  let ytControlsTimer = null;
  let ytCategory = "all";

  function loadYouTubeIframeApi() {
    if (window.YT && window.YT.Player) {
      ytApiLoaded = true;
      return;
    }
    if (document.getElementById("ytApiScript")) return;
    const tag = document.createElement("script");
    tag.id = "ytApiScript";
    tag.src = "https://www.youtube.com/iframe_api";
    document.head.appendChild(tag);
  }

  window.onYouTubeIframeAPIReady = function() {
    ytApiLoaded = true;
    if (ytQueue.length > 0 && ytActive && !ytPlayer) {
      const vid = ytQueue[ytIndex] ? ytQueue[ytIndex].id : "";
      if (vid) initYtPlayer(vid);
    }
  };

  function initYtPlayer(videoId) {
    if (ytPlayer && ytPlayer.loadVideoById) {
      ytPlayer.loadVideoById(videoId);
      return;
    }
    ytPlayer = new YT.Player("ytPlayer", {
      videoId: videoId,
      playerVars: {
        autoplay: 1,
        controls: 1,
        rel: 0,
        modestbranding: 1,
        playsinline: 1,
        enablejsapi: 1,
        fs: 0,
        origin: location.origin
      },
      events: {
        onReady: onYtReady,
        onStateChange: onYtStateChange,
        onError: onYtError
      }
    });
  }

  function onYtReady(event) {
    try {
      if (event.target.unMute) event.target.unMute();
      if (event.target.setVolume) event.target.setVolume(100);
      event.target.playVideo();
    } catch (_) {}
    updateYtUi();
    fetch("/youtube/control", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: "ensure_dac" })
    }).catch(() => {});
  }

  function onYtStateChange(event) {
    const playBtn = document.getElementById("ytPlayBtn");
    if (event.data === (window.YT ? YT.PlayerState.ENDED : 0)) {
      // Continuous autoplay next video when current video ends!
      ytNext(null, true);
    } else if (event.data === (window.YT ? YT.PlayerState.PLAYING : 1)) {
      try {
        if (event.target.unMute) event.target.unMute();
        if (event.target.setVolume) event.target.setVolume(100);
      } catch (_) {}
      fetch("/youtube/control", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "ensure_dac" })
      }).catch(() => {});
      if (playBtn) playBtn.textContent = "⏸";
      updateYtUi();
    } else if (event.data === (window.YT ? YT.PlayerState.PAUSED : 2)) {
      if (playBtn) playBtn.textContent = "▶";
    }
  }

  function onYtError() {
    // If embedding is disallowed or error occurs, skip to next video automatically
    showSubtitle("info", "YouTube", "다음 음악 영상으로 이동합니다.", "자동 건너뛰기", 2200);
    setTimeout(() => ytNext(null, true), 800);
  }

  async function startKpopMv(category) {
    category = category || "all";
    ytCategory = category;
    updateYtCategoryPills(category);
    try {
      const r = await fetch("/youtube/kpop?category=" + encodeURIComponent(category));
      if (!r.ok) return;
      const data = await r.json();
      if (data.videos && data.videos.length > 0) {
        startYouTubePlayback(data.videos, 0, category);
      }
    } catch (_) {}
  }

  function loadMvCategory(category) {
    ytCategory = category || "all";
    updateYtCategoryPills(ytCategory);
    startKpopMv(ytCategory);
  }

  function updateYtCategoryPills(cat) {
    const tabs = ["all", "girlgroup", "boygroup", "solo", "classic"];
    const idMap = { all: "ytTabAll", girlgroup: "ytTabGirl", boygroup: "ytTabBoy", solo: "ytTabSolo", classic: "ytTabClassic" };
    tabs.forEach(t => {
      const el = document.getElementById(idMap[t]);
      if (el) {
        if (t === cat) el.classList.add("active");
        else el.classList.remove("active");
      }
    });
  }

  function startYouTubePlayback(videos, startIndex, query) {
    startIndex = startIndex || 0;
    query = query || "kpop";
    ytQueue = videos || [];
    ytIndex = Math.max(0, Math.min(ytQueue.length - 1, startIndex));
    ytActive = true;

    if (query === "classic" || query === "classical" || query === "클래식") {
      ytCategory = "classic";
      updateYtCategoryPills("classic");
    }

    const overlay = document.getElementById("ytOverlay");
    if (overlay) {
      overlay.style.display = "flex";
      overlay.setAttribute("aria-hidden", "false");
    }

    loadYouTubeIframeApi();
    const cur = ytQueue[ytIndex];
    if (cur) {
      if (ytApiLoaded && window.YT && window.YT.Player) {
        initYtPlayer(cur.id);
      }
    }
    updateYtUi();
    resetYtControlsTimer();
    showYtTapBadge("▶");

    // Pause LMS local state
    playbackState.mode = "pause";
    const playBtn = document.getElementById("playBtn");
    if (playBtn) playBtn.textContent = "▶";
  }

  function updateYtUi() {
    const cur = ytQueue[ytIndex];
    if (!cur) return;
    const isClassic = ytCategory === "classic" || cur.category === "classic";
    const tEl = document.getElementById("ytMainTitle");
    const sEl = document.getElementById("ytSubTitle");
    const qEl = document.getElementById("ytQueueBadge");
    const bEl = document.getElementById("ytBadgeText");
    if (bEl) bEl.textContent = isClassic ? "🎻 CLASSIC" : "📺 K-POP MV";
    if (tEl) tEl.textContent = cur.title || (isClassic ? "클래식 명곡 연주" : "K-POP 뮤직비디오");
    if (sEl) sEl.textContent = cur.artist ? (cur.artist + (isClassic ? " · 명품 연주 연속 재생" : " · K-POP 연속 재생")) : "연속 재생 중";
    if (qEl) qEl.textContent = (ytIndex + 1) + " / " + ytQueue.length;
  }

  function ytNext(e, notify) {
    if (e) e.stopPropagation();
    if (ytQueue.length === 0) return;
    ytIndex = (ytIndex + 1) % ytQueue.length;
    const cur = ytQueue[ytIndex];
    if (ytPlayer && ytPlayer.loadVideoById) {
      try { ytPlayer.loadVideoById(cur.id); } catch (_) {}
    }
    showYtTapBadge("⏭");
    updateYtUi();
    resetYtControlsTimer();
    if (notify !== false) notifyYtControl("sync", { index: ytIndex });
  }

  function ytPrev(e, notify) {
    if (e) e.stopPropagation();
    if (ytQueue.length === 0) return;
    ytIndex = (ytIndex - 1 + ytQueue.length) % ytQueue.length;
    const cur = ytQueue[ytIndex];
    if (ytPlayer && ytPlayer.loadVideoById) {
      try { ytPlayer.loadVideoById(cur.id); } catch (_) {}
    }
    showYtTapBadge("⏮");
    updateYtUi();
    resetYtControlsTimer();
    if (notify !== false) notifyYtControl("sync", { index: ytIndex });
  }

  function ytToggle(e) {
    if (e) e.stopPropagation();
    if (!ytPlayer) return;
    let isPlaying = false;
    try {
      const st = ytPlayer.getPlayerState ? ytPlayer.getPlayerState() : -1;
      isPlaying = (st === (window.YT ? YT.PlayerState.PLAYING : 1));
      if (isPlaying) {
        ytPlayer.pauseVideo();
        showYtTapBadge("⏸");
        notifyYtControl("pause");
      } else {
        ytPlayer.playVideo();
        showYtTapBadge("▶");
        notifyYtControl("play");
      }
    } catch (_) {}
    resetYtControlsTimer();
  }

  function showYtTapBadge(icon) {
    const badge = document.getElementById("ytTapBadge");
    if (!badge) return;
    badge.textContent = icon;
    badge.classList.remove("show");
    void badge.offsetWidth;
    badge.classList.add("show");
    setTimeout(() => badge.classList.remove("show"), 450);
  }

  function handleYtStageClick(e) {
    const overlay = document.getElementById("ytOverlay");
    if (!overlay) return;
    if (overlay.classList.contains("yt-ui-hidden")) {
      overlay.classList.remove("yt-ui-hidden");
      resetYtControlsTimer();
    } else {
      ytToggle(e);
    }
  }

  function resetYtControlsTimer() {
    clearTimeout(ytControlsTimer);
    const overlay = document.getElementById("ytOverlay");
    if (overlay) overlay.classList.remove("yt-ui-hidden");
    ytControlsTimer = setTimeout(() => {
      if (ytActive && overlay) overlay.classList.add("yt-ui-hidden");
    }, 4500);
  }

  function closeYouTube(e, notify) {
    if (e) e.stopPropagation();
    ytActive = false;
    clearTimeout(ytControlsTimer);
    if (ytPlayer && ytPlayer.stopVideo) {
      try { ytPlayer.stopVideo(); } catch (_) {}
    }
    const overlay = document.getElementById("ytOverlay");
    if (overlay) {
      overlay.style.display = "none";
      overlay.setAttribute("aria-hidden", "true");
    }
    if (notify !== false) notifyYtControl("stop");
    setTimeout(refresh, 200);
  }

  function notifyYtControl(act, extra) {
    const payload = Object.assign({ action: act }, extra || {});
    fetch("/youtube/control", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    }).catch(() => {});
  }

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

    if (ev.type === "youtube_play") {
      if (ev.videos && ev.videos.length > 0) {
        startYouTubePlayback(ev.videos, ev.index || 0, ev.query || "kpop");
      }
      return;
    }

    if (ev.type === "youtube_cmd") {
      if (ev.cmd === "stop") {
        closeYouTube(null, false);
      } else if (ev.cmd === "pause") {
        if (ytPlayer && ytPlayer.pauseVideo) ytPlayer.pauseVideo();
      } else if (ev.cmd === "play") {
        if (ytPlayer && ytPlayer.playVideo) ytPlayer.playVideo();
      } else if (ev.cmd === "next") {
        ytNext(null, false);
      } else if (ev.cmd === "prev") {
        ytPrev(null, false);
      }
      return;
    }

    if (ev.type === "youtube_duck") {
      if (ytPlayer && ytPlayer.setVolume) {
        ytPlayer.setVolume(ev.duck ? 15 : 100);
      }
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

  function showArtFeedback(icon) {
    const badge = document.getElementById("artTapBadge");
    if (!badge) return;
    badge.textContent = icon;
    badge.classList.remove("show");
    void badge.offsetWidth;
    badge.classList.add("show");
    setTimeout(() => badge.classList.remove("show"), 480);
  }

  async function ctrl(action) {
    if (isDacVolumeFixed && (action === "vol_up" || action === "vol_down")) {
      showSubtitle("ok", "🎧 DAC 볼륨 고정", "DAC 모드에서는 고음질 출력을 위해 볼륨이 100%로 고정되어 있습니다.", "앰프나 스피커에서 볼륨을 조절해 주세요", 4500);
      return;
    }
    if (action === "next") {
      showArtFeedback("⏭");
      const titleEl = document.getElementById("trackTitle");
      if (titleEl) titleEl.style.opacity = "0.5";
    } else if (action === "prev") {
      showArtFeedback("⏮");
      const titleEl = document.getElementById("trackTitle");
      if (titleEl) titleEl.style.opacity = "0.5";
    } else if (action === "toggle") {
      const willPlay = (playbackState.mode !== "play");
      showArtFeedback(willPlay ? "▶" : "⏸");
    }
    try {
      await fetch("/display/control", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({action})
      });
      setTimeout(refresh, 200);
      if (action === "next" || action === "prev") {
        setTimeout(refresh, 600);
        setTimeout(refresh, 1200);
        setTimeout(refresh, 2200);
      }
    } catch (_) {}
  }

  let touchStartX = 0;
  let touchStartY = 0;
  let touchStartTime = 0;
  let touchMoved = false;
  let touchOnControls = false;
  let lastRippleAt = 0;
  let lastRippleX = -1;
  let lastRippleY = -1;

  function spawnTouchRipple(x, y) {
    if (typeof x !== "number" || typeof y !== "number") return;
    const now = Date.now();
    if (now - lastRippleAt < 70 && Math.hypot(x - lastRippleX, y - lastRippleY) < 25) {
      return;
    }
    lastRippleAt = now;
    lastRippleX = x;
    lastRippleY = y;

    const rip = document.createElement("div");
    rip.className = "touch-ripple";
    rip.style.left = x + "px";
    rip.style.top = y + "px";
    document.body.appendChild(rip);
    rip.addEventListener("animationend", () => rip.remove(), {once: true});
    setTimeout(() => { if (rip.parentNode) rip.remove(); }, 480);
  }

  function handleTouchStart(e) {
    if (!e.touches) return;
    for (let i = 0; i < e.touches.length; i++) {
      spawnTouchRipple(e.touches[i].clientX, e.touches[i].clientY);
    }
    if (e.touches.length !== 1) return;
    touchOnControls = Boolean(e.target && e.target.closest(".controls, button, a, .hud"));
    touchStartX = e.touches[0].clientX;
    touchStartY = e.touches[0].clientY;
    touchStartTime = Date.now();
    touchMoved = false;
  }

  function handleTouchMove(e) {
    if (touchOnControls || !e.touches || e.touches.length !== 1) return;
    const dx = e.touches[0].clientX - touchStartX;
    const dy = e.touches[0].clientY - touchStartY;
    if (Math.abs(dx) > 15 || Math.abs(dy) > 15) {
      touchMoved = true;
    }
  }

  function handleTouchEnd(e) {
    if (touchOnControls) return;
    if (!e.changedTouches || e.changedTouches.length === 0) return;
    const dx = e.changedTouches[0].clientX - touchStartX;
    const dy = e.changedTouches[0].clientY - touchStartY;
    const dt = Date.now() - touchStartTime;
    const absDx = Math.abs(dx);
    const absDy = Math.abs(dy);

    if (absDx >= 55 && absDx > absDy * 1.4 && dt < 650) {
      if (e.cancelable) e.preventDefault();
      e.stopPropagation();
      if (dx < 0) {
        ctrl("next");
      } else {
        ctrl("prev");
      }
    }
  }

  function handleCoverClick(ev) {
    if (touchMoved) return;
    if (ev) {
      ev.stopPropagation();
      ev.preventDefault();
    }
    ctrl("toggle");
  }

  let micTriggerBusy = false;
  let lastMicTriggerAt = 0;

  async function triggerMic(ev) {
    if (ev) {
      ev.stopPropagation();
      if (ev.cancelable && ev.type === "touchend") {
        ev.preventDefault();
      }
    }
    const now = Date.now();
    if (micTriggerBusy || (now - lastMicTriggerAt < 450)) return;
    lastMicTriggerAt = now;
    micTriggerBusy = true;

    const micBtn = document.getElementById("micBtn");
    if (micBtn) micBtn.classList.add("active");

    if (quizActive) {
      showSubtitle(
        "recording",
        '<span class="wave"><span></span><span></span><span></span><span></span></span> 🎙️ 영어 퀴즈 답변 듣는 중',
        "정답을 말씀해 주세요...",
        '"다시", "천천히", "힌트", "패스", "그만"',
        0
      );
    } else {
      showSubtitle(
        "recording",
        '<span class="wave"><span></span><span></span><span></span><span></span></span> 🎙️ 음성 듣는 중',
        "말씀해 주세요...",
        "말씀을 마치면 자동으로 인식합니다",
        0
      );
    }

    try {
      const r = await fetch("/voice/listen", {method: "POST", body: "{}"});
      const data = await r.json().catch(() => ({}));
      if (!r.ok && data && data.message) {
        showSubtitle("ambient", "🎙️ 음성 안내", data.message, "", 3200);
      }
      refresh();
    } catch (_) {
    } finally {
      micTriggerBusy = false;
    }
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

      if (s.youtube && s.youtube.active && !ytActive) {
        if (s.youtube.current) {
          startYouTubePlayback([s.youtube.current], s.youtube.index || 0, s.youtube.query || "kpop");
        }
      } else if (s.youtube && !s.youtube.active && ytActive) {
        closeYouTube(null, false);
      }

      const np = s.now_playing || {};
      const hasTrack = Boolean(np.title) || np.mode === "play";
      document.getElementById("idleClock").style.display = hasTrack ? "none" : "block";
      const trackTitleEl = document.getElementById("trackTitle");
      if (trackTitleEl) {
        trackTitleEl.style.opacity = "";
        trackTitleEl.textContent = hasTrack ? (np.title || "재생 중") : "재생 중인 곡 없음";
      }
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

  const appRootEl = document.getElementById("appRoot");
  appRootEl.addEventListener("touchstart", handleTouchStart, {passive: true});
  appRootEl.addEventListener("touchmove", handleTouchMove, {passive: true});
  appRootEl.addEventListener("touchend", handleTouchEnd, {passive: false});

  const micBtnEl = document.getElementById("micBtn");
  if (micBtnEl) {
    micBtnEl.addEventListener("touchend", (e) => {
      triggerMic(e);
    }, {passive: false});
  }

  window.addEventListener("pointerdown", (e) => {
    if (e.pointerType !== "touch") {
      spawnTouchRipple(e.clientX, e.clientY);
    }
  }, {passive: true});

  function bindTouchFeedback() {
    const sel = ".btn, .art-wrap, .hud-btn, .yt-btn, .yt-kpop-pill, .yt-close-btn";
    const markActive = (e) => {
      const el = e.target && e.target.closest(sel);
      if (el) el.classList.add("touch-active");
    };
    const clearActive = (e) => {
      const el = e.target && e.target.closest(sel);
      if (el) {
        setTimeout(() => el.classList.remove("touch-active"), 120);
      } else {
        document.querySelectorAll(".touch-active").forEach((item) => {
          setTimeout(() => item.classList.remove("touch-active"), 120);
        });
      }
    };
    document.addEventListener("pointerdown", markActive, {passive: true});
    document.addEventListener("pointerup", clearActive, {passive: true});
    document.addEventListener("pointercancel", clearActive, {passive: true});
    document.addEventListener("pointerleave", clearActive, {passive: true});

    document.addEventListener("touchstart", markActive, {passive: true});
    document.addEventListener("touchend", clearActive, {passive: true});
    document.addEventListener("touchcancel", clearActive, {passive: true});
  }
  bindTouchFeedback();

  // Tap empty background to briefly toggle minimal HUD (ignored if swipe occurred)
  appRootEl.addEventListener("click", (e) => {
    if (touchMoved) return;
    if (e.target.closest("button, a, .art-wrap")) return;
    const hud = document.getElementById("hud");
    if (hud && hud.classList.contains("visible")) hideHud();
    else showHud(4000);
  });

  document.getElementById("artWrap").addEventListener("dblclick", (e) => {
    e.stopPropagation();
    e.preventDefault();
    ctrl("next");
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
