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

  var _root = null;
  var _pollTimer = null;
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
    render();
    startPolling();
    refresh();
  };

  window.addEventListener('hashchange', function () {
    if (location.hash.indexOf('#/flow-demo') !== 0) stopPolling();
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
    _root.innerHTML = pageHtml();
    bind();
  }

  function bind() {
    var refreshBtn = document.getElementById('flow-refresh-btn');
    if (refreshBtn) refreshBtn.addEventListener('click', refresh);
  }

  function pageHtml() {
    var activeRun = currentActiveRun();
    if (!activeRun && !state.error) {
      return '<section class="page flow-demo flow-demo-blank"></section>';
    }
    return '' +
      '<section class="page flow-demo">' +
        '<header class="page-header">' +
          '<div class="page-title-wrap">' +
            '<p class="eyebrow">Session Flow</p>' +
            '<h1>Live benchmark monitor.</h1>' +
            '<p class="subtitle">Background REST/MCP runs appear here as the server updates their run state.</p>' +
          '</div>' +
          '<div class="flow-actions">' +
            '<button class="btn btn-secondary" id="flow-refresh-btn" type="button">Refresh</button>' +
          '</div>' +
        '</header>' +
        statusBannerHtml() +
        (activeRun
          ? '<div class="results-grid flow-results-grid">' + runCardHtml(activeRun) + '</div>'
          : '') +
      '</section>';
  }

  function statusBannerHtml() {
    if (state.error) {
      return '<div class="flow-fail-banner"><strong>Error.</strong> ' + escapeHtml(state.error) + '</div>';
    }
    return '';
  }

  function currentActiveRun() {
    for (var i = 0; i < state.runs.length; i += 1) {
      var run = state.runs[i];
      if (run.status === 'active' && displayStatus(run) === 'active') {
        return run;
      }
    }
    return null;
  }

  function runCardHtml(run) {
    var status = displayStatus(run);
    var conversation = run.conversation || [];
    var logs = domainToolLogs(run.recent_tool_logs || []);
    var href = runHref(run, status);
    var tag = href ? 'a' : 'article';
    var open = href ? ' href="' + href + '"' : '';

    return '' +
      '<' + tag + ' class="session-card flow-run-card"' + open + '>' +
        '<div class="session-top">' +
          '<div>' +
            '<h2 class="session-title">' +
              '<span>' + escapeHtml(run.public_task_label || 'Run') + '</span>' +
              '<span class="badge">' + escapeHtml(statusLabel(status)) + '</span>' +
              (run.mode ? '<span class="badge">' + escapeHtml(run.mode) + '</span>' : '') +
            '</h2>' +
            '<p class="session-subtitle">' +
              'Run <code>' + escapeHtml(shortId(run.run_id)) + '</code>' +
              (run.session_id ? ' · Session <code>' + escapeHtml(shortId(run.session_id)) + '</code>' : '') +
              ' · ' + escapeHtml(formatTimestamp(run.updated_at || run.created_at)) +
            '</p>' +
          '</div>' +
          '<span class="status-pill ' + statusClass(status) + '">' + escapeHtml(statusLabel(status)) + '</span>' +
        '</div>' +
        '<div class="session-meta-row">' +
          '<span class="meta-chip">' + escapeHtml(String(run.turn || 0) + ' turns') + '</span>' +
          '<span class="meta-chip">' + escapeHtml(String(conversation.length) + ' messages') + '</span>' +
          '<span class="meta-chip">' + escapeHtml(String(logs.length) + ' recent tools') + '</span>' +
          (run.session_phase ? '<span class="meta-chip">' + escapeHtml(run.session_phase) + '</span>' : '') +
          (run.error ? '<span class="meta-chip flow-error-chip">' + escapeHtml(run.error) + '</span>' : '') +
        '</div>' +
        previewHtml(run) +
      '</' + tag + '>';
  }

  function runHref(run, status) {
    if (status === 'completed' && run.session_id) {
      return '#/review/' + encodeURIComponent(run.session_id);
    }
    return '';
  }

  function previewHtml(run) {
    var conversation = run.conversation || [];
    var logs = domainToolLogs(run.recent_tool_logs || []);
    return '' +
      '<div class="flow-card-preview">' +
        '<div class="flow-preview-block flow-conversation-block">' +
          '<h3>Conversation</h3>' +
          (conversation.length ? '<div class="flow-conversation-list">' + conversation.map(messageHtml).join('') + '</div>' : '<p class="detail-empty-note">' + escapeHtml(emptyConversationText(run)) + '</p>') +
        '</div>' +
        '<aside class="flow-preview-block flow-tools-block">' +
          '<h3>Tool Activity</h3>' +
          (logs.length ? toolListHtml(logs.slice(-8).reverse()) : '<p class="detail-empty-note">' + escapeHtml(emptyToolText(run)) + '</p>') +
        '</aside>' +
      '</div>';
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
      return log && log.name !== 'send_message';
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

  function statusClass(status) {
    if (status === 'active' || status === 'claimed' || status === 'waiting') return 'running';
    if (status === 'completed') return 'completed';
    if (status === 'failed') return 'failed';
    if (status === 'cancelled') return 'cancelled';
    if (status === 'stale') return 'stale';
    return 'pending';
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

  function shortId(value) {
    return String(value || '').slice(0, 8) || '-';
  }

  function formatTimestamp(value) {
    if (!value) return 'Unknown time';
    var date = new Date(value);
    if (isNaN(date.getTime())) return String(value);
    return date.toLocaleString();
  }

  function formatTime(value) {
    if (!value) return '-';
    var date = new Date(value);
    if (isNaN(date.getTime())) return String(value);
    return date.toLocaleTimeString([], {hour: '2-digit', minute: '2-digit', second: '2-digit'});
  }
})();
