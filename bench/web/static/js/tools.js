/**
 * tools.js — Tool call panel rendering.
 *
 * Cards show preview (3-line clamp). Click tool name → modal with full details.
 * Exports: QTB.addToolStart, QTB.updateToolResult, QTB.clearTools, QTB.buildToolReplay
 */

(function (window) {
  'use strict';

  var pendingTools = {};
  var _toolCounter = 0;
  // Store full data for modal access
  var _toolData = {};

  function truncate(s, n) {
    if (typeof s !== 'string') s = JSON.stringify(s, null, 2);
    if (!s) return '';
    return s.length > n ? s.slice(0, n) + '\u2026' : s;
  }

  function formatArgs(args) {
    if (!args || typeof args !== 'object') return '';
    var keys = Object.keys(args);
    if (keys.length === 0) return '';
    return keys.map(function (k) {
      var v = args[k];
      var val = typeof v === 'string' ? v : JSON.stringify(v, null, 2);
      return k + ': ' + val;
    }).join('\n');
  }

  function formatArgsShort(args) {
    if (!args || typeof args !== 'object') return '';
    var keys = Object.keys(args);
    if (keys.length === 0) return '';
    return keys.map(function (k) {
      var v = args[k];
      var val = typeof v === 'string' ? truncate(v, 80) : JSON.stringify(v);
      return k + ': ' + val;
    }).join('\n');
  }

  // ── Modal for full tool details ───────────────────────────

  function showToolModal(toolInfo) {
    // Reuse the global showModal from app.js
    var name = toolInfo.name || 'unknown';
    var status = toolInfo.success !== false ? 'ok' : 'fail';
    var duration = toolInfo.duration_ms ? Math.round(toolInfo.duration_ms) + 'ms' : '';

    var html =
      '<div style="margin-bottom:16px;display:flex;align-items:center;gap:10px">' +
        '<span style="font-family:var(--font-mono);font-size:16px;font-weight:700;color:var(--accent)">' +
          QTB.escapeHtml(name) +
        '</span>' +
        '<span class="tool-status ' + (toolInfo.success !== false ? 'success' : 'failed') + '">' + status + '</span>' +
        (duration ? '<span style="color:var(--text-muted);font-size:13px;font-family:var(--font-mono)">' + duration + '</span>' : '') +
      '</div>';

    if (toolInfo.args && Object.keys(toolInfo.args).length > 0) {
      html += '<div style="margin-bottom:12px">' +
        '<div style="font-size:12px;font-weight:600;color:var(--text-muted);margin-bottom:6px;text-transform:uppercase;letter-spacing:0.05em">Arguments</div>' +
        '<pre>' + QTB.escapeHtml(formatArgs(toolInfo.args)) + '</pre>' +
      '</div>';
    }

    if (toolInfo.result) {
      html += '<div>' +
        '<div style="font-size:12px;font-weight:600;color:var(--text-muted);margin-bottom:6px;text-transform:uppercase;letter-spacing:0.05em">Result</div>' +
        '<pre>' + QTB.escapeHtml(toolInfo.result) + '</pre>' +
      '</div>';
    }

    // Access the global showModal (defined in app.js, attached to window)
    if (window._qtbShowModal) {
      window._qtbShowModal(name, html);
    }
  }

  // ── Build tool card element ───────────────────────────────

  function buildToolCard(id, name, statusClass, statusText, argsPreview, resultPreview, duration) {
    var div = document.createElement('div');
    div.className = 'tool-call';
    div.id = 'tool-' + id;

    var header = document.createElement('div');
    header.className = 'tool-header';

    var nameSpan = document.createElement('span');
    nameSpan.className = 'tool-name';
    nameSpan.textContent = name;
    nameSpan.addEventListener('click', function () {
      var info = _toolData[id];
      if (info) showToolModal(info);
    });

    var statusSpan = document.createElement('span');
    statusSpan.className = 'tool-status ' + statusClass;
    statusSpan.textContent = statusText;

    var durSpan = document.createElement('span');
    durSpan.className = 'tool-duration';
    durSpan.textContent = duration || '';

    header.appendChild(nameSpan);
    header.appendChild(statusSpan);
    header.appendChild(durSpan);
    div.appendChild(header);

    // Preview (clamped)
    var preview = argsPreview || resultPreview || '';
    if (preview) {
      var previewDiv = document.createElement('div');
      previewDiv.className = 'tool-preview';
      previewDiv.textContent = preview;
      div.appendChild(previewDiv);
    }

    return div;
  }

  // ── Public: live events ───────────────────────────────────

  function addToolStart(container, data) {
    _toolCounter++;
    var id = data.call_id || ('t' + _toolCounter);

    // Store full data
    _toolData[id] = {
      name: data.name || 'unknown',
      args: data.args || {},
      result: null,
      success: null,
      duration_ms: null,
    };

    var preview = formatArgsShort(data.args);
    var div = buildToolCard(id, data.name || 'unknown', 'running', 'running', preview, '', '');

    container.appendChild(div);
    pendingTools[id] = { el: div, start: data.ts || Date.now() / 1000 };
    container.scrollTop = container.scrollHeight;
    return div;
  }

  function updateToolResult(container, data) {
    var id = data.call_id || '';
    var pending = pendingTools[id];
    if (!pending) return;

    var el = pending.el;
    var ok = data.success !== false;

    // Update status badge
    var statusSpan = el.querySelector('.tool-status');
    statusSpan.className = 'tool-status ' + (ok ? 'success' : 'failed');
    statusSpan.textContent = ok ? 'ok' : 'fail';

    // Update duration
    var durSpan = el.querySelector('.tool-duration');
    if (data.duration_ms != null) {
      durSpan.textContent = Math.round(data.duration_ms) + 'ms';
    }

    // Update stored data
    if (_toolData[id]) {
      _toolData[id].result = data.result || '';
      _toolData[id].success = data.success;
      _toolData[id].duration_ms = data.duration_ms;
    }

    // Update preview to show result snippet
    var previewDiv = el.querySelector('.tool-preview');
    if (data.result && previewDiv) {
      previewDiv.textContent = truncate(data.result, 200);
    }

    delete pendingTools[id];
    container.scrollTop = container.scrollHeight;
  }

  function clearTools(container) {
    container.innerHTML = '';
    pendingTools = {};
    _toolCounter = 0;
    _toolData = {};
  }

  // ── Public: replay from saved results ─────────────────────

  function buildToolReplay(container, toolLogs) {
    clearTools(container);
    if (!toolLogs || !toolLogs.length) {
      container.innerHTML = '<div class="empty-state">No tool calls</div>';
      return;
    }
    toolLogs.forEach(function (log, i) {
      var name = log.name || log.tool_name || 'unknown';
      var args = log.args || log.input_args || {};
      var result = log.result || '';
      var success = log.success !== false;
      var durationMs = log.duration_ms || null;
      var id = 'r' + i;

      _toolData[id] = {
        name: name,
        args: args,
        result: result,
        success: success,
        duration_ms: durationMs,
      };

      var statusClass = success ? 'success' : 'failed';
      var statusText = success ? 'ok' : 'fail';
      var duration = durationMs ? Math.round(durationMs) + 'ms' : '';

      // Preview: show args summary or result snippet
      var preview = formatArgsShort(args);
      if (!preview && result) preview = truncate(result, 150);

      var div = buildToolCard(id, name, statusClass, statusText, preview, '', duration);
      container.appendChild(div);
    });
  }

  // ── Exports ───────────────────────────────────────────────

  window.QTB = window.QTB || {};
  window.QTB.addToolStart = addToolStart;
  window.QTB.updateToolResult = updateToolResult;
  window.QTB.clearTools = clearTools;
  window.QTB.buildToolReplay = buildToolReplay;

})(window);
