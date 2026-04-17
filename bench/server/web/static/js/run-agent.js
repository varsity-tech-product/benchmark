/**
 * run-agent.js — "My Agent" flow for the Run page.
 *
 * 1. User selects task → POST /ui/runs → show connection info (MCP URL + token)
 * 2. Poll /ui/runs/{id} for status changes (waiting → claimed → active)
 * 3. Poll /ui/runs/{id}/live for real-time conversation + tool logs
 * 4. Cancel button → POST /ui/runs/{id}/cancel
 * 5. On completed → show link to Results
 */
(function () {
  'use strict';

  // Expose via QTB namespace for app.js integration
  window.QTB = window.QTB || {};

  var _runId = null;
  var _pollTimer = null;
  var _livePollTimer = null;

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
    fetch('/ui/tasks/catalog')
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
      _renderCreatePage(app, tasks);
    });
  };

  function _renderCreatePage(app, tasks) {
    var taskOptions = tasks.map(function (t) {
      return '<option value="' + escapeHtml(t.label) + '">' +
        escapeHtml(t.label) + ' (' + escapeHtml(t.category) + ')' +
        '</option>';
    }).join('');

    app.innerHTML =
      '<section class="page run-page">' +
        '<header class="page-header run-sticky-header">' +
          '<div class="page-title-wrap">' +
            '<p class="eyebrow">Run · My Agent</p>' +
            '<h1>Connect your agent to the benchmark.</h1>' +
          '</div>' +
        '</header>' +
        '<div class="run-agent-create-panel">' +
          '<aside class="panel">' +
            '<h2>Select Task</h2>' +
            '<label class="filter-field">' +
              '<span class="filter-label">Task</span>' +
              '<select id="myagent-task-select" class="filter-select">' + taskOptions + '</select>' +
            '</label>' +
            '<div class="run-actions" style="margin-top:1rem;">' +
              '<button class="btn btn-primary" id="myagent-create-btn" type="button">Create Run</button>' +
              '<button class="btn btn-secondary" id="myagent-back-btn" type="button">Back</button>' +
            '</div>' +
            '<div id="myagent-error" class="run-error" style="display:none;"></div>' +
          '</aside>' +
        '</div>' +
      '</section>';

    // Bind
    var createBtn = document.getElementById('myagent-create-btn');
    var backBtn = document.getElementById('myagent-back-btn');
    var taskSelect = document.getElementById('myagent-task-select');
    var errorDiv = document.getElementById('myagent-error');

    if (backBtn) {
      backBtn.addEventListener('click', function () {
        _cleanup();
        // Reset to mode picker — hack into app.js state
        if (window.QTB._resetRunMode) window.QTB._resetRunMode();
      });
    }

    if (createBtn) {
      createBtn.addEventListener('click', function () {
        var task = taskSelect ? taskSelect.value : '';
        if (!task) return;
        createBtn.disabled = true;
        createBtn.textContent = 'Creating...';
        errorDiv.style.display = 'none';

        fetch('/ui/runs', {
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
          '<a class="btn btn-primary" id="myagent-results-link" href="#" style="display:none;">View Results</a>' +
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
        fetch('/ui/runs/' + _runId + '/cancel', {method: 'POST'})
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
      fetch('/ui/runs/' + _runId)
        .then(function (r) { return r.json(); })
        .then(function (data) {
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
                link.href = '#/results/' + data.session_id;
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
      fetch('/ui/runs/' + _runId + '/live')
        .then(function (r) { return r.json(); })
        .then(function (data) {
          _updateStatus(data.run_status);
          if (data.turn != null) {
            var turnPill = document.getElementById('myagent-turn-pill');
            if (turnPill) turnPill.textContent = 'Turn ' + data.turn;
          }
          _renderConversation(data.conversation || []);
          _renderToolLogs(data.recent_tool_logs || []);

          if (data.run_status === 'completed' || data.run_status === 'failed' || data.run_status === 'cancelled') {
            _stopPolling();
            // Show results link for completed runs
            if (data.run_status === 'completed' && data.session_id) {
              var link = document.getElementById('myagent-results-link');
              if (link) {
                link.href = '#/results/' + data.session_id;
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

  function _renderConversation(conversation) {
    var el = document.getElementById('myagent-conversation');
    if (!el || !conversation.length) return;

    // Use chat.js buildConversationReplay for full rendering
    if (window.QTB && typeof window.QTB.buildConversationReplay === 'function') {
      // Only re-render if conversation changed
      if (conversation.length !== _lastConvLen) {
        _lastConvLen = conversation.length;
        el.innerHTML = '';
        window.QTB.buildConversationReplay(el, conversation, [], []);
      }
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

  function _renderToolLogs(logs) {
    var el = document.getElementById('myagent-tools');
    if (!el || !logs.length) return;

    // Use tools.js buildToolReplay for rich rendering
    if (window.QTB && typeof window.QTB.buildToolReplay === 'function') {
      if (logs.length !== _lastToolLen) {
        _lastToolLen = logs.length;
        el.innerHTML = '';
        window.QTB.buildToolReplay(el, logs);
      }
    } else {
      // Fallback: simple list
      el.innerHTML = logs.map(function (log) {
        var icon = log.success !== false ? '✓' : '✗';
        var dur = log.duration_ms != null ? ' (' + Math.round(log.duration_ms) + 'ms)' : '';
        return '<div class="tool-event">' +
          '<span class="tool-icon">' + icon + '</span> ' +
          '<strong>' + escapeHtml(log.name) + '</strong>' + dur +
          '</div>';
      }).join('');
    }
  }

  function _cleanup() {
    _stopPolling();
    _runId = null;
    _lastConvLen = 0;
    _lastToolLen = 0;
  }

  // Allow app.js to reset mode
  window.QTB._resetRunMode = null; // set by app.js

})();
