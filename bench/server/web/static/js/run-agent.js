/**
 * run-agent.js — "My Agent" flow for the Run page.
 *
 * 1. User selects task → POST /ui/runs → show connection info (MCP URL + token)
 * 2. Poll /ui/runs/{id} for status changes (waiting → claimed → active)
 * 3. Poll /ui/runs/{id}/live for real-time conversation + tool logs
 * 4. Cancel button → POST /ui/runs/{id}/cancel
 * 5. On completed → show link to Human Review
 */
(function () {
  'use strict';

  // Expose via QTB namespace for app.js integration
  window.QTB = window.QTB || {};

  var _runId = null;
  var _pollTimer = null;
  var _livePollTimer = null;

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

  function publicTaskLabel(taskId) {
    if (!taskId) return '—';
    var m = taskId.match(/^([A-Za-z]\d{2})/);
    return m ? m[1] : taskId;
  }

  // ── Catalog loading ──

  var _catalog = null;

  function loadCatalog(callback) {
    if (_catalog) { callback(_catalog); return; }
    _authFetch('/ui/tasks/catalog/labels')
      .then(function (r) { return r.json(); })
      .then(function (data) {
        _catalog = data.tasks || [];
        callback(_catalog);
      })
      .catch(function () { callback([]); });
  }

  // ── Main render ──

  window.QTB.renderMyAgentPage = function (app, state, payload) {
    // If we have an active run, show the monitor page
    if (_runId) {
      _renderMonitorPage(app);
      return;
    }

    // Otherwise show task selection + create run form
    loadCatalog(function (tasks) {
      _renderCreatePage(app, tasks, payload && payload.tasks);
    });
  };

  function _taskLabels(tasks, fallbackTasks) {
    var labels = [];
    var seen = {};
    (tasks || []).concat(fallbackTasks || []).forEach(function (task) {
      var label = task && (task.label || publicTaskLabel(task.task_id || task.id));
      if (!label || seen[label]) return;
      seen[label] = true;
      labels.push(label);
    });
    return labels;
  }

  function _renderCreatePage(app, tasks, fallbackTasks) {
    var labels = _taskLabels(tasks, fallbackTasks);
    var hasTasks = labels.length > 0;
    var selectedLabel = hasTasks ? labels[0] : '';
    var taskOptions = hasTasks ? labels.map(function (label) {
      return '<option value="' + escapeHtml(label) + '">' +
        escapeHtml(label) +
        '</option>';
    }).join('') : '<option value="">No tasks available</option>';

    app.innerHTML =
      '<section class="page run-page">' +
        '<header class="page-header run-sticky-header">' +
          '<div class="page-title-wrap">' +
            '<p class="eyebrow">Run · My Agent</p>' +
            '<h1>Connect your agent to the benchmark.</h1>' +
            '<p class="subtitle">Create a task-specific run, manage the REST API key, or open the QuantTutorBench REST agent skill.</p>' +
          '</div>' +
        '</header>' +
        '<div class="run-agent-create-panel">' +
          '<aside class="panel run-agent-create-card">' +
            '<div class="run-agent-card-head">' +
              '<div>' +
                '<h2>Agent Connection</h2>' +
                '<p class="detail-empty-note">Create a task-specific run to receive a run token and connection details.</p>' +
              '</div>' +
              '<span class="run-selected-badge" id="myagent-selected-badge">' + escapeHtml(selectedLabel || 'No task') + '</span>' +
            '</div>' +
            '<label class="filter-field">' +
              '<span class="filter-label">Task</span>' +
              '<select id="myagent-task-select" class="filter-select"' + (hasTasks ? '' : ' disabled') + '>' + taskOptions + '</select>' +
            '</label>' +
            '<div class="run-task-reflection" id="myagent-task-reflection" aria-live="polite">' +
              '<span>Selected task</span>' +
              '<strong id="myagent-task-reflection-value">' + escapeHtml(selectedLabel || 'Unavailable') + '</strong>' +
            '</div>' +
            '<div class="run-actions">' +
              '<button class="btn btn-primary" id="myagent-create-btn" type="button"' + (hasTasks ? '' : ' disabled') + '>Create Run</button>' +
            '</div>' +
            '<div id="myagent-error" class="run-error" style="display:none;"></div>' +
          '</aside>' +
          '<section class="panel run-agent-api-card">' +
            '<h2>Agent API</h2>' +
            '<p class="detail-empty-note">Use the REST agent skill for API-key based agents, or open the API key manager before connecting an external agent.</p>' +
            '<div class="run-agent-api-actions">' +
              '<button class="btn btn-secondary" id="myagent-api-key-btn" type="button">API Key</button>' +
              '<a class="btn btn-secondary" href="/skills/quanttutorbench-rest-agent" target="_blank" rel="noreferrer">REST Agent Skill</a>' +
            '</div>' +
          '</section>' +
        '</div>' +
      '</section>';

    // Bind
    var createBtn = document.getElementById('myagent-create-btn');
    var apiKeyBtn = document.getElementById('myagent-api-key-btn');
    var taskSelect = document.getElementById('myagent-task-select');
    var errorDiv = document.getElementById('myagent-error');
    var selectedBadge = document.getElementById('myagent-selected-badge');
    var taskReflection = document.getElementById('myagent-task-reflection');
    var taskReflectionValue = document.getElementById('myagent-task-reflection-value');
    var pulseTimer = null;

    if (apiKeyBtn) {
      apiKeyBtn.addEventListener('click', function () {
        if (window.QTB && typeof window.QTB.openApiKeyModal === 'function') {
          window.QTB.openApiKeyModal();
        }
      });
    }

    function updateTaskReflection(active, pulse) {
      var value = taskSelect && taskSelect.value ? taskSelect.value : 'Unavailable';
      if (selectedBadge) selectedBadge.textContent = value;
      if (taskReflectionValue) taskReflectionValue.textContent = value;
      if (taskSelect) taskSelect.classList.toggle('is-active', !!active);
      if (taskReflection) {
        taskReflection.classList.toggle('is-active', !!active);
        if (pulse) {
          taskReflection.classList.add('is-updated');
          clearTimeout(pulseTimer);
          pulseTimer = setTimeout(function () {
            taskReflection.classList.remove('is-updated');
          }, 520);
        }
      }
    }

    if (taskSelect) {
      updateTaskReflection(false, false);
      taskSelect.addEventListener('focus', function () { updateTaskReflection(true, false); });
      taskSelect.addEventListener('pointerdown', function () { updateTaskReflection(true, false); });
      taskSelect.addEventListener('blur', function () { updateTaskReflection(false, false); });
      taskSelect.addEventListener('change', function () { updateTaskReflection(true, true); });
    }

    if (createBtn) {
      createBtn.addEventListener('click', function () {
        var task = taskSelect ? taskSelect.value : '';
        if (!task) return;
        createBtn.disabled = true;
        createBtn.textContent = 'Creating...';
        errorDiv.style.display = 'none';

        _authFetch('/ui/runs', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({task: task, mode: 'agent'})
        })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (data.error) {
            errorDiv.textContent = data.error;
            errorDiv.style.display = 'block';
            createBtn.disabled = false;
            createBtn.textContent = 'Create Run';
            return;
          }
          _runId = data.run_id;
          if (data.control_token) {
            _rememberControlToken(data.run_id, data.control_token);
          }
          _renderMonitorPage(app, data);
        })
        .catch(function (err) {
          errorDiv.textContent = 'Network error: ' + err.message;
          errorDiv.style.display = 'block';
          createBtn.disabled = false;
          createBtn.textContent = 'Create Run';
        });
      });
    }
  }

  // ── Monitor page (connection info + live data) ──

  function _renderMonitorPage(app, createData) {
    var token = (createData && createData.token) || '';
    var mcp_url = (createData && createData.mcp_url) || '';
    var label = (createData && createData.public_task_label) || '';
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
          ) : '') +
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
        _ownerFetch('/ui/runs/' + _runId + '/cancel', {method: 'POST'})
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
    _pollTimer = setInterval(function () {
      if (!_runId) return;
      _ownerFetch('/ui/runs/' + _runId)
        .then(function (r) {
          if (r.status === 401) {
            _stopPolling();
            _showTokenLostMessage();
            return null;
          }
          return r.json();
        })
        .then(function (data) {
          if (data == null) return;
          _updateStatus(data.status);
          if (data.status === 'active') {
            _startLivePoll();
            clearInterval(_pollTimer);
            _pollTimer = null;
          }
          if (data.status === 'completed' || data.status === 'failed' || data.status === 'cancelled') {
            _stopPolling();
            if (data.status === 'completed' && data.session_id) {
              var link = document.getElementById('myagent-results-link');
              if (link) {
                link.href = '#/review/' + data.session_id;
                link.style.display = 'inline-block';
              }
            }
          }
        });
    }, 3000);
  }

  function _startLivePoll() {
    // Hide connection panel, show live grid
    var connectPanel = document.getElementById('myagent-connect-panel');
    var liveGrid = document.getElementById('myagent-live-grid');
    if (connectPanel) connectPanel.style.display = 'none';
    if (liveGrid) liveGrid.style.display = 'grid';

    if (_livePollTimer) clearInterval(_livePollTimer);
    _livePollTimer = setInterval(function () {
      if (!_runId) return;
      _ownerFetch('/ui/runs/' + _runId + '/live')
        .then(function (r) {
          if (r.status === 401) {
            _stopPolling();
            _showTokenLostMessage();
            return null;
          }
          return r.json();
        })
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
            // Show review link for completed runs
            if (data.run_status === 'completed' && data.session_id) {
              var link = document.getElementById('myagent-results-link');
              if (link) {
                link.href = '#/review/' + data.session_id;
                link.style.display = 'inline-block';
              }
            }
            // Hide cancel button on terminal state
            var cancelBtn = document.getElementById('myagent-cancel-btn');
            if (cancelBtn) cancelBtn.style.display = 'none';
          }
        });
    }, 2000);
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

  function _updateStatus(status) {
    var pill = document.getElementById('myagent-status-pill');
    if (!pill) return;
    var labels = {
      waiting: '● Waiting for agent',
      claimed: '● Agent connected',
      active: '● Running',
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
      student_message: parsed.student_message || '',
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
        var role = msg.role === 'user' ? 'Student' : 'Tutor';
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
    _runId = null;
    _lastConvLen = 0;
    _lastToolLen = 0;
    _lastSendLen = 0;
  }

})();
