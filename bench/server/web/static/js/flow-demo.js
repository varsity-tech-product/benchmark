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
  var state = {
    runs: [],
    loading: true,
    error: '',
    lastUpdated: '',
    statusFilter: 'all',
    search: ''
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
        state.loading = false;
        state.error = '';
        state.lastUpdated = new Date().toISOString();
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

  function render() {
    if (!_root) return;
    _root.innerHTML = pageHtml();
    bind();
  }

  function bind() {
    var refreshBtn = document.getElementById('flow-refresh-btn');
    if (refreshBtn) refreshBtn.addEventListener('click', refresh);

    var status = document.getElementById('flow-status-filter');
    if (status) {
      status.value = state.statusFilter;
      status.addEventListener('change', function (event) {
        state.statusFilter = event.target.value || 'all';
        render();
      });
    }

    var search = document.getElementById('flow-search');
    if (search) {
      search.value = state.search;
      search.addEventListener('input', function (event) {
        state.search = String(event.target.value || '').trim().toLowerCase();
        render();
      });
    }
  }

  function pageHtml() {
    var statuses = sortedUnique(state.runs.map(function (run) { return displayStatus(run); }));
    var visible = filteredRuns();
    return '' +
      '<section class="page flow-demo">' +
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
        filterHtml(statuses) +
        '<div class="results-meta">' +
          '<div class="results-count">' + escapeHtml(String(visible.length)) + ' run(s) shown</div>' +
        '</div>' +
        (visible.length
          ? '<div class="results-grid flow-results-grid">' + visible.map(runCardHtml).join('') + '</div>'
          : emptyHtml()) +
      '</section>';
  }

  function summaryHtml() {
    var live = state.runs.filter(isLiveRun).length;
    var completed = state.runs.filter(function (run) { return run.status === 'completed'; }).length;
    var failed = state.runs.filter(function (run) { return run.status === 'failed'; }).length;
    var updated = state.lastUpdated ? formatTime(state.lastUpdated) : 'pending';
    return '' +
      '<div class="summary-strip flow-summary-strip">' +
        summaryPill('Live', String(live)) +
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

  function filterHtml(statuses) {
    var options = ['<option value="all">All</option>'].concat(statuses.map(function (status) {
      var selected = status === state.statusFilter ? ' selected' : '';
      return '<option value="' + escapeHtml(status) + '"' + selected + '>' + escapeHtml(statusLabel(status)) + '</option>';
    }));
    return '' +
      '<section class="panel filter-bar">' +
        '<label class="filter-field">' +
          '<span class="filter-label">Status</span>' +
          '<select id="flow-status-filter" class="filter-select">' + options.join('') + '</select>' +
        '</label>' +
        '<label class="filter-field">' +
          '<span class="filter-label">Search</span>' +
          '<input id="flow-search" class="filter-input" type="search" placeholder="task, run_id, session_id..." value="' + escapeHtml(state.search) + '">' +
        '</label>' +
      '</section>';
  }

  function filteredRuns() {
    return state.runs.filter(function (run) {
      var status = displayStatus(run);
      if (state.statusFilter !== 'all' && status !== state.statusFilter) return false;
      if (state.search) {
        var haystack = [
          run.run_id,
          run.session_id,
          run.public_task_label,
          run.status,
          run.observer_status
        ].join(' ').toLowerCase();
        if (haystack.indexOf(state.search) === -1) return false;
      }
      return true;
    });
  }

  function runCardHtml(run) {
    var status = displayStatus(run);
    var conversation = run.conversation || [];
    var logs = run.recent_tool_logs || [];
    var href = run.status === 'completed' && run.session_id
      ? '#/results/' + encodeURIComponent(run.session_id)
      : '';
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

  function previewHtml(run) {
    var conversation = run.conversation || [];
    var logs = run.recent_tool_logs || [];
    var messages = conversation.slice(Math.max(conversation.length - 2, 0));
    return '' +
      '<div class="flow-card-preview">' +
        '<div class="flow-preview-block">' +
          '<h3>Conversation</h3>' +
          (messages.length ? messages.map(messagePreviewHtml).join('') : '<p class="detail-empty-note">' + escapeHtml(emptyConversationText(run)) + '</p>') +
        '</div>' +
        '<div class="flow-preview-block">' +
          '<h3>Tool Activity</h3>' +
          (logs.length ? toolListHtml(logs.slice(-4).reverse()) : '<p class="detail-empty-note">' + escapeHtml(emptyToolText(run)) + '</p>') +
        '</div>' +
      '</div>';
  }

  function messagePreviewHtml(msg) {
    var role = (msg.role === 'user' || msg.role === 'student') ? 'Student' : 'Tutor';
    return '' +
      '<div class="flow-message-preview">' +
        '<strong>' + escapeHtml(role) + '</strong>' +
        '<span>' + escapeHtml(truncate(msg.content || '', 260)) + '</span>' +
      '</div>';
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

  function emptyHtml() {
    if (state.runs.length) return '<div class="empty-state"><p>No runs match the current filters.</p></div>';
    return '<div class="empty-state"><p>Waiting for benchmark runs.</p></div>';
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

  function emptyToolText(run) {
    var status = displayStatus(run);
    if (status === 'active') return 'Tool calls will appear as the agent works.';
    if (status === 'completed') return 'Archived tool calls are available in Results.';
    return statusLabel(status || 'pending');
  }

  function isLiveRun(run) {
    if (run.is_live === true) return true;
    return LIVE_STATUSES[run.status] && displayStatus(run) !== 'stale';
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

  function sortedUnique(values) {
    var map = {};
    values.forEach(function (value) {
      if (value == null || value === '') return;
      map[String(value)] = true;
    });
    return Object.keys(map).sort();
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

  function truncate(value, maxLength) {
    var text = String(value || '').replace(/\s+/g, ' ').trim();
    if (text.length <= maxLength) return text;
    return text.slice(0, maxLength - 1) + '…';
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
