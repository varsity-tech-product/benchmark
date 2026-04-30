/**
 * run-agent.js — "My Agent" flow for the Run page.
 *
 * 1. Generate a REST API key for the current reviewer.
 * 2. Render a copyable prompt with the REST skill URL, curl command, and key.
 * 3. Poll active runs when this module receives one from a server response.
 * 4. On completed → show link to Human Review.
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

  // ── Main render ──

  window.QTB.renderMyAgentPage = function (app, state, payload) {
    if (_runId) {
      _renderMonitorPage(app);
      return;
    }

    _renderPromptPage(app, {loading: true});
    _loadApiKeyStatus()
      .then(function (status) {
        _renderPromptPage(app, {status: status});
      })
      .catch(function (error) {
        _renderPromptPage(app, {
          error: error && error.message ? error.message : String(error || 'Unable to load API key status.')
        });
      });
  };

  function _absoluteUrl(path) {
    if (/^https?:\/\//i.test(path)) return path;
    return window.location.origin + path;
  }

  function _skillPageUrl() {
    return _absoluteUrl('/skills/quanttutorbench-rest-agent');
  }

  function _skillRawUrl() {
    return _absoluteUrl('/skill.md');
  }

  function _jsonResponse(response) {
    return response.json().then(function (payload) {
      if (!response.ok) {
        throw new Error(payload.error || ('HTTP ' + response.status));
      }
      return payload;
    });
  }

  function _loadApiKeyStatus() {
    return _authFetch('/ui/api-key').then(_jsonResponse);
  }

  function _generateApiKey() {
    return _authFetch('/ui/api-key', {method: 'POST'}).then(_jsonResponse)
      .then(function (payload) {
        if (!payload.api_key) {
          throw new Error('API key was not returned.');
        }
        return payload;
      });
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

  function _buildPromptPlaceholder(status, loading, error) {
    var rawSkillUrl = _skillRawUrl();
    if (loading) {
      return [
        'Loading API key status...',
        '',
        'Read ' + rawSkillUrl + ' and follow the instructions to join QuantTutorBench.'
      ].join('\n');
    }
    if (error) {
      return [
        'Unable to load API key status.',
        error,
        '',
        'Read ' + rawSkillUrl + ' and follow the instructions to join QuantTutorBench.'
      ].join('\n');
    }
    return [
      status && status.has_key
        ? 'Generate a fresh agent prompt to reveal a full REST API key in this text box.'
        : 'Generate an agent prompt to create a REST API key and place it in this text box.',
      '',
      'Read ' + rawSkillUrl + ' and follow the instructions to join QuantTutorBench.'
    ].join('\n');
  }

  function _apiKeyStatusText(status, apiKey, loading, error) {
    if (loading) return 'Loading key status';
    if (apiKey) return 'Fresh API key embedded';
    if (error) return 'Key status unavailable';
    if (status && status.has_key) {
      return 'Active key: ' + (status.key_hint ? status.key_hint + '...' : 'available');
    }
    return 'Ready to generate';
  }

  function _renderPromptPage(app, options) {
    options = options || {};
    var status = options.status || {};
    var apiKey = options.apiKey || '';
    var loading = !!options.loading;
    var error = options.error || '';
    var skillUrl = _skillPageUrl();
    var rawSkillUrl = _skillRawUrl();
    var prompt = apiKey
      ? _buildAgentPrompt(apiKey)
      : _buildPromptPlaceholder(status, loading, error);
    var copyDisabled = apiKey ? '' : ' disabled';
    var generateDisabled = loading ? ' disabled' : '';
    var generateText = status && status.has_key ? 'Generate Fresh Prompt' : 'Generate Prompt';

    app.innerHTML =
      '<section class="page run-page">' +
        '<header class="page-header run-sticky-header">' +
          '<div class="page-title-wrap">' +
            '<p class="eyebrow">Run · My Agent</p>' +
            '<h1>Connect your agent to the benchmark.</h1>' +
            '<p class="subtitle">Generate a copyable prompt with the REST skill URL and your API key.</p>' +
          '</div>' +
        '</header>' +
        '<div class="run-agent-prompt-panel">' +
          '<section class="panel run-agent-prompt-card">' +
            '<div class="run-agent-prompt-head">' +
              '<div>' +
                '<h2>Agent Prompt</h2>' +
                '<p class="detail-empty-note">Copy this text into your agent after generation. It includes the REST API key.</p>' +
              '</div>' +
              '<span class="run-selected-badge">' + escapeHtml(_apiKeyStatusText(status, apiKey, loading, error)) + '</span>' +
            '</div>' +
            '<textarea id="myagent-prompt-text" class="run-agent-prompt-text" readonly spellcheck="false">' + escapeHtml(prompt) + '</textarea>' +
            '<div class="run-agent-skill-url">' +
              '<span>Skill URL</span>' +
              '<code>' + escapeHtml(skillUrl) + '</code>' +
              '<span>Raw Skill URL</span>' +
              '<code>' + escapeHtml(rawSkillUrl) + '</code>' +
            '</div>' +
            '<div class="run-agent-api-actions">' +
              '<button class="btn btn-primary" id="myagent-generate-prompt-btn" type="button"' + generateDisabled + '>' + escapeHtml(generateText) + '</button>' +
              '<button class="btn btn-secondary" id="myagent-copy-prompt-btn" type="button"' + copyDisabled + '>Copy Prompt</button>' +
              '<a class="btn btn-secondary" href="' + escapeHtml(skillUrl) + '" target="_blank" rel="noreferrer">Open Skill</a>' +
            '</div>' +
            '<div id="myagent-error" class="run-error"' + (error ? '' : ' style="display:none;"') + '>' + escapeHtml(error) + '</div>' +
          '</section>' +
        '</div>' +
      '</section>';

    var generateBtn = document.getElementById('myagent-generate-prompt-btn');
    var copyBtn = document.getElementById('myagent-copy-prompt-btn');
    var promptText = document.getElementById('myagent-prompt-text');
    var errorDiv = document.getElementById('myagent-error');

    if (generateBtn) {
      generateBtn.addEventListener('click', function () {
        generateBtn.disabled = true;
        generateBtn.textContent = 'Generating...';
        if (errorDiv) errorDiv.style.display = 'none';
        _generateApiKey()
          .then(function (payload) {
            _renderPromptPage(app, {status: payload, apiKey: payload.api_key});
          })
          .catch(function (err) {
            if (errorDiv) {
              errorDiv.textContent = err && err.message ? err.message : String(err || 'Unable to generate prompt.');
              errorDiv.style.display = 'block';
            }
            generateBtn.disabled = false;
            generateBtn.textContent = generateText;
          });
      });
    }
    if (copyBtn) {
      copyBtn.addEventListener('click', function () {
        var value = promptText ? promptText.value : '';
        if (!value) return;
        if (navigator.clipboard) {
          navigator.clipboard.writeText(value).then(function () {
            copyBtn.textContent = 'Copied';
          }).catch(function () {
            _fallbackCopy(promptText);
            copyBtn.textContent = 'Copied';
          });
        } else {
          _fallbackCopy(promptText);
          copyBtn.textContent = 'Copied';
        }
      });
    }
  }

  function _fallbackCopy(textarea) {
    if (!textarea) return;
    textarea.focus();
    textarea.select();
    try {
      document.execCommand('copy');
    } catch (e) {
      // Browser copy fallback failed; selected text is still ready for manual copy.
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
