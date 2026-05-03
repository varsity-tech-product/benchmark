/**
 * flow-demo.js - passive live monitor for benchmark runs.
 *
 * This page observes runs created by the REST/MCP API. Terminal-driven tests
 * appear here while they run.
 */
(function () {
  'use strict';

  window.QTB = window.QTB || {};

  var escapeHtml = (window.QTB && window.QTB.escapeHtml) || function (value) {
    var div = document.createElement('div');
    div.textContent = String(value == null ? '' : value);
    return div.innerHTML;
  };

  var POLL_MS = 2000;
  var LIVE_STATUSES = {waiting: true, claimed: true, active: true};

  var _root = null;
  var _pollTimer = null;
  var _renderKey = '';
  var _conversationKeys = [];
  var _toolKey = '';
  var state = {
    runs: [],
    error: ''
  };

  function authFetch(url, options) {
    if (window.QTB && typeof window.QTB.authFetch === 'function') {
      return window.QTB.authFetch(url, options);
    }
    return fetch(url, options);
  }

  window.QTB.renderFlowDemoPage = function (app) {
    _root = app;
    _root.classList.add('flow-app');
    _renderKey = '';
    _conversationKeys = [];
    _toolKey = '';
    render();
    startPolling();
    refresh();
  };

  window.addEventListener('hashchange', function () {
    if (location.hash.indexOf('#/flow-demo') !== 0) {
      stopPolling();
      if (_root) _root.classList.remove('flow-app');
    }
  });

  function startPolling() {
    stopPolling();
    _pollTimer = setInterval(refresh, POLL_MS);
  }

  function stopPolling() {
    if (_pollTimer) {
      clearInterval(_pollTimer);
      _pollTimer = null;
    }
  }

  function refresh() {
    return authFetch('/ui/runs/live')
      .then(function (resp) {
        if (!resp.ok) throw new Error('HTTP ' + resp.status);
        return resp.json();
      })
      .catch(function () {
        return authFetch('/ui/runs').then(function (resp) {
          if (!resp.ok) throw new Error('HTTP ' + resp.status);
          return resp.json();
        });
      })
      .then(function (payload) {
        state.runs = sortRuns(payload.runs || []);
        state.error = '';
        render();
      })
      .catch(function (err) {
        state.error = err && err.message ? err.message : String(err);
        render();
      });
  }

  function sortRuns(runs) {
    return runs.slice().sort(function (a, b) {
      return Date.parse(b.created_at || '') - Date.parse(a.created_at || '');
    });
  }

  function render() {
    if (!_root) return;
    var liveRun = currentLiveRun();
    var nextKey = pageKey(liveRun);

    if (_renderKey === nextKey && liveRun && !state.error) {
      patchLiveRun(liveRun);
      hydrateLiveRun(liveRun);
      return;
    }

    _renderKey = nextKey;
    _conversationKeys = [];
    _toolKey = '';
    _root.innerHTML = pageHtml(liveRun);
    bind(liveRun);
  }

  function bind(liveRun) {
    var refreshBtn = document.getElementById('flow-refresh-btn');
    if (refreshBtn) refreshBtn.addEventListener('click', refresh);
    if (liveRun) hydrateLiveRun(liveRun);
  }

  function pageHtml(liveRun) {
    if (!liveRun && !state.error) {
      return '<section class="page flow-demo flow-demo-blank"></section>';
    }
    return '' +
      '<section class="page flow-demo">' +
        '<header class="page-header">' +
          '<div class="page-title-wrap">' +
            '<h1>Session Flow</h1>' +
          '</div>' +
          '<div class="flow-actions">' +
            '<button class="btn btn-secondary" id="flow-refresh-btn" type="button">Refresh</button>' +
          '</div>' +
        '</header>' +
        statusBannerHtml() +
        (liveRun
          ? liveRunHtml(liveRun)
          : '') +
      '</section>';
  }

  function statusBannerHtml() {
    if (state.error) {
      return '<div class="flow-fail-banner"><strong>Error.</strong> ' + escapeHtml(state.error) + '</div>';
    }
    return '';
  }

  function currentLiveRun() {
    var fallback = null;
    for (var i = 0; i < state.runs.length; i += 1) {
      var run = state.runs[i];
      if (run.status === 'active' && displayStatus(run) === 'active') {
        return run;
      }
      if (!fallback && LIVE_STATUSES[displayStatus(run)]) fallback = run;
    }
    return fallback;
  }

  function pageKey(run) {
    if (state.error) return 'error:' + state.error;
    if (!run) return 'blank';
    return [
      'run',
      run.run_id || '',
      displayStatus(run),
      run.public_task_label || '',
      run.mode || '',
      run.session_id || ''
    ].join('|');
  }

  function liveRunHtml(run) {
    var status = displayStatus(run);
    var conversation = run.conversation || [];
    var logs = domainToolLogs(run.recent_tool_logs || []);

    return '' +
      '<article class="flow-live-monitor" data-run-id="' + escapeHtml(run.run_id || '') + '">' +
        '<div class="flow-live-top">' +
          '<h2 class="flow-live-title">' +
            '<span>' + escapeHtml(run.public_task_label || 'Run') + '</span>' +
            '<span class="badge">' + escapeHtml(statusLabel(status)) + '</span>' +
            (run.mode ? '<span class="badge">' + escapeHtml(run.mode) + '</span>' : '') +
          '</h2>' +
          '<div class="session-meta-row flow-live-meta">' +
            '<span class="meta-chip" data-flow-meta="turn">' + escapeHtml(String(run.turn || 0) + ' turns') + '</span>' +
            '<span class="meta-chip" data-flow-meta="messages">' + escapeHtml(String(conversation.length) + ' messages') + '</span>' +
            '<span class="meta-chip" data-flow-meta="tools">' + escapeHtml(String(logs.length) + ' tool calls') + '</span>' +
            '<span class="meta-chip" data-flow-meta="phase"' + (run.session_phase ? '' : ' hidden') + '>' + escapeHtml(run.session_phase || '') + '</span>' +
            (run.error ? '<span class="meta-chip flow-error-chip">' + escapeHtml(run.error) + '</span>' : '') +
          '</div>' +
        '</div>' +
        '<div class="flow-live-grid">' +
          '<section class="flow-live-panel flow-conversation-panel">' +
            '<div class="flow-panel-header">' +
              '<h3>Conversation</h3>' +
              '<span data-flow-count="conversation">' + escapeHtml(String(conversation.length) + ' messages') + '</span>' +
            '</div>' +
            '<div id="flow-conversation" class="flow-conversation-body" aria-live="polite">' +
              '<div class="empty-state">' + escapeHtml(emptyConversationText(run)) + '</div>' +
            '</div>' +
          '</section>' +
          '<aside class="flow-live-panel flow-tools-panel">' +
            '<div class="flow-panel-header">' +
              '<h3>Tool Activity</h3>' +
              '<span data-flow-count="tools">' + escapeHtml(String(logs.length)) + '</span>' +
            '</div>' +
            '<div id="flow-tools" class="flow-tools-body">' +
              '<div class="empty-state">' + escapeHtml(emptyToolText(run)) + '</div>' +
            '</div>' +
          '</aside>' +
        '</div>' +
      '</article>';
  }

  function patchLiveRun(run) {
    var conversation = run.conversation || [];
    var logs = domainToolLogs(run.recent_tool_logs || []);
    setText('[data-flow-meta="turn"]', String(run.turn || 0) + ' turns');
    setText('[data-flow-meta="messages"]', String(conversation.length) + ' messages');
    setText('[data-flow-meta="tools"]', String(logs.length) + ' tool calls');
    setOptionalText('[data-flow-meta="phase"]', run.session_phase || '');
    setText('[data-flow-count="conversation"]', String(conversation.length) + ' messages');
    setText('[data-flow-count="tools"]', String(logs.length));
  }

  function setText(selector, value) {
    var el = _root ? _root.querySelector(selector) : document.querySelector(selector);
    if (el) el.textContent = value;
  }

  function setOptionalText(selector, value) {
    var el = _root ? _root.querySelector(selector) : document.querySelector(selector);
    if (!el) return;
    el.textContent = value;
    el.hidden = !value;
  }

  function hydrateLiveRun(run) {
    var conversationEl = document.getElementById('flow-conversation');
    var toolsEl = document.getElementById('flow-tools');
    var conversation = run.conversation || [];
    var logs = domainToolLogs(run.recent_tool_logs || []);
    var nextToolKey = payloadKey(logs);

    if (conversationEl) hydrateConversation(conversationEl, conversation, run);

    if (toolsEl && nextToolKey !== _toolKey) {
      _toolKey = nextToolKey;
      if (logs.length && window.QTB && typeof window.QTB.buildToolReplay === 'function') {
        window.QTB.buildToolReplay(toolsEl, logs);
      } else if (logs.length) {
        toolsEl.innerHTML = toolListHtml(logs.slice().reverse());
      } else {
        toolsEl.innerHTML = '<div class="empty-state">' + escapeHtml(emptyToolText(run)) + '</div>';
      }
    }
  }

  function hydrateConversation(container, conversation, run) {
    var nextKeys = conversation.map(messageKey);
    if (arraysEqual(nextKeys, _conversationKeys)) return;

    var stickToBottom = isNearBottom(container) || _conversationKeys.length === 0;
    var previousScrollTop = container.scrollTop;

    if (!conversation.length) {
      container.innerHTML = '<div class="empty-state">' + escapeHtml(emptyConversationText(run)) + '</div>';
      _conversationKeys = [];
      return;
    }

    var firstChanged = firstChangedIndex(_conversationKeys, nextKeys);
    var nodes = conversationNodes(container);

    if (nodes.length === _conversationKeys.length && firstChanged >= 0) {
      removeConversationTail(nodes, firstChanged);
      removeConversationEmptyState(container);
      for (var i = firstChanged; i < conversation.length; i += 1) {
        appendConversationMessage(container, conversation[i]);
      }
    } else {
      container.innerHTML = '';
      for (var j = 0; j < conversation.length; j += 1) {
        appendConversationMessage(container, conversation[j]);
      }
    }

    _conversationKeys = nextKeys;
    if (stickToBottom) {
      container.scrollTop = container.scrollHeight;
    } else {
      container.scrollTop = previousScrollTop;
    }
  }

  function appendConversationMessage(container, msg) {
    if (window.QTB && typeof window.QTB.addChatMessage === 'function') {
      window.QTB.addChatMessage(
        container,
        msg.role,
        msg.content || msg.message || '',
        msg.content_blocks || null,
        null,
        null
      );
      return;
    }
    container.insertAdjacentHTML('beforeend', messageHtml(msg));
  }

  function messageHtml(msg) {
    var roleClass = normalizedRole(msg && msg.role);
    var role = roleLabel(msg && msg.role, roleClass);
    return '' +
      '<div class="flow-message flow-message-' + escapeHtml(roleClass) + '">' +
        '<strong>' + escapeHtml(role) + '</strong>' +
        '<div class="flow-message-body">' + renderMessageContent(msg) + '</div>' +
      '</div>';
  }

  function conversationNodes(container) {
    return Array.prototype.slice.call(container.querySelectorAll('.msg, .flow-message'));
  }

  function removeConversationTail(nodes, startIndex) {
    for (var i = nodes.length - 1; i >= startIndex; i -= 1) {
      nodes[i].remove();
    }
  }

  function removeConversationEmptyState(container) {
    var empty = container.querySelector('.empty-state');
    if (empty) empty.remove();
  }

  function messageKey(msg) {
    return payloadKey({
      role: msg.role || '',
      content: msg.content || msg.message || '',
      content_blocks: msg.content_blocks || null
    });
  }

  function arraysEqual(a, b) {
    if (a.length !== b.length) return false;
    for (var i = 0; i < a.length; i += 1) {
      if (a[i] !== b[i]) return false;
    }
    return true;
  }

  function firstChangedIndex(a, b) {
    var length = Math.min(a.length, b.length);
    for (var i = 0; i < length; i += 1) {
      if (a[i] !== b[i]) return i;
    }
    return length;
  }

  function isNearBottom(container) {
    return container.scrollHeight - container.scrollTop - container.clientHeight < 48;
  }

  function payloadKey(value) {
    try {
      return JSON.stringify(value || []);
    } catch (err) {
      return String((value || []).length);
    }
  }

  function renderMessageContent(msg) {
    var text = messageText(msg);
    return escapeHtml(text).replace(/\n/g, '<br>');
  }

  function messageText(msg) {
    if (!msg) return '';

    if (Array.isArray(msg.content_blocks)) {
      var blockText = msg.content_blocks.map(contentBlockText).filter(Boolean);
      if (blockText.length) return blockText.join('\n\n');
    }

    var content = msg.content != null ? msg.content : msg.message;
    if (Array.isArray(content)) {
      return content.map(contentBlockText).filter(Boolean).join('\n\n');
    }
    if (content && typeof content === 'object') {
      return JSON.stringify(content, null, 2);
    }
    return String(content == null ? '' : content);
  }

  function contentBlockText(block) {
    if (!block) return '';
    if (typeof block === 'string') return block;
    if (typeof block !== 'object') return '';
    if (block.type === 'text') return block.text || '';
    if (block.text) return block.text;
    if (block.type === 'tool_use' && block.name === 'send_message' && block.input) {
      return block.input.text || '';
    }
    return '';
  }

  function normalizedRole(role) {
    return (role === 'user' || role === 'student') ? 'student' : 'tutor';
  }

  function roleLabel(role, roleClass) {
    if (roleClass === 'student') return 'Student';
    if (role === 'assistant' || role === 'tutor' || !role) return 'Tutor';
    return titleCase(role);
  }

  function domainToolLogs(logs) {
    return (logs || []).filter(function (log) {
      var name = String((log && (log.name || log.tool_name)) || '');
      return name !== 'send_message';
    });
  }

  function toolListHtml(logs) {
    return '<div class="flow-tool-list">' + logs.map(function (log) {
      var ok = log.success === false ? ' err' : ' ok';
      var duration = log.duration_ms != null ? Math.round(log.duration_ms) + ' ms' : '';
      return '' +
        '<div class="flow-tool-item' + ok + '">' +
          '<div class="flow-tool-head">' +
            '<strong>' + escapeHtml(log.name || 'tool') + '</strong>' +
            '<span>' + escapeHtml(duration) + '</span>' +
          '</div>' +
          '<div class="flow-tool-meta">' + escapeHtml(formatTime(log.timestamp)) + '</div>' +
        '</div>';
    }).join('') + '</div>';
  }

  function emptyConversationText(run) {
    var status = displayStatus(run);
    if (status === 'waiting') return 'Run created.';
    if (status === 'claimed') return 'Agent connected.';
    if (status === 'active') return 'Session starting.';
    if (status === 'stale') return 'Previous server process ended before archive creation.';
    if (status === 'completed') return 'Open Human Review for the archived replay.';
    if (status === 'failed') return run.error || 'Run failed.';
    if (status === 'cancelled') return 'Run cancelled.';
    return 'Waiting for session activity.';
  }

  function emptyToolText(run) {
    var status = displayStatus(run);
    if (status === 'active') return 'Tool calls will appear as the agent works.';
    if (status === 'completed') return 'Archived tool calls are available in Human Review.';
    return statusLabel(status || 'pending');
  }

  function displayStatus(run) {
    return run.observer_status || run.status || '';
  }

  function statusLabel(status) {
    var labels = {
      waiting: 'Waiting',
      claimed: 'Claimed',
      active: 'Active',
      stale: 'Stale',
      completed: 'Completed',
      failed: 'Failed',
      cancelled: 'Cancelled'
    };
    return labels[status] || titleCase(status || '');
  }

  function titleCase(value) {
    return String(value || '')
      .split(/[_\s-]+/)
      .filter(Boolean)
      .map(function (part) { return part.charAt(0).toUpperCase() + part.slice(1); })
      .join(' ');
  }

  function formatTime(value) {
    if (!value) return '-';
    var date = new Date(value);
    if (isNaN(date.getTime())) return String(value);
    return date.toLocaleTimeString([], {hour: '2-digit', minute: '2-digit', second: '2-digit'});
  }
})();
