/**
 * tools.js — isolated tool replay helpers for the new session UI.
 *
 * This is derived from the legacy implementation, but image sources are kept
 * path-relative so `app.js` can rewrite them onto `/ui/results/{session_id}`.
 */

(function (window) {
  'use strict';

  var pendingTools = {};
  var toolCounter = 0;
  var toolData = {};

  function truncate(value, length) {
    if (typeof value !== 'string') value = JSON.stringify(value, null, 2);
    if (!value) return '';
    return value.length > length ? value.slice(0, length) + '\u2026' : value;
  }

  function formatArgs(args) {
    if (!args || typeof args !== 'object') return '';
    var keys = Object.keys(args);
    if (!keys.length) return '';
    return keys.map(function (key) {
      var value = args[key];
      var rendered = typeof value === 'string' ? value : JSON.stringify(value, null, 2);
      return key + ': ' + rendered;
    }).join('\n');
  }

  function formatArgsShort(args) {
    if (!args || typeof args !== 'object') return '';
    var keys = Object.keys(args);
    if (!keys.length) return '';
    return keys.map(function (key) {
      var value = args[key];
      var rendered = typeof value === 'string' ? truncate(value, 80) : JSON.stringify(value);
      return key + ': ' + rendered;
    }).join('\n');
  }

  function appendOutputImages(html, outputFiles) {
    if (!outputFiles || !outputFiles.length) return html;
    html += '<div style="margin-top:12px">' +
      '<div style="font-size:12px;font-weight:600;color:var(--text-muted);margin-bottom:6px;text-transform:uppercase;letter-spacing:0.05em">Output Images</div>';
    outputFiles.forEach(function (filename) {
      html += '<img src="' + QTB.escapeHtml(filename) +
        '" alt="' + QTB.escapeHtml(filename) +
        '" class="cb-tool-img" style="max-width:100%;border-radius:var(--radius);margin-top:8px;cursor:pointer;" />';
    });
    html += '</div>';
    return html;
  }

  function showToolModal(toolInfo) {
    if (!window._qtbShowModal) return;

    var name = toolInfo.name || 'unknown';
    var ok = toolInfo.success !== false;
    var duration = toolInfo.duration_ms ? Math.round(toolInfo.duration_ms) + 'ms' : '';
    var html =
      '<div style="margin-bottom:16px;display:flex;align-items:center;gap:10px">' +
        '<span style="font-family:var(--font-mono);font-size:16px;font-weight:700;color:var(--accent)">' +
          QTB.escapeHtml(name) +
        '</span>' +
        '<span class="tool-status ' + (ok ? 'success' : 'failed') + '">' + (ok ? 'ok' : 'fail') + '</span>' +
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

    html = appendOutputImages(html, toolInfo.output_files || []);
    window._qtbShowModal(name, html);
  }

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
      var info = toolData[id];
      if (info) showToolModal(info);
    });

    var statusSpan = document.createElement('span');
    statusSpan.className = 'tool-status ' + statusClass;
    statusSpan.textContent = statusText;

    var durationSpan = document.createElement('span');
    durationSpan.className = 'tool-duration';
    durationSpan.textContent = duration || '';

    header.appendChild(nameSpan);
    header.appendChild(statusSpan);
    header.appendChild(durationSpan);
    div.appendChild(header);

    var preview = argsPreview || resultPreview || '';
    if (preview) {
      var previewDiv = document.createElement('div');
      previewDiv.className = 'tool-preview';
      previewDiv.textContent = preview;
      div.appendChild(previewDiv);
    }

    return div;
  }

  function addToolStart(container, data) {
    toolCounter += 1;
    var id = data.call_id || ('t' + toolCounter);

    toolData[id] = {
      name: data.name || 'unknown',
      args: data.args || {},
      result: null,
      success: null,
      duration_ms: null,
      output_files: null
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

    var statusSpan = el.querySelector('.tool-status');
    statusSpan.className = 'tool-status ' + (ok ? 'success' : 'failed');
    statusSpan.textContent = ok ? 'ok' : 'fail';

    var durationSpan = el.querySelector('.tool-duration');
    if (data.duration_ms != null) {
      durationSpan.textContent = Math.round(data.duration_ms) + 'ms';
    }

    if (toolData[id]) {
      toolData[id].result = data.result || '';
      toolData[id].success = data.success;
      toolData[id].duration_ms = data.duration_ms;
      toolData[id].output_files = data.output_files || null;
    }

    var previewDiv = el.querySelector('.tool-preview');
    if (data.result && previewDiv) {
      previewDiv.textContent = truncate(data.result, 200);
    }

    if (data.output_files && data.output_files.length > 0) {
      var imageWrap = document.createElement('div');
      imageWrap.className = 'tool-images';
      data.output_files.forEach(function (filename) {
        var img = document.createElement('img');
        img.src = filename;
        img.alt = filename;
        img.className = 'tool-img-preview';
        img.addEventListener('click', function () {
          window.open(img.src, '_blank');
        });
        imageWrap.appendChild(img);
      });
      el.appendChild(imageWrap);
    }

    delete pendingTools[id];
    container.scrollTop = container.scrollHeight;
  }

  function clearTools(container) {
    container.innerHTML = '';
    pendingTools = {};
    toolCounter = 0;
    toolData = {};
  }

  function extractOutputFiles(log, result) {
    var outputFiles = (log.output_files || []).slice();
    if (outputFiles.length === 0 && result && log.success !== false) {
      var imageRegex = /(?:saved to|wrote|created|generated)\s+\/\S+\/([\w./-]+\.(?:png|jpg|jpeg|svg|gif))/gi;
      var match;
      while ((match = imageRegex.exec(result)) !== null) {
        outputFiles.push(match[1]);
      }
    }
    return outputFiles;
  }

  function buildToolReplay(container, toolLogs) {
    clearTools(container);
    if (!toolLogs || !toolLogs.length) {
      container.innerHTML = '<div class="empty-state">No tool calls</div>';
      return;
    }

    toolLogs.forEach(function (log, index) {
      var name = log.name || log.tool_name || 'unknown';
      var args = log.args || log.input_args || {};
      var result = log.result || '';
      var success = log.success !== false;
      var durationMs = log.duration_ms || null;
      var id = 'r' + index;
      var outputFiles = extractOutputFiles(log, result);

      toolData[id] = {
        name: name,
        args: args,
        result: result,
        success: success,
        duration_ms: durationMs,
        output_files: outputFiles
      };

      var statusClass = success ? 'success' : 'failed';
      var statusText = success ? 'ok' : 'fail';
      var duration = durationMs ? Math.round(durationMs) + 'ms' : '';
      var preview = formatArgsShort(args);
      if (!preview && result) preview = truncate(result, 150);

      var div = buildToolCard(id, name, statusClass, statusText, preview, '', duration);
      if (outputFiles.length > 0) {
        var imageWrap = document.createElement('div');
        imageWrap.className = 'tool-images';
        outputFiles.forEach(function (filename) {
          var img = document.createElement('img');
          img.src = filename;
          img.alt = filename;
          img.className = 'tool-img-preview';
          img.addEventListener('click', function () {
            window.open(img.src, '_blank');
          });
          imageWrap.appendChild(img);
        });
        div.appendChild(imageWrap);
      }
      container.appendChild(div);
    });
  }

  window.QTB = window.QTB || {};
  window.QTB.addToolStart = addToolStart;
  window.QTB.updateToolResult = updateToolResult;
  window.QTB.clearTools = clearTools;
  window.QTB.buildToolReplay = buildToolReplay;
})(window);
