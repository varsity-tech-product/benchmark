/**
 * chat.js — isolated chat replay helpers for the new session UI.
 *
 * This is derived from the legacy implementation, with replay-friendly image
 * paths so the new app can remap them to `/ui/results/{session_id}/files/...`.
 */

(function (window) {
  'use strict';

  var avatarId = 0;

  function avatarTutor() {
    var gradientId = 'tg' + (++avatarId);
    return '<svg viewBox="0 0 32 32" fill="none" xmlns="http://www.w3.org/2000/svg">' +
      '<circle cx="16" cy="16" r="16" fill="url(#' + gradientId + ')"/>' +
      '<defs><linearGradient id="' + gradientId + '" x1="0" y1="0" x2="32" y2="32">' +
        '<stop stop-color="#d97706"/><stop offset="1" stop-color="#92400e"/>' +
      '</linearGradient></defs>' +
      '<path d="M10 22 L16 8 L22 22 Z" fill="white" opacity="0.85"/>' +
      '<circle cx="16" cy="16" r="2" fill="#d97706"/>' +
    '</svg>';
  }

  function avatarStudent() {
    var gradientId = 'sg' + (++avatarId);
    return '<svg viewBox="0 0 32 32" fill="none" xmlns="http://www.w3.org/2000/svg">' +
      '<circle cx="16" cy="16" r="16" fill="url(#' + gradientId + ')"/>' +
      '<defs><linearGradient id="' + gradientId + '" x1="0" y1="0" x2="32" y2="32">' +
        '<stop stop-color="#78350f"/><stop offset="1" stop-color="#451a03"/>' +
      '</linearGradient></defs>' +
      '<circle cx="16" cy="12" r="4.5" fill="white" opacity="0.9"/>' +
      '<path d="M8 27 C8 21 11.5 18 16 18 C20.5 18 24 21 24 27" fill="white" opacity="0.75"/>' +
    '</svg>';
  }

  function normalizeRole(role) {
    if (role === 'user') return 'user';
    return 'tutor';
  }

  function roleLabel(normalizedRole) {
    return normalizedRole === 'user' ? 'User' : 'Tutor';
  }

  function truncate(value, length) {
    if (!value) return '';
    return value.length > length ? value.slice(0, length) + '\u2026' : value;
  }

  function titleCaseToken(value) {
    return String(value || '')
      .split(/[_\s-]+/)
      .filter(Boolean)
      .map(function (part) {
        return part.charAt(0).toUpperCase() + part.slice(1);
      })
      .join(' ');
  }

  function normalizeAssetRef(ref) {
    if (!ref) return '';
    var value = String(ref).trim();
    value = value.replace(/^https?:\/\/[^/]+\//i, '/');
    value = value.replace(/^\/api\/files\/live\//, '');
    value = value.replace(/^\/workspace\//, '');
    value = value.replace(/^\.\//, '');
    return value.replace(/^\/+/, '');
  }

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

  function appendToolImages(el, fullLog, markdownImageRefs) {
    var outputFiles = (fullLog && fullLog.output_files) || [];
    if (outputFiles.length === 0 && fullLog && fullLog.result && fullLog.success !== false) {
      var imageRegex = /(?:saved to|wrote|created|generated)\s+\/\S+\/([\w./-]+\.(?:png|jpg|jpeg|svg|gif))/gi;
      var match;
      while ((match = imageRegex.exec(fullLog.result)) !== null) {
        outputFiles.push(match[1]);
      }
    }

    var deduped = outputFiles.filter(function (filename) {
      return !markdownImageRefs[normalizeAssetRef(filename)];
    });
    if (!deduped.length) return;

    var imageWrap = document.createElement('div');
    imageWrap.className = 'cb-tool-images';
    deduped.forEach(function (filename) {
      var img = document.createElement('img');
      img.src = filename;
      img.alt = filename;
      img.className = 'cb-tool-img';
      img.addEventListener('click', function () {
        window.open(img.src, '_blank');
      });
      imageWrap.appendChild(img);
    });
    el.appendChild(imageWrap);
  }

  function showToolDetailModal(info) {
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
      html += '<div style="margin-bottom:12px">' +
        '<div style="font-size:12px;font-weight:600;color:var(--text-muted);margin-bottom:6px;text-transform:uppercase;letter-spacing:0.05em">Arguments</div>' +
        '<pre>' + QTB.escapeHtml(JSON.stringify(info.args, null, 2)) + '</pre>' +
      '</div>';
    }

    if (info.result) {
      html += '<div>' +
        '<div style="font-size:12px;font-weight:600;color:var(--text-muted);margin-bottom:6px;text-transform:uppercase;letter-spacing:0.05em">Result</div>' +
        '<pre>' + QTB.escapeHtml(typeof info.result === 'string' ? info.result : JSON.stringify(info.result, null, 2)) + '</pre>' +
      '</div>';
    }

    var outputFiles = info.output_files || [];
    if (outputFiles.length > 0) {
      html += '<div style="margin-top:12px">' +
        '<div style="font-size:12px;font-weight:600;color:var(--text-muted);margin-bottom:6px;text-transform:uppercase;letter-spacing:0.05em">Output Images</div>';
      outputFiles.forEach(function (filename) {
        html += '<img src="' + QTB.escapeHtml(filename) +
          '" alt="' + QTB.escapeHtml(filename) +
          '" class="cb-tool-img" style="max-width:100%;border-radius:var(--radius);margin-top:8px;cursor:pointer;" />';
      });
      html += '</div>';
    }

    window._qtbShowModal(name, html);
  }

  function createInlineToolEl(toolUse, toolResult, fullLog, markdownImageRefs) {
    var el = document.createElement('div');
    var isError = (toolResult && toolResult.is_error) || (fullLog && fullLog.success === false);
    el.className = 'cb-tool' + (isError ? ' cb-tool-error' : '');

    var statusIcon = toolResult ? (isError ? '\u2717' : '\u2713') : '\u2026';
    var statusClass = toolResult ? (isError ? 'failed' : 'success') : 'running';
    var durationStr = '';
    if (fullLog && fullLog.duration_ms) {
      var milliseconds = Math.round(fullLog.duration_ms);
      durationStr = milliseconds >= 1000 ? (milliseconds / 1000).toFixed(1) + 's' : milliseconds + 'ms';
    }

    el.innerHTML =
      '<div class="cb-tool-header">' +
        '<span class="cb-tool-icon">\u26A1</span>' +
        '<span class="cb-tool-name">' + QTB.escapeHtml(toolUse.name) + '</span>' +
        '<span class="cb-tool-status ' + statusClass + '">' + statusIcon + '</span>' +
        (durationStr ? '<span class="cb-tool-duration">' + durationStr + '</span>' : '') +
      '</div>';

    appendToolImages(el, fullLog, markdownImageRefs);

    el.querySelector('.cb-tool-name').addEventListener('click', function (event) {
      event.stopPropagation();
      var info = fullLog || {
        name: toolUse.name,
        args: toolUse.input || {},
        result: toolResult ? toolResult.content : '',
        success: !(toolResult && toolResult.is_error)
      };
      showToolDetailModal(info);
    });

    return el;
  }

  function collectMarkdownImageRefs(blocks) {
    var refs = {};
    blocks.forEach(function (block) {
      if (block.type !== 'text' || !block.text) return;
      var markdownImageRegex = /!\[[^\]]*\]\(([^)]+)\)/g;
      var match;
      while ((match = markdownImageRegex.exec(block.text)) !== null) {
        refs[normalizeAssetRef(match[1])] = true;
      }
    });
    return refs;
  }

  function createContentBlocksEl(blocks, turnToolLogs) {
    var container = document.createElement('div');
    container.className = 'cb-container';

    var markdownImageRefs = collectMarkdownImageRefs(blocks);
    var toolLogIndex = 0;

    for (var index = 0; index < blocks.length; index += 1) {
      var block = blocks[index];

      if (block.type === 'thinking') {
        container.appendChild(createThinkingBlockEl(block.text || ''));
      } else if (block.type === 'tool_use') {
        var result = null;
        if (index + 1 < blocks.length && blocks[index + 1].type === 'tool_result') {
          result = blocks[index + 1];
          index += 1;
        }

        var fullLog = null;
        if (turnToolLogs) {
          while (toolLogIndex < turnToolLogs.length) {
            if (turnToolLogs[toolLogIndex].name === block.name) {
              fullLog = turnToolLogs[toolLogIndex];
              toolLogIndex += 1;
              break;
            }
            toolLogIndex += 1;
          }
        }

        // send_message: render the delivered text as a bubble instead of a tool card
        if (block.name === 'send_message') {
          var smText = (block.input && block.input.text) || '';
          if (smText) {
            var smBubble = document.createElement('div');
            smBubble.className = 'bubble';
            smBubble.innerHTML = QTB.renderMarkdown(smText);
            container.appendChild(smBubble);
          }
        } else {
          container.appendChild(createInlineToolEl(block, result, fullLog, markdownImageRefs));
        }
      } else if (block.type === 'text') {
        var bubble = document.createElement('div');
        bubble.className = 'bubble';
        bubble.innerHTML = QTB.renderMarkdown(block.text || '');
        container.appendChild(bubble);
      }
    }

    return container;
  }

  function attachmentPath(attachment) {
    if (!attachment || typeof attachment !== 'object') return '';
    return String(attachment.path || attachment.filename || attachment.name || '').trim();
  }

  function attachmentName(attachment) {
    var path = attachmentPath(attachment);
    if (!path) return 'attachment';
    var parts = path.split('/');
    return parts[parts.length - 1] || path;
  }

  function isImageAttachment(attachment) {
    if (!attachment || typeof attachment !== 'object') return false;
    if (attachment.is_image) return true;
    return /\.(png|jpe?g|gif|bmp|webp|svg)$/i.test(attachmentPath(attachment));
  }

  function renderAttachmentModalItem(attachment) {
    var path = attachmentPath(attachment);
    var name = attachmentName(attachment);
    var meta = [];
    if (attachment && attachment.truncated) meta.push('truncated');
    if (attachment && attachment.media_type) meta.push(attachment.media_type);

    var html =
      '<div class="protocol-attachment-item">' +
        '<div class="protocol-attachment-head">' +
          '<span class="protocol-attachment-name">' + QTB.escapeHtml(name) + '</span>' +
          (meta.length ? '<span class="protocol-attachment-meta">' + QTB.escapeHtml(meta.join(' · ')) + '</span>' : '') +
        '</div>';

    if (path && isImageAttachment(attachment)) {
      html += '<img class="protocol-attachment-image" src="' + QTB.escapeHtml(path) + '" alt="' + QTB.escapeHtml(name) + '">';
    } else if (attachment && attachment.content) {
      html += '<pre class="protocol-attachment-content">' + QTB.escapeHtml(attachment.content) + '</pre>';
    } else if (path) {
      html += '<p class="protocol-attachment-path"><code>' + QTB.escapeHtml(path) + '</code></p>';
    } else {
      html += '<p class="protocol-attachment-path">Attachment metadata unavailable.</p>';
    }

    return html + '</div>';
  }

  function showSendMessageModal(event) {
    if (!window._qtbShowModal) return;

    var ok = event.success !== false;
    var duration = event.duration_ms ? Math.round(event.duration_ms) + 'ms' : '';

    // Header — same format as domain tool modals
    var html =
      '<div style="margin-bottom:16px;display:flex;align-items:center;gap:10px">' +
        '<span style="font-family:var(--font-mono);font-size:16px;font-weight:700;color:var(--accent)">send_message</span>' +
        '<span class="tool-status ' + (ok ? 'success' : 'failed') + '">' + (ok ? 'ok' : 'fail') + '</span>' +
        (duration ? '<span style="color:var(--text-muted);font-size:13px;font-family:var(--font-mono)">' + duration + '</span>' : '') +
      '</div>';

    // Arguments — rebuild from event fields, show all input params
    var args = {};
    if (event.request_text) args.text = event.request_text;
    if (event.reasoning) args.reasoning = event.reasoning;
    if (event.attachments && event.attachments.length) args.attachments = event.attachments;
    // Include any extra input fields stored in raw_args
    if (event.raw_args && typeof event.raw_args === 'object') {
      var extraKeys = Object.keys(event.raw_args);
      for (var i = 0; i < extraKeys.length; i++) {
        var k = extraKeys[i];
        if (!(k in args) && event.raw_args[k] != null && event.raw_args[k] !== '') {
          args[k] = event.raw_args[k];
        }
      }
    }

    if (Object.keys(args).length > 0) {
      html += '<div style="margin-bottom:12px">' +
        '<div style="font-size:12px;font-weight:600;color:var(--text-muted);margin-bottom:6px;text-transform:uppercase;letter-spacing:0.05em">Arguments</div>' +
        '<pre>' + QTB.escapeHtml(JSON.stringify(args, null, 2)) + '</pre>' +
      '</div>';
    }

    // Result — show raw result JSON (user_message, status, reason, etc.)
    var result = {};
    if (event.user_message) result.user_message = event.user_message;
    if (event.status) result.status = event.status;
    if (event.reason) result.reason = event.reason;
    if (event.error) result.error = event.error;
    // Include raw_result if available for full fidelity
    if (event.raw_result) {
      try {
        var parsed = typeof event.raw_result === 'string' ? JSON.parse(event.raw_result) : event.raw_result;
        if (typeof parsed === 'object' && parsed) result = parsed;
      } catch (e) { /* keep constructed result */ }
    }

    if (Object.keys(result).length > 0) {
      html += '<div>' +
        '<div style="font-size:12px;font-weight:600;color:var(--text-muted);margin-bottom:6px;text-transform:uppercase;letter-spacing:0.05em">Result</div>' +
        '<pre>' + QTB.escapeHtml(JSON.stringify(result, null, 2)) + '</pre>' +
      '</div>';
    }

    window._qtbShowModal('send_message', html);
  }

  function createProtocolEventEl(event) {
    var el = document.createElement('button');
    var status = String(event.status || 'active').toLowerCase();
    var reason = event.reason ? titleCaseToken(event.reason) : '';
    var replyState = event.user_message ? 'user reply received' : 'no user reply';
    var attachmentCount = event.attachments && event.attachments.length ? event.attachments.length : 0;

    el.type = 'button';
    el.className = 'protocol-event';
    el.innerHTML =
      '<span class="protocol-name">send_message</span>' +
      '<span class="protocol-arrow">\u2192</span>' +
      '<span class="protocol-status ' + QTB.escapeHtml(status) + '">' + QTB.escapeHtml(titleCaseToken(status)) + '</span>' +
      '<span class="protocol-summary">' + QTB.escapeHtml(reason || replyState) + '</span>' +
      (attachmentCount ? '<span class="protocol-attachment-chip">' + QTB.escapeHtml(String(attachmentCount)) + ' attachment' + (attachmentCount === 1 ? '' : 's') + '</span>' : '') +
      (event.duration_ms ? '<span class="protocol-duration">' + QTB.escapeHtml(Math.round(event.duration_ms) + 'ms') + '</span>' : '');

    el.addEventListener('click', function () {
      showSendMessageModal(event);
    });
    return el;
  }

  function createMessageEl(role, content, contentBlocks, turnToolLogs, sendMessageEvents) {
    var normalizedRole = normalizeRole(role);

    var msg = document.createElement('div');
    msg.className = 'msg ' + normalizedRole;

    var avatar = document.createElement('div');
    avatar.className = 'avatar avatar-' + normalizedRole;
    avatar.innerHTML = normalizedRole === 'tutor' ? avatarTutor() : avatarStudent();

    var col = document.createElement('div');
    col.className = 'msg-col';

    var label = document.createElement('div');
    label.className = 'msg-label';
    label.textContent = roleLabel(normalizedRole);
    col.appendChild(label);

    if (contentBlocks && contentBlocks.length > 0 && normalizedRole === 'tutor') {
      col.appendChild(createContentBlocksEl(contentBlocks, turnToolLogs));
    } else {
      var bubble = document.createElement('div');
      bubble.className = 'bubble';
      bubble.innerHTML = QTB.renderMarkdown(content || '');
      col.appendChild(bubble);
    }

    if (normalizedRole === 'tutor' && sendMessageEvents && sendMessageEvents.length) {
      sendMessageEvents.forEach(function (event) {
        col.appendChild(createProtocolEventEl(event));
      });
    }

    msg.appendChild(avatar);
    msg.appendChild(col);
    return msg;
  }

  var THINKING_CLASS = 'qtb-thinking';
  var RESPONDING_CLASS = 'qtb-responding';

  function showThinking(container, text) {
    hideThinking(container);

    var msg = document.createElement('div');
    msg.className = 'msg tutor ' + THINKING_CLASS;

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
    var el = container.querySelector('.' + THINKING_CLASS);
    if (!el) return showThinking(container, text);
    var span = el.querySelector('.thinking-text');
    if (span) span.textContent = text;
  }

  function hideThinking(container) {
    var el = container.querySelector('.' + THINKING_CLASS);
    if (el) el.remove();
  }

  function showResponding(container) {
    hideResponding(container);

    var msg = document.createElement('div');
    msg.className = 'msg user ' + RESPONDING_CLASS;

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
    var el = container.querySelector('.' + RESPONDING_CLASS);
    if (el) el.remove();
  }

  function addChatMessage(container, role, content, contentBlocks, toolLogs, sendMessageEvents) {
    hideThinking(container);
    var el = createMessageEl(role, content, contentBlocks, toolLogs, sendMessageEvents);
    container.appendChild(el);
    container.scrollTop = container.scrollHeight;
    return el;
  }

  function clearChat(container) {
    container.innerHTML = '';
  }

  function buildConversationReplay(container, conversation, toolLogs, sendMessageEvents) {
    clearChat(container);
    if (!conversation || !conversation.length) {
      container.innerHTML = '<div class="empty-state">No conversation data</div>';
      return false;
    }

    var toolsByTurn = {};
    if (toolLogs) {
      toolLogs.forEach(function (log) {
        var turnIndex = log.turn_index || 0;
        if (!toolsByTurn[turnIndex]) toolsByTurn[turnIndex] = [];
        toolsByTurn[turnIndex].push(log);
      });
    }

    var sendEventsByTurn = {};
    if (sendMessageEvents) {
      sendMessageEvents.forEach(function (event) {
        var turnIndex = event.turn_index || 0;
        if (!sendEventsByTurn[turnIndex]) sendEventsByTurn[turnIndex] = [];
        sendEventsByTurn[turnIndex].push(event);
      });
    }

    var hasContentBlocks = false;
    var assistantIndex = 0;

    conversation.forEach(function (turn) {
      var content = turn.content || turn.message || '';
      var blocks = turn.content_blocks || null;
      var turnToolLogs = null;
      var turnSendEvents = null;

      if (normalizeRole(turn.role) === 'tutor') {
        turnToolLogs = toolsByTurn[assistantIndex] || null;
        turnSendEvents = sendEventsByTurn[assistantIndex] || null;
        if (blocks && blocks.length > 0) hasContentBlocks = true;
        assistantIndex += 1;
      }

      addChatMessage(container, turn.role, content, blocks, turnToolLogs, turnSendEvents);
    });

    return hasContentBlocks;
  }

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
