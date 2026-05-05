/**
 * chat.js — Chat UI with inline SVG avatars + name labels.
 *
 * Tutor (assistant) left-aligned, Student (user) right-aligned.
 * Supports two rendering modes:
 * 1. Simple: single markdown bubble per turn
 * 2. Content blocks: interleaved thinking → tool_use → tool_result → text
 *    (Claude Code style, only for assistant turns with content_blocks)
 *
 * Exports: QTB.addChatMessage(container, role, content, contentBlocks, toolLogs)
 *          QTB.clearChat(container)
 *          QTB.buildConversationReplay(container, conversation, toolLogs)
 *          QTB.showThinking(container, text)
 *          QTB.hideThinking(container)
 */

(function (window) {
  'use strict';

  var _avatarId = 0;

  // ── SVG avatars (unique gradient IDs per instance) ────────

  function avatarTutor() {
    var gid = 'tg' + (++_avatarId);
    return '<svg viewBox="0 0 32 32" fill="none" xmlns="http://www.w3.org/2000/svg">' +
      '<circle cx="16" cy="16" r="16" fill="url(#' + gid + ')"/>' +
      '<defs><linearGradient id="' + gid + '" x1="0" y1="0" x2="32" y2="32">' +
        '<stop stop-color="#d97706"/><stop offset="1" stop-color="#92400e"/>' +
      '</linearGradient></defs>' +
      '<path d="M10 22 L16 8 L22 22 Z" fill="white" opacity="0.85"/>' +
      '<circle cx="16" cy="16" r="2" fill="#d97706"/>' +
    '</svg>';
  }

  function avatarStudent() {
    var gid = 'sg' + (++_avatarId);
    return '<svg viewBox="0 0 32 32" fill="none" xmlns="http://www.w3.org/2000/svg">' +
      '<circle cx="16" cy="16" r="16" fill="url(#' + gid + ')"/>' +
      '<defs><linearGradient id="' + gid + '" x1="0" y1="0" x2="32" y2="32">' +
        '<stop stop-color="#78350f"/><stop offset="1" stop-color="#451a03"/>' +
      '</linearGradient></defs>' +
      '<circle cx="16" cy="12" r="4.5" fill="white" opacity="0.9"/>' +
      '<path d="M8 27 C8 21 11.5 18 16 18 C20.5 18 24 21 24 27" fill="white" opacity="0.75"/>' +
    '</svg>';
  }

  // ── Role normalization ────────────────────────────────────

  // run_state.json uses "user"/"assistant"; live SSE uses "user"/"tutor"
  function normalizeRole(role) {
    if (role === 'user' || role === 'user') return 'user';
    return 'tutor';
  }

  function roleLabel(normalized) {
    return normalized === 'user' ? 'User' : 'Tutor';
  }

  // ── Utility ───────────────────────────────────────────────

  function truncateStr(s, n) {
    if (!s) return '';
    return s.length > n ? s.slice(0, n) + '\u2026' : s;
  }

  function briefArgs(input) {
    if (!input || typeof input !== 'object') return '';
    var keys = Object.keys(input);
    if (keys.length === 0) return '';
    var parts = keys.map(function (k) {
      var v = input[k];
      var val = typeof v === 'string' ? truncateStr(v, 40) : JSON.stringify(v);
      return k + ': ' + val;
    });
    var s = parts.join(', ');
    return truncateStr(s, 120);
  }

  // ── Content blocks rendering ──────────────────────────────

  function createThinkingBlockEl(text) {
    var el = document.createElement('div');
    el.className = 'cb-thinking collapsed';

    var header = document.createElement('div');
    header.className = 'cb-thinking-header';
    header.innerHTML =
      '<span class="cb-toggle">\u25B8</span>' +
      '<span class="cb-thinking-label">Thinking</span>';

    var body = document.createElement('div');
    body.className = 'cb-thinking-body';
    body.innerHTML = '<pre>' + QTB.escapeHtml(text) + '</pre>';

    header.addEventListener('click', function () {
      el.classList.toggle('collapsed');
      var toggle = el.querySelector('.cb-toggle');
      toggle.textContent = el.classList.contains('collapsed') ? '\u25B8' : '\u25BE';
    });

    el.appendChild(header);
    el.appendChild(body);
    return el;
  }

  function createInlineToolEl(toolUse, toolResult, fullLog, mdImageNames) {
    var el = document.createElement('div');
    var isError = (toolResult && toolResult.is_error) || (fullLog && fullLog.success === false);
    el.className = 'cb-tool' + (isError ? ' cb-tool-error' : '');

    var statusIcon = toolResult
      ? (isError ? '\u2717' : '\u2713')
      : '\u2026';
    var statusClass = toolResult
      ? (isError ? 'failed' : 'success')
      : 'running';

    // Duration from fullLog
    var durationStr = '';
    if (fullLog && fullLog.duration_ms) {
      var ms = Math.round(fullLog.duration_ms);
      durationStr = ms >= 1000 ? (ms / 1000).toFixed(1) + 's' : ms + 'ms';
    }

    var html =
      '<div class="cb-tool-header">' +
        '<span class="cb-tool-icon">\u26A1</span>' +
        '<span class="cb-tool-name">' + QTB.escapeHtml(toolUse.name) + '</span>' +
        '<span class="cb-tool-status ' + statusClass + '">' + statusIcon + '</span>' +
        (durationStr ? '<span class="cb-tool-duration">' + durationStr + '</span>' : '') +
      '</div>';

    el.innerHTML = html;

    // Render inline images if tool produced image files
    var outputFiles = (fullLog && fullLog.output_files) || [];
    // Fallback: extract image filenames from result text (old data)
    if (outputFiles.length === 0 && fullLog && fullLog.result && fullLog.success !== false) {
      var imgRe = /(?:saved to|wrote|created|generated)\s+\/\S+\/([\w.-]+\.(?:png|jpg|jpeg|svg|gif))/gi;
      var m;
      while ((m = imgRe.exec(fullLog.result)) !== null) {
        outputFiles.push(m[1]);
      }
    }
    // Dedup: skip images already shown in a text bubble's markdown
    var deduped = mdImageNames
      ? outputFiles.filter(function (f) { return !mdImageNames[f]; })
      : outputFiles;
    if (deduped.length > 0) {
      var imgWrap = document.createElement('div');
      imgWrap.className = 'cb-tool-images';
      deduped.forEach(function (fname) {
        var img = document.createElement('img');
        // Bare filename; caller rewrites for live (/api/files/live/) or replay (/api/results/.../files/)
        img.src = fname;
        img.alt = fname;
        img.className = 'cb-tool-img';
        img.addEventListener('click', function () {
          window.open(img.src, '_blank');
        });
        imgWrap.appendChild(img);
      });
      el.appendChild(imgWrap);
    }

    // Click tool name → modal with full details
    el.querySelector('.cb-tool-name').addEventListener('click', function (e) {
      e.stopPropagation();
      var info = fullLog || {
        name: toolUse.name,
        args: toolUse.input || {},
        result: (toolResult ? toolResult.content : ''),
        success: !(toolResult && toolResult.is_error),
      };
      _showToolDetailModal(info);
    });

    return el;
  }

  function _showToolDetailModal(info) {
    if (!window._qtbShowModal) return;
    var name = info.name || 'unknown';
    var ok = info.success !== false;
    var duration = info.duration_ms ? Math.round(info.duration_ms) + 'ms' : '';

    var html =
      '<div style="margin-bottom:16px;display:flex;align-items:center;gap:10px">' +
        '<span style="font-family:var(--font-mono);font-size:16px;font-weight:700;color:var(--accent)">' +
          QTB.escapeHtml(name) +
        '</span>' +
        '<span class="tool-status ' + (ok ? 'success' : 'failed') + '">' + (ok ? 'ok' : 'fail') + '</span>' +
        (duration ? '<span style="color:var(--text-muted);font-size:13px;font-family:var(--font-mono)">' + duration + '</span>' : '') +
      '</div>';

    if (info.args && Object.keys(info.args).length > 0) {
      html +=
        '<div style="margin-bottom:12px">' +
          '<div style="font-size:12px;font-weight:600;color:var(--text-muted);margin-bottom:6px;text-transform:uppercase;letter-spacing:0.05em">Arguments</div>' +
          '<pre>' + QTB.escapeHtml(JSON.stringify(info.args, null, 2)) + '</pre>' +
        '</div>';
    }

    if (info.result) {
      html +=
        '<div>' +
          '<div style="font-size:12px;font-weight:600;color:var(--text-muted);margin-bottom:6px;text-transform:uppercase;letter-spacing:0.05em">Result</div>' +
          '<pre>' + QTB.escapeHtml(typeof info.result === 'string' ? info.result : JSON.stringify(info.result, null, 2)) + '</pre>' +
        '</div>';
    }

    // Show images in modal if available
    var outputFiles = info.output_files || [];
    if (outputFiles.length > 0) {
      html += '<div style="margin-top:12px">' +
        '<div style="font-size:12px;font-weight:600;color:var(--text-muted);margin-bottom:6px;text-transform:uppercase;letter-spacing:0.05em">Output Images</div>';
      outputFiles.forEach(function (fname) {
        // Modal images: use live endpoint (modals are typically shown during live runs)
        // For replay, the user sees images inline in the tool card instead
        html += '<img src="/api/files/live/' + encodeURIComponent(fname) +
          '" alt="' + QTB.escapeHtml(fname) +
          '" class="cb-tool-img" style="max-width:100%;border-radius:var(--radius);margin-top:8px;cursor:pointer;" ' +
          'onerror="this.style.display=\'none\'" />';
      });
      html += '</div>';
    }

    window._qtbShowModal(name, html);
  }

  /**
   * Render a sequence of content_blocks for one assistant turn.
   *
   * @param {Array} blocks - [{type:"thinking",text:""}, {type:"tool_use",name:"",input:{}},
   *                          {type:"tool_result",content:"",is_error:false}, {type:"text",text:""}]
   * @param {Array} turnToolLogs - ToolCallLog objects for this turn (matched by turn_index)
   * @returns {HTMLElement} container div
   */
  function createContentBlocksEl(blocks, turnToolLogs) {
    var container = document.createElement('div');
    container.className = 'cb-container';

    // Pre-scan: collect image filenames already referenced in text blocks'
    // markdown (e.g. ![Chart](chart_xxx.png)) to avoid showing them twice
    // (once in the text bubble, once in the tool card).
    var mdImageNames = {};
    for (var p = 0; p < blocks.length; p++) {
      if (blocks[p].type === 'text' && blocks[p].text) {
        var mdImgRe = /!\[[^\]]*\]\(([^)]+)\)/g;
        var mm;
        while ((mm = mdImgRe.exec(blocks[p].text)) !== null) {
          // Extract bare filename from possibly path-like refs
          var fname = mm[1].split('/').pop();
          if (fname) mdImageNames[fname] = true;
        }
      }
    }

    var toolLogIdx = 0;

    for (var i = 0; i < blocks.length; i++) {
      var block = blocks[i];

      if (block.type === 'thinking') {
        container.appendChild(createThinkingBlockEl(block.text || ''));
      } else if (block.type === 'tool_use') {
        // Look ahead for paired tool_result
        var result = null;
        if (i + 1 < blocks.length && blocks[i + 1].type === 'tool_result') {
          result = blocks[i + 1];
          i++; // skip the tool_result
        }

        // Match to full tool_log (consumed in order within the turn)
        var fullLog = null;
        if (turnToolLogs) {
          while (toolLogIdx < turnToolLogs.length) {
            if (turnToolLogs[toolLogIdx].name === block.name) {
              fullLog = turnToolLogs[toolLogIdx];
              toolLogIdx++;
              break;
            }
            toolLogIdx++;
          }
        }

        container.appendChild(createInlineToolEl(block, result, fullLog, mdImageNames));
      } else if (block.type === 'text') {
        var bubble = document.createElement('div');
        bubble.className = 'bubble';
        bubble.innerHTML = QTB.renderMarkdown(block.text || '');
        container.appendChild(bubble);
      }
    }

    return container;
  }

  // ── Build a chat message DOM element ──────────────────────

  function createMessageEl(role, content, contentBlocks, turnToolLogs) {
    var norm = normalizeRole(role);

    var msg = document.createElement('div');
    msg.className = 'msg ' + norm;

    // Avatar
    var avatar = document.createElement('div');
    avatar.className = 'avatar avatar-' + norm;
    avatar.innerHTML = norm === 'tutor' ? avatarTutor() : avatarStudent();

    // Column: label + content
    var col = document.createElement('div');
    col.className = 'msg-col';

    var label = document.createElement('div');
    label.className = 'msg-label';
    label.textContent = roleLabel(norm);

    col.appendChild(label);

    if (contentBlocks && contentBlocks.length > 0 && norm === 'tutor') {
      // Render interleaved content blocks (Claude Code style)
      var blocksEl = createContentBlocksEl(contentBlocks, turnToolLogs);
      col.appendChild(blocksEl);
    } else {
      // Simple text bubble
      var bubble = document.createElement('div');
      bubble.className = 'bubble';
      bubble.innerHTML = QTB.renderMarkdown(content);
      col.appendChild(bubble);
    }

    msg.appendChild(avatar);
    msg.appendChild(col);

    return msg;
  }

  // ── Thinking indicator (live streaming) ────────────────────

  var THINKING_CLS = 'qtb-thinking';

  function showThinking(container, text) {
    hideThinking(container);

    var msg = document.createElement('div');
    msg.className = 'msg tutor ' + THINKING_CLS;

    var avatar = document.createElement('div');
    avatar.className = 'avatar avatar-tutor';
    avatar.innerHTML = avatarTutor();

    var col = document.createElement('div');
    col.className = 'msg-col';

    var label = document.createElement('div');
    label.className = 'msg-label';
    label.textContent = 'Tutor';

    var bubble = document.createElement('div');
    bubble.className = 'bubble thinking-bubble';
    bubble.innerHTML =
      '<div class="thinking-dots"><span></span><span></span><span></span></div>' +
      '<span class="thinking-text">' + QTB.escapeHtml(text || 'Thinking...') + '</span>';

    col.appendChild(label);
    col.appendChild(bubble);
    msg.appendChild(avatar);
    msg.appendChild(col);

    container.appendChild(msg);
    container.scrollTop = container.scrollHeight;
  }

  function updateThinking(container, text) {
    var el = container.querySelector('.' + THINKING_CLS);
    if (!el) return showThinking(container, text);
    var span = el.querySelector('.thinking-text');
    if (span) span.textContent = text;
  }

  function hideThinking(container) {
    var el = container.querySelector('.' + THINKING_CLS);
    if (el) el.remove();
  }

  // ── Responding indicator (user typing) ──────────────────

  var RESPONDING_CLS = 'qtb-responding';

  function showResponding(container) {
    hideResponding(container);

    var msg = document.createElement('div');
    msg.className = 'msg user ' + RESPONDING_CLS;

    var avatar = document.createElement('div');
    avatar.className = 'avatar avatar-user';
    avatar.innerHTML = avatarStudent();

    var col = document.createElement('div');
    col.className = 'msg-col';

    var label = document.createElement('div');
    label.className = 'msg-label';
    label.textContent = 'User';

    var bubble = document.createElement('div');
    bubble.className = 'bubble thinking-bubble responding-bubble';
    bubble.innerHTML =
      '<div class="thinking-dots responding-dots"><span></span><span></span><span></span></div>' +
      '<span class="thinking-text">Responding...</span>';

    col.appendChild(label);
    col.appendChild(bubble);
    msg.appendChild(avatar);
    msg.appendChild(col);

    container.appendChild(msg);
    container.scrollTop = container.scrollHeight;
  }

  function hideResponding(container) {
    var el = container.querySelector('.' + RESPONDING_CLS);
    if (el) el.remove();
  }

  // ── Public API ────────────────────────────────────────────

  function addChatMessage(container, role, content, contentBlocks, toolLogs) {
    hideThinking(container);
    var el = createMessageEl(role, content, contentBlocks, toolLogs);
    container.appendChild(el);
    container.scrollTop = container.scrollHeight;
    return el;
  }

  function clearChat(container) {
    container.innerHTML = '';
  }

  /**
   * Replay a saved conversation. Returns true if any content_blocks were rendered.
   *
   * @param {HTMLElement} container - chat area element
   * @param {Array} conversation - [{role, content, content_blocks?}, ...]
   * @param {Array} toolLogs - [{name, args, result, turn_index, ...}, ...]
   * @returns {boolean} hasContentBlocks
   */
  function buildConversationReplay(container, conversation, toolLogs) {
    clearChat(container);
    if (!conversation || !conversation.length) {
      container.innerHTML = '<div class="empty-state">No conversation data</div>';
      return false;
    }

    // Group tool_logs by turn_index for efficient lookup
    var toolsByTurn = {};
    if (toolLogs) {
      toolLogs.forEach(function (log) {
        var ti = log.turn_index || 0;
        if (!toolsByTurn[ti]) toolsByTurn[ti] = [];
        toolsByTurn[ti].push(log);
      });
    }

    var hasContentBlocks = false;
    var assistantIdx = 0;

    conversation.forEach(function (turn) {
      var content = turn.content || turn.message || '';
      var blocks = turn.content_blocks || null;
      var turnToolLogs = null;

      if (normalizeRole(turn.role) === 'tutor') {
        turnToolLogs = toolsByTurn[assistantIdx] || null;
        if (blocks && blocks.length > 0) hasContentBlocks = true;
        assistantIdx++;
      }

      addChatMessage(container, turn.role, content, blocks, turnToolLogs);
    });

    return hasContentBlocks;
  }

  // ── Exports ───────────────────────────────────────────────

  window.QTB = window.QTB || {};
  window.QTB.addChatMessage = addChatMessage;
  window.QTB.clearChat = clearChat;
  window.QTB.buildConversationReplay = buildConversationReplay;
  window.QTB.showThinking = showThinking;
  window.QTB.updateThinking = updateThinking;
  window.QTB.hideThinking = hideThinking;
  window.QTB.showResponding = showResponding;
  window.QTB.hideResponding = hideResponding;

})(window);
