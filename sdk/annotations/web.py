from __future__ import annotations

import json
import threading
import webbrowser
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
    h1 { margin: 0 0 6px; font-size: 28px; }
    p { margin: 0; color: var(--muted); }
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
    }
    button.secondary { background: #374151; }
    button.danger { background: var(--danger); }
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
    <h1>Session Annotation Workbench</h1>
    <p id="header-summary">Loading session…</p>
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
          <div class="meta-line" id="audio-summary"></div>
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
        <h2>Scalar Curves</h2>
        <div id="curve-grid" class="grid"></div>
      </div>
    </section>
    <aside class="stack">
      <div class="panel">
        <h2>Session Annotation</h2>
        <form id="session-form" class="form-grid"></form>
        <div class="actions">
          <button id="save-session" type="button">Save Session</button>
        </div>
      </div>
      <div class="panel">
        <h2>Span Annotation</h2>
        <form id="span-form" class="form-grid"></form>
        <div class="actions">
          <button id="save-span" type="button">Save Span</button>
          <button id="new-span" class="secondary" type="button">New Span</button>
          <button id="delete-span" class="danger" type="button">Delete Span</button>
        </div>
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
    };

    const colors = ["#0f766e", "#d97706", "#2563eb", "#9333ea", "#dc2626", "#059669", "#4f46e5"];

    async function loadState() {
      const response = await fetch("/api/state");
      state.payload = await response.json();
      const records = state.payload.aligned_records || [];
      const slider = document.getElementById("sequence-slider");
      slider.max = String(Math.max(records.length - 1, 0));
      state.currentSequence = Math.min(state.currentSequence, Math.max(records.length - 1, 0));
      slider.value = String(state.currentSequence);
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

    function renderAll() {
      renderHeader();
      renderTimeline();
      renderVideos();
      renderCurves();
      renderSessionForm();
      renderSpanForm();
      renderKeyframeForm();
      renderSpanList();
      renderKeyframeList();
    }

    function renderHeader() {
      const summary = state.payload.session_summary;
      document.getElementById("header-summary").textContent =
        `${summary.session_id} · ${summary.sensor_names.join(", ")} · schema ${state.payload.schema.version}`;
      const record = currentRecord();
      document.getElementById("sequence-label").textContent =
        record ? `Seq ${record.sequence_id}` : "Seq -";
      document.getElementById("time-label").textContent =
        record ? `Time ${record.aligned_time.toFixed(3)}s` : "Time -";
      document.getElementById("annotation-label").textContent =
        `Annotations ${state.payload.annotations.spans.length} spans / ${state.payload.annotations.keyframes.length} keyframes`;
      const audio = state.payload.audio_summary || [];
      document.getElementById("audio-summary").innerHTML = audio.length
        ? audio.map((item) => `<span>${item.sensor_name}: ${item.frame_count} audio frames</span>`).join("")
        : "<span>No audio summary</span>";
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

    function renderCurves() {
      const target = document.getElementById("curve-grid");
      const streams = state.payload.scalar_streams || [];
      if (!streams.length) {
        target.innerHTML = '<div class="empty-note">No scalar curves available for FT / IMU / Motors in this session.</div>';
        return;
      }
      target.innerHTML = streams.map((stream, index) => `
        <article class="curve-card">
          <header>${stream.label}</header>
          <canvas id="curve-${index}" class="curve-canvas" width="360" height="120"></canvas>
        </article>
      `).join("");
      streams.forEach((stream, index) => drawCurve(document.getElementById(`curve-${index}`), stream, colors[index % colors.length]));
    }

    function drawCurve(canvas, stream, color) {
      const ctx = canvas.getContext("2d");
      const points = stream.points || [];
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      ctx.fillStyle = "#ffffff";
      ctx.fillRect(0, 0, canvas.width, canvas.height);
      ctx.strokeStyle = "#e5e7eb";
      ctx.strokeRect(0, 0, canvas.width, canvas.height);
      if (!points.length) return;
      const minV = Math.min(...points.map((point) => point.value));
      const maxV = Math.max(...points.map((point) => point.value));
      const range = maxV - minV || 1;
      const xFor = (seq) => 18 + ((canvas.width - 36) * seq) / Math.max(points[points.length - 1].sequence_id, 1);
      const yFor = (value) => canvas.height - 18 - ((canvas.height - 36) * (value - minV)) / range;
      ctx.strokeStyle = color;
      ctx.beginPath();
      points.forEach((point, idx) => {
        const x = xFor(point.sequence_id);
        const y = yFor(point.value);
        if (idx === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      });
      ctx.stroke();

      const currentPoint = points.find((point) => point.sequence_id === state.currentSequence);
      if (currentPoint) {
        ctx.fillStyle = "#111827";
        ctx.beginPath();
        ctx.arc(xFor(currentPoint.sequence_id), yFor(currentPoint.value), 4, 0, Math.PI * 2);
        ctx.fill();
      }
    }

    function fieldControl(scope, field, value) {
      const fieldId = `${scope}-${field.id}`;
      if (field.type === "string") {
        return `<div class="field"><label for="${fieldId}">${field.label}</label><textarea id="${fieldId}" data-scope="${scope}" data-field="${field.id}">${value ?? ""}</textarea></div>`;
      }
      if (field.type === "number") {
        return `<div class="field"><label for="${fieldId}">${field.label}</label><input id="${fieldId}" type="number" step="any" data-scope="${scope}" data-field="${field.id}" value="${value ?? ""}" /></div>`;
      }
      if (field.type === "bool") {
        return `<div class="field"><label class="inline-check"><input id="${fieldId}" type="checkbox" data-scope="${scope}" data-field="${field.id}" ${value ? "checked" : ""} />${field.label}</label></div>`;
      }
      if (field.type === "enum") {
        const options = ['<option value=""></option>'].concat(field.options.map((option) =>
          `<option value="${option}" ${value === option ? "selected" : ""}>${option}</option>`
        ));
        return `<div class="field"><label for="${fieldId}">${field.label}</label><select id="${fieldId}" data-scope="${scope}" data-field="${field.id}">${options.join("")}</select></div>`;
      }
      if (field.type === "multi_enum") {
        const selected = new Set(Array.isArray(value) ? value : []);
        return `<div class="field"><label>${field.label}</label><div class="field-group check-group">${
          field.options.map((option, index) => `
            <label><input type="checkbox" data-scope="${scope}" data-field="${field.id}" data-kind="multi_enum" value="${option}" ${selected.has(option) ? "checked" : ""} />${option}</label>
          `).join("")
        }</div></div>`;
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
      const startSeq = selected ? selected.start_sequence_id : state.currentSequence;
      const endSeq = selected ? selected.end_sequence_id : state.currentSequence;
      const startTime = selected ? selected.start_time : record ? record.aligned_time : null;
      const endTime = selected ? selected.end_time : record ? record.aligned_time : null;
      target.innerHTML = `
        <div class="field">
          <label for="span-id">Span ID</label>
          <input id="span-id" type="text" value="${selected ? selected.id : ""}" disabled />
        </div>
        <div class="field-group">
          <div class="field"><label for="span-start-seq">Start Sequence</label><input id="span-start-seq" type="number" value="${startSeq}" /></div>
          <div class="field"><label for="span-end-seq">End Sequence</label><input id="span-end-seq" type="number" value="${endSeq}" /></div>
          <div class="field"><label for="span-start-time">Start Time</label><input id="span-start-time" type="number" step="any" value="${startTime ?? ""}" /></div>
          <div class="field"><label for="span-end-time">End Time</label><input id="span-end-time" type="number" step="any" value="${endTime ?? ""}" /></div>
        </div>
        ${schemaFields("span").map((field) => fieldControl("span", field, current[field.id] ?? defaultsFor("span")[field.id])).join("")}
      `;
    }

    function renderKeyframeForm() {
      const target = document.getElementById("keyframe-form");
      const selected = selectedKeyframe();
      const record = currentRecord();
      const current = selected ? selected.data : defaultsFor("keyframe");
      const sequenceId = selected ? selected.sequence_id : state.currentSequence;
      const alignedTime = selected ? selected.aligned_time : record ? record.aligned_time : null;
      target.innerHTML = `
        <div class="field">
          <label for="keyframe-id">Keyframe ID</label>
          <input id="keyframe-id" type="text" value="${selected ? selected.id : ""}" disabled />
        </div>
        <div class="field-group">
          <div class="field"><label for="keyframe-sequence">Sequence</label><input id="keyframe-sequence" type="number" value="${sequenceId}" /></div>
          <div class="field"><label for="keyframe-time">Aligned Time</label><input id="keyframe-time" type="number" step="any" value="${alignedTime ?? ""}" /></div>
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
          if (checked.length) data[field.id] = checked;
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
        if (field.type === "number") data[field.id] = Number(raw);
        else data[field.id] = raw;
      });
      return data;
    }

    async function saveSessionAnnotation() {
      await fetch("/api/session", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ data: readScopedData("session") }),
      });
      await loadState();
    }

    async function saveSpanAnnotation() {
      const numOrNull = (value) => value === "" ? null : Number(value);
      const payload = {
        start_sequence_id: Number(document.getElementById("span-start-seq").value),
        end_sequence_id: Number(document.getElementById("span-end-seq").value),
        start_time: numOrNull(document.getElementById("span-start-time").value),
        end_time: numOrNull(document.getElementById("span-end-time").value),
        data: readScopedData("span"),
      };
      const id = state.selectedSpanId;
      await fetch(id ? `/api/spans/${id}` : "/api/spans", {
        method: id ? "PUT" : "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      await loadState();
    }

    async function saveKeyframeAnnotation() {
      const numOrNull = (value) => value === "" ? null : Number(value);
      const payload = {
        sequence_id: Number(document.getElementById("keyframe-sequence").value),
        aligned_time: numOrNull(document.getElementById("keyframe-time").value),
        data: readScopedData("keyframe"),
      };
      const id = state.selectedKeyframeId;
      await fetch(id ? `/api/keyframes/${id}` : "/api/keyframes", {
        method: id ? "PUT" : "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      await loadState();
    }

    async function deleteSpanAnnotation() {
      if (!state.selectedSpanId) return;
      await fetch(`/api/spans/${state.selectedSpanId}`, { method: "DELETE" });
      state.selectedSpanId = null;
      await loadState();
    }

    async function deleteKeyframeAnnotation() {
      if (!state.selectedKeyframeId) return;
      await fetch(`/api/keyframes/${state.selectedKeyframeId}`, { method: "DELETE" });
      state.selectedKeyframeId = null;
      await loadState();
    }

    function resetSpanForm() {
      state.selectedSpanId = null;
      state.dragStart = null;
      state.dragCurrent = null;
      renderSpanForm();
      renderTimeline();
    }

    function resetKeyframeForm() {
      state.selectedKeyframeId = null;
      renderKeyframeForm();
    }

    function bindEvents() {
      document.getElementById("sequence-slider").addEventListener("input", (event) => {
        state.currentSequence = Number(event.target.value);
        renderAll();
      });
      document.getElementById("save-session").addEventListener("click", saveSessionAnnotation);
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
        state.currentSequence = state.dragStart;
        renderAll();
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
        document.getElementById("span-start-seq").value = String(start);
        document.getElementById("span-end-seq").value = String(end);
        document.getElementById("span-start-time").value = String(records[start] ? records[start].aligned_time : "");
        document.getElementById("span-end-time").value = String(records[end] ? records[end].aligned_time : "");
        state.dragStart = null;
        state.dragCurrent = null;
        renderTimeline();
      });
      canvas.addEventListener("dblclick", (event) => {
        const sequence = seqFromEvent(event);
        const records = state.payload.aligned_records || [];
        state.currentSequence = sequence;
        document.getElementById("keyframe-sequence").value = String(sequence);
        document.getElementById("keyframe-time").value = String(records[sequence] ? records[sequence].aligned_time : "");
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
        self.session_dir = Path(session_dir)
        self.reader = SessionReader(self.session_dir)
        self.service = create_annotation_service(self.session_dir, schema_path=schema_path, annotator=annotator)
        self.frame_index = self._build_frame_index()
        self.visual_streams = self._discover_visual_streams()
        self.audio_summary = self._discover_audio_summary()

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

        return ThreadingHTTPServer((host, port), AnnotationRequestHandler)

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
            "scalar_streams": self._build_scalar_streams(aligned_records),
            "audio_summary": self.audio_summary,
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
        artifact_path = self.session_dir / reference["path"]
        storage = reference.get("storage")
        if storage == "png":
            data = artifact_path.read_bytes()
            handler.send_response(HTTPStatus.OK)
            handler.send_header("Content-Type", "image/png")
            handler.send_header("Content-Length", str(len(data)))
            handler.end_headers()
            handler.wfile.write(data)
            return
        if storage == "npy" and cv2 is not None:
            array = np.load(artifact_path, allow_pickle=False)
            normalized = cv2.normalize(array, None, 0, 255, cv2.NORM_MINMAX)
            success, encoded = cv2.imencode(".png", normalized.astype(np.uint8))
            if not success:
                raise RuntimeError("无法将 npy artifact 转换为 PNG")
            data = encoded.tobytes()
            handler.send_response(HTTPStatus.OK)
            handler.send_header("Content-Type", "image/png")
            handler.send_header("Content-Length", str(len(data)))
            handler.end_headers()
            handler.wfile.write(data)
            return
        raise ValueError(f"暂不支持该 artifact 预览: {storage}")

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

    def _build_scalar_streams(self, aligned_records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        series: dict[str, dict[str, Any]] = {}
        for record in aligned_records:
            sequence_id = int(record["sequence_id"])
            for sensor_name, frame_info in record.get("frames", {}).items():
                frame = self.frame_index.get(sensor_name, {}).get(frame_info["frame_id"])
                if frame is None:
                    continue
                flattened: dict[str, float] = {}
                self._collect_scalar_values(frame.payload, prefix=sensor_name, output=flattened)
                for key, value in flattened.items():
                    stream = series.setdefault(
                        key,
                        {
                            "id": key,
                            "label": key,
                            "points": [],
                        },
                    )
                    stream["points"].append(
                        {
                            "sequence_id": sequence_id,
                            "aligned_time": record["aligned_time"],
                            "value": value,
                        }
                    )
        return list(series.values())

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

    def _is_visual_reference(self, value: Any) -> bool:
        if not isinstance(value, dict) or "path" not in value:
            return False
        storage = value.get("storage")
        if storage == "png":
            return True
        return storage == "npy" and cv2 is not None


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
