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
  var ACTIVE_STATUSES = {waiting: true, claimed: true, active: true};
  var TERMINAL_STATUSES = {completed: true, failed: true, cancelled: true};

  var _root = null;
  var _pollTimer = null;
  var state = {
    runs: [],
    selectedRunId: '',
    loading: true,
    error: '',
    lastUpdated: ''
  };

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
    return fetch('/ui/runs/live')
      .then(function (resp) {
        if (!resp.ok) throw new Error('HTTP ' + resp.status);
        return resp.json();
      })
      .catch(function () {
        return fetch('/ui/runs').then(function (resp) {
          if (!resp.ok) throw new Error('HTTP ' + resp.status);
          return resp.json();
        });
      })
      .then(function (payload) {
        state.runs = sortRuns(payload.runs || []);
        state.loading = false;
        state.error = '';
        state.lastUpdated = new Date().toISOString();
        ensureSelection();
        render();
      })
      .catch(function (err) {
        state.loading = false;
        state.error = err && err.message ? err.message : String(err);
        render();
      });
  }

  function sortRuns(runs) {
    return runs.slice().sort(function (a, b) {
      return Date.parse(b.created_at || '') - Date.parse(a.created_at || '');
    });
  }

  function ensureSelection() {
    var selected = findRun(state.selectedRunId);
    if (selected) return;

    var active = state.runs.filter(isLiveRun);
    state.selectedRunId = (active[0] || state.runs[0] || {}).run_id || '';
  }

  function findRun(runId) {
    for (var i = 0; i < state.runs.length; i++) {
      if (state.runs[i].run_id === runId) return state.runs[i];
    }
    return null;
  }

  function selectedRun() {
    return findRun(state.selectedRunId) || state.runs[0] || null;
  }

  function render() {
    if (!_root) return;
    _root.innerHTML = pageHtml();
    bind();
  }

  function bind() {
    var refreshBtn = document.getElementById('flow-refresh-btn');
    if (refreshBtn) refreshBtn.addEventListener('click', refresh);

    var items = document.querySelectorAll('[data-flow-run-id]');
    Array.prototype.forEach.call(items, function (item) {
      item.addEventListener('click', function (event) {
        if (item.getAttribute('href') && item.getAttribute('href') !== '#') return;
        event.preventDefault();
        state.selectedRunId = item.getAttribute('data-flow-run-id') || '';
        render();
      });
    });
  }

  function pageHtml() {
    var run = selectedRun();
    return '' +
      '<section class="page flow-demo flow-monitor">' +
        '<header class="page-header">' +
          '<div class="page-title-wrap">' +
            '<p class="eyebrow">Flow</p>' +
            '<h1>Live benchmark monitor.</h1>' +
            '<p class="subtitle">Background REST/MCP runs appear here as the server updates their run state.</p>' +
          '</div>' +
          '<div class="flow-actions">' +
            '<button class="btn btn-secondary" id="flow-refresh-btn" type="button">Refresh</button>' +
          '</div>' +
        '</header>' +
        summaryHtml() +
        statusBannerHtml() +
        '<div class="run-agent-grid flow-monitor-grid">' +
          '<aside class="panel run-control-panel flow-run-list-panel">' +
            '<h2>Runs</h2>' +
            runListHtml() +
          '</aside>' +
          '<section class="panel run-conversation-panel">' +
            '<div class="run-panel-header">' +
              '<div>' +
                '<h2>Conversation</h2>' +
                (run ? '<p>' + escapeHtml(runLabel(run)) + '</p>' : '') +
              '</div>' +
            '</div>' +
            '<div id="flow-conversation" class="run-conversation">' +
              conversationHtml(run) +
            '</div>' +
          '</section>' +
          '<aside class="panel run-tools-panel">' +
            '<h2>Tool Activity</h2>' +
            toolHtml(run) +
          '</aside>' +
        '</div>' +
      '</section>';
  }

  function summaryHtml() {
    var active = state.runs.filter(isLiveRun).length;
    var completed = state.runs.filter(function (run) { return run.status === 'completed'; }).length;
    var failed = state.runs.filter(function (run) { return run.status === 'failed'; }).length;
    var updated = state.lastUpdated ? formatTime(state.lastUpdated) : 'pending';
    return '' +
      '<div class="summary-strip flow-summary-strip">' +
        summaryPill('Active', String(active)) +
        summaryPill('Completed', String(completed)) +
        summaryPill('Failed', String(failed)) +
        summaryPill('Updated', updated) +
      '</div>';
  }

  function summaryPill(label, value) {
    return '<span class="summary-pill"><strong>' + escapeHtml(label) + '</strong> ' + escapeHtml(value) + '</span>';
  }

  function statusBannerHtml() {
    if (state.error) {
      return '<div class="flow-fail-banner"><strong>Error.</strong> ' + escapeHtml(state.error) + '</div>';
    }
    if (state.loading) {
      return '<div class="flow-now-banner">Loading live run state.</div>';
    }
    if (!state.runs.length) {
      return '<div class="flow-now-banner">Waiting for benchmark runs.</div>';
    }
    return '';
  }

  function runListHtml() {
    if (!state.runs.length) {
      return '<p class="detail-empty-note">Waiting for benchmark runs.</p>';
    }
    return '<div class="flow-run-list">' + state.runs.map(runItemHtml).join('') + '</div>';
  }

  function runItemHtml(run) {
    var selected = run.run_id === state.selectedRunId ? ' selected' : '';
    var terminal = TERMINAL_STATUSES[run.status];
    var status = displayStatus(run);
    var href = terminal && run.status === 'completed' && run.session_id
      ? '#/results/' + encodeURIComponent(run.session_id)
      : '#';
    var session = run.session_id ? run.session_id.slice(0, 8) : 'pending';
    var turn = run.turn != null ? 'Turn ' + run.turn : titleCase(status || '');
    return '' +
      '<a class="flow-run-item' + selected + '" href="' + href + '" data-flow-run-id="' + escapeHtml(run.run_id || '') + '">' +
        '<span class="flow-run-item-top">' +
          '<strong>' + escapeHtml(run.public_task_label || '-') + '</strong>' +
          '<span class="summary-pill run-status-' + escapeHtml(status || '') + '">' + escapeHtml(statusLabel(status)) + '</span>' +
        '</span>' +
        '<span class="flow-run-item-meta">' + escapeHtml(session) + ' · ' + escapeHtml(turn) + '</span>' +
        '<span class="flow-run-item-time">' + escapeHtml(formatTime(run.updated_at || run.created_at)) + '</span>' +
      '</a>';
  }

  function conversationHtml(run) {
    if (!run) return '<div class="run-empty-conversation">Waiting for benchmark runs.</div>';
    var conversation = run.conversation || [];
    if (!conversation.length) {
      return '<div class="run-empty-conversation">' + escapeHtml(emptyConversationText(run)) + '</div>';
    }
    return conversation.map(messageHtml).join('');
  }

  function emptyConversationText(run) {
    var status = displayStatus(run);
    if (status === 'waiting') return 'Run created.';
    if (status === 'claimed') return 'Agent connected.';
    if (status === 'active') return 'Session starting.';
    if (status === 'stale') return 'Previous server process ended before archive creation.';
    if (status === 'completed') return 'Open Results for the archived replay.';
    if (status === 'failed') return run.error || 'Run failed.';
    if (status === 'cancelled') return 'Run cancelled.';
    return 'Waiting for session activity.';
  }

  function messageHtml(msg) {
    var role = (msg.role === 'user' || msg.role === 'student') ? 'student' : 'tutor';
    var label = role === 'student' ? 'Student' : 'Tutor';
    var body = renderMarkdown(msg.content || '');
    return '' +
      '<article class="run-message ' + role + '">' +
        '<div class="run-message-label">' + label + '</div>' +
        '<div class="run-message-bubble">' + body + '</div>' +
      '</article>';
  }

  function renderMarkdown(value) {
    if (window.QTB && typeof window.QTB.renderMarkdown === 'function') {
      return window.QTB.renderMarkdown(value || '');
    }
    return '<p>' + escapeHtml(value || '') + '</p>';
  }

  function toolHtml(run) {
    if (!run) return '<p class="detail-empty-note">Waiting for benchmark runs.</p>';
    var logs = run.recent_tool_logs || [];
    if (!logs.length) {
      return '<p class="detail-empty-note">' + escapeHtml(emptyToolText(run)) + '</p>';
    }
    return '<div class="flow-tool-list">' + logs.slice().reverse().map(toolItemHtml).join('') + '</div>';
  }

  function emptyToolText(run) {
    var status = displayStatus(run);
    if (status === 'active') return 'Tool calls will appear as the agent works.';
    if (status === 'completed') return 'Archived tool calls are available in Results.';
    return titleCase(status || 'pending');
  }

  function toolItemHtml(log) {
    var ok = log.success === false ? ' err' : ' ok';
    var duration = log.duration_ms != null ? Math.round(log.duration_ms) + ' ms' : '';
    return '' +
      '<article class="flow-tool-item' + ok + '">' +
        '<div class="flow-tool-head">' +
          '<strong>' + escapeHtml(log.name || 'tool') + '</strong>' +
          '<span>' + escapeHtml(duration) + '</span>' +
        '</div>' +
        '<div class="flow-tool-meta">' + escapeHtml(formatTime(log.timestamp)) + '</div>' +
      '</article>';
  }

  function runLabel(run) {
    var parts = [
      run.public_task_label || 'Run',
      statusLabel(displayStatus(run)),
      run.session_phase || ''
    ].filter(Boolean);
    return parts.join(' · ');
  }

  function isLiveRun(run) {
    if (run.is_live === true) return true;
    return ACTIVE_STATUSES[run.status] && displayStatus(run) !== 'stale';
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
