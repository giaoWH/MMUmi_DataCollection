from __future__ import annotations

import io
import json
import threading
import webbrowser
import wave
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import numpy as np

from sdk.storage import SessionReader

from .service import AnnotationService, create_annotation_service

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None


INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>SDK Annotation</title>
  <style>
    :root {
      --bg: #f5f1e8;
      --panel: #fffaf0;
      --ink: #1c2430;
      --muted: #6b7280;
      --line: #d8d0c2;
      --accent: #0f766e;
      --accent-soft: #ccfbf1;
      --danger: #b91c1c;
      --shadow: 0 8px 30px rgba(28, 36, 48, 0.08);
      font-family: "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      color: var(--ink);
      background:
        radial-gradient(circle at top left, rgba(15,118,110,0.10), transparent 28%),
        radial-gradient(circle at top right, rgba(251,191,36,0.14), transparent 24%),
        linear-gradient(180deg, #f8f4ec 0%, var(--bg) 100%);
    }
    header {
      padding: 20px 24px 8px;
    }
    .header-bar {
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      gap: 16px;
      flex-wrap: wrap;
    }
    h1 { margin: 0 0 6px; font-size: 28px; }
    p { margin: 0; color: var(--muted); }
    .header-actions {
      display: grid;
      gap: 8px;
      justify-items: end;
    }
    .page-controls {
      display: flex;
      align-items: center;
      gap: 8px;
      flex-wrap: wrap;
      justify-content: flex-end;
    }
    .page-status {
      color: var(--muted);
      font-size: 13px;
      min-width: 96px;
      text-align: right;
    }
    main {
      display: grid;
      grid-template-columns: minmax(0, 1.6fr) minmax(360px, 0.9fr);
      gap: 18px;
      padding: 16px 24px 24px;
    }
    .panel {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 18px;
      box-shadow: var(--shadow);
      padding: 16px;
    }
    .stack { display: grid; gap: 18px; }
    .toolbar {
      display: grid;
      gap: 10px;
    }
    .toolbar-top {
      display: flex;
      justify-content: space-between;
      gap: 12px;
      flex-wrap: wrap;
      align-items: center;
    }
    .slider-wrap {
      display: grid;
      gap: 6px;
    }
    #sequence-slider { width: 100%; }
    .timeline {
      position: relative;
      border-radius: 16px;
      overflow: hidden;
      background: #fef7e8;
      border: 1px solid var(--line);
      padding: 10px;
    }
    #timeline {
      display: block;
      width: 100%;
      height: 96px;
      cursor: crosshair;
    }
    .grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 12px;
    }
    .video-card, .curve-card {
      border: 1px solid var(--line);
      border-radius: 14px;
      overflow: hidden;
      background: #fff;
    }
    .video-card header, .curve-card header {
      padding: 10px 12px;
      border-bottom: 1px solid var(--line);
      background: rgba(15,118,110,0.05);
      font-size: 13px;
    }
    .video-frame {
      width: 100%;
      aspect-ratio: 4 / 3;
      object-fit: contain;
      display: block;
      background: #111827;
    }
    .audio-card {
      border: 1px solid var(--line);
      border-radius: 14px;
      padding: 12px;
      background: #fff;
      display: grid;
      gap: 8px;
    }
    .audio-player {
      width: 100%;
    }
    .video-empty {
      aspect-ratio: 4 / 3;
      display: grid;
      place-items: center;
      color: var(--muted);
      font-size: 13px;
      background: #f3f4f6;
    }
    .curve-canvas {
      width: 100%;
      height: 120px;
      display: block;
    }
    .signal-groups {
      display: grid;
      gap: 14px;
    }
    .signal-group {
      display: grid;
      gap: 10px;
      padding: 12px;
      border: 1px solid var(--line);
      border-radius: 14px;
      background: rgba(255, 255, 255, 0.78);
    }
    .signal-group-title {
      margin: 0;
      font-size: 14px;
      color: var(--accent);
      letter-spacing: 0.02em;
      text-transform: uppercase;
    }
    .panel h2 {
      margin: 0 0 12px;
      font-size: 18px;
    }
    .form-grid {
      display: grid;
      gap: 10px;
    }
    .field {
      display: grid;
      gap: 6px;
      font-size: 14px;
    }
    .field input[type="text"],
    .field input[type="number"],
    .field textarea,
    .field select {
      border: 1px solid var(--line);
      border-radius: 10px;
      padding: 9px 10px;
      background: white;
      color: var(--ink);
    }
    .field textarea { min-height: 74px; resize: vertical; }
    .field small { color: var(--muted); }
    .field-group {
      display: grid;
      gap: 8px;
      padding: 12px;
      border: 1px dashed var(--line);
      border-radius: 12px;
      background: rgba(255,255,255,0.6);
    }
    .check-group {
      display: flex;
      gap: 12px;
      flex-wrap: wrap;
    }
    .check-group label, .inline-check {
      display: inline-flex;
      gap: 6px;
      align-items: center;
      font-size: 13px;
    }
    .actions {
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
    }
    button {
      border: 0;
      border-radius: 10px;
      padding: 10px 14px;
      background: var(--accent);
      color: white;
      cursor: pointer;
      font-weight: 600;
      transition: transform 120ms ease, opacity 120ms ease, box-shadow 120ms ease;
    }
    button:hover { box-shadow: 0 6px 18px rgba(15, 118, 110, 0.18); }
    button:active { transform: translateY(1px) scale(0.98); }
    button:disabled {
      cursor: wait;
      opacity: 0.78;
      box-shadow: none;
    }
    button.secondary { background: #374151; }
    button.danger { background: var(--danger); }
    .feedback {
      min-height: 18px;
      margin-top: 8px;
      font-size: 13px;
      color: var(--muted);
      transition: color 120ms ease;
    }
    .feedback.success { color: var(--accent); }
    .feedback.error { color: var(--danger); }
    .feedback.pending { color: #1d4ed8; }
    .list-table {
      width: 100%;
      border-collapse: collapse;
      font-size: 13px;
    }
    .list-table th, .list-table td {
      text-align: left;
      padding: 8px 6px;
      border-bottom: 1px solid var(--line);
      vertical-align: top;
    }
    .list-table tbody tr { cursor: pointer; }
    .list-table tbody tr:hover { background: rgba(15,118,110,0.05); }
    .pill {
      display: inline-block;
      padding: 3px 8px;
      border-radius: 999px;
      background: var(--accent-soft);
      color: var(--accent);
      font-size: 12px;
      margin-right: 6px;
    }
    .meta-line {
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      color: var(--muted);
      font-size: 13px;
    }
    .empty-note {
      color: var(--muted);
      font-size: 13px;
      padding: 12px 0;
    }
    @media (max-width: 1100px) {
      main { grid-template-columns: 1fr; }
    }
  </style>
</head>
  <body>
  <header>
    <div class="header-bar">
      <div>
        <h1>Session Annotation Workbench</h1>
        <p id="header-summary">Loading session…</p>
      </div>
      <div class="header-actions">
        <div class="page-controls">
          <span id="page-status" class="page-status">Page - / -</span>
          <button id="page-up" class="secondary" type="button">PageUp</button>
          <button id="page-down" class="secondary" type="button">PageDown</button>
        </div>
        <div id="navigation-feedback" class="feedback" aria-live="polite"></div>
      </div>
    </div>
  </header>
  <main>
    <section class="stack">
      <div class="panel toolbar">
        <div class="toolbar-top">
          <div class="meta-line">
            <span id="sequence-label" class="pill">Seq 0</span>
            <span id="time-label">Time 0.000</span>
            <span id="annotation-label">Annotations 0 / 0</span>
          </div>
          <div class="meta-line">
            <span id="session-duration">Session Duration -</span>
            <span id="audio-summary"></span>
          </div>
        </div>
        <div class="slider-wrap">
          <input id="sequence-slider" type="range" min="0" max="0" value="0" />
        </div>
        <div class="timeline">
          <canvas id="timeline" width="1200" height="96"></canvas>
        </div>
      </div>
      <div class="panel">
        <h2>Multi-View Playback</h2>
        <div id="video-grid" class="grid"></div>
      </div>
      <div class="panel">
        <h2>Audio Playback</h2>
        <div id="audio-grid" class="grid"></div>
      </div>
      <div class="panel">
        <h2>Synced Signals</h2>
        <div id="curve-grid" class="grid"></div>
      </div>
    </section>
    <aside class="stack">
      <div class="panel">
        <h2>Session Annotation</h2>
        <form id="session-form" class="form-grid"></form>
        <div class="actions">
          <button id="save-session" type="button">Save Session</button>
          <button id="close-annotator" class="secondary" type="button">Close</button>
        </div>
        <div id="session-feedback" class="feedback" aria-live="polite"></div>
      </div>
      <div class="panel">
        <h2>Span Annotation</h2>
        <form id="span-form" class="form-grid"></form>
        <div class="actions">
          <button id="save-span" type="button">Save Span</button>
          <button id="new-span" class="secondary" type="button">New Span</button>
          <button id="delete-span" class="danger" type="button">Delete Span</button>
        </div>
        <div id="span-feedback" class="feedback" aria-live="polite"></div>
        <div id="span-list-wrap"></div>
      </div>
      <div class="panel">
        <h2>Keyframe Annotation</h2>
        <form id="keyframe-form" class="form-grid"></form>
        <div class="actions">
          <button id="save-keyframe" type="button">Save Keyframe</button>
          <button id="new-keyframe" class="secondary" type="button">New Keyframe</button>
          <button id="delete-keyframe" class="danger" type="button">Delete Keyframe</button>
        </div>
        <div id="keyframe-feedback" class="feedback" aria-live="polite"></div>
        <div id="keyframe-list-wrap"></div>
      </div>
    </aside>
  </main>
  <script>
    const state = {
      payload: null,
      currentSequence: 0,
      selectedSpanId: null,
      selectedKeyframeId: null,
      dragStart: null,
      dragCurrent: null,
      feedbackTimers: {},
      isDirty: false,
    };

    const colors = ["#0f766e", "#d97706", "#2563eb", "#9333ea", "#dc2626", "#059669", "#4f46e5"];
    const SIGNAL_WINDOW_RADIUS = 45;

    async function loadState() {
      const response = await fetch("/api/state");
      state.payload = await response.json();
      const records = state.payload.aligned_records || [];
      const slider = document.getElementById("sequence-slider");
      slider.max = String(Math.max(records.length - 1, 0));
      state.currentSequence = Math.min(state.currentSequence, Math.max(records.length - 1, 0));
      slider.value = String(state.currentSequence);
      state.isDirty = false;
      renderAll();
    }

    function currentRecord() {
      return (state.payload.aligned_records || [])[state.currentSequence] || null;
    }

    function schemaFields(scope) {
      return (state.payload.schema.fields || []).filter((field) => field.scope === scope);
    }

    function defaultsFor(scope) {
      return (state.payload.defaults && state.payload.defaults[scope]) || {};
    }

    function formatWallTime(timestampSec) {
      if (timestampSec === null || timestampSec === undefined || Number.isNaN(timestampSec)) return "";
      const date = new Date(Number(timestampSec) * 1000);
      const pad = (value, width = 2) => String(value).padStart(width, "0");
      return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} `
        + `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}.`
        + `${pad(date.getMilliseconds(), 3)}`;
    }

    function setWallTimeField(hiddenId, displayId, timestampSec) {
      const hidden = document.getElementById(hiddenId);
      const display = document.getElementById(displayId);
      const raw = timestampSec === null || timestampSec === undefined ? "" : String(timestampSec);
      if (hidden) hidden.value = raw;
      if (display) display.value = raw === "" ? "" : formatWallTime(timestampSec);
    }

    function formatDuration(durationSec) {
      if (durationSec === null || durationSec === undefined || Number.isNaN(durationSec)) return "-";
      const totalMs = Math.max(0, Math.round(Number(durationSec) * 1000));
      const hours = Math.floor(totalMs / 3600000);
      const minutes = Math.floor((totalMs % 3600000) / 60000);
      const seconds = Math.floor((totalMs % 60000) / 1000);
      const milliseconds = totalMs % 1000;
      const pad = (value, width = 2) => String(value).padStart(width, "0");
      return `${pad(hours)}:${pad(minutes)}:${pad(seconds)}.${pad(milliseconds, 3)}`;
    }

    function currentSequenceId() {
      const record = currentRecord();
      return record ? record.sequence_id : state.currentSequence;
    }

    function recordForSequenceId(sequenceId) {
      return (state.payload.aligned_records || []).find((record) => record.sequence_id === sequenceId) || null;
    }

    function syncSpanTimesFromSequences() {
      const startSequence = Number(document.getElementById("span-start-seq").value);
      const endSequence = Number(document.getElementById("span-end-seq").value);
      const startRecord = Number.isFinite(startSequence) ? recordForSequenceId(startSequence) : null;
      const endRecord = Number.isFinite(endSequence) ? recordForSequenceId(endSequence) : null;
      setWallTimeField("span-start-time", "span-start-time-display", startRecord ? startRecord.aligned_time : null);
      setWallTimeField("span-end-time", "span-end-time-display", endRecord ? endRecord.aligned_time : null);
    }

    function syncKeyframeTimeFromSequence() {
      const sequence = Number(document.getElementById("keyframe-sequence").value);
      const record = Number.isFinite(sequence) ? recordForSequenceId(sequence) : null;
      setWallTimeField("keyframe-time", "keyframe-time-display", record ? record.aligned_time : null);
    }

    function bindDynamicFormEvents() {
      const spanStart = document.getElementById("span-start-seq");
      const spanEnd = document.getElementById("span-end-seq");
      const keyframeSequence = document.getElementById("keyframe-sequence");
      const customControls = document.querySelectorAll("[data-other-toggle]");
      const editableControls = document.querySelectorAll("form input, form select, form textarea");
      if (spanStart) spanStart.addEventListener("input", syncSpanTimesFromSequences);
      if (spanEnd) spanEnd.addEventListener("input", syncSpanTimesFromSequences);
      if (keyframeSequence) keyframeSequence.addEventListener("input", syncKeyframeTimeFromSequence);
      customControls.forEach((input) => {
        const update = () => syncOtherInputVisibility(input.dataset.scope, input.dataset.field);
        input.addEventListener("change", update);
        input.addEventListener("input", update);
        update();
      });
      editableControls.forEach((input) => {
        if (input.type === "hidden" || input.readOnly || input.disabled) return;
        const markDirty = () => {
          state.isDirty = true;
        };
        input.addEventListener("input", markDirty);
        input.addEventListener("change", markDirty);
      });
    }

    function setFeedback(scope, message, kind = "success", timeoutMs = 2200) {
      const target = document.getElementById(`${scope}-feedback`);
      if (!target) return;
      target.textContent = message;
      target.className = `feedback ${kind}`;
      if (state.feedbackTimers[scope]) {
        clearTimeout(state.feedbackTimers[scope]);
      }
      if (timeoutMs > 0) {
        state.feedbackTimers[scope] = setTimeout(() => {
          target.textContent = "";
          target.className = "feedback";
          state.feedbackTimers[scope] = null;
        }, timeoutMs);
      }
    }

    async function runButtonAction({
      buttonId,
      scope,
      pendingLabel,
      pendingMessage,
      successMessage,
      action,
      timeoutMs = 2200,
    }) {
      const button = document.getElementById(buttonId);
      const defaultLabel = button.dataset.defaultLabel || button.textContent;
      button.dataset.defaultLabel = defaultLabel;
      button.disabled = true;
      button.textContent = pendingLabel;
      setFeedback(scope, pendingMessage, "pending", 0);
      try {
        await action();
        setFeedback(scope, successMessage, "success", timeoutMs);
      } catch (error) {
        const message = error && error.message ? error.message : String(error);
        setFeedback(scope, message, "error", 4200);
      } finally {
        button.disabled = false;
        button.textContent = defaultLabel;
      }
    }

    function signalWindow() {
      const records = state.payload.aligned_records || [];
      if (!records.length) {
        return { start: 0, end: 0 };
      }
      const minSequence = records[0].sequence_id;
      const maxSequence = records[records.length - 1].sequence_id;
      const current = currentSequenceId();
      return {
        start: Math.max(minSequence, current - SIGNAL_WINDOW_RADIUS),
        end: Math.min(maxSequence, current + SIGNAL_WINDOW_RADIUS),
      };
    }

    function setCurrentSequence(sequence) {
      const records = state.payload.aligned_records || [];
      if (!records.length) return;
      const next = Math.max(0, Math.min(records.length - 1, sequence));
      state.currentSequence = next;
      document.getElementById("sequence-slider").value = String(next);
      renderAll();
    }

    function renderAll() {
      renderHeader();
      renderTimeline();
      renderVideos();
      renderAudio();
      renderCurves();
      renderSessionForm();
      renderSpanForm();
      renderKeyframeForm();
      renderSpanList();
      renderKeyframeList();
      bindDynamicFormEvents();
    }

    function renderHeader() {
      const summary = state.payload.session_summary;
      const navigation = state.payload.navigation || {};
      const records = state.payload.aligned_records || [];
      document.getElementById("header-summary").textContent =
        `${summary.session_id} · ${summary.sensor_names.join(", ")} · schema ${state.payload.schema.version}`;
      document.getElementById("page-status").textContent =
        `Page ${navigation.position ?? "-"} / ${navigation.total ?? "-"}`;
      const pageUpButton = document.getElementById("page-up");
      const pageDownButton = document.getElementById("page-down");
      pageUpButton.disabled = !navigation.has_previous;
      pageDownButton.disabled = !navigation.has_next;
      const record = currentRecord();
      document.getElementById("sequence-label").textContent =
        record ? `Seq ${record.sequence_id}` : "Seq -";
      document.getElementById("time-label").textContent =
        record ? `Time ${formatWallTime(record.aligned_time)}` : "Time -";
      const duration = records.length >= 2
        ? records[records.length - 1].aligned_time - records[0].aligned_time
        : 0;
      document.getElementById("session-duration").textContent =
        `Session Duration ${formatDuration(duration)}`;
      document.getElementById("annotation-label").textContent =
        `Annotations ${state.payload.annotations.spans.length} spans / ${state.payload.annotations.keyframes.length} keyframes`;
      const audio = state.payload.audio_summary || [];
      document.getElementById("audio-summary").innerHTML = audio.length
        ? audio.map((item) => `${item.sensor_name}: ${item.frame_count} audio frames`).join(" · ")
        : "No audio summary";
    }

    function renderTimeline() {
      const canvas = document.getElementById("timeline");
      const ctx = canvas.getContext("2d");
      const records = state.payload.aligned_records || [];
      const spans = state.payload.annotations.spans || [];
      const keyframes = state.payload.annotations.keyframes || [];
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      ctx.fillStyle = "#fff9ed";
      ctx.fillRect(0, 0, canvas.width, canvas.height);
      ctx.strokeStyle = "#d8d0c2";
      ctx.strokeRect(0, 0, canvas.width, canvas.height);
      if (!records.length) {
        ctx.fillStyle = "#6b7280";
        ctx.fillText("No aligned records", 16, 32);
        return;
      }
      const xForSeq = (seq) => {
        if (records.length <= 1) return 20;
        return 20 + ((canvas.width - 40) * seq) / (records.length - 1);
      };
      ctx.strokeStyle = "#cbd5e1";
      ctx.beginPath();
      ctx.moveTo(20, 64);
      ctx.lineTo(canvas.width - 20, 64);
      ctx.stroke();

      spans.forEach((span, index) => {
        const x1 = xForSeq(span.start_sequence_id);
        const x2 = xForSeq(span.end_sequence_id);
        ctx.fillStyle = `rgba(15, 118, 110, ${0.22 + (index % 3) * 0.08})`;
        ctx.fillRect(x1, 28, Math.max(x2 - x1, 4), 26);
      });

      keyframes.forEach((item, index) => {
        const x = xForSeq(item.sequence_id);
        ctx.strokeStyle = colors[index % colors.length];
        ctx.beginPath();
        ctx.moveTo(x, 16);
        ctx.lineTo(x, 82);
        ctx.stroke();
      });

      if (state.dragStart !== null && state.dragCurrent !== null) {
        const start = Math.min(state.dragStart, state.dragCurrent);
        const end = Math.max(state.dragStart, state.dragCurrent);
        const x1 = xForSeq(start);
        const x2 = xForSeq(end);
        ctx.fillStyle = "rgba(37, 99, 235, 0.20)";
        ctx.fillRect(x1, 10, Math.max(x2 - x1, 4), 78);
      }

      const currentX = xForSeq(state.currentSequence);
      ctx.strokeStyle = "#111827";
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.moveTo(currentX, 8);
      ctx.lineTo(currentX, 88);
      ctx.stroke();
      ctx.lineWidth = 1;
    }

    function renderVideos() {
      const target = document.getElementById("video-grid");
      const record = currentRecord();
      const streams = state.payload.visual_streams || [];
      if (!streams.length) {
        target.innerHTML = '<div class="empty-note">No PNG-backed visual streams found in this session.</div>';
        return;
      }
      target.innerHTML = streams.map((stream) => {
        const frame = record && record.frames && record.frames[stream.sensor_name];
        if (!frame) {
          return `<article class="video-card"><header>${stream.label}</header><div class="video-empty">Missing at current aligned step</div></article>`;
        }
        const src = `/api/frame?sensor_name=${encodeURIComponent(stream.sensor_name)}&frame_id=${frame.frame_id}&payload_key=${encodeURIComponent(stream.payload_key)}`;
        return `<article class="video-card"><header>${stream.label}</header><img class="video-frame" src="${src}" alt="${stream.label}" /></article>`;
      }).join("");
    }

    function renderAudio() {
      const target = document.getElementById("audio-grid");
      const streams = state.payload.audio_streams || [];
      if (!streams.length) {
        target.innerHTML = '<div class="empty-note">No audio streams found in this session.</div>';
        return;
      }
      target.innerHTML = streams.map((stream) => {
        const src = `/api/audio?sensor_name=${encodeURIComponent(stream.sensor_name)}`;
        const details = [];
        if (stream.channels) details.push(`${stream.channels} ch`);
        if (stream.sample_rate) details.push(`${stream.sample_rate} Hz`);
        if (stream.chunk) details.push(`chunk ${stream.chunk}`);
        if (stream.duration_sec !== null && stream.duration_sec !== undefined) details.push(`duration ${stream.duration_sec.toFixed(2)}s`);
        return `
          <article class="audio-card">
            <strong>${stream.label}</strong>
            <div class="meta-line">${details.map((item) => `<span>${item}</span>`).join("")}</div>
            <audio class="audio-player" controls preload="none" src="${src}"></audio>
          </article>
        `;
      }).join("");
    }

    function renderCurves() {
      const target = document.getElementById("curve-grid");
      const streams = state.payload.scalar_streams || [];
      if (!streams.length) {
        target.innerHTML = '<div class="empty-note">No synchronized FT / IMU / Motors / Audio signals available in this session.</div>';
        return;
      }
      const groups = groupSignalStreams(streams);
      let curveIndex = 0;
      target.innerHTML = `
        <div class="signal-groups">
          ${groups.map((group) => `
            <section class="signal-group">
              <h3 class="signal-group-title">${group.sensorName}</h3>
              <div class="grid">
                ${group.streams.map((stream) => {
                  const canvasId = `curve-${curveIndex++}`;
                  return `
                    <article class="curve-card">
                      <header>${stream.shortLabel}</header>
                      <canvas id="${canvasId}" class="curve-canvas" width="360" height="120"></canvas>
                    </article>
                  `;
                }).join("")}
              </div>
            </section>
          `).join("")}
        </div>
      `;
      curveIndex = 0;
      streams.forEach((stream, index) => {
        const canvas = document.getElementById(`curve-${curveIndex++}`);
        drawCurve(canvas, stream, colors[index % colors.length]);
        bindCurveScrub(canvas);
      });
    }

    function groupSignalStreams(streams) {
      const groups = [];
      const bySensor = new Map();
      streams.forEach((stream) => {
        const sensorName = signalSensorName(stream);
        const enriched = { ...stream, shortLabel: signalShortLabel(stream, sensorName) };
        if (!bySensor.has(sensorName)) {
          const group = { sensorName, streams: [] };
          bySensor.set(sensorName, group);
          groups.push(group);
        }
        bySensor.get(sensorName).streams.push(enriched);
      });
      return groups;
    }

    function signalSensorName(stream) {
      const label = stream.label || stream.id || "signal";
      const sensorName = label.split(".")[0];
      return sensorName || "signal";
    }

    function signalShortLabel(stream, sensorName) {
      const label = stream.label || stream.id || "";
      const prefix = `${sensorName}.`;
      return label.startsWith(prefix) ? label.slice(prefix.length) : label;
    }

    function drawCurve(canvas, stream, color) {
      const ctx = canvas.getContext("2d");
      const allPoints = stream.points || [];
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      ctx.fillStyle = "#ffffff";
      ctx.fillRect(0, 0, canvas.width, canvas.height);
      ctx.strokeStyle = "#e5e7eb";
      ctx.strokeRect(0, 0, canvas.width, canvas.height);
      if (!allPoints.length) return;
      const window = signalWindow();
      const points = allPoints.filter((point) => point.sequence_id >= window.start && point.sequence_id <= window.end);
      if (!points.length) {
        ctx.fillStyle = "#6b7280";
        ctx.fillText("No samples in current window", 16, 32);
        return;
      }
      const minV = Math.min(...points.map((point) => point.value));
      const maxV = Math.max(...points.map((point) => point.value));
      const range = maxV - minV || 1;
      const sequenceRange = Math.max(window.end - window.start, 1);
      const xFor = (seq) => 18 + ((canvas.width - 36) * (seq - window.start)) / sequenceRange;
      const yFor = (value) => canvas.height - 18 - ((canvas.height - 36) * (value - minV)) / range;
      ctx.fillStyle = "#6b7280";
      ctx.font = "11px sans-serif";
      ctx.fillText(`Seq ${window.start} - ${window.end}`, 12, 14);
      ctx.strokeStyle = "#f1f5f9";
      ctx.beginPath();
      ctx.moveTo(18, canvas.height / 2);
      ctx.lineTo(canvas.width - 18, canvas.height / 2);
      ctx.stroke();
      ctx.strokeStyle = color;
      ctx.beginPath();
      points.forEach((point, idx) => {
        const x = xFor(point.sequence_id);
        const y = yFor(point.value);
        if (idx === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      });
      ctx.stroke();

      const currentSeq = currentSequenceId();
      const cursorX = xFor(currentSeq);
      ctx.strokeStyle = "#111827";
      ctx.beginPath();
      ctx.moveTo(cursorX, 8);
      ctx.lineTo(cursorX, canvas.height - 8);
      ctx.stroke();

      const currentPoint = points.find((point) => point.sequence_id === currentSeq);
      if (currentPoint) {
        ctx.fillStyle = "#111827";
        ctx.beginPath();
        ctx.arc(xFor(currentPoint.sequence_id), yFor(currentPoint.value), 4, 0, Math.PI * 2);
        ctx.fill();
        ctx.fillText(`${currentPoint.value.toFixed(3)}`, canvas.width - 68, 14);
      }
    }

    function bindCurveScrub(canvas) {
      canvas.onclick = (event) => {
        const rect = canvas.getBoundingClientRect();
        const ratio = Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width));
        const window = signalWindow();
        const targetSequence = Math.round(window.start + ratio * Math.max(window.end - window.start, 1));
        setCurrentSequence(targetSequence);
      };
    }

    function escapeHtml(value) {
      return String(value ?? "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;");
    }

    function customOtherValue(field, value) {
      if (!field.options || !field.options.includes("other")) return "";
      if (field.type === "enum") {
        return value && !field.options.includes(value) ? String(value) : "";
      }
      if (field.type === "multi_enum" && Array.isArray(value)) {
        const custom = value.find((item) => !field.options.includes(item));
        return custom ? String(custom) : "";
      }
      return "";
    }

    function syncOtherInputVisibility(scope, fieldId) {
      const wrapper = document.getElementById(`${scope}-${fieldId}-other-wrap`);
      const input = document.getElementById(`${scope}-${fieldId}-other-input`);
      if (!wrapper || !input) return;
      const toggleInputs = [...document.querySelectorAll(`[data-scope="${scope}"][data-field="${fieldId}"][data-other-toggle]`)];
      const visible = toggleInputs.some((element) => {
        if (element.type === "checkbox") return element.checked;
        return element.value === "__custom__";
      });
      wrapper.style.display = visible ? "grid" : "none";
      if (!visible) input.value = "";
    }

    function fieldControl(scope, field, value) {
      const fieldId = `${scope}-${field.id}`;
      if (field.type === "string") {
        return `<div class="field"><label for="${fieldId}">${field.label}</label><textarea id="${fieldId}" data-scope="${scope}" data-field="${field.id}">${escapeHtml(value ?? "")}</textarea></div>`;
      }
      if (field.type === "number") {
        return `<div class="field"><label for="${fieldId}">${field.label}</label><input id="${fieldId}" type="number" step="any" data-scope="${scope}" data-field="${field.id}" value="${value ?? ""}" /></div>`;
      }
      if (field.type === "bool") {
        return `<div class="field"><label class="inline-check"><input id="${fieldId}" type="checkbox" data-scope="${scope}" data-field="${field.id}" ${value ? "checked" : ""} />${field.label}</label></div>`;
      }
      if (field.type === "enum") {
        const customValue = customOtherValue(field, value);
        const selectedValue = customValue ? "__custom__" : (value ?? "");
        const options = ['<option value=""></option>'].concat(field.options.map((option) => {
          const optionValue = option === "other" ? "__custom__" : option;
          const optionLabel = option === "other" ? "other (specify)" : option;
          return `<option value="${optionValue}" ${selectedValue === optionValue ? "selected" : ""}>${escapeHtml(optionLabel)}</option>`;
        }));
        if (!field.options.includes("other")) {
          return `<div class="field"><label for="${fieldId}">${field.label}</label><select id="${fieldId}" data-scope="${scope}" data-field="${field.id}">${options.join("")}</select></div>`;
        }
        return `<div class="field"><label for="${fieldId}">${field.label}</label><select id="${fieldId}" data-scope="${scope}" data-field="${field.id}" data-other-toggle="true">${options.join("")}</select><div id="${fieldId}-other-wrap" class="field" style="display:${selectedValue === "__custom__" ? "grid" : "none"};"><label for="${fieldId}-other-input">Other Label</label><input id="${fieldId}-other-input" type="text" value="${escapeHtml(customValue)}" placeholder="Enter custom label" /></div></div>`;
      }
      if (field.type === "multi_enum") {
        const selected = new Set(Array.isArray(value) ? value : []);
        const customValue = customOtherValue(field, value);
        return `<div class="field"><label>${field.label}</label><div class="field-group check-group">${
          field.options.map((option) => {
            const optionValue = option === "other" ? "__custom__" : option;
            const checked = option === "other" ? Boolean(customValue) : selected.has(option);
            const toggleAttr = option === "other" ? ' data-other-toggle="true"' : "";
            const optionLabel = option === "other" ? "other (specify)" : option;
            return `<label><input type="checkbox" data-scope="${scope}" data-field="${field.id}" data-kind="multi_enum" value="${optionValue}"${toggleAttr} ${checked ? "checked" : ""} />${escapeHtml(optionLabel)}</label>`;
          }).join("")
        }</div><div id="${fieldId}-other-wrap" class="field" style="display:${customValue ? "grid" : "none"};"><label for="${fieldId}-other-input">Other Label</label><input id="${fieldId}-other-input" type="text" value="${escapeHtml(customValue)}" placeholder="Enter custom label" /></div></div>`;
      }
      return "";
    }

    function renderSessionForm() {
      const target = document.getElementById("session-form");
      const current = (state.payload.annotations.session && state.payload.annotations.session.data) || {};
      const fields = schemaFields("session");
      target.innerHTML = fields.map((field) => fieldControl("session", field, current[field.id] ?? defaultsFor("session")[field.id])).join("");
    }

    function selectedSpan() {
      return (state.payload.annotations.spans || []).find((item) => item.id === state.selectedSpanId) || null;
    }

    function selectedKeyframe() {
      return (state.payload.annotations.keyframes || []).find((item) => item.id === state.selectedKeyframeId) || null;
    }

    function renderSpanForm() {
      const target = document.getElementById("span-form");
      const selected = selectedSpan();
      const record = currentRecord();
      const current = selected ? selected.data : defaultsFor("span");
      const startSeq = selected ? selected.start_sequence_id : currentSequenceId();
      const endSeq = selected ? selected.end_sequence_id : currentSequenceId();
      const startTime = selected ? selected.start_time : record ? record.aligned_time : null;
      const endTime = selected ? selected.end_time : record ? record.aligned_time : null;
      target.innerHTML = `
        <div class="field">
          <label for="span-id">Span ID</label>
          <input id="span-id" type="text" value="${selected ? selected.id : ""}" placeholder="Auto generate" />
        </div>
        <div class="field-group">
          <div class="field"><label for="span-start-seq">Start Sequence</label><input id="span-start-seq" type="number" value="${startSeq}" /></div>
          <div class="field"><label for="span-end-seq">End Sequence</label><input id="span-end-seq" type="number" value="${endSeq}" /></div>
          <div class="field"><label for="span-start-time-display">Start Time</label><input id="span-start-time-display" type="text" value="${startTime === null ? "" : formatWallTime(startTime)}" readonly /><input id="span-start-time" type="hidden" value="${startTime ?? ""}" /></div>
          <div class="field"><label for="span-end-time-display">End Time</label><input id="span-end-time-display" type="text" value="${endTime === null ? "" : formatWallTime(endTime)}" readonly /><input id="span-end-time" type="hidden" value="${endTime ?? ""}" /></div>
        </div>
        ${schemaFields("span").map((field) => fieldControl("span", field, current[field.id] ?? defaultsFor("span")[field.id])).join("")}
      `;
    }

    function renderKeyframeForm() {
      const target = document.getElementById("keyframe-form");
      const selected = selectedKeyframe();
      const record = currentRecord();
      const current = selected ? selected.data : defaultsFor("keyframe");
      const sequenceId = selected ? selected.sequence_id : currentSequenceId();
      const alignedTime = selected ? selected.aligned_time : record ? record.aligned_time : null;
      target.innerHTML = `
        <div class="field">
          <label for="keyframe-id">Keyframe ID</label>
          <input id="keyframe-id" type="text" value="${selected ? selected.id : ""}" placeholder="Auto generate" />
        </div>
        <div class="field-group">
          <div class="field"><label for="keyframe-sequence">Sequence</label><input id="keyframe-sequence" type="number" value="${sequenceId}" /></div>
          <div class="field"><label for="keyframe-time-display">Aligned Time</label><input id="keyframe-time-display" type="text" value="${alignedTime === null ? "" : formatWallTime(alignedTime)}" readonly /><input id="keyframe-time" type="hidden" value="${alignedTime ?? ""}" /></div>
        </div>
        ${schemaFields("keyframe").map((field) => fieldControl("keyframe", field, current[field.id] ?? defaultsFor("keyframe")[field.id])).join("")}
      `;
    }

    function renderSpanList() {
      const target = document.getElementById("span-list-wrap");
      const spans = state.payload.annotations.spans || [];
      if (!spans.length) {
        target.innerHTML = '<div class="empty-note">No span annotations yet.</div>';
        return;
      }
      target.innerHTML = `
        <table class="list-table">
          <thead><tr><th>ID</th><th>Range</th><th>Data</th></tr></thead>
          <tbody>
            ${spans.map((item) => `
              <tr data-span-id="${item.id}">
                <td>${item.id.slice(0, 8)}</td>
                <td>${item.start_sequence_id} - ${item.end_sequence_id}</td>
                <td>${Object.entries(item.data || {}).map(([key, value]) => `<span class="pill">${key}:${Array.isArray(value) ? value.join("|") : value}</span>`).join("")}</td>
              </tr>
            `).join("")}
          </tbody>
        </table>
      `;
      target.querySelectorAll("[data-span-id]").forEach((row) => {
        row.addEventListener("click", () => {
          state.selectedSpanId = row.dataset.spanId;
          renderSpanForm();
        });
      });
    }

    function renderKeyframeList() {
      const target = document.getElementById("keyframe-list-wrap");
      const keyframes = state.payload.annotations.keyframes || [];
      if (!keyframes.length) {
        target.innerHTML = '<div class="empty-note">No keyframe annotations yet.</div>';
        return;
      }
      target.innerHTML = `
        <table class="list-table">
          <thead><tr><th>ID</th><th>Seq</th><th>Data</th></tr></thead>
          <tbody>
            ${keyframes.map((item) => `
              <tr data-keyframe-id="${item.id}">
                <td>${item.id.slice(0, 8)}</td>
                <td>${item.sequence_id}</td>
                <td>${Object.entries(item.data || {}).map(([key, value]) => `<span class="pill">${key}:${Array.isArray(value) ? value.join("|") : value}</span>`).join("")}</td>
              </tr>
            `).join("")}
          </tbody>
        </table>
      `;
      target.querySelectorAll("[data-keyframe-id]").forEach((row) => {
        row.addEventListener("click", () => {
          state.selectedKeyframeId = row.dataset.keyframeId;
          renderKeyframeForm();
        });
      });
    }

    function readScopedData(scope) {
      const data = {};
      schemaFields(scope).forEach((field) => {
        if (field.type === "multi_enum") {
          const checked = [...document.querySelectorAll(`[data-scope="${scope}"][data-field="${field.id}"]`)]
            .filter((input) => input.checked)
            .map((input) => input.value);
          const supportsCustom = Array.isArray(field.options) && field.options.includes("other");
          const normalized = checked.filter((item) => item !== "__custom__");
          if (supportsCustom && checked.includes("__custom__")) {
            const otherInput = document.getElementById(`${scope}-${field.id}-other-input`);
            const customValue = otherInput ? otherInput.value.trim() : "";
            if (!customValue) {
              throw new Error(`${field.label}: selecting other requires a custom label`);
            }
            normalized.push(customValue);
          }
          if (normalized.length) data[field.id] = normalized;
          return;
        }
        const input = document.querySelector(`[data-scope="${scope}"][data-field="${field.id}"]`);
        if (!input) return;
        if (field.type === "bool") {
          data[field.id] = input.checked;
          return;
        }
        const raw = input.value;
        if (raw === "") return;
        if (field.type === "number") {
          data[field.id] = Number(raw);
          return;
        }
        if (field.type === "enum" && Array.isArray(field.options) && field.options.includes("other") && raw === "__custom__") {
          const otherInput = document.getElementById(`${scope}-${field.id}-other-input`);
          const customValue = otherInput ? otherInput.value.trim() : "";
          if (!customValue) {
            throw new Error(`${field.label}: selecting other requires a custom label`);
          }
          data[field.id] = customValue;
          return;
        }
        data[field.id] = raw;
      });
      return data;
    }

    async function saveSessionAnnotation() {
      await runButtonAction({
        buttonId: "save-session",
        scope: "session",
        pendingLabel: "Saving...",
        pendingMessage: "正在保存 session 标注...",
        successMessage: "Session 标注已保存",
        action: async () => {
          const response = await fetch("/api/session", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ data: readScopedData("session") }),
          });
          const result = await response.json();
          if (!response.ok || !result.ok) {
            throw new Error(result.error || "failed to save session annotation");
          }
          await loadState();
        },
      });
    }

    async function saveSpanAnnotation() {
      await runButtonAction({
        buttonId: "save-span",
        scope: "span",
        pendingLabel: "Saving...",
        pendingMessage: "正在保存 span 标注...",
        successMessage: "Span 标注已保存",
        action: async () => {
        const numOrNull = (value) => value === "" ? null : Number(value);
        const rawId = document.getElementById("span-id").value.trim();
        const payload = {
          id: rawId || undefined,
          start_sequence_id: Number(document.getElementById("span-start-seq").value),
          end_sequence_id: Number(document.getElementById("span-end-seq").value),
          start_time: numOrNull(document.getElementById("span-start-time").value),
          end_time: numOrNull(document.getElementById("span-end-time").value),
          data: readScopedData("span"),
        };
        const id = state.selectedSpanId;
        const response = await fetch(id ? `/api/spans/${id}` : "/api/spans", {
          method: id ? "PUT" : "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        const result = await response.json();
        if (!response.ok || !result.ok) {
          throw new Error(result.error || "failed to save span annotation");
        }
        state.selectedSpanId = result.result.id;
        await loadState();
        },
      });
    }

    async function saveKeyframeAnnotation() {
      await runButtonAction({
        buttonId: "save-keyframe",
        scope: "keyframe",
        pendingLabel: "Saving...",
        pendingMessage: "正在保存 keyframe 标注...",
        successMessage: "Keyframe 标注已保存",
        action: async () => {
        const numOrNull = (value) => value === "" ? null : Number(value);
        const rawId = document.getElementById("keyframe-id").value.trim();
        const payload = {
          id: rawId || undefined,
          sequence_id: Number(document.getElementById("keyframe-sequence").value),
          aligned_time: numOrNull(document.getElementById("keyframe-time").value),
          data: readScopedData("keyframe"),
        };
        const id = state.selectedKeyframeId;
        const response = await fetch(id ? `/api/keyframes/${id}` : "/api/keyframes", {
          method: id ? "PUT" : "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        const result = await response.json();
        if (!response.ok || !result.ok) {
          throw new Error(result.error || "failed to save keyframe annotation");
        }
        state.selectedKeyframeId = result.result.id;
        await loadState();
        },
      });
    }

    async function deleteSpanAnnotation() {
      if (!state.selectedSpanId) {
        setFeedback("span", "请先选择一个 span 标注", "error", 2600);
        return;
      }
      await runButtonAction({
        buttonId: "delete-span",
        scope: "span",
        pendingLabel: "Deleting...",
        pendingMessage: "正在删除 span 标注...",
        successMessage: "Span 标注已删除",
        action: async () => {
          const response = await fetch(`/api/spans/${state.selectedSpanId}`, { method: "DELETE" });
          const result = await response.json();
          if (!response.ok || !result.ok) {
            throw new Error(result.error || "failed to delete span annotation");
          }
          state.selectedSpanId = null;
          await loadState();
        },
      });
    }

    async function deleteKeyframeAnnotation() {
      if (!state.selectedKeyframeId) {
        setFeedback("keyframe", "请先选择一个 keyframe 标注", "error", 2600);
        return;
      }
      await runButtonAction({
        buttonId: "delete-keyframe",
        scope: "keyframe",
        pendingLabel: "Deleting...",
        pendingMessage: "正在删除 keyframe 标注...",
        successMessage: "Keyframe 标注已删除",
        action: async () => {
          const response = await fetch(`/api/keyframes/${state.selectedKeyframeId}`, { method: "DELETE" });
          const result = await response.json();
          if (!response.ok || !result.ok) {
            throw new Error(result.error || "failed to delete keyframe annotation");
          }
          state.selectedKeyframeId = null;
          await loadState();
        },
      });
    }

    async function navigateSession(direction) {
      if (state.isDirty) {
        setFeedback("navigation", "当前有未保存修改，请先保存后再翻页", "error", 3200);
        window.alert("当前有未保存修改，请先保存后再翻页");
        return;
      }
      const buttonId = direction === "previous" ? "page-up" : "page-down";
      const successMessage = direction === "previous" ? "已切换到上一段 session" : "已切换到下一段 session";
      await runButtonAction({
        buttonId,
        scope: "navigation",
        pendingLabel: "Loading...",
        pendingMessage: "正在切换 session...",
        successMessage,
        action: async () => {
          const response = await fetch("/api/navigate", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ direction }),
          });
          const result = await response.json();
          if (!response.ok || !result.ok) {
            throw new Error(result.error || "failed to navigate session");
          }
          state.currentSequence = 0;
          state.selectedSpanId = null;
          state.selectedKeyframeId = null;
          await loadState();
        },
      });
    }

    async function closeAnnotator() {
      const button = document.getElementById("close-annotator");
      button.disabled = true;
      try {
        await fetch("/api/shutdown", { method: "POST" });
      } catch (error) {
        console.error("failed to stop annotation server", error);
      }
      document.body.innerHTML = `
        <main style="display:grid;place-items:center;min-height:100vh;padding:24px;">
          <section class="panel" style="max-width:520px;text-align:center;">
            <h1>Annotation Closed</h1>
            <p>The local annotation server has been stopped. You can close this tab now.</p>
          </section>
        </main>
      `;
      setTimeout(() => {
        window.close();
      }, 150);
    }

    function resetSpanForm() {
      state.selectedSpanId = null;
      state.dragStart = null;
      state.dragCurrent = null;
      state.isDirty = false;
      renderSpanForm();
      renderTimeline();
      setFeedback("span", "已切换到新建 span 模式", "success", 1800);
    }

    function resetKeyframeForm() {
      state.selectedKeyframeId = null;
      state.isDirty = false;
      renderKeyframeForm();
      setFeedback("keyframe", "已切换到新建 keyframe 模式", "success", 1800);
    }

    function bindEvents() {
      document.getElementById("sequence-slider").addEventListener("input", (event) => {
        setCurrentSequence(Number(event.target.value));
      });
      document.getElementById("page-up").addEventListener("click", () => navigateSession("previous"));
      document.getElementById("page-down").addEventListener("click", () => navigateSession("next"));
      document.getElementById("save-session").addEventListener("click", saveSessionAnnotation);
      document.getElementById("close-annotator").addEventListener("click", closeAnnotator);
      document.getElementById("save-span").addEventListener("click", saveSpanAnnotation);
      document.getElementById("new-span").addEventListener("click", resetSpanForm);
      document.getElementById("delete-span").addEventListener("click", deleteSpanAnnotation);
      document.getElementById("save-keyframe").addEventListener("click", saveKeyframeAnnotation);
      document.getElementById("new-keyframe").addEventListener("click", resetKeyframeForm);
      document.getElementById("delete-keyframe").addEventListener("click", deleteKeyframeAnnotation);

      const canvas = document.getElementById("timeline");
      const seqFromEvent = (event) => {
        const rect = canvas.getBoundingClientRect();
        const records = state.payload.aligned_records || [];
        if (!records.length) return 0;
        const ratio = Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width));
        return Math.round(ratio * (records.length - 1));
      };

      canvas.addEventListener("mousedown", (event) => {
        state.dragStart = seqFromEvent(event);
        state.dragCurrent = state.dragStart;
        setCurrentSequence(state.dragStart);
      });
      canvas.addEventListener("mousemove", (event) => {
        if (state.dragStart === null) return;
        state.dragCurrent = seqFromEvent(event);
        renderTimeline();
      });
      window.addEventListener("mouseup", () => {
        if (state.dragStart === null || state.dragCurrent === null) return;
        const start = Math.min(state.dragStart, state.dragCurrent);
        const end = Math.max(state.dragStart, state.dragCurrent);
        const records = state.payload.aligned_records || [];
        document.getElementById("span-start-seq").value = String(records[start] ? records[start].sequence_id : start);
        document.getElementById("span-end-seq").value = String(records[end] ? records[end].sequence_id : end);
        syncSpanTimesFromSequences();
        state.dragStart = null;
        state.dragCurrent = null;
        renderTimeline();
      });
      canvas.addEventListener("dblclick", (event) => {
        const sequence = seqFromEvent(event);
        const records = state.payload.aligned_records || [];
        state.currentSequence = sequence;
        document.getElementById("sequence-slider").value = String(sequence);
        document.getElementById("keyframe-sequence").value = String(records[sequence] ? records[sequence].sequence_id : sequence);
        syncKeyframeTimeFromSequence();
        renderAll();
      });
    }

    window.addEventListener("DOMContentLoaded", async () => {
      bindEvents();
      await loadState();
    });
  </script>
</body>
</html>
"""


class AnnotationWebApp:
    def __init__(
        self,
        session_dir: str | Path,
        *,
        schema_path: str | None = None,
        annotator: str | None = None,
    ) -> None:
        self.schema_path = schema_path
        self.annotator = annotator
        self.session_dir = Path(session_dir)
        self.reader = SessionReader(self.session_dir)
        self.service = create_annotation_service(self.session_dir, schema_path=schema_path, annotator=annotator)
        self.frame_index = self._build_frame_index()
        self.visual_streams = self._discover_visual_streams()
        self.audio_feature_index = self._build_audio_feature_index()
        self.audio_summary = self._discover_audio_summary()
        self._server: ThreadingHTTPServer | None = None
        self._refresh_navigation()

    def create_server(self, host: str = "127.0.0.1", port: int = 0) -> ThreadingHTTPServer:
        app = self

        class AnnotationRequestHandler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                parsed = urlparse(self.path)
                if parsed.path == "/":
                    app._write_html(self, INDEX_HTML)
                    return
                if parsed.path == "/api/state":
                    app._write_json(self, app.build_state())
                    return
                if parsed.path == "/api/frame":
                    params = parse_qs(parsed.query)
                    sensor_name = params.get("sensor_name", [None])[0]
                    frame_id = params.get("frame_id", [None])[0]
                    payload_key = params.get("payload_key", [None])[0]
                    if sensor_name is None or frame_id is None or payload_key is None:
                        app._write_error(self, HTTPStatus.BAD_REQUEST, "缺少 frame 参数")
                        return
                    try:
                        app.write_frame_response(
                            self,
                            sensor_name=sensor_name,
                            frame_id=int(frame_id),
                            payload_key=payload_key,
                        )
                    except Exception as exc:  # pragma: no cover - handled in tests via status code
                        app._write_error(self, HTTPStatus.BAD_REQUEST, str(exc))
                    return
                if parsed.path == "/api/audio":
                    params = parse_qs(parsed.query)
                    sensor_name = params.get("sensor_name", [None])[0]
                    frame_id = params.get("frame_id", [None])[0]
                    if sensor_name is None:
                        app._write_error(self, HTTPStatus.BAD_REQUEST, "缺少 audio 参数")
                        return
                    try:
                        app.write_audio_response(
                            self,
                            sensor_name=sensor_name,
                            frame_id=int(frame_id) if frame_id is not None else None,
                        )
                    except Exception as exc:  # pragma: no cover - handled in tests via status code
                        app._write_error(self, HTTPStatus.BAD_REQUEST, str(exc))
                    return
                app._write_error(self, HTTPStatus.NOT_FOUND, "未找到页面")

            def do_POST(self) -> None:  # noqa: N802
                self._handle_mutation("POST")

            def do_PUT(self) -> None:  # noqa: N802
                self._handle_mutation("PUT")

            def do_DELETE(self) -> None:  # noqa: N802
                self._handle_mutation("DELETE")

            def _handle_mutation(self, method: str) -> None:
                parsed = urlparse(self.path)
                body = self.rfile.read(int(self.headers.get("Content-Length", "0") or "0"))
                payload = json.loads(body.decode("utf-8")) if body else {}
                try:
                    if parsed.path == "/api/session" and method == "POST":
                        result = app.service.upsert_session_annotation(payload.get("data", {}))
                    elif parsed.path == "/api/navigate" and method == "POST":
                        result = app.navigate_session(str(payload.get("direction", "")))
                    elif parsed.path == "/api/shutdown" and method == "POST":
                        result = {"stopping": True}
                        app.schedule_shutdown()
                    elif parsed.path == "/api/spans" and method == "POST":
                        result = app.service.create_span(payload)
                    elif parsed.path.startswith("/api/spans/"):
                        span_id = parsed.path.rsplit("/", 1)[-1]
                        if method == "PUT":
                            result = app.service.update_span(span_id, payload)
                        elif method == "DELETE":
                            app.service.delete_span(span_id)
                            result = {"deleted": span_id}
                        else:
                            raise ValueError("不支持的 span 操作")
                    elif parsed.path == "/api/keyframes" and method == "POST":
                        result = app.service.create_keyframe(payload)
                    elif parsed.path.startswith("/api/keyframes/"):
                        keyframe_id = parsed.path.rsplit("/", 1)[-1]
                        if method == "PUT":
                            result = app.service.update_keyframe(keyframe_id, payload)
                        elif method == "DELETE":
                            app.service.delete_keyframe(keyframe_id)
                            result = {"deleted": keyframe_id}
                        else:
                            raise ValueError("不支持的 keyframe 操作")
                    else:
                        app._write_error(self, HTTPStatus.NOT_FOUND, "未找到 API")
                        return
                except Exception as exc:  # pragma: no cover
                    app._write_error(self, HTTPStatus.BAD_REQUEST, str(exc))
                    return
                app._write_json(self, {"ok": True, "result": result})

            def log_message(self, format: str, *args: object) -> None:  # noqa: A003
                return

        server = ThreadingHTTPServer((host, port), AnnotationRequestHandler)
        self._server = server
        return server

    def build_state(self) -> dict[str, Any]:
        aligned_records = list(self.reader.iter_aligned_records())
        bundle = self.service.load_bundle()
        return {
            "session_summary": self.reader.summary(),
            "schema": bundle.schema.to_dict(),
            "defaults": {
                scope: bundle.schema.defaults_for_scope(scope)
                for scope in ("session", "span", "keyframe")
            },
            "annotations": {
                "manifest": bundle.manifest,
                "session": bundle.session,
                "spans": bundle.spans,
                "keyframes": bundle.keyframes,
            },
            "aligned_records": [
                {
                    "sequence_id": record["sequence_id"],
                    "aligned_time": record["aligned_time"],
                    "frames": record.get("frames", {}),
                    "missing_sensors": record.get("missing_sensors", []),
                }
                for record in aligned_records
            ],
            "visual_streams": self.visual_streams,
            "audio_streams": self._discover_audio_streams(),
            "scalar_streams": self._build_scalar_streams(aligned_records),
            "audio_summary": self.audio_summary,
            "navigation": self._navigation_payload(),
        }

    def navigate_session(self, direction: str) -> dict[str, Any]:
        if direction not in {"previous", "next"}:
            raise ValueError("direction 必须是 previous 或 next")
        if not self.sibling_sessions:
            raise ValueError("当前目录下没有可翻页的 session")
        if self.current_session_index is None:
            raise ValueError("当前 session 不在可翻页列表中")
        offset = -1 if direction == "previous" else 1
        target_index = self.current_session_index + offset
        if not (0 <= target_index < len(self.sibling_sessions)):
            raise ValueError("没有更多 session 可以翻页")
        self._load_session(self.sibling_sessions[target_index])
        return {
            "session_dir": str(self.session_dir),
            "session_id": self.reader.manifest.session_id,
            "navigation": self._navigation_payload(),
        }

    def write_frame_response(
        self,
        handler: BaseHTTPRequestHandler,
        *,
        sensor_name: str,
        frame_id: int,
        payload_key: str,
    ) -> None:
        frame = self.frame_index.get(sensor_name, {}).get(frame_id)
        if frame is None:
            raise FileNotFoundError(f"未找到 frame: {sensor_name}#{frame_id}")
        reference = frame.payload.get(payload_key)
        if not isinstance(reference, dict) or "path" not in reference:
            raise ValueError(f"payload {payload_key} 不是 artifact 引用")
        storage = reference.get("storage")
        if storage == "png":
            artifact_path = self.session_dir / reference["path"]
            if self._should_reencode_legacy_rgb_png(frame, payload_key, reference):
                if cv2 is None:
                    raise RuntimeError("未安装 opencv-python，无法修正 legacy PNG 颜色通道")
                image = cv2.imread(str(artifact_path), cv2.IMREAD_UNCHANGED)
                if image is None:
                    raise FileNotFoundError(f"无法读取 PNG artifact: {artifact_path}")
                if image.ndim == 3 and image.shape[2] == 3:
                    image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
                success, encoded = cv2.imencode(".png", image)
                if not success:
                    raise RuntimeError("无法重新编码 legacy PNG artifact")
                data = encoded.tobytes()
                handler.send_response(HTTPStatus.OK)
                handler.send_header("Content-Type", "image/png")
                handler.send_header("Content-Length", str(len(data)))
                handler.end_headers()
                handler.wfile.write(data)
                return
            data = artifact_path.read_bytes()
            handler.send_response(HTTPStatus.OK)
            handler.send_header("Content-Type", "image/png")
            handler.send_header("Content-Length", str(len(data)))
            handler.end_headers()
            handler.wfile.write(data)
            return
        if storage in {"npy", "mp4_frame"} and cv2 is not None:
            array = self.reader.load_artifact(reference)
            data = self._encode_visual_preview(reference, array)
            handler.send_response(HTTPStatus.OK)
            handler.send_header("Content-Type", "image/png")
            handler.send_header("Content-Length", str(len(data)))
            handler.end_headers()
            handler.wfile.write(data)
            return
        raise ValueError(f"暂不支持该 artifact 预览: {storage}")

    def _encode_visual_preview(self, reference: dict[str, Any], array: np.ndarray) -> bytes:
        preview = np.asarray(array)
        if preview.ndim == 3 and preview.shape[2] == 3 and reference.get("channel_order") == "rgb":
            preview = cv2.cvtColor(preview, cv2.COLOR_RGB2BGR)
        elif preview.dtype != np.uint8:
            preview = cv2.normalize(preview, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
        success, encoded = cv2.imencode(".png", preview)
        if not success:
            raise RuntimeError("无法将当前帧编码为 PNG 预览")
        return encoded.tobytes()

    def _should_reencode_legacy_rgb_png(
        self,
        frame,
        payload_key: str,
        reference: dict[str, Any],
    ) -> bool:
        if reference.get("storage") != "png":
            return False
        if reference.get("channel_order") is not None:
            return False
        if payload_key not in {"color", "image"}:
            return False
        if frame.sensor_type in {
            "camera_sensor",
            "gelsight_sensor",
            "fake_camera_sensor",
            "fake_gelsight_sensor",
        }:
            return True
        return frame.modality in {"rgb", "visuotactile"} and frame.sensor_type not in {
            "realsense",
            "fake_realsense",
        }

    def write_audio_response(
        self,
        handler: BaseHTTPRequestHandler,
        *,
        sensor_name: str,
        frame_id: int | None = None,
    ) -> None:
        if frame_id is None:
            audio_array, metadata = self._load_audio_session(sensor_name)
        else:
            frame = self.frame_index.get(sensor_name, {}).get(frame_id)
            if frame is None:
                raise FileNotFoundError(f"未找到 frame: {sensor_name}#{frame_id}")
            if frame.modality != "audio":
                raise ValueError(f"{sensor_name} 不是音频流")

            audio_payload = frame.payload.get("audio")
            if audio_payload is None:
                raise ValueError(f"{sensor_name}#{frame_id} 不包含 audio payload")

            audio_array = self._load_audio_array(audio_payload)
            metadata = dict(frame.metadata)

        wav_data = self._encode_wav_bytes(audio_array, metadata=metadata)
        handler.send_response(HTTPStatus.OK)
        handler.send_header("Content-Type", "audio/wav")
        handler.send_header("Content-Length", str(len(wav_data)))
        handler.end_headers()
        handler.wfile.write(wav_data)

    def _write_json(self, handler: BaseHTTPRequestHandler, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.send_header("Content-Length", str(len(data)))
        handler.end_headers()
        handler.wfile.write(data)

    def _write_html(self, handler: BaseHTTPRequestHandler, payload: str) -> None:
        data = payload.encode("utf-8")
        handler.send_response(HTTPStatus.OK)
        handler.send_header("Content-Type", "text/html; charset=utf-8")
        handler.send_header("Content-Length", str(len(data)))
        handler.end_headers()
        handler.wfile.write(data)

    def _write_error(self, handler: BaseHTTPRequestHandler, status: HTTPStatus, message: str) -> None:
        self._write_json(handler, {"ok": False, "error": message}, status=status)

    def _load_session(self, session_dir: str | Path) -> None:
        self.session_dir = Path(session_dir)
        self.reader = SessionReader(self.session_dir)
        self.service = create_annotation_service(
            self.session_dir,
            schema_path=self.schema_path,
            annotator=self.annotator,
        )
        self.frame_index = self._build_frame_index()
        self.visual_streams = self._discover_visual_streams()
        self.audio_feature_index = self._build_audio_feature_index()
        self.audio_summary = self._discover_audio_summary()
        self._refresh_navigation()

    def _refresh_navigation(self) -> None:
        self.sibling_sessions = self._discover_sibling_sessions()
        try:
            self.current_session_index = self.sibling_sessions.index(self.session_dir)
        except ValueError:
            self.current_session_index = None

    def _navigation_payload(self) -> dict[str, Any]:
        total = len(self.sibling_sessions)
        current_index = self.current_session_index
        return {
            "total": total,
            "position": (current_index + 1) if current_index is not None else None,
            "has_previous": current_index is not None and current_index > 0,
            "has_next": current_index is not None and current_index < total - 1,
            "current_session_dir": str(self.session_dir),
            "current_session_name": self.session_dir.name,
        }

    def _discover_sibling_sessions(self) -> list[Path]:
        parent = self.session_dir.parent
        if not parent.exists():
            return [self.session_dir]
        candidates = [
            path
            for path in parent.iterdir()
            if path.is_dir() and self._looks_like_session_dir(path)
        ]
        if self.session_dir not in candidates:
            candidates.append(self.session_dir)
        return sorted(candidates, key=lambda item: item.name)

    def _looks_like_session_dir(self, path: Path) -> bool:
        if (path / "manifest.json").exists() or (path / "meta.json").exists():
            return True
        return (path / "streams").is_dir() and (path / "aligned.jsonl").exists()

    def _build_frame_index(self) -> dict[str, dict[int, Any]]:
        return {
            sensor_name: {
                frame.frame_id: frame
                for frame in self.reader.iter_sensor_frames(sensor_name, load_payload=False)
            }
            for sensor_name in self.reader.sensor_names()
        }

    def _discover_visual_streams(self) -> list[dict[str, Any]]:
        streams: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for sensor_name, frames in self.frame_index.items():
            for frame in frames.values():
                for payload_key, value in frame.payload.items():
                    if not self._is_visual_reference(value):
                        continue
                    key = (sensor_name, payload_key)
                    if key in seen:
                        continue
                    seen.add(key)
                    streams.append(
                        {
                            "sensor_name": sensor_name,
                            "payload_key": payload_key,
                            "label": f"{sensor_name}.{payload_key}",
                        }
                    )
        return streams

    def _discover_audio_summary(self) -> list[dict[str, Any]]:
        summaries: list[dict[str, Any]] = []
        for sensor_name, stream in self.reader.manifest.sensors.items():
            if stream.modality != "audio":
                continue
            frame_count = sum(1 for _ in self.reader.iter_sensor_frames(sensor_name, load_payload=False))
            summaries.append(
                {
                    "sensor_name": sensor_name,
                    "frame_count": frame_count,
                    "metadata": stream.metadata,
                }
            )
        return summaries

    def _discover_audio_streams(self) -> list[dict[str, Any]]:
        streams: list[dict[str, Any]] = []
        for item in self.audio_summary:
            metadata = item.get("metadata", {}) or {}
            sample_rate = metadata.get("sample_rate")
            channels = metadata.get("channels")
            total_samples = 0
            for frame in self.reader.iter_sensor_frames(item["sensor_name"], load_payload=False):
                audio_payload = frame.payload.get("audio")
                if sample_rate in (None, 0) and isinstance(audio_payload, dict):
                    sample_rate = audio_payload.get("sample_rate", sample_rate)
                if channels is None and isinstance(audio_payload, dict):
                    channels = audio_payload.get("channels", channels)
                total_samples += self._audio_payload_length(audio_payload)
            duration_sec = (
                float(total_samples) / float(sample_rate)
                if sample_rate not in (None, 0) and total_samples > 0
                else None
            )
            streams.append(
                {
                    "sensor_name": item["sensor_name"],
                    "label": item["sensor_name"],
                    "channels": channels,
                    "sample_rate": sample_rate,
                    "chunk": metadata.get("chunk"),
                    "duration_sec": duration_sec,
                }
            )
        return streams

    def _build_scalar_streams(self, aligned_records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        series: dict[str, dict[str, Any]] = {}
        for record in aligned_records:
            sequence_id = int(record["sequence_id"])
            for sensor_name, frame_info in record.get("frames", {}).items():
                frame = self.frame_index.get(sensor_name, {}).get(frame_info["frame_id"])
                if frame is None:
                    continue
                if frame.modality == "audio":
                    audio_features = self.audio_feature_index.get(sensor_name, {}).get(frame.frame_id, {})
                    for key, value in audio_features.items():
                        self._append_scalar_point(
                            series,
                            key=key,
                            sequence_id=sequence_id,
                            aligned_time=record["aligned_time"],
                            value=value,
                        )
                    continue
                if frame.modality == "force_torque":
                    wrench_features = self._extract_wrench_features(sensor_name, frame.payload)
                    for key, value in wrench_features.items():
                        self._append_scalar_point(
                            series,
                            key=key,
                            sequence_id=sequence_id,
                            aligned_time=record["aligned_time"],
                            value=value,
                        )
                    continue
                if frame.modality == "motor_state":
                    motor_features = self._extract_motor_features(sensor_name, frame.payload)
                    for key, value in motor_features.items():
                        self._append_scalar_point(
                            series,
                            key=key,
                            sequence_id=sequence_id,
                            aligned_time=record["aligned_time"],
                            value=value,
                        )
                    continue
                flattened: dict[str, float] = {}
                self._collect_scalar_values(frame.payload, prefix=sensor_name, output=flattened)
                for key, value in flattened.items():
                    self._append_scalar_point(
                        series,
                        key=key,
                        sequence_id=sequence_id,
                        aligned_time=record["aligned_time"],
                        value=value,
                    )
        return [series[key] for key in sorted(series.keys())]

    def _append_scalar_point(
        self,
        series: dict[str, dict[str, Any]],
        *,
        key: str,
        sequence_id: int,
        aligned_time: float,
        value: float,
    ) -> None:
        stream = series.setdefault(
            key,
            {
                "id": key,
                "label": self._format_scalar_label(key),
                "points": [],
            },
        )
        stream["points"].append(
            {
                "sequence_id": sequence_id,
                "aligned_time": aligned_time,
                "value": value,
            }
        )

    def _format_scalar_label(self, key: str) -> str:
        parts = key.split(".")
        if len(parts) == 3 and parts[1] in {"force", "torque"} and parts[2] in {"0", "1", "2"}:
            axis = {"0": "x", "1": "y", "2": "z"}[parts[2]]
            return ".".join([parts[0], parts[1], axis])
        return key

    def _collect_scalar_values(self, value: Any, *, prefix: str, output: dict[str, float]) -> None:
        if isinstance(value, dict):
            if {"path", "storage", "shape", "dtype"} <= set(value.keys()):
                return
            for child_key, child_value in value.items():
                self._collect_scalar_values(child_value, prefix=f"{prefix}.{child_key}", output=output)
            return
        if isinstance(value, list):
            if value and all(isinstance(item, (int, float)) and not isinstance(item, bool) for item in value):
                for index, item in enumerate(value):
                    output[f"{prefix}.{index}"] = float(item)
            return
        if isinstance(value, np.ndarray):
            if value.ndim == 1:
                for index, item in enumerate(value.tolist()):
                    if isinstance(item, (int, float)) and not isinstance(item, bool):
                        output[f"{prefix}.{index}"] = float(item)
            return
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            output[prefix] = float(value)

    def _extract_wrench_features(self, sensor_name: str, payload: dict[str, Any]) -> dict[str, float]:
        features: dict[str, float] = {}

        force = payload.get("force")
        torque = payload.get("torque")
        if isinstance(force, list) and len(force) >= 3:
            for index, value in enumerate(force[:3]):
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    features[f"{sensor_name}.force.{index}"] = float(value)
        if isinstance(torque, list) and len(torque) >= 3:
            for index, value in enumerate(torque[:3]):
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    features[f"{sensor_name}.torque.{index}"] = float(value)

        if features:
            return features

        wrench = payload.get("force_torque")
        if isinstance(wrench, list) and len(wrench) >= 6:
            for index, value in enumerate(wrench[:3]):
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    features[f"{sensor_name}.force.{index}"] = float(value)
            for index, value in enumerate(wrench[3:6]):
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    features[f"{sensor_name}.torque.{index}"] = float(value)
        return features

    def _extract_motor_features(self, sensor_name: str, payload: dict[str, Any]) -> dict[str, float]:
        features: dict[str, float] = {}

        for motor_key in ("motor_1", "motor_2"):
            motor_payload = payload.get(motor_key)
            if not isinstance(motor_payload, dict):
                continue
            for field_name in ("position", "velocity", "torque"):
                value = motor_payload.get(field_name)
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    features[f"{sensor_name}.{motor_key}.{field_name}"] = float(value)

        if features:
            return features

        state = payload.get("motor_state")
        if isinstance(state, list) and len(state) >= 6:
            fallback_fields = (
                ("motor_1", "position", state[0]),
                ("motor_1", "velocity", state[1]),
                ("motor_1", "torque", state[2]),
                ("motor_2", "position", state[3]),
                ("motor_2", "velocity", state[4]),
                ("motor_2", "torque", state[5]),
            )
            for motor_key, field_name, value in fallback_fields:
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    features[f"{sensor_name}.{motor_key}.{field_name}"] = float(value)
        return features

    def _build_audio_feature_index(self) -> dict[str, dict[int, dict[str, float]]]:
        features: dict[str, dict[int, dict[str, float]]] = {}
        for sensor_name, stream in self.reader.manifest.sensors.items():
            if stream.modality != "audio":
                continue
            per_frame: dict[int, dict[str, float]] = {}
            for frame in self.reader.iter_sensor_frames(sensor_name, load_payload=True):
                audio_payload = frame.payload.get("audio")
                if audio_payload is None:
                    continue
                per_frame[frame.frame_id] = self._extract_audio_features(sensor_name, audio_payload)
            features[sensor_name] = per_frame
        return features

    def _load_audio_array(self, value: Any) -> np.ndarray:
        if isinstance(value, dict) and {"path", "storage", "shape", "dtype"} <= set(value.keys()):
            loaded = self.reader.load_artifact(value)
        else:
            loaded = value
        array = np.asarray(loaded)
        if array.size == 0:
            raise ValueError("音频数据为空")
        if array.ndim == 1:
            return array.reshape(-1, 1)
        if array.ndim == 2:
            return array
        raise ValueError(f"不支持的音频形状: {array.shape}")

    def _load_audio_session(self, sensor_name: str) -> tuple[np.ndarray, dict[str, Any]]:
        stream = self.reader.manifest.sensors.get(sensor_name)
        if stream is None:
            raise FileNotFoundError(f"未找到音频流: {sensor_name}")
        if stream.modality != "audio":
            raise ValueError(f"{sensor_name} 不是音频流")

        chunks: list[np.ndarray] = []
        metadata = dict(stream.metadata or {})
        for frame in self.reader.iter_sensor_frames(sensor_name, load_payload=True):
            if frame.modality != "audio":
                continue
            audio_payload = frame.payload.get("audio")
            if audio_payload is None:
                continue
            array = self._load_audio_array(audio_payload)
            if not metadata:
                metadata = dict(frame.metadata)
            chunks.append(array)

        if not chunks:
            raise ValueError(f"{sensor_name} 不包含可播放的音频帧")

        channel_count = chunks[0].shape[1]
        normalized_chunks: list[np.ndarray] = []
        for chunk in chunks:
            if chunk.shape[1] != channel_count:
                raise ValueError(f"{sensor_name} 音频通道数不一致，无法拼接整段音频")
            normalized_chunks.append(chunk)
        return np.concatenate(normalized_chunks, axis=0), metadata

    def _audio_payload_length(self, value: Any) -> int:
        if value is None:
            return 0
        if isinstance(value, dict) and {"path", "storage", "shape", "dtype"} <= set(value.keys()):
            shape = value.get("shape") or []
            if isinstance(shape, list) and shape:
                return int(shape[0])
            return 0
        if isinstance(value, list):
            return len(value)
        if isinstance(value, np.ndarray):
            return int(value.shape[0]) if value.ndim >= 1 else 0
        return 0

    def _encode_wav_bytes(self, audio_array: np.ndarray, *, metadata: dict[str, Any]) -> bytes:
        sample_rate = int(metadata.get("sample_rate") or 48000)
        channels = int(metadata.get("channels") or (audio_array.shape[1] if audio_array.ndim == 2 else 1))
        pcm = np.asarray(audio_array)
        if pcm.ndim == 1:
            pcm = pcm.reshape(-1, 1)
        if pcm.shape[1] != channels:
            channels = pcm.shape[1]

        if pcm.dtype != np.int16:
            if np.issubdtype(pcm.dtype, np.floating):
                pcm = np.clip(pcm, -1.0, 1.0)
                pcm = (pcm * 32767.0).astype(np.int16)
            else:
                pcm = np.clip(pcm, -32768, 32767).astype(np.int16)

        with io.BytesIO() as buffer:
            with wave.open(buffer, "wb") as wav_file:
                wav_file.setnchannels(channels)
                wav_file.setsampwidth(2)
                wav_file.setframerate(sample_rate)
                wav_file.writeframes(np.ascontiguousarray(pcm).tobytes())
            return buffer.getvalue()

    def _extract_audio_features(self, sensor_name: str, audio_payload: Any) -> dict[str, float]:
        array = np.asarray(audio_payload, dtype=np.float64)
        if array.size == 0:
            return {}
        if array.ndim == 1:
            array = array.reshape(-1, 1)
        elif array.ndim > 2:
            array = array.reshape(array.shape[0], -1)

        rms = np.sqrt(np.mean(np.square(array), axis=0))
        peak = np.max(np.abs(array), axis=0)
        if array.shape[1] == 1:
            return {
                f"{sensor_name}.audio_rms": float(rms[0]),
                f"{sensor_name}.audio_peak": float(peak[0]),
            }

        features: dict[str, float] = {}
        for channel_index, value in enumerate(rms.tolist()):
            features[f"{sensor_name}.audio_rms.{channel_index}"] = float(value)
        for channel_index, value in enumerate(peak.tolist()):
            features[f"{sensor_name}.audio_peak.{channel_index}"] = float(value)
        return features

    def _is_visual_reference(self, value: Any) -> bool:
        if not isinstance(value, dict) or "path" not in value:
            return False
        storage = value.get("storage")
        if storage == "png":
            return True
        if storage == "mp4_frame":
            return cv2 is not None
        return storage == "npy" and cv2 is not None

    def schedule_shutdown(self) -> None:
        if self._server is None:
            return
        threading.Thread(target=self._server.shutdown, daemon=True).start()


class RunningAnnotationServer:
    def __init__(self, app: AnnotationWebApp, server: ThreadingHTTPServer, thread: threading.Thread | None = None) -> None:
        self.app = app
        self.server = server
        self.thread = thread

    @property
    def url(self) -> str:
        host, port = self.server.server_address[:2]
        return f"http://{host}:{port}/"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        if self.thread is not None:
            self.thread.join(timeout=5.0)


def start_annotation_server(
    session_dir: str | Path,
    *,
    schema_path: str | None = None,
    annotator: str | None = None,
    host: str = "127.0.0.1",
    port: int = 0,
    background: bool = True,
) -> RunningAnnotationServer:
    app = AnnotationWebApp(session_dir, schema_path=schema_path, annotator=annotator)
    server = app.create_server(host=host, port=port)
    thread: threading.Thread | None = None
    if background:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
    return RunningAnnotationServer(app, server, thread=thread)


def run_annotation_server(
    session_dir: str | Path,
    *,
    schema_path: str | None = None,
    annotator: str | None = None,
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = True,
) -> None:
    running = start_annotation_server(
        session_dir,
        schema_path=schema_path,
        annotator=annotator,
        host=host,
        port=port,
        background=False,
    )
    if open_browser:
        webbrowser.open(running.url)
    try:
        running.server.serve_forever()
    finally:
        running.close()
