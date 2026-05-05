/**
 * run-agent.js — "My Run" progress and run monitor flow.
 *
 * 1. Render the current user's run history grouped by public task label.
 * 2. Keep the run history refreshed while the page is open.
 * 3. Monitor an individual run when opened from the history.
 * 4. Share the agent prompt builder with the API key modal.
 */
(function () {
  'use strict';

  // Expose via QTB namespace for app.js integration
  window.QTB = window.QTB || {};

  var _runId = null;
  var _pollTimer = null;
  var _livePollTimer = null;
  var _runListTimer = null;
  var _myRunGeneration = 0;

  // ── Owner-token store ──
  // Keyed by run_id so multiple tabs/runs can coexist. Persisted in
  // sessionStorage under 'qtb_run_tokens' so a reload keeps monitoring.
  var _TOKEN_STORAGE_KEY = 'qtb_run_tokens';

  function _loadTokens() {
    try {
      var raw = sessionStorage.getItem(_TOKEN_STORAGE_KEY);
      return raw ? JSON.parse(raw) : {};
    } catch (e) { return {}; }
  }

  function _saveTokens(tokens) {
    try {
      sessionStorage.setItem(_TOKEN_STORAGE_KEY, JSON.stringify(tokens));
    } catch (e) { /* quota/permission — ignore */ }
  }

  function _rememberControlToken(runId, controlToken) {
    if (!runId || !controlToken) return;
    var t = _loadTokens();
    t[runId] = controlToken;
    _saveTokens(t);
  }

  function _forgetControlToken(runId) {
    if (!runId) return;
    var t = _loadTokens();
    if (runId in t) { delete t[runId]; _saveTokens(t); }
  }

  function _ownerFetch(url, options) {
    options = options || {};
    options.headers = options.headers || {};
    var tokens = _loadTokens();
    var tok = _runId ? tokens[_runId] : null;
    if (tok) {
      options.headers['Authorization'] = 'Bearer ' + tok;
    }
    return _authFetch(url, options);
  }

  function _authFetch(url, options) {
    if (window.QTB && typeof window.QTB.authFetch === 'function') {
      return window.QTB.authFetch(url, options);
    }
    return fetch(url, options);
  }

  function escapeHtml(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  // ── Main render ──

  window.QTB.renderMyAgentPage = function (app, state, payload) {
    _cleanup();
    var generation = ++_myRunGeneration;

    _renderMyRunPage(app, {loading: true, runs: []});
    _refreshMyRunPage(app, false, generation);
    _runListTimer = setInterval(function () {
      _refreshMyRunPage(app, true, generation);
    }, 5000);
  };

  window.QTB.renderRunMonitorPage = function (app, runId) {
    _cleanup();
    _runId = String(runId || '').trim();
    if (!_runId) {
      app.innerHTML =
        '<section class="error-state">' +
          '<p class="eyebrow">Error</p>' +
          '<h1>Run unavailable</h1>' +
          '<p>Missing run id.</p>' +
        '</section>';
      return;
    }
    _renderMonitorPage(app);
  };

  window.addEventListener('hashchange', function () {
    if (
      location.hash.indexOf('#/my-run/') !== 0 &&
      location.hash.indexOf('#/run/') !== 0
    ) {
      _cleanup();
    }
  });

  function _absoluteUrl(path) {
    if (/^https?:\/\//i.test(path)) return path;
    return window.location.origin + path;
  }

  function _skillRawUrl() {
    return _absoluteUrl('/skill.md');
  }

  function _jsonResponse(response) {
    return response.json().then(function (payload) {
      if (!response.ok) {
        var error = new Error(payload.error || ('HTTP ' + response.status));
        error.permanent = response.status === 404;
        throw error;
      }
      return payload;
    });
  }

  function _loadMyRuns() {
    return _authFetch('/ui/runs?mine=true').then(_jsonResponse)
      .then(function (payload) {
        return payload.runs || [];
      });
  }

  function _refreshMyRunPage(app, quiet, generation) {
    return _loadMyRuns()
      .then(function (runs) {
        if (!_isCurrentMyRunPage(generation)) return runs;
        _renderMyRunPage(app, {runs: runs});
        return runs;
      })
      .catch(function (error) {
        if (!_isCurrentMyRunPage(generation)) return;
        var message = error && error.message ? error.message : String(error || 'Unable to load runs.');
        if (quiet) {
          _markMyRunRefreshError(message);
        } else {
          _renderMyRunPage(app, {error: message, runs: []});
        }
      });
  }

  function _isCurrentMyRunPage(generation) {
    return generation === _myRunGeneration && location.hash === '#/my-run';
  }

  function _markMyRunRefreshError(message) {
    var target = document.getElementById('my-run-refresh-status');
    if (target) target.textContent = 'Refresh failed: ' + message;
  }

  function _buildAgentPrompt(apiKey) {
    var baseUrl = window.location.origin;
    var rawSkillUrl = _skillRawUrl();
    return [
      'Read ' + rawSkillUrl + ' and follow the instructions to join QuantTutorBench.',
      '',
      'Benchmark base URL:',
      baseUrl,
      '',
      'REST API key:',
      apiKey
    ].join('\n');
  }

  window.QTB.agentSkillRawUrl = _skillRawUrl;
  window.QTB.buildAgentPrompt = _buildAgentPrompt;

  function _renderMyRunPage(app, options) {
    options = options || {};
    var runs = _sortRuns(options.runs || []);
    var loading = !!options.loading;
    var error = options.error || '';
    var stats = _summarizeRuns(runs);

    app.innerHTML =
      '<section class="page run-page my-run-page">' +
        '<header class="page-header run-sticky-header my-run-header">' +
          '<div class="page-title-wrap">' +
            '<p class="eyebrow">Run · My Run</p>' +
            '<h1>My Run</h1>' +
            '<p class="subtitle">Current benchmark task progress and recent run activity.</p>' +
          '</div>' +
          _myRunSummaryHtml(stats) +
        '</header>' +
        '<div class="my-run-toolbar">' +
          '<span id="my-run-refresh-status" class="my-run-refresh-status">' +
            (loading ? 'Loading runs...' : 'Updated ' + escapeHtml(_formatTimestamp(new Date()))) +
          '</span>' +
          '<div class="run-actions">' +
            '<button class="btn btn-secondary" id="my-run-refresh-btn" type="button">Refresh</button>' +
            '<button class="btn btn-primary" id="my-run-api-key-btn" type="button">API Key</button>' +
          '</div>' +
        '</div>' +
        _myRunBodyHtml(runs, loading, error) +
      '</section>';

    _bindMyRunActions(app);
  }

  function _bindMyRunActions(app) {
    var refreshBtn = document.getElementById('my-run-refresh-btn');
    if (refreshBtn) {
      refreshBtn.addEventListener('click', function () {
        refreshBtn.disabled = true;
        var status = document.getElementById('my-run-refresh-status');
        if (status) status.textContent = 'Refreshing...';
        _refreshMyRunPage(app, false, _myRunGeneration);
      });
    }
    var apiKeyButtons = [
      document.getElementById('my-run-api-key-btn'),
      document.getElementById('my-run-empty-api-key-btn')
    ];
    apiKeyButtons.forEach(function (apiKeyBtn) {
      if (!apiKeyBtn) return;
      apiKeyBtn.addEventListener('click', function () {
        if (window.QTB && typeof window.QTB.openApiKeyModal === 'function') {
          window.QTB.openApiKeyModal();
        }
      });
    });
  }

  function _myRunSummaryHtml(stats) {
    return '' +
      '<div class="summary-strip my-run-summary">' +
        _myRunMetric('Completed', stats.completed) +
        _myRunMetric('In Progress', stats.in_progress) +
        _myRunMetric('Failed', stats.failed) +
        _myRunMetric('Not Started', stats.not_started) +
      '</div>';
  }

  function _myRunMetric(label, value) {
    return '<span class="summary-pill my-run-metric"><strong>' + escapeHtml(value) + '</strong> ' + escapeHtml(label) + '</span>';
  }

  function _myRunBodyHtml(runs, loading, error) {
    if (error) {
      return '' +
        '<section class="error-state my-run-state-card">' +
          '<p class="eyebrow">Error</p>' +
          '<h1>Run list unavailable</h1>' +
          '<p>' + escapeHtml(error) + '</p>' +
        '</section>';
    }
    if (loading) {
      return '' +
        '<section class="loading-state my-run-state-card">' +
          '<p class="eyebrow">Loading</p>' +
          '<h1>Loading runs</h1>' +
          '<p>Fetching current progress.</p>' +
        '</section>';
    }
    if (!runs.length) {
      return '' +
        '<section class="empty-state my-run-state-card my-run-empty">' +
          '<p class="eyebrow">Empty</p>' +
          '<h1>No runs yet.</h1>' +
          '<p>Generate an agent prompt from API Key, then run your agent.</p>' +
          '<button class="btn btn-primary" id="my-run-empty-api-key-btn" type="button">API Key</button>' +
        '</section>';
    }
    var groups = _groupRunsByTask(runs);
    return '<div class="my-run-task-list">' + groups.map(_taskGroupHtml).join('') + '</div>';
  }

  function _groupRunsByTask(runs) {
    var byTask = {};
    runs.forEach(function (run) {
      var key = run.task_id || run.public_task_label || 'Unknown Task';
      if (!byTask[key]) {
        byTask[key] = {
          key: key,
          publicLabel: run.public_task_label || '',
          runs: []
        };
      }
      byTask[key].runs.push(run);
    });
    return Object.keys(byTask).map(function (key) {
      var group = byTask[key];
      group.runs = _sortRuns(group.runs);
      group.latest = group.runs[0] || {};
      group.recentAt = _activityTimestamp(group.latest);
      return group;
    }).sort(function (a, b) {
      return _activityValue(b.latest) - _activityValue(a.latest);
    });
  }

  function _taskGroupHtml(group) {
    var latest = group.latest || {};
    var subtitle = [];
    subtitle.push(group.runs.length + (group.runs.length === 1 ? ' run' : ' runs'));
    subtitle.push('Recent ' + _formatTimestamp(group.recentAt));
    if (group.publicLabel && group.publicLabel !== group.key) {
      subtitle.push('Label ' + group.publicLabel);
    }
    return '' +
      '<article class="my-run-task-card">' +
        '<div class="my-run-task-head">' +
          '<div class="my-run-task-title">' +
            '<h2>' + escapeHtml(group.key) + '</h2>' +
            '<div class="my-run-task-meta">' + escapeHtml(subtitle.join(' · ')) + '</div>' +
          '</div>' +
          _statusPillHtml(latest.status) +
        '</div>' +
        '<div class="my-run-row-list">' +
          group.runs.map(_runRowHtml).join('') +
        '</div>' +
      '</article>';
  }

  function _runRowHtml(run) {
    var href = '#/my-run/' + encodeURIComponent(run.run_id || '');
    var details = [];
    if (run.mode) details.push(_titleCase(run.mode));
    if (run.persona_id) details.push('Persona ' + run.persona_id);
    if (run.session_id) details.push('Session ' + _shortId(run.session_id));
    details.push(_formatTimestamp(_activityTimestamp(run)));
    return '' +
      '<a class="my-run-row" href="' + escapeHtml(href) + '">' +
        '<div class="my-run-row-main">' +
          '<span class="my-run-row-title">' + escapeHtml(_shortId(run.run_id)) + '</span>' +
          '<span class="my-run-row-detail">' + escapeHtml(details.join(' · ')) + '</span>' +
        '</div>' +
        _statusPillHtml(run.status) +
      '</a>';
  }

  function _summarizeRuns(runs) {
    var stats = {
      completed: 0,
      in_progress: 0,
      failed: 0,
      not_started: 0
    };
    runs.forEach(function (run) {
      var bucket = _statusBucket(run.status);
      stats[bucket] += 1;
    });
    return stats;
  }

  function _statusBucket(status) {
    if (status === 'completed') return 'completed';
    if (status === 'active' || status === 'claimed') return 'in_progress';
    if (status === 'failed' || status === 'cancelled') return 'failed';
    return 'not_started';
  }

  function _statusPillHtml(status) {
    var clean = String(status || 'waiting');
    return '<span class="status-pill ' + escapeHtml(_statusTone(clean)) + ' my-run-status run-status-' + escapeHtml(clean) + '">' + escapeHtml(_statusLabel(clean)) + '</span>';
  }

  function _statusTone(status) {
    if (status === 'completed') return 'completed';
    if (status === 'active' || status === 'claimed') return 'running';
    if (status === 'failed') return 'failed';
    if (status === 'cancelled') return 'cancelled';
    return 'pending';
  }

  function _statusLabel(status) {
    var labels = {
      waiting: 'Not Started',
      claimed: 'In Progress',
      active: 'In Progress',
      completed: 'Completed',
      failed: 'Failed',
      cancelled: 'Cancelled'
    };
    return labels[status] || _titleCase(status);
  }

  function _sortRuns(runs) {
    return runs.slice().sort(function (a, b) {
      return _activityValue(b) - _activityValue(a);
    });
  }

  function _activityTimestamp(run) {
    return (run && (run.completed_at || run.updated_at || run.claimed_at || run.created_at)) || '';
  }

  function _activityValue(run) {
    var value = _activityTimestamp(run);
    var date = value ? new Date(value) : null;
    var time = date && !isNaN(date.getTime()) ? date.getTime() : 0;
    return time;
  }

  function _formatTimestamp(value) {
    if (!value) return 'Unknown time';
    var date = value instanceof Date ? value : new Date(value);
    if (isNaN(date.getTime())) return String(value);
    return date.toLocaleString();
  }

  function _titleCase(value) {
    return String(value || '')
      .split(/[_\s-]+/)
      .filter(Boolean)
      .map(function (part) {
        return part.charAt(0).toUpperCase() + part.slice(1);
      })
      .join(' ');
  }

  // ── Monitor page (connection info + live data) ──

  function _renderMonitorPage(app, createData) {
    var token = (createData && createData.token) || '';
    var mcp_url = (createData && createData.mcp_url) || '';
    var label = (createData && createData.public_task_label) || _shortId(_runId);
    var launchCmd = (createData && createData.launch_command) || '';

    app.innerHTML =
      '<section class="page run-page">' +
        '<header class="page-header run-sticky-header">' +
          '<div class="page-title-wrap">' +
            '<p class="eyebrow">Run · My Agent</p>' +
            '<h1>Run <span id="myagent-label">' + escapeHtml(label) + '</span></h1>' +
          '</div>' +
          '<div class="summary-strip">' +
            '<span class="summary-pill" id="myagent-status-pill">● Waiting</span>' +
            '<span class="summary-pill" id="myagent-turn-pill">Turn 0</span>' +
          '</div>' +
        '</header>' +

        // Connection info (shown while waiting/claimed)
        '<div id="myagent-connect-panel" class="panel run-connect-panel">' +
          '<h2>Connect your agent</h2>' +
          (token ? (
            '<div class="run-connect-section">' +
              '<h3>MCP</h3>' +
              '<div class="run-connect-field">' +
                '<span class="run-connect-label">Server URL</span>' +
                '<code class="run-connect-value">' + escapeHtml(mcp_url) + '</code>' +
              '</div>' +
              '<div class="run-connect-field">' +
                '<span class="run-connect-label">Auth Token</span>' +
                '<code class="run-connect-value">' + escapeHtml(token) + '</code>' +
                '<button class="btn btn-small run-copy-btn" data-copy="' + escapeHtml(token) + '">Copy</button>' +
              '</div>' +
            '</div>' +
            '<div class="run-connect-section">' +
              '<h3>CLI</h3>' +
              '<code class="run-connect-cmd">' + escapeHtml(launchCmd) + '</code>' +
              '<button class="btn btn-small run-copy-btn" data-copy="' + escapeHtml(launchCmd) + '">Copy</button>' +
            '</div>'
          ) : '<p class="detail-empty-note">Waiting for agent connection details.</p>') +
        '</div>' +

        // Live conversation + tools grid
        '<div class="run-agent-grid" id="myagent-live-grid" style="display:none;">' +
          '<section class="panel run-conversation-panel">' +
            '<h2>Conversation</h2>' +
            '<div id="myagent-conversation" class="chat-messages"></div>' +
          '</section>' +
          '<aside class="panel run-tools-panel">' +
            '<h2>Tool Activity</h2>' +
            '<div id="myagent-tools" class="tool-events"></div>' +
          '</aside>' +
        '</div>' +

        // Actions
        '<div class="run-actions" style="margin-top:1rem;">' +
          '<button class="btn btn-danger" id="myagent-cancel-btn" type="button">Cancel Run</button>' +
          '<a class="btn btn-primary" id="myagent-results-link" href="#" style="display:none;">Open Human Review</a>' +
        '</div>' +
      '</section>';

    // Bind copy buttons
    document.querySelectorAll('.run-copy-btn').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var text = btn.getAttribute('data-copy');
        if (navigator.clipboard) {
          navigator.clipboard.writeText(text);
          btn.textContent = 'Copied!';
          setTimeout(function () { btn.textContent = 'Copy'; }, 1500);
        }
      });
    });

    // Bind cancel
    var cancelBtn = document.getElementById('myagent-cancel-btn');
    if (cancelBtn) {
      cancelBtn.addEventListener('click', function () {
        if (!_runId) return;
        cancelBtn.disabled = true;
        _ownerFetch('/ui/runs/' + encodeURIComponent(_runId) + '/cancel', {method: 'POST'})
          .then(function () {
            _cleanup();
            _updateStatus('cancelled');
          })
          .catch(function () { cancelBtn.disabled = false; });
      });
    }

    // Start polling
    _startStatusPoll();
  }

  // ── Polling ──

  function _startStatusPoll() {
    if (_pollTimer) clearInterval(_pollTimer);
    _pollRunStatus();
    _pollTimer = setInterval(_pollRunStatus, 3000);
  }

  function _pollRunStatus() {
    if (!_runId) return;
    _ownerFetch('/ui/runs/' + encodeURIComponent(_runId))
      .then(_runJsonResponse)
      .then(function (data) {
        if (data == null) return;
        _updateRunHeader(data);
        _updateStatus(data.status);
        if (data.status === 'active') {
          _startLivePoll();
          if (_pollTimer) {
            clearInterval(_pollTimer);
            _pollTimer = null;
          }
        }
        if (data.status === 'completed' || data.status === 'failed' || data.status === 'cancelled') {
          _stopPolling();
          _showTerminalState(data);
        }
      })
      .catch(_showRunLoadError);
  }

  function _startLivePoll() {
    // Hide connection panel, show live grid
    var connectPanel = document.getElementById('myagent-connect-panel');
    var liveGrid = document.getElementById('myagent-live-grid');
    if (connectPanel) connectPanel.style.display = 'none';
    if (liveGrid) liveGrid.style.display = 'grid';

    if (_livePollTimer) clearInterval(_livePollTimer);
    _pollLiveData();
    _livePollTimer = setInterval(_pollLiveData, 2000);
  }

  function _pollLiveData() {
    if (!_runId) return;
    _ownerFetch('/ui/runs/' + encodeURIComponent(_runId) + '/live')
      .then(_runJsonResponse)
      .then(function (data) {
        if (data == null) return;
        _updateStatus(data.run_status);
        if (data.turn != null) {
          var turnPill = document.getElementById('myagent-turn-pill');
          if (turnPill) turnPill.textContent = 'Turn ' + data.turn;
        }
        _renderLiveData(data.conversation || [], data.recent_tool_logs || []);

        if (data.run_status === 'completed' || data.run_status === 'failed' || data.run_status === 'cancelled') {
          _stopPolling();
          _showTerminalState({
            status: data.run_status,
            session_id: data.session_id
          });
        }
      })
      .catch(_showRunLoadError);
  }

  function _stopPolling() {
    if (_pollTimer) { clearInterval(_pollTimer); _pollTimer = null; }
    if (_livePollTimer) { clearInterval(_livePollTimer); _livePollTimer = null; }
  }

  function _showTokenLostMessage() {
    var pill = document.getElementById('myagent-status-pill');
    if (pill) pill.textContent = '● Access expired';
    var cancelBtn = document.getElementById('myagent-cancel-btn');
    if (cancelBtn) cancelBtn.style.display = 'none';
    var connectPanel = document.getElementById('myagent-connect-panel');
    if (connectPanel) {
      var msg = document.createElement('div');
      msg.className = 'run-token-lost';
      msg.textContent =
        'Run access expired or token lost. Cannot resume monitoring. ' +
        'Archived sessions will still appear under Human Review once the run completes.';
      connectPanel.appendChild(msg);
    }
  }

  function _runJsonResponse(response) {
    if (response.status === 401 || response.status === 403) {
      _stopPolling();
      _showTokenLostMessage();
      return null;
    }
    return response.json().then(function (payload) {
      if (!response.ok) {
        throw new Error(payload.error || ('HTTP ' + response.status));
      }
      return payload;
    });
  }

  function _updateRunHeader(data) {
    var label = document.getElementById('myagent-label');
    if (!label) return;
    label.textContent = data.public_task_label || data.task_id || _shortId(data.run_id || _runId);
  }

  function _showTerminalState(data) {
    if (data.status === 'completed' && data.session_id) {
      var link = document.getElementById('myagent-results-link');
      if (link) {
        link.href = '#/review/' + encodeURIComponent(data.session_id);
        link.style.display = 'inline-block';
      }
    }
    var cancelBtn = document.getElementById('myagent-cancel-btn');
    if (cancelBtn) cancelBtn.style.display = 'none';
  }

  function _showRunLoadError(error) {
    if (!_runId) return;
    if (error && error.permanent) _stopPolling();
    var connectPanel = document.getElementById('myagent-connect-panel');
    if (connectPanel) {
      connectPanel.innerHTML =
        '<h2>' + (error && error.permanent ? 'Run unavailable' : 'Connection retrying') + '</h2>' +
        '<p class="detail-empty-note">' + escapeHtml(error && error.message ? error.message : String(error || 'Unable to load run.')) + '</p>';
    }
    _updateStatus(error && error.permanent ? 'failed' : 'retrying');
  }

  function _updateStatus(status) {
    var pill = document.getElementById('myagent-status-pill');
    if (!pill) return;
    var labels = {
      waiting: '● Waiting for agent',
      claimed: '● Agent connected',
      active: '● Running',
      retrying: '● Retrying',
      completed: '✓ Completed',
      failed: '✗ Failed',
      cancelled: '— Cancelled'
    };
    pill.textContent = labels[status] || status;
    pill.className = 'summary-pill run-status-' + status;
  }

  var _lastConvLen = 0;
  var _lastToolLen = 0;
  var _lastSendLen = 0;

  /**
   * Split tool logs into domain tools and send_message events.
   * Mirrors ui_indexer.py _split_tool_logs() logic.
   */
  function _splitLogs(logs) {
    var domainTools = [];
    var sendEvents = [];
    var sendIndex = 0;
    (logs || []).forEach(function (log) {
      if (log.name === 'send_message') {
        sendEvents.push(_buildSendEvent(log, sendIndex));
        sendIndex += 1;
      } else {
        domainTools.push(log);
      }
    });
    return { tools: domainTools, sends: sendEvents };
  }

  /**
   * Build a send_message event object from a raw tool log.
   * Mirrors ui_indexer.py _build_send_message_event().
   * @param {object} log - raw tool log entry
   * @param {number} index - 0-based index among send_message events (maps to assistant turn)
   */
  function _buildSendEvent(log, index) {
    var args = log.args || {};
    var rawResult = log.result;
    var parsed = {};
    if (typeof rawResult === 'string') {
      try { parsed = JSON.parse(rawResult); } catch (e) { parsed = {}; }
    } else if (typeof rawResult === 'object' && rawResult) {
      parsed = rawResult;
    }
    return {
      name: 'send_message',
      request_text: args.text || '',
      attachments: args.attachments || [],
      user_message: parsed.user_message || '',
      status: parsed.status || 'active',
      reason: parsed.reason || '',
      error: parsed.error || '',
      success: log.success !== false,
      duration_ms: log.duration_ms || null,
      timestamp: log.timestamp || null,
      turn_index: (typeof log.turn_index === 'number') ? log.turn_index : index,
      raw_args: args,
      raw_result: rawResult
    };
  }

  function _renderLiveData(conversation, rawLogs) {
    var split = _splitLogs(rawLogs);
    _renderConversation(conversation, split.sends);
    _renderDomainTools(split.tools);
  }

  function _renderConversation(conversation, sendEvents) {
    var el = document.getElementById('myagent-conversation');
    if (!el) return;

    var convChanged = conversation.length !== _lastConvLen;
    var sendChanged = sendEvents.length !== _lastSendLen;
    if (!convChanged && !sendChanged) return;

    _lastConvLen = conversation.length;
    _lastSendLen = sendEvents.length;

    // Use chat.js buildConversationReplay for full rendering
    if (window.QTB && typeof window.QTB.buildConversationReplay === 'function') {
      el.innerHTML = '';
      window.QTB.buildConversationReplay(el, conversation, [], sendEvents);
    } else {
      // Fallback: simple text display
      el.innerHTML = conversation.map(function (msg) {
        var role = msg.role === 'user' ? 'User' : 'Tutor';
        return '<div class="chat-msg chat-' + escapeHtml(msg.role) + '">' +
          '<strong>' + role + ':</strong> ' + escapeHtml((msg.content || '').substring(0, 500)) +
          '</div>';
      }).join('');
    }
    el.scrollTop = el.scrollHeight;
  }

  function _renderDomainTools(tools) {
    var el = document.getElementById('myagent-tools');
    if (!el) return;

    // Use tools.js buildToolReplay for rich rendering (domain tools only)
    if (window.QTB && typeof window.QTB.buildToolReplay === 'function') {
      if (tools.length !== _lastToolLen) {
        _lastToolLen = tools.length;
        el.innerHTML = '';
        window.QTB.buildToolReplay(el, tools);
      }
    } else {
      // Fallback: simple list
      if (tools.length !== _lastToolLen) {
        _lastToolLen = tools.length;
        el.innerHTML = tools.map(function (log) {
          var icon = log.success !== false ? '✓' : '✗';
          var dur = log.duration_ms != null ? ' (' + Math.round(log.duration_ms) + 'ms)' : '';
          return '<div class="tool-event">' +
            '<span class="tool-icon">' + icon + '</span> ' +
            '<strong>' + escapeHtml(log.name) + '</strong>' + dur +
            '</div>';
        }).join('');
      }
    }
  }

  function _cleanup() {
    _stopPolling();
    if (_runListTimer) { clearInterval(_runListTimer); _runListTimer = null; }
    _myRunGeneration += 1;
    _runId = null;
    _lastConvLen = 0;
    _lastToolLen = 0;
    _lastSendLen = 0;
  }

  function _shortId(value) {
    return String(value || '').slice(0, 8) || '-';
  }

})();
