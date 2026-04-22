/**
 * app.js — SPA router, API calls, SSE connection, page logic.
 *
 * Hash-based routing: #/, #/run, #/results, #/results/:path, #/tasks, #/tasks/:category
 */

(function (window) {
  'use strict';

  // ── State ─────────────────────────────────────────────────

  var state = {
    sse: null,
    connected: false,
    running: false,
    startTime: null,
    msgCount: 0,
    toolCount: 0,
    timerInterval: null,
  };

  // Active eval tracking (state-based, mirroring _singleState pattern)
  var _evalState = {
    jobs: {},  // key → { status, error, scores, duration, req, startTime, steps: {}, timerInterval }
  };
  var _evalDetailKey = null;   // currently viewed eval detail key
  var _evalPollInterval = null; // polling interval for SSE-miss fallback

  // Single-run multi-task state
  var _singleState = {
    jobs: {},           // "task_id__persona_id" → { status, error, duration, req, startTime }
  };
  var _singleJobBuffers = {};  // key → [SSE events]
  var _singleDetailKey = null; // currently viewed single-task detail key
  var _singleElapsedTimer = null; // setInterval ID for live elapsed display

  // SSE dedup: track last processed _seq to avoid replaying already-seen events
  var _lastProcessedSeq = 0;

  function _startSingleElapsedTimer(key) {
    _stopSingleElapsedTimer();
    _singleElapsedTimer = setInterval(function () {
      var info = _singleState.jobs[key];
      if (!info || !info.startTime) return;
      var sec = Math.round((Date.now() - info.startTime) / 1000);
      var scope = _subPages['/run/single/' + key] || document;
      var el = scope.querySelector('#rsd-elapsed');
      if (el) {
        var m = Math.floor(sec / 60);
        el.textContent = (m > 0 ? m + 'm ' : '') + (sec % 60) + 's';
      }
    }, 1000);
  }

  function _stopSingleElapsedTimer() {
    if (_singleElapsedTimer) { clearInterval(_singleElapsedTimer); _singleElapsedTimer = null; }
  }


  // ── API helpers ───────────────────────────────────────────

  function api(path) {
    return fetch('/api' + path).then(function (r) {
      if (!r.ok) throw new Error('API error: ' + r.status);
      return r.json();
    });
  }

  function apiPost(path, body) {
    return fetch('/api' + path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }).then(function (r) {
      if (!r.ok) {
        return r.json().then(function (d) {
          throw new Error(d.detail || 'API error: ' + r.status);
        });
      }
      return r.json();
    });
  }

  // ── SSE connection (probe + exponential backoff + state recovery) ──

  var _sseRetryCount = 0;
  var _sseRetryTimer = null;
  var _SSE_BASE_DELAY = 1000;     // 1 s initial
  var _SSE_MAX_DELAY  = 5000;     // 5 s cap (fast reconnect)
  var _SSE_MAX_RETRIES = 60;      // ~5 min total before giving up

  // Heartbeat watchdog: detect stale SSE connections (e.g. server-side crash
  // that doesn't close the HTTP connection — onerror never fires)
  var _lastSSEMessageTime = 0;
  var _sseWatchdogTimer = null;
  var _SSE_WATCHDOG_INTERVAL = 5000;  // check every 5s
  var _SSE_STALE_THRESHOLD = 15000;   // 15s without any message → stale

  function _startSSEWatchdog() {
    _stopSSEWatchdog();
    _lastSSEMessageTime = Date.now();
    _sseWatchdogTimer = setInterval(function () {
      if (!state.sse) return;
      var elapsed = Date.now() - _lastSSEMessageTime;
      if (elapsed > _SSE_STALE_THRESHOLD) {
        console.warn('[SSE] Watchdog: no message for ' + Math.round(elapsed / 1000) + 's — force reconnecting');
        state.sse.close();
        state.sse = null;
        state.connected = false;
        _stopSSEWatchdog();
        // Pause eval polling to avoid race with SSE replay
        _stopEvalPolling();
        _scheduleSSERetry();
      }
    }, _SSE_WATCHDOG_INTERVAL);
  }

  function _stopSSEWatchdog() {
    if (_sseWatchdogTimer) { clearInterval(_sseWatchdogTimer); _sseWatchdogTimer = null; }
  }

  function connectSSE() {
    if (state.sse) state.sse.close();
    if (_sseRetryTimer) { clearTimeout(_sseRetryTimer); _sseRetryTimer = null; }
    _sseRetryCount = 0;
    _openEventSource(false);
  }

  function _openEventSource(replay) {
    var url = '/api/events' + (replay ? '?replay=1' : '');
    var es = new EventSource(url);
    state.sse = es;

    es.onopen = function () {
      _sseRetryCount = 0;
      state.connected = true;
      _startSSEWatchdog();
      if (_isSingleRunning() || _groupState.running) updateConnectionUI('connected');
      // Resume eval polling after delay — give SSE replay time to deliver missed events
      var hasActiveEvals = Object.keys(_evalState.jobs).some(function (k) {
        var s = _evalState.jobs[k].status;
        return s === 'running' || s === 'pending' || s === 'stopping';
      });
      if (hasActiveEvals && !_evalPollInterval) {
        setTimeout(function () { _startEvalPolling(); }, 5000);
      }
    };

    es.onmessage = function (e) {
      _lastSSEMessageTime = Date.now();
      var data;
      try { data = JSON.parse(e.data); } catch (_) { return; }
      if (data.type === 'heartbeat') return;  // watchdog reset only
      // Dedup: skip events we've already processed (from SSE replay on reconnect)
      // Detect server restart: seq jumps back (e.g. 119 → 1) — reset tracker
      if (data._seq != null) {
        if (data._seq < _lastProcessedSeq && data._seq <= 5) {
          // Server restarted — seq reset detected
          console.log('[SSE] Server restart detected (seq ' + _lastProcessedSeq + ' → ' + data._seq + '), resetting dedup');
          _lastProcessedSeq = 0;
        }
        if (data._seq <= _lastProcessedSeq) {
          console.log('%c[SSE] DEDUP skip ' + data.type + ' key=' + (data.job_key || '?') + ' seq=' + data._seq + ' (last=' + _lastProcessedSeq + ')', 'color:#95a5a6');
          return;
        }
        _lastProcessedSeq = data._seq;
      }
      // [DIAG] Enhanced SSE log with timestamp
      var _ts = new Date().toISOString().slice(11, 23);
      var _evalInfo = (data.mode === 'eval' || data.type === 'eval_step') ? ' eval=' + (data.task_id || '?') + '__' + (data.persona_id || '?') + (data.step ? ' step=' + data.step : '') : '';
      console.log('[SSE ' + _ts + '] ' + data.type + ' key=' + (data.job_key || '?') + ' seq=' + (data._seq || '') + (data.error ? ' error=' + data.error : '') + _evalInfo);
      handleSSEEvent(data);
    };

    es.onerror = function () {
      es.close();
      state.sse = null;
      state.connected = false;
      _stopSSEWatchdog();
      _stopEvalPolling(); // pause polling — will resume after reconnect
      if (_isSingleRunning()) {
        // Mark all running single jobs as disconnected
        var ss = _getSingleScope();
        var sb = ss.querySelector('#run-status-bar');
        if (sb) {
          sb.textContent = 'Server disconnected \u2014 sessions may have ended.';
          sb.style.color = 'var(--error)';
        }
      }
      _scheduleSSERetry();
    };
  }

  function _scheduleSSERetry() {
    _sseRetryCount++;
    if (_sseRetryCount > _SSE_MAX_RETRIES) {
      updateConnectionUI('disconnected');
      if (_isSingleRunning()) {
        var rscope = _getSingleScope();
        var sb = rscope.querySelector('#run-status-bar');
        if (sb) {
          sb.textContent = 'Server disconnected \u2014 sessions may have ended.';
          sb.style.color = 'var(--error)';
        }
      }
      return;
    }

    updateConnectionUI('reconnecting');
    var delay = Math.min(_SSE_BASE_DELAY * Math.pow(2, Math.min(_sseRetryCount - 1, 3)), _SSE_MAX_DELAY);
    delay += Math.floor(Math.random() * 500);
    _sseRetryTimer = setTimeout(function () {
      // Probe server health before creating EventSource
      fetch('/api/status').then(function (r) {
        if (r.ok) _openEventSource(false);   // server alive → reconnect WITHOUT replay (fresh start)
        else _scheduleSSERetry();            // server error → retry later
      }).catch(function () {
        _scheduleSSERetry();                 // server unreachable → retry later
      });
    }, delay);
  }

  function updateConnectionUI(status) {
    var nav = document.getElementById('nav-status');
    var dot = document.getElementById('connection-dot');
    var text = document.getElementById('connection-text');
    // Only show connection indicator while a run/group is active or just completed
    var visible = _isSingleRunning() || _groupState.running || status === 'completed';
    if (nav) nav.style.display = visible ? '' : 'none';
    if (dot) {
      dot.className = 'dot ' + (
        status === 'completed' ? 'dot-completed' :
        status === 'connected' ? 'dot-connected' :
        status === 'reconnecting' ? 'dot-reconnecting' :
        'dot-disconnected'
      );
    }
    if (text) text.textContent = status;
  }

  // ── Job event buffers (shared by group + single runs) ────
  // Keyed by "task_id__persona_id". Each buffer stores all conversation/tool
  // events for that job so the detail view can replay them on navigation.
  var _groupJobBuffers = {};
  // Currently viewed group detail key (null when on list view)
  var _groupDetailKey = null;

  function _bufferGroupEvent(jobKey, data) {
    if (!_groupJobBuffers[jobKey]) _groupJobBuffers[jobKey] = [];
    _groupJobBuffers[jobKey].push(data);
  }

  function handleSSEEvent(data) {
    // Route events with job_key to the correct buffer (group or single)
    if (data.job_key) {
      var jk = data.job_key;
      var t = data.type;
      // [DIAG] Log all keyed events with routing destination
      var _dest = _groupState.jobs[jk] ? 'GROUP' : _singleState.jobs[jk] ? 'SINGLE' : 'UNROUTED';
      if (t === 'session_start' || t === 'session_end') {
        console.log('%c[ROUTE] ' + t + ' key=' + jk + ' → ' + _dest + ' seq=' + data._seq + ' error=' + (data.error || 'none'), 'color:#e67e22;font-weight:bold');
      }
      // Agent progress: update thinking indicator with iteration info
      if (t === 'agent_progress') {
        var scope = null;
        if (_singleState.jobs[jk] && _singleDetailKey === jk) {
          scope = _subPages['/run/single/' + jk] || document;
        } else if (_groupState.jobs[jk] && _groupDetailKey === jk) {
          scope = _subPages['/run/group/' + jk] || document;
        }
        if (scope) {
          var chatEl = scope.querySelector('#rsd-chat') || scope.querySelector('#rgd-chat');
          if (chatEl) {
            var label = 'Thinking...';
            if (data.has_tool_use) label = 'Planning tool call...';
            else if (data.has_text) label = 'Composing response...';
            else if (data.thinking_len > 0) label = 'Thinking... (' + Math.round(data.thinking_len / 100) * 100 + ' chars)';
            if (data.iteration > 1) label += ' (step ' + data.iteration + ')';
            QTB.updateThinking(chatEl, label);
          }
        }
        return;
      }
      if (t === 'student_message' || t === 'tutor_response' || t === 'tool_start' || t === 'tool_result') {
        // Buffer conversation/tool events
        if (_groupState.jobs[jk]) {
          _bufferGroupEvent(jk, data);
          if (_groupDetailKey === jk) _renderGroupDetailEvent(data);
        } else if (_singleState.jobs[jk]) {
          _bufferSingleEvent(jk, data);
          if (_singleDetailKey === jk) _renderSingleDetailEvent(data);
        }
        return;
      }
      if (t === 'session_start') {
        if (_groupState.jobs[jk]) {
          _bufferGroupEvent(jk, data);
          if (_groupDetailKey === jk) _renderGroupDetailEvent(data);
        } else if (_singleState.jobs[jk]) {
          _bufferSingleEvent(jk, data);
          _onSingleTaskStart(data);
          if (_singleDetailKey === jk) _renderSingleDetailEvent(data);
        }
        return;
      }
      if (t === 'session_end') {
        if (data.mode === 'eval') { onEvalEnd(data); return; }
        if (_groupState.jobs[jk]) {
          _bufferGroupEvent(jk, data);
          if (_groupDetailKey === jk) _renderGroupDetailEvent(data);
        } else if (_singleState.jobs[jk]) {
          _bufferSingleEvent(jk, data);
          _onSingleTaskEnd(data);
          if (_singleDetailKey === jk) _renderSingleDetailEvent(data);
        }
        return;
      }
    }

    switch (data.type) {
      case 'session_start':
        if (data.mode === 'eval') onEvalStart(data);
        // Non-keyed session_start for single runs shouldn't happen anymore, ignore
        break;
      case 'student_message': break; // should have job_key
      case 'tutor_response':  break;
      case 'tool_start':      break;
      case 'tool_result':     break;
      case 'session_end':
        if (data.mode === 'eval') onEvalEnd(data);
        // Non-keyed session_end shouldn't happen anymore
        break;
      case 'eval_step':       onEvalStep(data); break;
      case 'group_start':     onGroupStart(data); break;
      case 'group_task_start': onGroupTaskStart(data); break;
      case 'group_task_end':  onGroupTaskEnd(data); break;
      case 'group_end':       onGroupEnd(data); break;
    }
  }

  // ── Single-run multi-task helpers ───────────────────────────

  function _bufferSingleEvent(jobKey, data) {
    if (!_singleJobBuffers[jobKey]) _singleJobBuffers[jobKey] = [];
    _singleJobBuffers[jobKey].push(data);
  }

  function _getSingleScope() {
    return _subPages['/run/single'] || document;
  }

  function _isSingleRunning() {
    return Object.keys(_singleState.jobs).some(function (k) {
      var s = _singleState.jobs[k] && _singleState.jobs[k].status;
      return s === 'running' || s === 'stopping';
    });
  }

  function _updateSingleToolbar() {
    var ss = _getSingleScope();
    var jobs = _singleState.jobs;
    var keys = Object.keys(jobs);
    var running = keys.filter(function (k) { return jobs[k].status === 'running' || jobs[k].status === 'stopping'; }).length;
    var failed = keys.filter(function (k) { return jobs[k].status === 'error' || jobs[k].status === 'cancelled'; }).length;
    var total = keys.length;
    var countEl = ss.querySelector('#rs-active-count');
    if (countEl) countEl.textContent = running + ' running / ' + total + ' total';
    var rerunBtn = ss.querySelector('#rs-rerun-selected');
    if (rerunBtn) rerunBtn.disabled = failed === 0;
    var stopBtn = ss.querySelector('#rs-stop-selected');
    if (stopBtn) stopBtn.disabled = running === 0;
    var counter = ss.querySelector('#rs-counter');
    if (counter) counter.textContent = total;
    // Update connection UI
    var visible = running > 0 || _groupState.running;
    var nav = document.getElementById('nav-status');
    if (nav) nav.style.display = visible ? '' : 'none';
  }

  function _addSingleJobRow(key, req) {
    var ss = _getSingleScope();
    var jobArea = ss.querySelector('#rs-job-area');
    if (!jobArea) return;
    // Remove empty state
    var empty = jobArea.querySelector('.empty-state');
    if (empty) empty.remove();
    // Create job list container if missing
    var list = jobArea.querySelector('#rs-job-list');
    if (!list) {
      list = document.createElement('div');
      list.className = 'rg-job-list';
      list.id = 'rs-job-list';
      jobArea.appendChild(list);
    }
    // If row already exists, reset its DOM to pending state (reuse pattern from _rerunSingleJob)
    var existingRow = list.querySelector('#rs-job-' + CSS.escape(key));
    // [DIAG]
    console.log('%c[ADD_ROW] key=' + key + ' existingRow=' + !!existingRow, 'color:#2ecc71;font-weight:bold');
    if (existingRow) {
      var st = existingRow.querySelector('.rg-job-status');
      if (st) { st.textContent = '\u25CB'; st.className = 'rg-job-status rg-pending'; }
      var detail = existingRow.querySelector('.rs-job-detail');
      if (detail) detail.textContent = '';
      var stopBtn = existingRow.querySelector('.rs-stop-btn');
      if (stopBtn) { stopBtn.disabled = true; stopBtn.textContent = '\u25A0'; }
      var rerunBtn = existingRow.querySelector('.rs-rerun-btn');
      if (rerunBtn) rerunBtn.disabled = true;
      return;
    }

    var row = document.createElement('div');
    row.className = 'rg-job rs-job';
    row.id = 'rs-job-' + key;
    row.setAttribute('data-key', key);
    row.innerHTML =
      '<input type="checkbox" class="rs-checkbox">' +
      '<span class="rg-job-status rg-pending">\u25CB</span>' +
      '<span class="rg-job-task rs-job-task">' + QTB.escapeHtml(req.task_id) + '</span>' +
      '<span class="rg-job-persona">' + QTB.escapeHtml(req.persona_id) + '</span>' +
      '<span class="rg-job-detail rs-job-detail"></span>' +
      '<div class="rs-job-actions">' +
        '<button class="btn btn-xs btn-secondary rs-rerun-btn" disabled title="Rerun">\u21BB</button>' +
        '<button class="btn btn-xs btn-danger rs-stop-btn" disabled title="Stop">\u25A0</button>' +
      '</div>';

    // Click task name → detail view
    row.querySelector('.rs-job-task').addEventListener('click', function (e) {
      e.stopPropagation();
      window.location.hash = '#/run/single/' + key;
    });

    // Per-row Stop button
    var stopBtn = row.querySelector('.rs-stop-btn');
    stopBtn.addEventListener('click', function (e) {
      e.stopPropagation();
      confirmDialog('Stop task ' + req.task_id + '?', 'Yes, stop', 'No, cancel').then(function (yes) {
        if (!yes) return;
        stopBtn.disabled = true;
        stopBtn.textContent = '...';
        // Update state to stopping
        if (_singleState.jobs[key]) _singleState.jobs[key].status = 'stopping';
        // Show stopping overlay + update badge on detail view if viewing this task
        if (_singleDetailKey === key) {
          var detailScope = _subPages['/run/single/' + key] || document;
          _showStoppingOverlay(detailScope);
          var badge = detailScope.querySelector('#rsd-status-badge');
          if (badge) { badge.textContent = 'stopping'; badge.className = 'rgd-status-badge rgd-stopping'; }
        }
        // Update row visual to stopping state
        var stEl = row.querySelector('.rg-job-status');
        if (stEl) { stEl.textContent = '\u29D7'; stEl.className = 'rg-job-status rg-stopping'; }
        var detailEl = row.querySelector('.rs-job-detail');
        if (detailEl) detailEl.textContent = 'Stopping\u2026';
        _updateRunButtonState(_getSingleScope());
        apiPost('/run/stop', { job_key: key }).catch(function () {
          stopBtn.disabled = false;
          stopBtn.textContent = '\u25A0';
          if (_singleState.jobs[key]) _singleState.jobs[key].status = 'running';
          if (stEl) { stEl.textContent = '\u25CF'; stEl.className = 'rg-job-status rg-running'; }
          if (detailEl) detailEl.textContent = '';
          if (_singleDetailKey === key) {
            var ds = _subPages['/run/single/' + key] || document;
            _removeStoppingOverlay(ds);
          }
          _updateRunButtonState(_getSingleScope());
        });
      });
    });

    // Per-row Rerun button
    var rerunBtn = row.querySelector('.rs-rerun-btn');
    rerunBtn.addEventListener('click', function (e) {
      e.stopPropagation();
      _rerunSingleJob(key);
    });

    // Checkbox change → update select-all state
    row.querySelector('.rs-checkbox').addEventListener('change', function () {
      _syncSingleSelectAll();
    });

    list.appendChild(row);
  }

  function _syncSingleSelectAll() {
    var ss = _getSingleScope();
    var all = Array.from(ss.querySelectorAll('.rs-checkbox'));
    var selectAll = ss.querySelector('#rs-select-all');
    if (!selectAll || all.length === 0) return;
    var allChecked = all.every(function (cb) { return cb.checked; });
    selectAll.checked = allChecked;
    selectAll.indeterminate = !allChecked && all.some(function (cb) { return cb.checked; });
  }

  function _onSingleTaskStart(data) {
    var key = data.job_key || (data.task_id + '__' + data.persona_id);
    var _prevStatus = _singleState.jobs[key] ? _singleState.jobs[key].status : 'NO_ENTRY';
    // [DIAG]
    console.log('%c[TASK_START] key=' + key + ' prevStatus=' + _prevStatus + ' seq=' + data._seq, 'color:#3498db;font-weight:bold');
    if (_singleState.jobs[key]) {
      _singleState.jobs[key].status = 'running';
      _singleState.jobs[key].startTime = Date.now();
    }
    var ss = _getSingleScope();
    var row = ss.querySelector('#rs-job-' + CSS.escape(key));
    if (row) {
      var st = row.querySelector('.rg-job-status');
      if (st) { st.textContent = '\u25CF'; st.className = 'rg-job-status rg-running'; }
      var stopBtn = row.querySelector('.rs-stop-btn');
      if (stopBtn) { stopBtn.disabled = false; }
      var rerunBtn = row.querySelector('.rs-rerun-btn');
      if (rerunBtn) { rerunBtn.disabled = true; }
    }
    // Update detail view badge + start elapsed timer if viewing this job
    if (_singleDetailKey === key) {
      var scope = _subPages['/run/single/' + key] || document;
      var badge = scope.querySelector('#rsd-status-badge');
      if (badge) { badge.textContent = 'running'; badge.className = 'rgd-status-badge rgd-running'; }
      _startSingleElapsedTimer(key);
    }
    updateConnectionUI('connected');
    _updateSingleToolbar();
  }

  function _onSingleTaskEnd(data) {
    var key = data.job_key || (data.task_id + '__' + data.persona_id);
    var jobInfo = _singleState.jobs[key];
    // [DIAG] Full state dump on session_end
    var _diag = {
      key: key,
      jobStatus: jobInfo ? jobInfo.status : 'NO_ENTRY',
      jobStartTime: jobInfo ? jobInfo.startTime : null,
      error: data.error,
      seq: data._seq,
      task_id: data.task_id,
      persona_id: data.persona_id,
      elapsed: jobInfo && jobInfo.startTime ? Math.round((Date.now() - jobInfo.startTime) / 1000) + 's' : 'N/A',
      bufferLen: (_singleJobBuffers[key] || []).length,
      allJobs: Object.keys(_singleState.jobs).map(function (k) { return k + ':' + _singleState.jobs[k].status; })
    };
    console.log('%c[TASK_END] ' + JSON.stringify(_diag, null, 2), 'color:#e74c3c;font-weight:bold');
    // Guard: ignore stale session_end from a previous run that arrives after a new run was submitted
    if (jobInfo && jobInfo.status === 'pending') {
      console.log('%c[TASK_END] BLOCKED by pending guard', 'color:#e74c3c;font-weight:bold');
      return;
    }
    var error = data.error;
    var duration = null;
    if (jobInfo) {
      jobInfo.status = error ? (error === 'cancelled' ? 'cancelled' : 'error') : 'done';
      jobInfo.error = error;
      if (jobInfo.startTime) duration = (Date.now() - jobInfo.startTime) / 1000;
      jobInfo.duration = duration;
      jobInfo.endData = data; // save for result navigation
    }

    var ss = _getSingleScope();
    var row = ss.querySelector('#rs-job-' + CSS.escape(key));
    if (row) {
      var st = row.querySelector('.rg-job-status');
      var detail = row.querySelector('.rs-job-detail');
      var stopBtn = row.querySelector('.rs-stop-btn');
      var rerunBtn = row.querySelector('.rs-rerun-btn');
      if (stopBtn) stopBtn.disabled = true;

      if (error === 'cancelled') {
        if (st) { st.textContent = '\u2717'; st.className = 'rg-job-status rg-error'; }
        if (detail) detail.textContent = 'Cancelled';
        if (rerunBtn) rerunBtn.disabled = false;
      } else if (error) {
        if (st) { st.textContent = '\u2717'; st.className = 'rg-job-status rg-error'; }
        if (detail) detail.textContent = 'Error: ' + error.slice(0, 60);
        if (rerunBtn) rerunBtn.disabled = false;
      } else {
        if (st) { st.textContent = '\u2713'; st.className = 'rg-job-status rg-done'; }
        var detailText = '';
        if (duration) detailText += Math.round(duration) + 's';
        if (detail) detail.textContent = detailText;
        if (rerunBtn) rerunBtn.disabled = true; // successful, no rerun needed
      }
    }

    // Update detail view badge if viewing this job
    if (_singleDetailKey === key) {
      var scope = _subPages['/run/single/' + key] || document;
      var badge = scope.querySelector('#rsd-status-badge');
      if (badge) {
        if (error) {
          badge.textContent = error === 'cancelled' ? 'cancelled' : 'error';
          badge.className = 'rgd-status-badge rgd-error';
        } else {
          badge.textContent = 'done';
          badge.className = 'rgd-status-badge rgd-done';
        }
      }
      var elapsedEl = scope.querySelector('#rsd-elapsed');
      if (elapsedEl && duration) {
        var ds = Math.round(duration);
        var dm = Math.floor(ds / 60);
        elapsedEl.textContent = (dm > 0 ? dm + 'm ' : '') + (ds % 60) + 's';
      }
      // Clean up thinking indicators
      var chatEl = scope.querySelector('#rsd-chat');
      if (chatEl) { QTB.hideThinking(chatEl); QTB.hideResponding(chatEl); }
      // Remove stopping overlay
      _removeStoppingOverlay(scope);
      // Stop elapsed timer
      _stopSingleElapsedTimer();
    }

    _updateSingleToolbar();

    // If no more running jobs, update connection UI
    if (!_isSingleRunning()) {
      updateConnectionUI('completed');
    }

    // Show cancelled modal with auto-close countdown (ported from onGroupEnd)
    if (error === 'cancelled') {
      var countdown = 5;
      var cancelHtml =
        '<div style="text-align:center;padding:20px 0">' +
          '<div style="font-size:40px;margin-bottom:12px">\u25A0</div>' +
          '<p style="font-size:16px;font-weight:600;margin-bottom:8px">Task Cancelled</p>' +
          '<p style="color:var(--text-muted);font-size:13px">' +
            QTB.escapeHtml(data.task_id || key) + '</p>' +
          '<p style="color:var(--text-muted);font-size:12px;margin-top:16px">' +
            'Auto-closing in <span id="modal-countdown-cancel">' + countdown + '</span>s</p>' +
        '</div>';
      showModal('Task Cancelled', cancelHtml);
      var _cancelTimer = setInterval(function () {
        countdown--;
        var cdEl = document.getElementById('modal-countdown-cancel');
        if (cdEl) cdEl.textContent = countdown;
        if (countdown <= 0) { clearInterval(_cancelTimer); closeModal(); }
      }, 1000);
    }
  }

  function _rerunSingleJob(key) {
    var jobInfo = _singleState.jobs[key];
    if (!jobInfo || !jobInfo.req) return;
    // [DIAG]
    console.log('%c[RERUN] key=' + key + ' prevStatus=' + jobInfo.status + ' lastProcessedSeq=' + _lastProcessedSeq, 'color:#9b59b6;font-weight:bold');
    // Clear old buffer
    _singleJobBuffers[key] = [];
    // Reset state
    jobInfo.status = 'pending';
    jobInfo.error = null;
    jobInfo.duration = null;
    jobInfo.startTime = null;
    jobInfo.endData = null;

    // Reset row UI
    var ss = _getSingleScope();
    var row = ss.querySelector('#rs-job-' + CSS.escape(key));
    if (row) {
      var st = row.querySelector('.rg-job-status');
      if (st) { st.textContent = '\u25CB'; st.className = 'rg-job-status rg-pending'; }
      var detail = row.querySelector('.rs-job-detail');
      if (detail) detail.textContent = '';
      var stopBtn = row.querySelector('.rs-stop-btn');
      if (stopBtn) { stopBtn.disabled = true; stopBtn.textContent = '\u25A0'; }
      var rerunBtn = row.querySelector('.rs-rerun-btn');
      if (rerunBtn) rerunBtn.disabled = true;
    }

    // Invalidate detail sub-page so it re-renders fresh
    var detailRoute = '/run/single/' + key;
    _staleRoutes[detailRoute] = true;

    // Re-submit
    apiPost('/run', jobInfo.req).then(function () {
      _updateSingleToolbar();
    }).catch(function (err) {
      jobInfo.status = 'error';
      jobInfo.error = err.message;
      if (row) {
        var st2 = row.querySelector('.rg-job-status');
        if (st2) { st2.textContent = '\u2717'; st2.className = 'rg-job-status rg-error'; }
        var detail2 = row.querySelector('.rs-job-detail');
        if (detail2) detail2.textContent = 'Error: ' + err.message.slice(0, 60);
        var rerunBtn2 = row.querySelector('.rs-rerun-btn');
        if (rerunBtn2) rerunBtn2.disabled = false;
      }
      _updateSingleToolbar();
    });
  }

  // ── Single-task detail view ───────────────────────────────

  function showRunSingleDetail(app, jobKey) {
    renderTemplate(app, 'tpl-run-single-detail');
    injectPanelStructure(app);
    _singleDetailKey = jobKey;

    var parts = jobKey.split('__');
    var taskId = parts[0] || jobKey;
    var personaId = parts[1] || '';

    var taskEl = app.querySelector('#rsd-task-id');
    if (taskEl) taskEl.textContent = taskId;
    var personaEl = app.querySelector('#rsd-persona-id');
    if (personaEl) personaEl.textContent = personaId;

    var jobInfo = _singleState.jobs[jobKey] || {};
    var badge = app.querySelector('#rsd-status-badge');
    if (badge) {
      var st = jobInfo.status || 'pending';
      badge.textContent = st;
      badge.className = 'rgd-status-badge rgd-' + (st === 'done' ? 'done' : st === 'error' || st === 'cancelled' ? 'error' : st === 'running' ? 'running' : st === 'stopping' ? 'stopping' : 'pending');
    }

    var elapsedEl = app.querySelector('#rsd-elapsed');
    if (elapsedEl && jobInfo.duration) {
      var s = Math.round(jobInfo.duration);
      var m = Math.floor(s / 60);
      elapsedEl.textContent = (m > 0 ? m + 'm ' : '') + (s % 60) + 's';
    }

    // Replay buffered events
    var chatEl = app.querySelector('#rsd-chat');
    var toolsEl = app.querySelector('#rsd-tools');
    if (chatEl) QTB.clearChat(chatEl);
    if (toolsEl) QTB.clearTools(toolsEl);

    var buffer = _singleJobBuffers[jobKey] || [];
    var msgCount = 0, toolCount = 0;

    buffer.forEach(function (evt) {
      switch (evt.type) {
        case 'student_message':
          msgCount++;
          if (chatEl) {
            QTB.hideResponding(chatEl);
            QTB.addChatMessage(chatEl, 'student', evt.content || '', null, null);
            QTB.showThinking(chatEl, 'Thinking...');
          }
          break;
        case 'tutor_response':
          msgCount++;
          if (chatEl) {
            QTB.hideThinking(chatEl);
            QTB.hideResponding(chatEl);
            QTB.addChatMessage(chatEl, 'tutor', evt.content || '', evt.content_blocks || null, null);
          }
          break;
        case 'tool_start':
          toolCount++;
          if (toolsEl) QTB.addToolStart(toolsEl, evt);
          if (chatEl) QTB.updateThinking(chatEl, 'Using ' + (evt.name || 'tool') + '...');
          break;
        case 'tool_result':
          if (toolsEl) QTB.updateToolResult(toolsEl, evt);
          break;
      }
    });

    // Show appropriate indicator
    if (jobInfo.status === 'running' && msgCount > 0 && chatEl) {
      var lastEvt = buffer[buffer.length - 1];
      if (lastEvt) {
        if (lastEvt.type === 'student_message') QTB.showThinking(chatEl, 'Thinking...');
        else if (lastEvt.type === 'tool_start') QTB.showThinking(chatEl, 'Using ' + (lastEvt.name || 'tool') + '...');
        else if (lastEvt.type === 'tool_result') QTB.showThinking(chatEl, 'Thinking...');
        else if (lastEvt.type === 'tutor_response') QTB.showResponding(chatEl);
      }
    } else if (chatEl) {
      QTB.hideThinking(chatEl);
      QTB.hideResponding(chatEl);
    }

    // Update counters
    var msgCountEl = app.querySelector('#rsd-msg-count');
    var toolCountEl = app.querySelector('#rsd-tool-total');
    var turnCountEl = app.querySelector('#rsd-turn-count');
    if (msgCountEl) msgCountEl.textContent = 'Messages: ' + msgCount;
    if (toolCountEl) toolCountEl.textContent = 'Tools: ' + toolCount;
    if (turnCountEl) turnCountEl.textContent = Math.ceil(msgCount / 2);

    if (toolCount > 0) {
      var toolPanel = app.querySelector('.run-tool-panel');
      if (toolPanel) toolPanel.classList.remove('collapsed');
    }

    // Start live elapsed timer if task is running
    if (jobInfo.status === 'running' && jobInfo.startTime) {
      _startSingleElapsedTimer(jobKey);
    }
  }

  function _renderSingleDetailEvent(data) {
    var scope = _subPages['/run/single/' + _singleDetailKey] || document;
    var chatEl = scope.querySelector('#rsd-chat');
    var toolsEl = scope.querySelector('#rsd-tools');
    var msgCountEl = scope.querySelector('#rsd-msg-count');
    var toolCountEl = scope.querySelector('#rsd-tool-total');
    var turnCountEl = scope.querySelector('#rsd-turn-count');

    switch (data.type) {
      case 'student_message':
        if (chatEl) {
          QTB.hideResponding(chatEl);
          QTB.addChatMessage(chatEl, 'student', data.content || '', null, null);
          QTB.showThinking(chatEl, 'Thinking...');
        }
        break;
      case 'tutor_response':
        if (chatEl) {
          QTB.hideThinking(chatEl);
          QTB.hideResponding(chatEl);
          QTB.addChatMessage(chatEl, 'tutor', data.content || '', data.content_blocks || null, null);
          rewriteLiveImages(chatEl);
          QTB.showResponding(chatEl);
        }
        break;
      case 'tool_start':
        if (toolsEl) {
          QTB.addToolStart(toolsEl, data);
          var toolPanel = scope.querySelector('.run-tool-panel');
          if (toolPanel) {
            toolPanel.classList.remove('collapsed');
            var reopenTab = scope.querySelector('#reopen-tools');
            if (reopenTab) {
              var arrow = reopenTab.querySelector('.tab-arrow');
              if (arrow) arrow.textContent = '\u25B6';
            }
          }
        }
        if (chatEl) QTB.updateThinking(chatEl, 'Using ' + (data.name || 'tool') + '...');
        break;
      case 'tool_result':
        if (toolsEl) QTB.updateToolResult(toolsEl, data);
        break;
      case 'session_end':
        if (chatEl) { QTB.hideThinking(chatEl); QTB.hideResponding(chatEl); }
        var badge = scope.querySelector('#rsd-status-badge');
        if (badge) {
          if (data.error) {
            badge.textContent = data.error === 'cancelled' ? 'cancelled' : 'error';
            badge.className = 'rgd-status-badge rgd-error';
          } else {
            badge.textContent = 'done';
            badge.className = 'rgd-status-badge rgd-done';
          }
        }
        break;
    }

    // Update counters from buffer
    if (_singleDetailKey) {
      var buf = _singleJobBuffers[_singleDetailKey] || [];
      var mc = 0, tc = 0;
      buf.forEach(function (e) {
        if (e.type === 'student_message' || e.type === 'tutor_response') mc++;
        if (e.type === 'tool_start') tc++;
      });
      if (msgCountEl) msgCountEl.textContent = 'Messages: ' + mc;
      if (toolCountEl) toolCountEl.textContent = 'Tools: ' + tc;
      if (turnCountEl) turnCountEl.textContent = Math.ceil(mc / 2);
    }
  }

  function _showStoppingOverlay(scope) {
    var page = scope.querySelector('.page-run') || scope.querySelector('.page-run-group') || scope.querySelector('.page-run-group-detail') || scope.querySelector('.page-eval-detail') || scope.querySelector('.page') || scope;
    if (page.querySelector('.stopping-overlay')) return;
    // Inject keyframes once
    if (!document.getElementById('stopping-kf')) {
      var st = document.createElement('style');
      st.id = 'stopping-kf';
      st.textContent =
        '@keyframes _stop-spin{to{transform:rotate(360deg)}}' +
        '@keyframes _stop-fade{from{opacity:0}to{opacity:1}}' +
        '@keyframes _stop-up{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:translateY(0)}}' +
        '@keyframes _stop-pulse{0%,100%{opacity:.45}50%{opacity:1}}';
      document.head.appendChild(st);
    }
    page.style.position = 'relative';
    var overlay = document.createElement('div');
    overlay.className = 'stopping-overlay';
    overlay.style.cssText =
      'position:absolute;top:0;left:0;right:0;bottom:0;z-index:100;' +
      'background:rgba(245,241,235,0.70);backdrop-filter:blur(6px);-webkit-backdrop-filter:blur(6px);' +
      'display:flex;align-items:center;justify-content:center;' +
      'animation:_stop-fade .2s ease;';
    // Card
    var card = document.createElement('div');
    card.style.cssText =
      'background:#faf8f5;border:1px solid #e0d8ce;border-radius:16px;' +
      'box-shadow:0 4px 24px rgba(0,0,0,0.08),0 1px 3px rgba(0,0,0,0.04);' +
      'padding:40px 48px;text-align:center;max-width:360px;width:100%;' +
      'animation:_stop-up .25s ease;';
    // Spinner
    card.innerHTML =
      '<div style="display:inline-block;width:44px;height:44px;margin-bottom:20px;' +
        'border:3px solid #e0d8ce;border-top-color:#d97706;border-radius:50%;' +
        'animation:_stop-spin .8s linear infinite"></div>' +
      '<div style="font-family:Playfair Display,Georgia,serif;font-size:20px;font-weight:700;' +
        'color:#2d2b28;margin-bottom:8px">Stopping Run</div>' +
      '<div style="font-family:Inter,system-ui,sans-serif;font-size:13px;color:#9c958d;' +
        'line-height:1.6;animation:_stop-pulse 2s ease-in-out infinite">' +
        'Waiting for the current operation to finish\u2026</div>';
    overlay.appendChild(card);
    page.appendChild(overlay);
  }

  function _removeStoppingOverlay(scope) {
    var page = scope.querySelector('.page-run') || scope.querySelector('.page-run-group') || scope.querySelector('.page-run-group-detail') || scope.querySelector('.page-eval-detail') || scope.querySelector('.page') || scope;
    var el = page.querySelector('.stopping-overlay');
    if (el) el.remove();
  }

  // ── SSE event handlers (Eval) ──────────────────────────────

  var EVAL_STEPS = ['quant_result', 'code_eval', 'tool_usage', 'result_judge', 'process_metrics', 'tutor_7d', 'qr_blend'];
  var EVAL_STEP_LABELS = {
    quant_result: 'Programmatic QR',
    code_eval: 'Code Evaluation',
    tool_usage: 'Tool Usage',
    result_judge: 'Result Judge',
    process_metrics: 'Process Metrics',
    tutor_7d: 'Tutor 7D',
    qr_blend: 'QR Blending',
  };

  function evalKey(data) { return data.task_id + '__' + data.persona_id; }

  function onEvalStart(data) {
    var key = evalKey(data);
    console.log('%c[EVAL_START] key=' + key + ' hasJob=' + !!_evalState.jobs[key] + ' seq=' + (data._seq || '?'), 'color:#2ecc71;font-weight:bold');
    var job = _evalState.jobs[key];
    if (job) {
      job.status = 'running';
      job.startTime = Date.now();
    } else {
      console.warn('[EVAL_START] No job entry for key=' + key + ' — event arrived before triggerEval completed?');
    }
    _updateEvalJobRow(key);
    _updateEvalDetailView(key);
    _updateEvalToolbar();
  }

  function onEvalStep(data) {
    var key = evalKey(data);
    var job = _evalState.jobs[key];
    console.log('%c[EVAL_STEP] key=' + key + ' step=' + data.step + ' status=' + data.status + ' jobStatus=' + (job ? job.status : 'NO_JOB') + ' seq=' + (data._seq || '?'), 'color:#3498db');
    if (!job) {
      console.warn('[EVAL_STEP] No job entry for key=' + key + ' — ignoring step event');
      return;
    }
    if (job.status === 'done' || job.status === 'error' || job.status === 'cancelled') {
      console.warn('[EVAL_STEP] Job already finished (status=' + job.status + ') but received step=' + data.step + ' — late event, ignoring');
      return;
    }
    // Store step state
    if (!job.steps) job.steps = {};
    job.steps[data.step] = { status: data.status, score: data.score };
    // Update detail view if viewing this eval
    _updateEvalDetailView(key);
  }

  function onEvalEnd(data) {
    var key = evalKey(data);
    var job = _evalState.jobs[key];
    var _stepsCompleted = job ? Object.keys(job.steps || {}).filter(function (s) { return (job.steps[s] || {}).status === 'done'; }).length : 0;
    console.log('%c[EVAL_END] key=' + key + ' error=' + (data.error || 'none') + ' hasScores=' + !!(data.scores) + ' jobStatus=' + (job ? job.status : 'NO_JOB') + ' stepsCompleted=' + _stepsCompleted + '/7 seq=' + (data._seq || '?'), 'color:#e74c3c;font-weight:bold');
    if (!job) {
      console.warn('[EVAL_END] No job entry for key=' + key + ' — ignoring');
      return;
    }
    // Guard: if job is already in a terminal state, ignore duplicate end events
    if (job.status === 'done' || job.status === 'error' || job.status === 'cancelled') {
      console.warn('[EVAL_END] Job already finished (status=' + job.status + ') — ignoring duplicate end event');
      return;
    }
    if (job.timerInterval) { clearInterval(job.timerInterval); job.timerInterval = null; }
    var error = data.error;
    var duration = null;
    if (job.startTime) duration = (Date.now() - job.startTime) / 1000;
    job.duration = duration;
    job.error = error;
    job.scores = data.scores || null;
    if (error) {
      job.status = error === 'cancelled' ? 'cancelled' : 'error';
      // Reset any running steps to pending (stop pulse animations)
      EVAL_STEPS.forEach(function (s) {
        if (job.steps[s] && job.steps[s].status === 'running') {
          job.steps[s] = { status: 'pending', score: null };
        }
      });
    } else {
      job.status = 'done';
      // Force-complete any steps still running/pending (SSE events may have been lost)
      EVAL_STEPS.forEach(function (s) {
        if (!job.steps[s] || job.steps[s].status !== 'done') {
          job.steps[s] = { status: 'done', score: (job.steps[s] || {}).score || null };
        }
      });
    }

    // Stop polling if no more active evals
    var stillRunning = Object.keys(_evalState.jobs).some(function (k) {
      var s = _evalState.jobs[k].status;
      return s === 'running' || s === 'pending' || s === 'stopping';
    });
    if (!stillRunning) _stopEvalPolling();

    // Update list row + detail view
    _updateEvalJobRow(key);
    _updateEvalDetailView(key);
    _updateEvalToolbar();

    // Invalidate results pages so scores refresh on next visit.
    // Do NOT invalidate /evaluate or /evaluate/results — their DOM (tabs,
    // filter selections) should be preserved across module switches.
    if (data.source && data.agent && data.model && data.category) {
      var resRoute = '/results/s/' + encodeURIComponent(data.source) + '/a/' + encodeURIComponent(data.agent) +
        '/m/' + encodeURIComponent(data.model) + '/c/' + encodeURIComponent(data.category);
      invalidateSubPage(resRoute);
    }

    // Show cancelled modal with auto-close (same style as run-single)
    if (error === 'cancelled') {
      var countdown = 5;
      var cancelHtml =
        '<div style="text-align:center;padding:20px 0">' +
          '<div style="font-size:40px;margin-bottom:12px">\u274C</div>' +
          '<p style="font-size:16px;font-weight:600;margin-bottom:8px">Evaluation Cancelled</p>' +
          '<p style="color:var(--text-muted);font-size:13px">' +
            QTB.escapeHtml(data.task_id || key) + '</p>' +
          '<p style="color:var(--text-muted);font-size:12px;margin-top:16px">' +
            'Auto-closing in <span id="modal-countdown-eval-cancel">' + countdown + '</span>s</p>' +
        '</div>';
      showModal('Evaluation Cancelled', cancelHtml);
      var _cancelTimer = setInterval(function () {
        countdown--;
        var cdEl = document.getElementById('modal-countdown-eval-cancel');
        if (cdEl) cdEl.textContent = countdown;
        if (countdown <= 0) { clearInterval(_cancelTimer); closeModal(); }
      }, 1000);
    }

    // Show completion modal only if success
    if (!error) {
      var scoresHtml = '';
      if (data.scores) {
        var mOas = data.scores.oas != null ? data.scores.oas.toFixed(4) : '-';
        var mQr = data.scores.qr != null ? data.scores.qr.toFixed(4) : '-';
        var mQp = data.scores.qp != null ? data.scores.qp.toFixed(4) : '-';
        scoresHtml = '<div class="eval-modal-scores">' +
          '<div class="eval-modal-score"><span class="ems-label">Overall</span><span class="ems-value">' + mOas + '</span></div>' +
          '<div class="eval-modal-score"><span class="ems-label">QR</span><span class="ems-value">' + mQr + '</span></div>' +
          '<div class="eval-modal-score"><span class="ems-label">QP</span><span class="ems-value">' + mQp + '</span></div>' +
        '</div>';
      }
      var resultHash = '';
      if (data.source && data.agent && data.model && data.category) {
        resultHash = '#/results/' + [data.source, data.agent, data.model, data.category, data.task_id, data.persona_id].map(encodeURIComponent).join('/');
      }
      var modalBody = '<p>Evaluation complete for <strong>' + QTB.escapeHtml(data.task_id) + '</strong></p>' +
        scoresHtml +
        (resultHash ? '<div style="margin-top:16px;text-align:center"><a href="' + resultHash + '" class="btn btn-primary" onclick="(function(){var m=document.getElementById(\'qtb-modal\');if(m)m.remove();})()" style="text-decoration:none">View Result</a></div>' : '');
      showModal('Evaluation Complete', modalBody);
    }

    // Proactive SSE resync
    api('/status').then(function (s) {
      if ((s.running || s.active_runs > 0) && !_isSingleRunning()) {
        if (state.sse) { state.sse.close(); state.sse = null; }
        _stopSSEWatchdog();
        _sseRetryCount = 0;
        _openEventSource(false);
      }
    }).catch(function () {});
  }

  // ── Eval detail view helpers ──────────────────────────────

  /** Render / update eval step indicators in the detail view. */
  function _updateEvalDetailView(key) {
    if (_evalDetailKey !== key) return;
    var scope = _subPages['/evaluate/active/' + key];
    if (!scope) return;
    var job = _evalState.jobs[key];
    if (!job) return;

    // Update status badge
    var badge = scope.querySelector('#ed-status-badge');
    if (badge) {
      var st = job.status;
      var label = st === 'cancelled' ? 'cancelled' : st;
      var cls = 'rgd-status-badge ';
      if (st === 'running') cls += 'rgd-running';
      else if (st === 'stopping') cls += 'rgd-stopping';
      else if (st === 'done') cls += 'rgd-done';
      else if (st === 'error' || st === 'cancelled') cls += 'rgd-error';
      else cls += 'rgd-pending';
      badge.textContent = label;
      badge.className = cls;
    }

    // Update elapsed
    var elapsedEl = scope.querySelector('#ed-elapsed');
    if (elapsedEl && job.startTime) {
      var sec = job.duration ? Math.round(job.duration) : Math.round((Date.now() - job.startTime) / 1000);
      var mm = Math.floor(sec / 60);
      elapsedEl.textContent = (mm > 0 ? mm + 'm ' : '') + (sec % 60) + 's';
    }

    // Update step indicators
    var stepsEl = scope.querySelector('#ed-steps');
    if (stepsEl) {
      EVAL_STEPS.forEach(function (s) {
        var stepEl = stepsEl.querySelector('[data-step="' + s + '"]');
        if (!stepEl) return;
        var stepData = (job.steps || {})[s];
        if (!stepData) return;
        var indicator = stepEl.querySelector('.step-indicator');
        var scoreEl = stepEl.querySelector('.step-score');
        if (stepData.status === 'running') {
          stepEl.className = 'eval-step running';
          if (indicator) indicator.textContent = '\u25CF';
        } else if (stepData.status === 'done') {
          stepEl.className = 'eval-step done';
          if (indicator) indicator.textContent = '\u2713';
          if (scoreEl && stepData.score != null) scoreEl.textContent = stepData.score.toFixed(4);
        }
      });
    }

    // Update final scores
    var finalEl = scope.querySelector('#ed-final');
    if (finalEl) {
      if (job.status === 'done' && job.scores) {
        var oas = job.scores.oas != null ? job.scores.oas.toFixed(4) : '-';
        var qr = job.scores.qr != null ? job.scores.qr.toFixed(4) : '-';
        var qp = job.scores.qp != null ? job.scores.qp.toFixed(4) : '-';
        finalEl.innerHTML = '<span class="eval-oas">OAS: ' + oas + '</span>' +
          '<span class="eval-sub">QR: ' + qr + '</span>' +
          '<span class="eval-sub">QP: ' + qp + '</span>';
      } else if (job.status === 'cancelled') {
        finalEl.innerHTML = '<span class="eval-cancelled-text">\u274C Cancelled</span>';
      } else if (job.status === 'error') {
        finalEl.innerHTML = '<span class="eval-error-text">Error: ' + QTB.escapeHtml((job.error || '').slice(0, 120)) + '</span>';
      }
    }

    // Remove stopping overlay on completion
    if (job.status === 'done' || job.status === 'error' || job.status === 'cancelled') {
      _removeStoppingOverlay(scope);
    }
  }

  /** Promise-based confirm dialog. Resolves true (yes) or false (no). */
  function confirmDialog(msg, yesText, noText) {
    return new Promise(function (resolve) {
      var overlay = document.createElement('div');
      overlay.className = 'modal-overlay confirm-overlay';
      overlay.innerHTML =
        '<div class="modal-content confirm-modal">' +
          '<div class="modal-body">' +
            '<p class="confirm-msg">' + QTB.escapeHtml(msg) + '</p>' +
            '<div class="confirm-btns">' +
              '<button class="btn btn-danger confirm-yes">' + QTB.escapeHtml(yesText || 'Yes, stop') + '</button>' +
              '<button class="btn btn-secondary confirm-no">' + QTB.escapeHtml(noText || 'No, cancel') + '</button>' +
            '</div>' +
          '</div>' +
        '</div>';
      document.body.appendChild(overlay);
      overlay.querySelector('.confirm-yes').addEventListener('click', function () {
        overlay.remove(); resolve(true);
      });
      overlay.querySelector('.confirm-no').addEventListener('click', function () {
        overlay.remove(); resolve(false);
      });
      overlay.addEventListener('click', function (e) {
        if (e.target === overlay) { overlay.remove(); resolve(false); }
      });
    });
  }

  /** Global triggerEval: post eval request, show success/fail modal with jump button. */
  function triggerEval(body, btn) {
    var key = body.task_id + '__' + body.persona_id;
    // If already running, show info and offer jump
    var existing = _evalState.jobs[key];
    if (existing && (existing.status === 'running' || existing.status === 'pending' || existing.status === 'stopping')) {
      showModal('Already Running', '<p>Evaluation for <strong>' + QTB.escapeHtml(body.task_id) + '</strong> is already in progress.</p>' +
        '<div style="margin-top:16px;text-align:center">' +
          '<a href="#/evaluate/active" class="btn btn-primary" onclick="(function(){var m=document.getElementById(\'qtb-modal\');if(m)m.remove();})()" style="text-decoration:none">Go to Active Evals</a>' +
        '</div>');
      return Promise.resolve();
    }
    if (btn) { btn.disabled = true; btn.textContent = 'Starting...'; }

    // Create job entry in state
    _evalState.jobs[key] = {
      status: 'pending',
      error: null,
      scores: null,
      duration: null,
      req: body,
      startTime: Date.now(),
      steps: {},
      timerInterval: null,
    };
    // Start timer
    _evalState.jobs[key].timerInterval = setInterval(function () {
      _updateEvalJobRowTimer(key);
    }, 1000);

    return apiPost('/eval', body).then(function () {
      if (btn) { btn.textContent = 'Evaluating...'; }
      _evalState.jobs[key].status = 'running';
      _injectEvalJobRow(key);
      _startEvalPolling();
      // Show success modal with jump button
      var countdown = 3;
      var modalHtml =
        '<div style="text-align:center;padding:12px 0">' +
          '<div style="font-size:36px;margin-bottom:8px">\u2705</div>' +
          '<p>Evaluation started for <strong>' + QTB.escapeHtml(body.task_id) + '</strong></p>' +
          '<div style="margin-top:16px;display:flex;gap:12px;justify-content:center">' +
            '<a href="#/evaluate/active" class="btn btn-primary" onclick="(function(){var m=document.getElementById(\'qtb-modal\');if(m)m.remove();})()" style="text-decoration:none">Go to Active Evals</a>' +
          '</div>' +
          '<p style="color:var(--text-muted);font-size:12px;margin-top:12px">' +
            'Auto-closing in <span id="modal-countdown-eval-start">' + countdown + '</span>s</p>' +
        '</div>';
      showModal('Evaluation Started', modalHtml);
      var _startTimer = setInterval(function () {
        countdown--;
        var cdEl = document.getElementById('modal-countdown-eval-start');
        if (cdEl) cdEl.textContent = countdown;
        if (countdown <= 0) { clearInterval(_startTimer); closeModal(); }
      }, 1000);
    }).catch(function (err) {
      if (_evalState.jobs[key] && _evalState.jobs[key].timerInterval) clearInterval(_evalState.jobs[key].timerInterval);
      delete _evalState.jobs[key];
      if (btn) {
        btn.disabled = false;
        btn.textContent = err.message && err.message.indexOf('Already') >= 0 ? 'Running...' : (btn.dataset && btn.dataset.scored === 'true' ? 'Re-evaluate' : 'Evaluate');
      }
      showModal('Eval Error', '<p style="color:var(--error)">' + QTB.escapeHtml(err.message) + '</p>');
    });
  }

  // ── Eval job list management (mirrors run-single pattern) ──

  /** Parse OAS/QR/QP from scores.md markdown text. */
  function _parseScoresFromMd(md) {
    var scores = {};
    var oasMatch = md.match(/Overall Agent Score \(OAS\)\s*\|\s*([\d.]+)/);
    var qrMatch = md.match(/Quant Result \(QR\)\s*\|\s*([\d.]+)/);
    var qpMatch = md.match(/Quant Process \(QP\)\s*\|\s*([\d.]+)/);
    if (oasMatch) scores.oas = parseFloat(oasMatch[1]);
    if (qrMatch) scores.qr = parseFloat(qrMatch[1]);
    if (qpMatch) scores.qp = parseFloat(qpMatch[1]);
    return (scores.oas != null || scores.qr != null || scores.qp != null) ? scores : null;
  }

  /** Inject a new row into /evaluate/active if mounted. */
  function _injectEvalJobRow(key) {
    var activePage = _subPages['/evaluate/active'];
    if (!activePage) return;
    var listEl = activePage.querySelector('.eval-active-list');
    if (!listEl) return;
    var existing = listEl.querySelector('[data-eval-key="' + CSS.escape(key) + '"]');
    if (existing) return;
    var job = _evalState.jobs[key];
    if (!job) return;
    var row = _buildEvalJobRow(key, job);
    listEl.insertBefore(row, listEl.firstChild);
    var empty = listEl.querySelector('.empty-state');
    if (empty) empty.remove();
    _updateEvalToolbar();
  }

  /** Update row status icons/text in the active list. */
  function _updateEvalJobRow(key) {
    var activePage = _subPages['/evaluate/active'];
    if (!activePage) { return; }
    var row = activePage.querySelector('[data-eval-key="' + CSS.escape(key) + '"]');
    if (!row) { _injectEvalJobRow(key); return; }
    var job = _evalState.jobs[key];
    if (!job) return;
    var statusEl = row.querySelector('.rg-job-status');
    var detailEl = row.querySelector('.ea-job-detail');
    var stopBtn = row.querySelector('.ea-stop-btn');
    var rerunBtn = row.querySelector('.ea-rerun-btn');
    var st = job.status;

    if (st === 'running') {
      if (statusEl) { statusEl.textContent = '\u25CF'; statusEl.className = 'rg-job-status rg-running'; }
      if (detailEl) detailEl.textContent = '';
      if (stopBtn) { stopBtn.disabled = false; stopBtn.style.display = ''; }
      if (rerunBtn) rerunBtn.disabled = true;
    } else if (st === 'stopping') {
      if (statusEl) { statusEl.textContent = '\u29D7'; statusEl.className = 'rg-job-status rg-stopping'; }
      if (detailEl) detailEl.textContent = 'Stopping\u2026';
      if (stopBtn) { stopBtn.disabled = true; stopBtn.style.display = ''; }
      if (rerunBtn) rerunBtn.disabled = true;
    } else if (st === 'cancelled') {
      if (statusEl) { statusEl.textContent = '\u2717'; statusEl.className = 'rg-job-status rg-error'; }
      if (detailEl) detailEl.textContent = 'Cancelled';
      if (stopBtn) stopBtn.style.display = 'none';
      if (rerunBtn) rerunBtn.disabled = false;
    } else if (st === 'error') {
      if (statusEl) { statusEl.textContent = '\u2717'; statusEl.className = 'rg-job-status rg-error'; }
      if (detailEl) detailEl.textContent = 'Error: ' + (job.error || '').slice(0, 60);
      if (stopBtn) stopBtn.style.display = 'none';
      if (rerunBtn) rerunBtn.disabled = false;
    } else if (st === 'done') {
      if (statusEl) { statusEl.textContent = '\u2713'; statusEl.className = 'rg-job-status rg-done'; }
      var dText = '';
      if (job.duration) dText += Math.round(job.duration) + 's';
      if (job.scores && job.scores.oas != null) dText += (dText ? ' \u00B7 ' : '') + 'OAS: ' + job.scores.oas.toFixed(4);
      if (detailEl) detailEl.textContent = dText;
      if (stopBtn) stopBtn.style.display = 'none';
      if (rerunBtn) rerunBtn.disabled = false;
    }
    _updateEvalToolbar();
  }

  /** Update timer text on an eval job row. */
  function _updateEvalJobRowTimer(key) {
    var job = _evalState.jobs[key];
    if (!job || !job.startTime) return;
    if (job.status === 'done' || job.status === 'error' || job.status === 'cancelled') return;
    var activePage = _subPages['/evaluate/active'];
    if (!activePage) return;
    var row = activePage.querySelector('[data-eval-key="' + CSS.escape(key) + '"]');
    if (!row) return;
    var timerEl = row.querySelector('.ea-timer');
    if (timerEl) {
      var sec = Math.round((Date.now() - job.startTime) / 1000);
      var m = Math.floor(sec / 60);
      timerEl.textContent = (m > 0 ? m + 'm ' : '') + (sec % 60) + 's';
    }
    // Also update detail view elapsed if viewing this eval
    if (_evalDetailKey === key) {
      var scope = _subPages['/evaluate/active/' + key];
      if (scope) {
        var elapsedEl = scope.querySelector('#ed-elapsed');
        if (elapsedEl) {
          var sec2 = Math.round((Date.now() - job.startTime) / 1000);
          var m2 = Math.floor(sec2 / 60);
          elapsedEl.textContent = (m2 > 0 ? m2 + 'm ' : '') + (sec2 % 60) + 's';
        }
      }
    }
  }

  /** Build a single eval job row (mirrors _addSingleJobRow structure). */
  function _buildEvalJobRow(key, job) {
    var req = job.req || {};
    var row = document.createElement('div');
    row.className = 'rg-job ea-job';
    row.setAttribute('data-eval-key', key);
    row.innerHTML =
      '<input type="checkbox" class="ea-checkbox">' +
      '<span class="rg-job-status rg-pending">\u25CB</span>' +
      '<span class="rg-job-task ea-job-task">' + QTB.escapeHtml(req.task_id || key.split('__')[0]) + '</span>' +
      '<span class="rg-job-persona">' + QTB.escapeHtml(req.persona_id || key.split('__')[1]) + '</span>' +
      '<span class="rg-job-detail ea-job-detail"></span>' +
      '<span class="ea-timer" style="font-family:var(--font-mono);font-size:12px;color:var(--text-muted);min-width:40px"></span>' +
      '<div class="ea-job-actions">' +
        '<button class="btn btn-xs btn-secondary ea-rerun-btn" disabled title="Re-evaluate">\u21BB</button>' +
        '<button class="btn btn-xs btn-danger ea-stop-btn" title="Stop">\u25A0</button>' +
      '</div>';

    // Update status to current
    var st = job.status;
    var statusEl = row.querySelector('.rg-job-status');
    if (st === 'running') {
      if (statusEl) { statusEl.textContent = '\u25CF'; statusEl.className = 'rg-job-status rg-running'; }
    } else if (st === 'done') {
      if (statusEl) { statusEl.textContent = '\u2713'; statusEl.className = 'rg-job-status rg-done'; }
      var stopBtn = row.querySelector('.ea-stop-btn');
      if (stopBtn) stopBtn.style.display = 'none';
      var rerunBtn = row.querySelector('.ea-rerun-btn');
      if (rerunBtn) rerunBtn.disabled = false;
    }

    // Click task name → detail view
    row.querySelector('.ea-job-task').addEventListener('click', function (e) {
      e.stopPropagation();
      window.location.hash = '#/evaluate/active/' + key;
    });

    // Stop button (with stopping intermediate state)
    row.querySelector('.ea-stop-btn').addEventListener('click', function (e) {
      e.stopPropagation();
      confirmDialog('Stop evaluation for ' + (req.task_id || key) + '?', 'Yes, stop', 'No, cancel').then(function (yes) {
        if (!yes) return;
        var curJob = _evalState.jobs[key];
        if (curJob) curJob.status = 'stopping';
        _updateEvalJobRow(key);
        // Show stopping overlay on detail view if viewing this eval
        if (_evalDetailKey === key) {
          var detailScope = _subPages['/evaluate/active/' + key];
          if (detailScope) {
            _showStoppingOverlay(detailScope);
            var badge = detailScope.querySelector('#ed-status-badge');
            if (badge) { badge.textContent = 'stopping'; badge.className = 'rgd-status-badge rgd-stopping'; }
          }
        }
        apiPost('/eval/stop', { task_id: req.task_id, persona_id: req.persona_id }).catch(function () {
          // Revert on error
          if (_evalState.jobs[key]) _evalState.jobs[key].status = 'running';
          _updateEvalJobRow(key);
          if (_evalDetailKey === key) {
            var ds = _subPages['/evaluate/active/' + key];
            if (ds) _removeStoppingOverlay(ds);
          }
        });
      });
    });

    // Re-evaluate button
    row.querySelector('.ea-rerun-btn').addEventListener('click', function (e) {
      e.stopPropagation();
      if (req.task_id) triggerEval(req, null);
    });

    // Checkbox sync
    row.querySelector('.ea-checkbox').addEventListener('change', function () { _syncEvalSelectAll(); });

    return row;
  }

  function _syncEvalSelectAll() {
    var activePage = _subPages['/evaluate/active'];
    if (!activePage) return;
    var all = Array.from(activePage.querySelectorAll('.ea-checkbox'));
    var selectAll = activePage.querySelector('#ea-select-all');
    if (!selectAll || all.length === 0) return;
    var allChecked = all.every(function (cb) { return cb.checked; });
    selectAll.checked = allChecked;
    selectAll.indeterminate = !allChecked && all.some(function (cb) { return cb.checked; });
  }

  function _updateEvalToolbar() {
    var activePage = _subPages['/evaluate/active'];
    if (!activePage) return;
    var total = Object.keys(_evalState.jobs).length;
    var running = Object.keys(_evalState.jobs).filter(function (k) {
      var s = _evalState.jobs[k].status;
      return s === 'running' || s === 'pending' || s === 'stopping';
    }).length;
    var countEl = activePage.querySelector('#ea-active-count');
    if (countEl) countEl.textContent = running + ' running / ' + total + ' total';
    var counterBadge = activePage.querySelector('#ea-counter');
    if (counterBadge) counterBadge.textContent = total;
  }

  // ── Eval polling (SSE fallback) ───────────────────────────

  function _startEvalPolling() {
    if (_evalPollInterval) return; // already polling
    _evalPollInterval = setInterval(_pollActiveEvals, 3000);
  }

  function _stopEvalPolling() {
    if (_evalPollInterval) { clearInterval(_evalPollInterval); _evalPollInterval = null; }
  }

  function _pollActiveEvals() {
    var pendingKeys = Object.keys(_evalState.jobs).filter(function (k) {
      var s = _evalState.jobs[k].status;
      return s === 'running' || s === 'pending' || s === 'stopping';
    });
    if (pendingKeys.length === 0) { _stopEvalPolling(); return; }
    api('/eval/active').then(function (serverList) {
      var serverKeys = serverList.map(function (e) { return e.task_id + '__' + e.persona_id; });
      pendingKeys.forEach(function (key) {
        if (serverKeys.indexOf(key) === -1) {
          _handleEvalGone(key);
        }
      });
    }).catch(function () {});
  }

  function _handleEvalGone(key) {
    var job = _evalState.jobs[key];
    if (!job) return;
    if (job.status === 'done' || job.status === 'error' || job.status === 'cancelled') return;
    console.log('%c[EVAL_POLL] _handleEvalGone key=' + key + ' jobStatus=' + job.status + ' stepsCompleted=' + Object.keys(job.steps || {}).filter(function (s) { return (job.steps[s] || {}).status === 'done'; }).length + '/7', 'color:#e67e22;font-weight:bold');
    var req = job.req;
    if (!req) { onEvalEnd({ task_id: key.split('__')[0], persona_id: key.split('__')[1], error: 'Eval ended unexpectedly', mode: 'eval' }); return; }
    var path = [req.source || 'run-single', req.agent, req.model, req.category, req.task_id, req.persona_id].map(encodeURIComponent).join('/');
    api('/results/' + path).then(function (data) {
      // Parse scores from scores.md markdown if available
      var scores = null;
      if (data._scores_md) {
        scores = _parseScoresFromMd(data._scores_md);
      }
      onEvalEnd({
        task_id: req.task_id,
        persona_id: req.persona_id,
        error: data._scores_md ? null : 'Eval ended without scores',
        mode: 'eval',
        scores: scores,
        source: req.source,
        agent: req.agent,
        model: req.model,
        category: req.category,
      });
    }).catch(function () {
      onEvalEnd({ task_id: req.task_id, persona_id: req.persona_id, error: 'Eval ended unexpectedly', mode: 'eval', source: req.source, agent: req.agent, model: req.model, category: req.category });
    });
  }

  // ── Modal (global) ────────────────────────────────────────

  function showModal(title, htmlContent) {
    closeModal();
    var overlay = document.createElement('div');
    overlay.className = 'modal-overlay';
    overlay.id = 'qtb-modal';
    overlay.addEventListener('click', function (e) { if (e.target === overlay) closeModal(); });
    overlay.innerHTML =
      '<div class="modal-content">' +
        '<div class="modal-header">' +
          '<span class="modal-title">' + QTB.escapeHtml(title) + '</span>' +
          '<button class="modal-close" id="modal-close-btn">&times;</button>' +
        '</div>' +
        '<div class="modal-body">' + htmlContent + '</div>' +
      '</div>';
    document.body.appendChild(overlay);
    document.getElementById('modal-close-btn').addEventListener('click', closeModal);
    document.addEventListener('keydown', modalEscHandler);
  }

  function closeModal() {
    var el = document.getElementById('qtb-modal');
    if (el) el.remove();
    document.removeEventListener('keydown', modalEscHandler);
  }

  function modalEscHandler(e) { if (e.key === 'Escape') closeModal(); }

  // Expose showModal globally for tools.js
  window._qtbShowModal = showModal;

  // ── Collapsible panel helper ──────────────────────────────

  function bindSideTab(scope, tabId, panelSelector, isLeft) {
    var tab = scope.querySelector('#' + tabId);
    if (!tab) return;
    var panel = scope.querySelector(panelSelector);
    var openArrow = isLeft ? '\u25C0' : '\u25B6';
    var closedArrow = isLeft ? '\u25B6' : '\u25C0';
    function syncTab() {
      if (!panel) return;
      var collapsed = panel.classList.contains('collapsed');
      var arrow = tab.querySelector('.tab-arrow');
      if (arrow) arrow.textContent = collapsed ? closedArrow : openArrow;
    }
    syncTab();
    tab.addEventListener('click', function () {
      if (!panel) return;
      panel.classList.toggle('collapsed');
      syncTab();
    });
  }

  // ── Router (module-container architecture) ────────────────
  //
  // Each top-level module (dashboard, run, results, evaluate, tasks)
  // gets a persistent container div.  Module switches = hide/show,
  // so DOM state (conversations, eval progress, etc.) is preserved.
  //
  // Within evaluate & results, each sub-route also gets a cached
  // sub-page div, so drilling down and back preserves running evals.

  var _modules = {};        // module-name → container div
  var _currentMod = null;   // active module name
  var _subPages = {};       // route-string → sub-page div (for evaluate & results)
  var _staleRoutes = {};    // route-string → true (needs re-render on next visit)
  var _alwaysFreshRoutes = {};  // route-string → true (always re-fetch on visit)
  var _lastModRoute = {};   // module-name → last active route (for module switch resume)
  var _evalActiveReady = false;  // true when /evaluate/active sub-page is mounted
  // _evalDetailKey is declared near _evalState at the top

  function getRoute() {
    var hash = window.location.hash || '#/';
    return hash.slice(1) || '/';
  }

  function _getModule(route) {
    if (route === '/') return 'dashboard';
    var seg = route.split('/')[1];
    return seg || 'dashboard';
  }

  function _getModContainer(mod) {
    if (!_modules[mod]) {
      var div = document.createElement('div');
      div.className = 'module-container';
      document.getElementById('app').appendChild(div);
      _modules[mod] = div;
    }
    return _modules[mod];
  }

  function _updateNavActive(route) {
    document.querySelectorAll('.nav-link').forEach(function (a) {
      var href = a.getAttribute('href');
      var active = href === '#' + route ||
        (route.startsWith('/run') && href === '#/run') ||
        (route.startsWith('/results') && href === '#/results') ||
        (route.startsWith('/tasks') && href === '#/tasks') ||
        (route.startsWith('/evaluate') && href === '#/evaluate');
      a.classList.toggle('active', active);
    });
  }

  /** Mark a cached sub-page as stale so it re-renders on next visit. */
  function invalidateSubPage(route) {
    _staleRoutes[route] = true;
  }

  function onRouteChange() {
    var route = getRoute();
    var newMod = _getModule(route);

    // When switching TO a different module's root, redirect to last-visited sub-route.
    // This preserves the user's place (e.g. eval progress page) across module switches.
    // Within the same module (e.g. breadcrumb back to root), allow normal navigation.
    var isModRoot = (newMod === 'dashboard' && route === '/') || route === '/' + newMod;
    if (isModRoot && newMod !== _currentMod && _lastModRoute[newMod] && _lastModRoute[newMod] !== route) {
      window.location.hash = '#' + _lastModRoute[newMod];
      return; // hashchange will fire onRouteChange again with the deep route
    }

    // Hide current module (don't destroy)
    if (_currentMod && _modules[_currentMod]) {
      _modules[_currentMod].classList.remove('active');
    }

    // Show target module
    var container = _getModContainer(newMod);
    container.classList.add('active');
    _currentMod = newMod;
    _updateNavActive(route);

    // Module-specific routing
    switch (newMod) {
      case 'run':
        // Track active detail keys for live SSE routing
        if (route.startsWith('/run/single/') && route !== '/run/single') {
          _singleDetailKey = route.slice('/run/single/'.length);
          _staleRoutes[route] = true;
        } else if (route === '/run/single') {
          _singleDetailKey = null;
          _stopSingleElapsedTimer();
        }
        if (route.startsWith('/run/group/') && route !== '/run/group') {
          _groupDetailKey = route.slice('/run/group/'.length);
          _staleRoutes[route] = true;
        } else if (route === '/run/group') {
          _groupDetailKey = null;
        }
        _routeSubPaged(container, route, _dispatchRun);
        break;

      case 'evaluate':
        // Track eval detail key for live SSE routing
        if (route.startsWith('/evaluate/active/') && route !== '/evaluate/active') {
          _evalDetailKey = route.slice('/evaluate/active/'.length);
          _staleRoutes[route] = true; // always refresh detail on visit
        } else {
          _evalDetailKey = null;
        }
        // Column view: all /evaluate/results/* share one cached sub-page
        _routeSubPaged(container, route.startsWith('/evaluate/results') ? '/evaluate/results' : route, function (target) {
          _dispatchEval(target, route);
        });
        break;

      case 'results':
        _routeSubPaged(container, route, _dispatchResults);
        break;

      case 'dashboard':
        // Dashboard always refreshes (lightweight)
        showDashboard(container);
        break;

      case 'tasks':
        _routeSubPaged(container, route, _dispatchTasks);
        break;

      default:
        container.innerHTML = '<div class="page"><div class="empty-state">Page not found</div></div>';
    }
  }

  /**
   * Sub-page caching for modules with drilldown routes.
   * Each unique route gets its own cached div inside the module container.
   * Switching between sub-routes = hide/show; stale pages re-render.
   */
  function _routeSubPaged(container, route, dispatcher) {
    // Track last active route per module (for resume on module switch)
    _lastModRoute[_currentMod] = route;

    // Hide all sub-pages in this module
    Array.from(container.children).forEach(function (c) { c.style.display = 'none'; });

    // Cached & not stale & not always-fresh → just show
    if (_subPages[route] && !_staleRoutes[route] && !_alwaysFreshRoutes[route]) {
      _subPages[route].style.display = '';
      return;
    }

    // Create or reuse sub-page div
    var sub = _subPages[route];
    if (!sub) {
      sub = document.createElement('div');
      sub.className = 'sub-page';
      container.appendChild(sub);
      _subPages[route] = sub;
    }
    sub.style.display = '';
    delete _staleRoutes[route];

    // Render content into sub-page
    dispatcher(sub, route);
  }

  function _dispatchRun(target, route) {
    if (route === '/run') showRunSelect(target);
    else if (route === '/run/single') {
      if (!target.firstElementChild || target.dataset.rendered !== 'single') {
        target.innerHTML = '';
        target.dataset.rendered = 'single';
        showRun(target);
      }
    }
    else if (route.startsWith('/run/single/')) {
      var detailKey = route.slice('/run/single/'.length);
      _singleDetailKey = detailKey;
      target.innerHTML = '';
      target.dataset.rendered = 'single-detail-' + detailKey;
      showRunSingleDetail(target, detailKey);
    }
    else if (route === '/run/group') {
      _groupDetailKey = null;
      if (!target.firstElementChild || target.dataset.rendered !== 'group') {
        target.innerHTML = '';
        target.dataset.rendered = 'group';
        showRunGroup(target);
      }
    }
    else if (route.startsWith('/run/group/')) {
      var gDetailKey = route.slice('/run/group/'.length);
      _groupDetailKey = gDetailKey;
      target.innerHTML = '';
      target.dataset.rendered = 'group-detail-' + gDetailKey;
      showRunGroupDetail(target, gDetailKey);
    }
    else target.innerHTML = '<div class="page"><div class="empty-state">Page not found</div></div>';
  }

  function _dispatchEval(target, route) {
    if (route === '/evaluate') { showEvaluateHome(target); }
    else if (route === '/evaluate/active') showEvalActive(target);
    else if (route.startsWith('/evaluate/active/') && route !== '/evaluate/active') {
      var eDetailKey = route.slice('/evaluate/active/'.length);
      target.innerHTML = '';
      target.dataset.rendered = 'eval-detail-' + eDetailKey;
      showEvalDetail(target, eDetailKey);
    }
    else if (route.startsWith('/evaluate/results')) { showEvalColumnView(target, route); }
    else target.innerHTML = '<div class="page"><div class="empty-state">Page not found</div></div>';
  }

  function _dispatchResults(target, route) {
    if (route === '/results') { _alwaysFreshRoutes[route] = true; showResultSources(target); }
    else if (route.match(/^\/results\/s\/[^/]+$/)) { _alwaysFreshRoutes[route] = true; showResultAgents(target, route.split('/')[3]); }
    else if (route.match(/^\/results\/s\/[^/]+\/a\/[^/]+$/)) { _alwaysFreshRoutes[route] = true; var ra = route.split('/'); showResultModels(target, ra[3], ra[5]); }
    else if (route.match(/^\/results\/s\/[^/]+\/a\/[^/]+\/m\/[^/]+$/)) { _alwaysFreshRoutes[route] = true; var rm = route.split('/'); showResultCategories(target, rm[3], rm[5], rm[7]); }
    else if (route.match(/^\/results\/s\/[^/]+\/a\/[^/]+\/m\/[^/]+\/c\/[^/]+$/)) { _alwaysFreshRoutes[route] = true; var rc = route.split('/'); showResultsInCategory(target, rc[3], rc[5], rc[7], rc[9]); }
    else if (route.startsWith('/results/')) showResultDetail(target, route.slice('/results/'.length));
    else target.innerHTML = '<div class="page"><div class="empty-state">Page not found</div></div>';
  }

  function _dispatchTasks(target, route) {
    if (route === '/tasks') showTaskFolders(target);
    else if (route.startsWith('/tasks/')) showTasksInCategory(target, route.slice('/tasks/'.length));
    else target.innerHTML = '<div class="page"><div class="empty-state">Page not found</div></div>';
  }

  // ── Page: Evaluate Home ────────────────────────────────────

  function showEvaluateHome(app) {
    var running = Object.keys(_evalState.jobs).filter(function (k) {
      var s = _evalState.jobs[k].status;
      return s === 'running' || s === 'pending' || s === 'stopping';
    }).length;
    var total = Object.keys(_evalState.jobs).length;
    app.innerHTML =
      '<div class="page">' +
        '<div class="page-header"><h1 class="page-title">Evaluate</h1></div>' +
        '<div class="eval-home-cards">' +
          '<div class="eval-home-card" id="eval-card-results">' +
            '<div class="eval-home-icon">' +
              '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" width="32" height="32">' +
                '<rect x="2" y="3" width="20" height="18" rx="2"/><path d="M2 9h20"/><path d="M9 21V9"/>' +
              '</svg>' +
            '</div>' +
            '<div class="eval-home-title">Browse Results</div>' +
            '<div class="eval-home-desc">View scored results or evaluate unscored runs</div>' +
          '</div>' +
          '<div class="eval-home-card" id="eval-card-active">' +
            '<div class="eval-home-icon">' +
              '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" width="32" height="32">' +
                '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 3"/>' +
              '</svg>' +
            '</div>' +
            '<div class="eval-home-title">Active Evaluations</div>' +
            '<div class="eval-home-desc">' +
              (running > 0
                ? '<span style="color:var(--accent);font-weight:600">' + running + ' running</span>, ' + total + ' total this session'
                : total > 0 ? total + ' evaluations this session' : 'No evaluations this session') +
            '</div>' +
          '</div>' +
        '</div>' +
      '</div>';
    app.querySelector('#eval-card-results').addEventListener('click', function () {
      window.location.hash = '#/evaluate/results';
    });
    app.querySelector('#eval-card-active').addEventListener('click', function () {
      window.location.hash = '#/evaluate/active';
    });
  }

  // ── Page: Evaluate Active (running/completed evals list) ──

  function showEvalActive(app) {
    var keys = Object.keys(_evalState.jobs);
    var running = keys.filter(function (k) {
      var s = _evalState.jobs[k].status;
      return s === 'running' || s === 'pending' || s === 'stopping';
    }).length;

    app.innerHTML =
      '<div class="page">' +
        '<div class="page-header">' +
          '<div class="panel-header">' +
            '<a href="#/evaluate" class="bc-back">\u2190</a>' +
            'Active Evaluations' +
            '<span id="ea-counter" class="badge">' + keys.length + '</span>' +
          '</div>' +
        '</div>' +
        '<div class="rs-toolbar" id="ea-toolbar">' +
          '<label class="eval-select-all-label">' +
            '<input type="checkbox" id="ea-select-all"> Select all' +
          '</label>' +
          '<div class="eval-toolbar-actions">' +
            '<button class="btn btn-sm btn-primary" id="ea-reeval-selected" disabled>Re-evaluate Selected</button>' +
            '<button class="btn btn-sm btn-danger" id="ea-stop-selected" disabled>Stop Selected</button>' +
          '</div>' +
          '<span class="eval-active-count" id="ea-active-count">' +
            running + ' running / ' + keys.length + ' total' +
          '</span>' +
        '</div>' +
        '<div class="eval-active-list rg-job-area">' +
          (keys.length === 0
            ? '<div class="empty-state">No evaluations this session. Click "Evaluate" from any result page to start.</div>'
            : '') +
        '</div>' +
      '</div>';

    var listEl = app.querySelector('.eval-active-list');
    // Render existing entries (newest first)
    var sortedKeys = keys.slice().reverse();
    sortedKeys.forEach(function (key) {
      var job = _evalState.jobs[key];
      if (!job) return;
      var row = _buildEvalJobRow(key, job);
      // Set correct status on the row
      _applyEvalRowStatus(row, job);
      listEl.appendChild(row);
    });

    // Select all
    var selectAll = app.querySelector('#ea-select-all');
    selectAll.addEventListener('change', function () {
      app.querySelectorAll('.ea-checkbox').forEach(function (cb) { cb.checked = selectAll.checked; });
    });

    // Re-evaluate Selected
    app.querySelector('#ea-reeval-selected').addEventListener('click', function () {
      var checked = Array.from(app.querySelectorAll('.ea-checkbox:checked'));
      if (checked.length === 0) { showModal('Notice', '<p>Please select tasks to evaluate.</p>'); return; }
      var tasks = checked.map(function (cb) {
        var row = cb.closest('.ea-job');
        var key = row && row.getAttribute('data-eval-key');
        return key && _evalState.jobs[key] && _evalState.jobs[key].req;
      }).filter(Boolean);
      if (tasks.length === 0) return;
      confirmDialog('Re-evaluate ' + tasks.length + ' selected task(s)?').then(function (yes) {
        if (!yes) return;
        tasks.forEach(function (req) { triggerEval(req, null); });
      });
    });

    // Stop Selected (with stopping state)
    app.querySelector('#ea-stop-selected').addEventListener('click', function () {
      var checked = Array.from(app.querySelectorAll('.ea-checkbox:checked'));
      if (checked.length === 0) { showModal('Notice', '<p>Please select tasks to stop.</p>'); return; }
      var runningKeys = checked.map(function (cb) {
        var row = cb.closest('.ea-job');
        var key = row && row.getAttribute('data-eval-key');
        return key;
      }).filter(function (k) {
        if (!k || !_evalState.jobs[k]) return false;
        var s = _evalState.jobs[k].status;
        return s === 'running' || s === 'pending';
      });
      if (runningKeys.length === 0) { showModal('Notice', '<p>No running evaluations among selected tasks.</p>'); return; }
      confirmDialog('Stop ' + runningKeys.length + ' running evaluation(s)?').then(function (yes) {
        if (!yes) return;
        runningKeys.forEach(function (key) {
          var job = _evalState.jobs[key];
          if (!job) return;
          job.status = 'stopping';
          _updateEvalJobRow(key);
          if (_evalDetailKey === key) {
            var ds = _subPages['/evaluate/active/' + key];
            if (ds) {
              _showStoppingOverlay(ds);
              var badge = ds.querySelector('#ed-status-badge');
              if (badge) { badge.textContent = 'stopping'; badge.className = 'rgd-status-badge rgd-stopping'; }
            }
          }
          apiPost('/eval/stop', { task_id: job.req.task_id, persona_id: job.req.persona_id }).catch(function () {
            if (_evalState.jobs[key]) _evalState.jobs[key].status = 'running';
            _updateEvalJobRow(key);
          });
        });
      });
    });

    // Enable toolbar buttons
    app.querySelector('#ea-reeval-selected').disabled = false;
    app.querySelector('#ea-stop-selected').disabled = false;
  }

  /** Apply status visuals to a freshly built row without going through _updateEvalJobRow. */
  function _applyEvalRowStatus(row, job) {
    var statusEl = row.querySelector('.rg-job-status');
    var detailEl = row.querySelector('.ea-job-detail');
    var stopBtn = row.querySelector('.ea-stop-btn');
    var rerunBtn = row.querySelector('.ea-rerun-btn');
    var st = job.status;
    if (st === 'running' || st === 'pending') {
      if (st === 'running' && statusEl) { statusEl.textContent = '\u25CF'; statusEl.className = 'rg-job-status rg-running'; }
    } else if (st === 'stopping') {
      if (statusEl) { statusEl.textContent = '\u29D7'; statusEl.className = 'rg-job-status rg-stopping'; }
      if (detailEl) detailEl.textContent = 'Stopping\u2026';
      if (stopBtn) stopBtn.disabled = true;
    } else if (st === 'cancelled') {
      if (statusEl) { statusEl.textContent = '\u2717'; statusEl.className = 'rg-job-status rg-error'; }
      if (detailEl) detailEl.textContent = 'Cancelled';
      if (stopBtn) stopBtn.style.display = 'none';
      if (rerunBtn) rerunBtn.disabled = false;
    } else if (st === 'error') {
      if (statusEl) { statusEl.textContent = '\u2717'; statusEl.className = 'rg-job-status rg-error'; }
      if (detailEl) detailEl.textContent = 'Error: ' + (job.error || '').slice(0, 60);
      if (stopBtn) stopBtn.style.display = 'none';
      if (rerunBtn) rerunBtn.disabled = false;
    } else if (st === 'done') {
      if (statusEl) { statusEl.textContent = '\u2713'; statusEl.className = 'rg-job-status rg-done'; }
      var dText = '';
      if (job.duration) dText += Math.round(job.duration) + 's';
      if (job.scores && job.scores.oas != null) dText += (dText ? ' \u00B7 ' : '') + 'OAS: ' + job.scores.oas.toFixed(4);
      if (detailEl) detailEl.textContent = dText;
      if (stopBtn) stopBtn.style.display = 'none';
      if (rerunBtn) rerunBtn.disabled = false;
    }
  }

  /** Eval detail page — shows step-by-step progress for a single eval job. */
  function showEvalDetail(app, key) {
    var job = _evalState.jobs[key];
    if (!job) {
      app.innerHTML = '<div class="page"><div class="empty-state">Evaluation not found: ' + QTB.escapeHtml(key) + '</div></div>';
      return;
    }
    var req = job.req || {};
    var stLabel = job.status === 'cancelled' ? 'cancelled' : job.status;
    var stClass = 'rgd-status-badge ';
    if (job.status === 'running') stClass += 'rgd-running';
    else if (job.status === 'stopping') stClass += 'rgd-stopping';
    else if (job.status === 'done') stClass += 'rgd-done';
    else if (job.status === 'error' || job.status === 'cancelled') stClass += 'rgd-error';
    else stClass += 'rgd-pending';

    var elapsedText = '-';
    if (job.startTime) {
      var sec = job.duration ? Math.round(job.duration) : Math.round((Date.now() - job.startTime) / 1000);
      var mm = Math.floor(sec / 60);
      elapsedText = (mm > 0 ? mm + 'm ' : '') + (sec % 60) + 's';
    }

    app.innerHTML =
      '<div class="page page-eval-detail">' +
        '<aside class="run-config rsd-config">' +
          '<div class="rgd-back-row">' +
            '<a href="#/evaluate/active" class="bc-back">\u2190 Back to list</a>' +
          '</div>' +
          '<div class="rgd-meta">' +
            '<div class="rgd-meta-label">Task</div>' +
            '<div class="rgd-meta-value" id="ed-task-id">' + QTB.escapeHtml(req.task_id || key.split('__')[0]) + '</div>' +
          '</div>' +
          '<div class="rgd-meta">' +
            '<div class="rgd-meta-label">Persona</div>' +
            '<div class="rgd-meta-value">' + QTB.escapeHtml(req.persona_id || key.split('__')[1]) + '</div>' +
          '</div>' +
          '<div class="rgd-meta">' +
            '<div class="rgd-meta-label">Agent</div>' +
            '<div class="rgd-meta-value">' + QTB.escapeHtml(req.agent || '-') + '</div>' +
          '</div>' +
          '<div class="rgd-meta">' +
            '<div class="rgd-meta-label">Model</div>' +
            '<div class="rgd-meta-value">' + QTB.escapeHtml(req.model || '-') + '</div>' +
          '</div>' +
          '<div class="rgd-meta">' +
            '<div class="rgd-meta-label">Status</div>' +
            '<div><span class="' + stClass + '" id="ed-status-badge">' + stLabel + '</span></div>' +
          '</div>' +
          '<div class="rgd-meta">' +
            '<div class="rgd-meta-label">Duration</div>' +
            '<div class="rgd-meta-value" id="ed-elapsed">' + elapsedText + '</div>' +
          '</div>' +
        '</aside>' +
        '<div class="run-main">' +
          '<div class="eval-detail-main">' +
            '<div class="panel-header">Evaluation Steps</div>' +
            '<div class="eval-progress-card" style="margin:0">' +
              '<div class="eval-steps" id="ed-steps">' +
                EVAL_STEPS.map(function (s) {
                  var stepData = (job.steps || {})[s];
                  var cls = 'eval-step pending';
                  var icon = '\u25CB';
                  var scoreText = '';
                  if (stepData) {
                    if (stepData.status === 'running') { cls = 'eval-step running'; icon = '\u25CF'; }
                    else if (stepData.status === 'done') { cls = 'eval-step done'; icon = '\u2713'; scoreText = stepData.score != null ? stepData.score.toFixed(4) : ''; }
                  }
                  return '<div class="' + cls + '" data-step="' + s + '">' +
                    '<span class="step-indicator">' + icon + '</span>' +
                    '<span class="step-name">' + EVAL_STEP_LABELS[s] + '</span>' +
                    '<span class="step-score">' + scoreText + '</span>' +
                  '</div>';
                }).join('') +
              '</div>' +
              '<div class="eval-final" id="ed-final">' +
                (function () {
                  if (job.status === 'done' && job.scores) {
                    var oas = job.scores.oas != null ? job.scores.oas.toFixed(4) : '-';
                    var qr = job.scores.qr != null ? job.scores.qr.toFixed(4) : '-';
                    var qp = job.scores.qp != null ? job.scores.qp.toFixed(4) : '-';
                    return '<span class="eval-oas">OAS: ' + oas + '</span>' +
                      '<span class="eval-sub">QR: ' + qr + '</span>' +
                      '<span class="eval-sub">QP: ' + qp + '</span>';
                  }
                  if (job.status === 'cancelled') return '<span class="eval-cancelled-text">\u274C Cancelled</span>';
                  if (job.status === 'error') return '<span class="eval-error-text">Error: ' + QTB.escapeHtml((job.error || '').slice(0, 120)) + '</span>';
                  return '';
                })() +
              '</div>' +
            '</div>' +
          '</div>' +
        '</div>' +
      '</div>';
  }

  // ── Page: Dashboard ───────────────────────────────────────

  function showDashboard(app) {
    renderTemplate(app, 'tpl-dashboard');
    api('/tasks').then(function (t) { var el = document.getElementById('dash-tasks'); if (el) el.textContent = t.length; }).catch(function () {});
    api('/results').then(function (results) {
      var ce = document.getElementById('dash-results');
      if (ce) ce.textContent = results.length;
      var le = document.getElementById('dash-recent');
      if (le) {
        var recent = results.slice(-5).reverse();
        le.innerHTML = recent.length === 0
          ? '<div class="empty-state">No results yet</div>'
          : recent.map(buildResultItemHTML).join('');
      }
    }).catch(function () {});
    api('/status').then(function (s) { var el = document.getElementById('dash-status'); if (el) el.textContent = s.running ? 'Running' : 'Idle'; }).catch(function () {});
  }

  // ── Page: Run ─────────────────────────────────────────────

  var _allTasks = [];
  var _agentDefaults = {}; // agent → [model1, model2, ...]

  function showRun(app) {
    renderTemplate(app, 'tpl-run');
    injectPanelStructure(app);

    // Load tasks → populate category + task cascade
    api('/tasks').then(function (tasks) {
      _allTasks = tasks;
      var catSel = app.querySelector('#run-category');
      var taskSel = app.querySelector('#run-task');
      if (!catSel || !taskSel) return;

      var cats = {};
      tasks.forEach(function (t) { cats[t.category || 'other'] = true; });
      var catList = Object.keys(cats).sort();
      catSel.innerHTML = catList.map(function (c) {
        var label = CATEGORY_LABELS[c] || c.replace(/_/g, ' ');
        return '<option value="' + c + '">' + QTB.escapeHtml(label) + '</option>';
      }).join('');

      function _updateSingleDefaults() {
        var tid = taskSel.value;
        var task = tasks.find(function (t) { return t.task_id === tid; });
        var mtEl = app.querySelector('#run-max-turns');
        var toEl = app.querySelector('#run-timeout');
        var mt = task && task.max_turns ? task.max_turns : '';
        var to = task && task.timeout_minutes ? task.timeout_minutes : '';
        if (mtEl) { mtEl.value = ''; mtEl.placeholder = mt ? mt + ' (Task Default)' : ''; }
        if (toEl) { toEl.value = ''; toEl.placeholder = to ? to + ' (Task Default)' : ''; }
      }

      function updateTaskList() {
        var cat = catSel.value;
        var filtered = tasks.filter(function (t) { return t.category === cat; });
        taskSel.innerHTML = filtered.map(function (t) {
          return '<option value="' + t.task_id + '">' + t.task_id + '</option>';
        }).join('');
        _updateSingleDefaults();
        _updateRunButtonState(app);
      }

      catSel.addEventListener('change', updateTaskList);
      taskSel.addEventListener('change', function () { _updateSingleDefaults(); _updateRunButtonState(app); });
      updateTaskList();
    }).catch(function () {});

    api('/personas').then(function (personas) {
      var sel = app.querySelector('#run-persona');
      if (sel) {
        sel.innerHTML = personas.map(function (p) {
          return '<option value="' + p.persona_id + '">' + p.persona_id + '</option>';
        }).join('');
        sel.addEventListener('change', function () { _updateRunButtonState(app); });
      }
    }).catch(function () {});

    // Wire up agent→model cascade (same logic as run-group)
    function _updateSingleModelSelect() {
      var agentSel = app.querySelector('#run-agent');
      var modelSel = app.querySelector('#run-model');
      if (!agentSel || !modelSel) return;
      var agent = agentSel.value;
      var models = _agentDefaults[agent] || [];
      modelSel.innerHTML = '<option value="">' + (models.length > 0 ? models[0] + ' (default)' : 'Default') + '</option>';
      models.forEach(function (m, i) {
        if (i === 0) return;
        var opt = document.createElement('option');
        opt.value = m;
        opt.textContent = m;
        modelSel.appendChild(opt);
      });
    }
    var runAgentSel = app.querySelector('#run-agent');
    if (runAgentSel) runAgentSel.addEventListener('change', function () { _updateSingleModelSelect(); });
    api('/agent-models').then(function (models) {
      _agentDefaults = models;
      _updateSingleModelSelect();
    }).catch(function () {
      _updateSingleModelSelect();
    });

    // Mode toggle hint
    var modeRadios = app.querySelectorAll('input[name="run-mode"]');
    var modeHint = app.querySelector('#run-mode-hint');
    modeRadios.forEach(function (r) {
      r.addEventListener('change', function () {
        if (modeHint) {
          modeHint.textContent = r.value === 'runonly'
            ? 'Run Only: execute the agent conversation and save results. You can evaluate later from the Evaluate tab.'
            : 'Run + Eval: execute the agent conversation, then automatically run full evaluation and scoring.';
        }
      });
    });

    // Run button → add job to list + POST /run
    var btnRun = app.querySelector('#btn-run');
    if (btnRun) {
      btnRun.addEventListener('click', function () {
        var modeEl = app.querySelector('input[name="run-mode"]:checked');
        var skipEval = !modeEl || modeEl.value === 'runonly';
        var maxTurnsVal = parseInt(app.querySelector('#run-max-turns').value);
        var timeoutVal = parseInt(app.querySelector('#run-timeout').value);
        var modelVal = (app.querySelector('#run-model') || {}).value || '';
        var req = {
          task_id: app.querySelector('#run-task').value,
          persona_id: app.querySelector('#run-persona').value,
          agent: app.querySelector('#run-agent').value,
          docker: app.querySelector('#run-docker').checked,
          skip_eval: skipEval,
        };
        if (modelVal) req.model = modelVal;
        if (maxTurnsVal > 0) req.max_turns = maxTurnsVal;
        if (timeoutVal > 0) req.timeout_minutes = timeoutVal;
        var key = req.task_id + '__' + req.persona_id;

        // [DIAG] Log click with full state snapshot
        var _prevInfo = _singleState.jobs[key];
        console.log('%c[RUN_CLICK] key=' + key + ' prevStatus=' + (_prevInfo ? _prevInfo.status : 'NEW') +
          ' allJobs=[' + Object.keys(_singleState.jobs).map(function (k) { return k + ':' + _singleState.jobs[k].status; }).join(', ') + ']' +
          ' lastProcessedSeq=' + _lastProcessedSeq, 'color:#9b59b6;font-weight:bold');

        // Mutual exclusion: same config already running or stopping
        var _curStatus = _singleState.jobs[key] && _singleState.jobs[key].status;
        if (_curStatus === 'running' || _curStatus === 'stopping') {
          showModal('Already Running', '<p>' + QTB.escapeHtml(req.task_id) + ' / ' + QTB.escapeHtml(req.persona_id) + ' is already ' + _curStatus + '.</p>');
          return;
        }

        // Register in state
        _singleState.jobs[key] = { status: 'pending', error: null, duration: null, req: req, startTime: null, endData: null };
        _singleJobBuffers[key] = [];
        _addSingleJobRow(key, req);

        var sb = app.querySelector('#run-status-bar');
        console.log('%c[RUN_POST] sending POST /run key=' + key, 'color:#9b59b6');
        apiPost('/run', req).then(function () {
          console.log('%c[RUN_POST] response OK key=' + key + ' jobStatus=' + (_singleState.jobs[key] ? _singleState.jobs[key].status : '?'), 'color:#9b59b6');
          if (sb) sb.textContent = 'Started: ' + req.task_id;
          _updateRunButtonState(app);
          _updateSingleToolbar();
        }).catch(function (err) {
          console.log('%c[RUN_POST] response ERROR key=' + key + ' err=' + err.message, 'color:#e74c3c');
          if (sb) sb.textContent = 'Error: ' + err.message;
          _singleState.jobs[key].status = 'error';
          _singleState.jobs[key].error = err.message;
          var row = app.querySelector('#rs-job-' + CSS.escape(key));
          if (row) {
            var st = row.querySelector('.rg-job-status');
            if (st) { st.textContent = '\u2717'; st.className = 'rg-job-status rg-error'; }
            var detail = row.querySelector('.rs-job-detail');
            if (detail) detail.textContent = 'Error: ' + err.message.slice(0, 60);
            var rerunBtn = row.querySelector('.rs-rerun-btn');
            if (rerunBtn) rerunBtn.disabled = false;
          }
          _updateSingleToolbar();
        });
      });
    }

    // Toolbar: Select all
    var selectAll = app.querySelector('#rs-select-all');
    if (selectAll) {
      selectAll.addEventListener('change', function () {
        app.querySelectorAll('.rs-checkbox').forEach(function (cb) { cb.checked = selectAll.checked; });
      });
    }

    // Toolbar: Rerun Selected
    var rerunSelBtn = app.querySelector('#rs-rerun-selected');
    if (rerunSelBtn) {
      rerunSelBtn.addEventListener('click', function () {
        var checked = Array.from(app.querySelectorAll('.rs-checkbox:checked'));
        if (checked.length === 0) { showModal('Notice', '<p>Please select tasks.</p>'); return; }
        var rerunKeys = checked.map(function (cb) {
          var row = cb.closest('.rs-job');
          return row && row.dataset.key;
        }).filter(function (k) {
          return k && _singleState.jobs[k] && (_singleState.jobs[k].status === 'error' || _singleState.jobs[k].status === 'cancelled');
        });
        if (rerunKeys.length === 0) { showModal('Notice', '<p>No failed/cancelled tasks among selected.</p>'); return; }
        confirmDialog('Rerun ' + rerunKeys.length + ' task(s)?', 'Yes, rerun', 'No, cancel').then(function (yes) {
          if (!yes) return;
          rerunKeys.forEach(function (k) { _rerunSingleJob(k); });
        });
      });
    }

    // Toolbar: Stop Selected
    var stopSelBtn = app.querySelector('#rs-stop-selected');
    if (stopSelBtn) {
      stopSelBtn.addEventListener('click', function () {
        var checked = Array.from(app.querySelectorAll('.rs-checkbox:checked'));
        if (checked.length === 0) { showModal('Notice', '<p>Please select tasks.</p>'); return; }
        var runningKeys = checked.map(function (cb) {
          var row = cb.closest('.rs-job');
          return row && row.dataset.key;
        }).filter(function (k) {
          return k && _singleState.jobs[k] && _singleState.jobs[k].status === 'running';
        });
        if (runningKeys.length === 0) { showModal('Notice', '<p>No running tasks among selected.</p>'); return; }
        confirmDialog('Stop ' + runningKeys.length + ' running task(s)?', 'Yes, stop', 'No, cancel').then(function (yes) {
          if (!yes) return;
          runningKeys.forEach(function (k) {
            // Update state to stopping
            if (_singleState.jobs[k]) _singleState.jobs[k].status = 'stopping';
            // Update each row to stopping visual state
            var row = app.querySelector('#rs-job-' + CSS.escape(k));
            if (row) {
              var stEl = row.querySelector('.rg-job-status');
              if (stEl) { stEl.textContent = '\u29D7'; stEl.className = 'rg-job-status rg-stopping'; }
              var detailEl = row.querySelector('.rs-job-detail');
              if (detailEl) detailEl.textContent = 'Stopping\u2026';
              var sBtn = row.querySelector('.rs-stop-btn');
              if (sBtn) { sBtn.disabled = true; sBtn.textContent = '...'; }
            }
            apiPost('/run/stop', { job_key: k }).catch(function () {});
          });
          _updateRunButtonState(app);
          // Show stopping overlay + update badge if viewing a stopped task's detail
          if (_singleDetailKey && runningKeys.indexOf(_singleDetailKey) !== -1) {
            var detailScope = _subPages['/run/single/' + _singleDetailKey] || document;
            _showStoppingOverlay(detailScope);
            var badge = detailScope.querySelector('#rsd-status-badge');
            if (badge) { badge.textContent = 'stopping'; badge.className = 'rgd-status-badge rgd-stopping'; }
          }
        });
      });
    }

    // Restore existing job rows if returning to this page
    var keys = Object.keys(_singleState.jobs);
    // [DIAG]
    if (keys.length > 0) console.log('%c[RESTORE] showRun restoring ' + keys.length + ' rows: ' + keys.map(function (k) { return k + ':' + _singleState.jobs[k].status; }).join(', '), 'color:#1abc9c;font-weight:bold');
    if (keys.length > 0) {
      keys.forEach(function (k) {
        var info = _singleState.jobs[k];
        if (info && info.req) {
          _addSingleJobRow(k, info.req);
          // Sync row state
          var row = app.querySelector('#rs-job-' + CSS.escape(k));
          if (row) {
            var st = row.querySelector('.rg-job-status');
            var detail = row.querySelector('.rs-job-detail');
            var stopBtn = row.querySelector('.rs-stop-btn');
            var rerunBtn = row.querySelector('.rs-rerun-btn');
            if (info.status === 'running') {
              if (st) { st.textContent = '\u25CF'; st.className = 'rg-job-status rg-running'; }
              if (stopBtn) stopBtn.disabled = false;
            } else if (info.status === 'done') {
              if (st) { st.textContent = '\u2713'; st.className = 'rg-job-status rg-done'; }
              var dt = '';
              if (info.duration) dt += Math.round(info.duration) + 's';
              if (detail) detail.textContent = dt;
            } else if (info.status === 'error' || info.status === 'cancelled') {
              if (st) { st.textContent = '\u2717'; st.className = 'rg-job-status rg-error'; }
              if (detail) detail.textContent = info.error === 'cancelled' ? 'Cancelled' : ('Error: ' + (info.error || '').slice(0, 60));
              if (rerunBtn) rerunBtn.disabled = false;
            }
          }
        }
      });
      _updateSingleToolbar();
    }
  }

  /** Disable Run button if same task+persona is already running or stopping. */
  function _updateRunButtonState(app) {
    var taskSel = app.querySelector('#run-task');
    var personaSel = app.querySelector('#run-persona');
    var btnRun = app.querySelector('#btn-run');
    if (!taskSel || !personaSel || !btnRun) return;
    var key = taskSel.value + '__' + personaSel.value;
    var info = _singleState.jobs[key];
    btnRun.disabled = !!(info && (info.status === 'running' || info.status === 'stopping'));
  }

  function injectPanelStructure(scope) {
    var layout = scope.querySelector('.run-layout');

    // For run-config: wrap children in panel-body, add header
    var config = scope.querySelector('.run-config');
    if (config && !config.querySelector('.panel-header')) {
      var children = Array.from(config.children);
      var header = document.createElement('div');
      header.className = 'panel-header';
      header.textContent = 'Config';
      var body = document.createElement('div');
      body.className = 'panel-body';
      children.forEach(function (c) { body.appendChild(c); });
      config.innerHTML = '';
      config.appendChild(header);
      config.appendChild(body);
    }

    // Add side tabs to layout
    if (layout) {
      if (!scope.querySelector('#reopen-config')) {
        var tabConfig = document.createElement('div');
        tabConfig.className = 'panel-reopen-tab tab-left';
        tabConfig.id = 'reopen-config';
        tabConfig.innerHTML = '<span class="tab-arrow">\u25C0</span><span class="tab-label">Config</span>';
        layout.appendChild(tabConfig);
      }
      if (!scope.querySelector('#reopen-tools') && scope.querySelector('.run-tool-panel')) {
        var tabTools = document.createElement('div');
        tabTools.className = 'panel-reopen-tab tab-right';
        tabTools.id = 'reopen-tools';
        tabTools.innerHTML = '<span class="tab-arrow">\u25B6</span><span class="tab-label">Tools</span>';
        layout.appendChild(tabTools);
      }
    }

    bindSideTab(scope, 'reopen-config', '.run-config', true);
    bindSideTab(scope, 'reopen-tools', '.run-tool-panel', false);
  }

  // ── Page: Run Select ───────────────────────────────────────
  function showRunSelect(app) {
    renderTemplate(app, 'tpl-run-select');
  }

  // ── Page: Run Group ──────────────────────────────────────
  var _groupState = {
    running: false,
    jobs: {},         // "task_id__persona_id" → { status, error, scores, duration }
    totalJobs: 0,
    completedJobs: 0,
    startTime: null,
    timerInterval: null,
  };

  /** Update #rg-model select options based on selected agent. */
  function _updateGroupModelSelect(app) {
    var agentSel = app.querySelector('#rg-agent');
    var modelSel = app.querySelector('#rg-model');
    if (!agentSel || !modelSel) return;
    var agent = agentSel.value;
    var models = _agentDefaults[agent] || [];
    modelSel.innerHTML = '<option value="">' + (models.length > 0 ? models[0] + ' (default)' : 'Default') + '</option>';
    models.forEach(function (m, i) {
      if (i === 0) return;
      var opt = document.createElement('option');
      opt.value = m;
      opt.textContent = m;
      modelSel.appendChild(opt);
    });
  }

  function showRunGroup(app) {
    renderTemplate(app, 'tpl-run-group');
    injectPanelStructure(app);

    // Populate category groups
    var _groupTasks = [];
    api('/tasks').then(function (tasks) {
      _groupTasks = tasks;
      var sel = app.querySelector('#rg-group');
      if (!sel) return;
      var cats = {};
      tasks.forEach(function (t) { cats[t.category || 'other'] = true; });
      var catList = Object.keys(cats).sort();
      sel.innerHTML = catList.map(function (c) {
        var label = CATEGORY_LABELS[c] || c.replace(/_/g, ' ');
        return '<option value="' + c + '">' + QTB.escapeHtml(label) + ' (' + c + ')</option>';
      }).join('');

      function _updateGroupDefaults() {
        var group = sel.value;
        var grouped = _groupTasks.filter(function (t) { return t.category === group; });
        var mtEl = app.querySelector('#rg-max-turns');
        var toEl = app.querySelector('#rg-timeout');
        if (grouped.length === 0) {
          if (mtEl) { mtEl.value = ''; mtEl.placeholder = ''; }
          if (toEl) { toEl.value = ''; toEl.placeholder = ''; }
          return;
        }
        var turns = grouped.map(function (t) { return t.max_turns || 0; }).filter(function (v) { return v > 0; });
        var timeouts = grouped.map(function (t) { return t.timeout_minutes || 0; }).filter(function (v) { return v > 0; });
        if (mtEl) {
          mtEl.value = '';
          if (turns.length > 0) {
            var mn = Math.min.apply(null, turns), mx = Math.max.apply(null, turns);
            mtEl.placeholder = (mn === mx ? '' + mn : mn + '–' + mx) + ' (Task Default)';
          } else { mtEl.placeholder = ''; }
        }
        if (toEl) {
          toEl.value = '';
          if (timeouts.length > 0) {
            var tmn = Math.min.apply(null, timeouts), tmx = Math.max.apply(null, timeouts);
            toEl.placeholder = (tmn === tmx ? '' + tmn : tmn + '–' + tmx) + ' (Task Default)';
          } else { toEl.placeholder = ''; }
        }
      }

      sel.addEventListener('change', _updateGroupDefaults);
      _updateGroupDefaults();
    }).catch(function () {});

    // Populate personas
    api('/personas').then(function (personas) {
      var sel = app.querySelector('#rg-persona');
      if (!sel) return;
      sel.innerHTML = '<option value="">All (from task definition)</option>' +
        personas.map(function (p) {
          return '<option value="' + p.persona_id + '">' + p.persona_id + '</option>';
        }).join('');
    }).catch(function () {});

    // Wire up agent→model cascade
    var agentSel = app.querySelector('#rg-agent');
    if (agentSel) agentSel.addEventListener('change', function () { _updateGroupModelSelect(app); });
    api('/agent-models').then(function (models) {
      _agentDefaults = models;
      _updateGroupModelSelect(app);
    }).catch(function () {
      _updateGroupModelSelect(app);
    });

    var btn = app.querySelector('#btn-run-group');
    if (btn) {
      btn.addEventListener('click', function () {
        // Clear previous buffers on new run
        _groupJobBuffers = {};

        var rgMt = app.querySelector('#rg-max-turns').value.trim();
        var rgTo = app.querySelector('#rg-timeout').value.trim();
        var rgMode = app.querySelector('input[name="rg-mode"]:checked');
        var skipEval = !rgMode || rgMode.value === 'runonly';
        var body = {
          group: app.querySelector('#rg-group').value,
          agent: app.querySelector('#rg-agent').value,
          persona: app.querySelector('#rg-persona').value || null,
          docker: true,
          workers: parseInt(app.querySelector('#rg-workers').value) || 3,
          skip_eval: skipEval,
        };
        var modelVal = app.querySelector('#rg-model').value;
        if (modelVal) body.model = modelVal;
        if (rgMt) body.max_turns = parseInt(rgMt);
        if (rgTo) body.timeout_minutes = parseInt(rgTo);
        btn.disabled = true;
        btn.textContent = 'Starting...';
        var sb = app.querySelector('#rg-status-bar');
        apiPost('/run-group', body).then(function (res) {
          btn.textContent = 'Running...';
          if (sb) sb.textContent = 'Group run started: ' + body.group + ' (' + (res.total_jobs || '?') + ' jobs)';
        }).catch(function (err) {
          btn.disabled = false;
          btn.textContent = 'Run Group';
          if (sb) sb.textContent = 'Error: ' + err.message;
        });
      });
    }

    var btnStop = app.querySelector('#btn-stop-group');
    if (btnStop) {
      btnStop.addEventListener('click', function () {
        confirmDialog('Stop the group run?', 'Yes, stop', 'No, cancel').then(function (yes) {
          if (!yes) return;
          btnStop.disabled = true;
          btnStop.textContent = 'Stopping...';
          _showStoppingOverlay(app);
          apiPost('/run-group/stop', {}).catch(function () {
            btnStop.disabled = false;
            btnStop.textContent = 'Stop';
            _removeStoppingOverlay(app);
          });
        });
      });
    }
  }

  /** Get the run-group sub-page scope for SSE handlers. */
  function _getGroupScope() {
    return _subPages['/run/group'] || document;
  }

  function onGroupStart(data) {
    _groupState.running = true;
    _groupState.jobs = {};
    _groupState.totalJobs = data.total_jobs || 0;
    _groupState.completedJobs = 0;
    _groupState.startTime = Date.now();

    var gs = _getGroupScope();
    var btnStop = gs.querySelector('#btn-stop-group');
    if (btnStop) { btnStop.disabled = false; btnStop.textContent = 'Stop'; }
    var btnRun = gs.querySelector('#btn-run-group');
    if (btnRun) { btnRun.disabled = true; btnRun.textContent = 'Running...'; }

    // Update template elements (already exist in DOM from tpl-run-group)
    var counter = gs.querySelector('#rg-counter');
    if (counter) counter.textContent = '0 / ' + _groupState.totalJobs;
    var timerEl = gs.querySelector('#rg-timer');
    if (timerEl) timerEl.textContent = '0s';
    var progressBar = gs.querySelector('#rg-progress-bar');
    if (progressBar) progressBar.style.display = '';
    var fill = gs.querySelector('#rg-fill');
    if (fill) fill.style.width = '0%';

    // Build job rows in the job area
    var jobArea = gs.querySelector('#rg-progress');
    if (jobArea && data.jobs) {
      var listHtml = '<div class="rg-job-list" id="rg-job-list">' +
        data.jobs.map(function (j) {
          var key = j.task_id + '__' + j.persona_id;
          _groupState.jobs[key] = { status: 'pending' };
          _groupJobBuffers[key] = _groupJobBuffers[key] || [];
          return '<div class="rg-job" id="rg-job-' + key + '" data-key="' + key + '">' +
            '<span class="rg-job-status rg-pending">\u25CB</span>' +
            '<span class="rg-job-task">' + QTB.escapeHtml(j.task_id) + '</span>' +
            '<span class="rg-job-persona">' + QTB.escapeHtml(j.persona_id) + '</span>' +
            '<span class="rg-job-detail"></span>' +
          '</div>';
        }).join('') +
      '</div>';
      jobArea.innerHTML = listHtml;

      // Click handler: navigate to detail view
      jobArea.querySelectorAll('.rg-job').forEach(function (row) {
        row.addEventListener('click', function () {
          var key = row.dataset.key;
          window.location.hash = '#/run/group/' + key;
        });
      });
    }

    // Start timer
    if (_groupState.timerInterval) clearInterval(_groupState.timerInterval);
    _groupState.timerInterval = setInterval(function () {
      if (!_groupState.startTime) return;
      var s = Math.round((Date.now() - _groupState.startTime) / 1000);
      var m = Math.floor(s / 60);
      var te = gs.querySelector('#rg-timer');
      if (te) te.textContent = (m > 0 ? m + 'm ' : '') + (s % 60) + 's';
    }, 1000);
  }

  function onGroupTaskStart(data) {
    var key = data.task_id + '__' + data.persona_id;
    if (_groupState.jobs[key]) _groupState.jobs[key].status = 'running';
    var gs = _getGroupScope();
    var row = gs.querySelector('#rg-job-' + key);
    if (row) {
      var st = row.querySelector('.rg-job-status');
      if (st) { st.textContent = '\u25CF'; st.className = 'rg-job-status rg-running'; }
    }
    // Update detail view status badge if viewing this job
    if (_groupDetailKey === key) {
      var scope = _subPages['/run/group/' + key] || document;
      var badge = scope.querySelector('#rgd-status-badge');
      if (badge) { badge.textContent = 'running'; badge.className = 'rgd-status-badge rgd-running'; }
    }
  }

  function onGroupTaskEnd(data) {
    var key = data.task_id + '__' + data.persona_id;
    _groupState.completedJobs++;
    var prevStatus = _groupState.jobs[key] ? _groupState.jobs[key].status : 'pending';
    if (_groupState.jobs[key]) {
      _groupState.jobs[key].status = data.error ? 'error' : 'done';
      _groupState.jobs[key].error = data.error;
      _groupState.jobs[key].duration = data.duration;
    }

    var gs = _getGroupScope();
    var row = gs.querySelector('#rg-job-' + key);
    if (row) {
      var st = row.querySelector('.rg-job-status');
      var detail = row.querySelector('.rg-job-detail');
      if (data.error === 'cancelled') {
        var wasRunning = prevStatus === 'running';
        if (wasRunning) {
          if (st) { st.textContent = '\u2717'; st.className = 'rg-job-status rg-error'; }
          if (detail) detail.textContent = 'Cancelled';
        } else {
          if (st) { st.textContent = '\u2014'; st.className = 'rg-job-status rg-pending'; }
          if (detail) detail.textContent = '';
        }
      } else if (data.error) {
        if (st) { st.textContent = '\u2717'; st.className = 'rg-job-status rg-error'; }
        if (detail) detail.textContent = 'Error: ' + data.error.slice(0, 60);
      } else {
        if (st) { st.textContent = '\u2713'; st.className = 'rg-job-status rg-done'; }
        var detailText = '';
        if (data.duration) detailText += Math.round(data.duration) + 's';
        if (data.scores) detailText += ' \u00B7 OAS: ' + (data.scores.oas != null ? data.scores.oas.toFixed(4) : '-');
        if (detail) detail.textContent = detailText;
      }
    }

    // Update progress bar + counter
    var pct = _groupState.totalJobs > 0 ? Math.round((_groupState.completedJobs / _groupState.totalJobs) * 100) : 0;
    var fill = gs.querySelector('#rg-fill');
    if (fill) fill.style.width = pct + '%';
    var counter = gs.querySelector('#rg-counter');
    if (counter) counter.textContent = _groupState.completedJobs + ' / ' + _groupState.totalJobs;

    // Update detail view status badge if viewing this job
    if (_groupDetailKey === key) {
      var scope = _subPages['/run/group/' + key] || document;
      var badge = scope.querySelector('#rgd-status-badge');
      if (badge) {
        if (data.error) {
          badge.textContent = 'error';
          badge.className = 'rgd-status-badge rgd-error';
        } else {
          badge.textContent = 'done';
          badge.className = 'rgd-status-badge rgd-done';
        }
      }
      var elapsedEl = scope.querySelector('#rgd-elapsed');
      if (elapsedEl && data.duration) {
        var ds = Math.round(data.duration);
        var dm = Math.floor(ds / 60);
        elapsedEl.textContent = (dm > 0 ? dm + 'm ' : '') + (ds % 60) + 's';
      }
    }
  }

  function onGroupEnd(data) {
    _groupState.running = false;
    if (_groupState.timerInterval) { clearInterval(_groupState.timerInterval); _groupState.timerInterval = null; }
    var gs = _getGroupScope();

    // Remove stopping overlay
    _removeStoppingOverlay(gs);

    // Clean up thinking indicators on active detail view
    if (_groupDetailKey) {
      var detailScope = _subPages['/run/group/' + _groupDetailKey] || document;
      var chatEl = detailScope.querySelector('#rgd-chat');
      if (chatEl) { QTB.hideThinking(chatEl); QTB.hideResponding(chatEl); }
    }

    var btn = gs.querySelector('#btn-run-group');
    if (btn) { btn.disabled = false; btn.textContent = 'Run Group'; }
    var btnStop = gs.querySelector('#btn-stop-group');
    if (btnStop) { btnStop.disabled = true; btnStop.textContent = 'Stop'; }
    var sb = gs.querySelector('#rg-status-bar');

    if (data.error === 'cancelled') {
      if (sb) sb.textContent = 'Cancelled: ' + (data.ok_count || 0) + ' completed, ' + (data.err_count || 0) + ' errors';
      var countdown = 5;
      var cancelHtml =
        '<div style="text-align:center;padding:20px 0">' +
          '<div style="font-size:40px;margin-bottom:12px">\u25A0</div>' +
          '<p style="font-size:16px;font-weight:600;margin-bottom:8px">Group Run Cancelled</p>' +
          '<p style="color:var(--text-muted);font-size:13px">' +
            (data.ok_count || 0) + ' job(s) completed, ' + (data.err_count || 0) + ' errors</p>' +
          '<p style="color:var(--text-muted);font-size:12px;margin-top:16px">' +
            'Auto-closing in <span id="modal-countdown-cancel">' + countdown + '</span>s</p>' +
        '</div>';
      showModal('Group Run Cancelled', cancelHtml);
      var _cancelTimer = setInterval(function () {
        countdown--;
        var cdEl = document.getElementById('modal-countdown-cancel');
        if (cdEl) cdEl.textContent = countdown;
        if (countdown <= 0) { clearInterval(_cancelTimer); closeModal(); }
      }, 1000);
      return;
    }

    if (sb) sb.textContent = 'Completed: ' + (data.ok_count || 0) + ' OK, ' + (data.err_count || 0) + ' errors';

    // Fill progress bar to 100%
    var fill = gs.querySelector('#rg-fill');
    if (fill) fill.style.width = '100%';

    // Show modal summary
    var body = '<p>Group run complete</p>' +
      '<div class="eval-modal-scores">' +
        '<div class="eval-modal-score"><span class="ems-label">Total</span><span class="ems-value">' + (data.total || 0) + '</span></div>' +
        '<div class="eval-modal-score"><span class="ems-label">OK</span><span class="ems-value">' + (data.ok_count || 0) + '</span></div>' +
        '<div class="eval-modal-score"><span class="ems-label">Errors</span><span class="ems-value">' + (data.err_count || 0) + '</span></div>' +
      '</div>';
    showModal('Group Run Complete', body);
  }

  // ── Page: Run Group Detail ──────────────────────────────

  function showRunGroupDetail(app, jobKey) {
    renderTemplate(app, 'tpl-run-group-detail');
    injectPanelStructure(app);
    _groupDetailKey = jobKey;

    var parts = jobKey.split('__');
    var taskId = parts[0] || jobKey;
    var personaId = parts[1] || '';

    // Fill read-only meta fields
    var taskEl = app.querySelector('#rgd-task-id');
    if (taskEl) taskEl.textContent = taskId;
    var personaEl = app.querySelector('#rgd-persona-id');
    if (personaEl) personaEl.textContent = personaId;

    // Update status badge from group state
    var jobInfo = _groupState.jobs[jobKey] || {};
    var badge = app.querySelector('#rgd-status-badge');
    if (badge) {
      var st = jobInfo.status || 'pending';
      badge.textContent = st;
      badge.className = 'rgd-status-badge rgd-' + (st === 'done' ? 'done' : st === 'error' ? 'error' : st === 'running' ? 'running' : 'pending');
    }

    // Show elapsed if completed
    var elapsedEl = app.querySelector('#rgd-elapsed');
    if (elapsedEl && jobInfo.duration) {
      var s = Math.round(jobInfo.duration);
      var m = Math.floor(s / 60);
      elapsedEl.textContent = (m > 0 ? m + 'm ' : '') + (s % 60) + 's';
    }

    // Replay buffered events
    var chatEl = app.querySelector('#rgd-chat');
    var toolsEl = app.querySelector('#rgd-tools');
    if (chatEl) QTB.clearChat(chatEl);
    if (toolsEl) QTB.clearTools(toolsEl);

    var buffer = _groupJobBuffers[jobKey] || [];
    var msgCount = 0, toolCount = 0;

    buffer.forEach(function (evt) {
      switch (evt.type) {
        case 'student_message':
          msgCount++;
          if (chatEl) {
            QTB.hideResponding(chatEl);
            QTB.addChatMessage(chatEl, 'student', evt.content || '', null, null);
            QTB.showThinking(chatEl, 'Thinking...');
          }
          break;
        case 'tutor_response':
          msgCount++;
          if (chatEl) {
            QTB.hideThinking(chatEl);
            QTB.hideResponding(chatEl);
            QTB.addChatMessage(chatEl, 'tutor', evt.content || '', evt.content_blocks || null, null);
          }
          break;
        case 'tool_start':
          toolCount++;
          if (toolsEl) QTB.addToolStart(toolsEl, evt);
          if (chatEl) QTB.updateThinking(chatEl, 'Using ' + (evt.name || 'tool') + '...');
          break;
        case 'tool_result':
          if (toolsEl) QTB.updateToolResult(toolsEl, evt);
          break;
      }
    });

    // Show appropriate indicator based on last event and job status
    if (jobInfo.status === 'running' && msgCount > 0 && chatEl) {
      var lastEvt = buffer[buffer.length - 1];
      if (lastEvt) {
        if (lastEvt.type === 'student_message') {
          QTB.showThinking(chatEl, 'Thinking...');
        } else if (lastEvt.type === 'tool_start') {
          QTB.showThinking(chatEl, 'Using ' + (lastEvt.name || 'tool') + '...');
        } else if (lastEvt.type === 'tool_result') {
          QTB.showThinking(chatEl, 'Thinking...');
        } else if (lastEvt.type === 'tutor_response') {
          QTB.showResponding(chatEl);
        }
      }
    } else if (chatEl) {
      // Job finished — clean up any lingering indicators
      QTB.hideThinking(chatEl);
      QTB.hideResponding(chatEl);
    }

    // Update counters
    var msgCountEl = app.querySelector('#rgd-msg-count');
    var toolCountEl = app.querySelector('#rgd-tool-total');
    var turnCountEl = app.querySelector('#rgd-turn-count');
    if (msgCountEl) msgCountEl.textContent = 'Messages: ' + msgCount;
    if (toolCountEl) toolCountEl.textContent = 'Tools: ' + toolCount;
    if (turnCountEl) turnCountEl.textContent = Math.ceil(msgCount / 2);

    // Auto-expand tool panel if there are tools
    if (toolCount > 0) {
      var toolPanel = app.querySelector('.run-tool-panel');
      if (toolPanel) toolPanel.classList.remove('collapsed');
    }
  }

  /**
   * Render a single event into the currently open group detail view (live).
   * Called by handleSSEEvent when _groupDetailKey matches the event's job_key.
   */
  function _renderGroupDetailEvent(data) {
    var scope = _subPages['/run/group/' + _groupDetailKey] || document;
    var chatEl = scope.querySelector('#rgd-chat');
    var toolsEl = scope.querySelector('#rgd-tools');
    var msgCountEl = scope.querySelector('#rgd-msg-count');
    var toolCountEl = scope.querySelector('#rgd-tool-total');
    var turnCountEl = scope.querySelector('#rgd-turn-count');

    switch (data.type) {
      case 'student_message':
        if (chatEl) {
          QTB.hideResponding(chatEl);
          QTB.addChatMessage(chatEl, 'student', data.content || '', null, null);
          QTB.showThinking(chatEl, 'Thinking...');
        }
        break;
      case 'tutor_response':
        if (chatEl) {
          QTB.hideThinking(chatEl);
          QTB.hideResponding(chatEl);
          QTB.addChatMessage(chatEl, 'tutor', data.content || '', data.content_blocks || null, null);
          rewriteLiveImages(chatEl);
          QTB.showResponding(chatEl);
        }
        break;
      case 'tool_start':
        if (toolsEl) {
          QTB.addToolStart(toolsEl, data);
          var toolPanel = scope.querySelector('.run-tool-panel');
          if (toolPanel) {
            toolPanel.classList.remove('collapsed');
            var reopenTab = scope.querySelector('#reopen-tools');
            if (reopenTab) {
              var arrow = reopenTab.querySelector('.tab-arrow');
              if (arrow) arrow.textContent = '\u25B6';
            }
          }
        }
        if (chatEl) QTB.updateThinking(chatEl, 'Using ' + (data.name || 'tool') + '...');
        break;
      case 'tool_result':
        if (toolsEl) QTB.updateToolResult(toolsEl, data);
        break;
      case 'session_end':
        if (chatEl) { QTB.hideThinking(chatEl); QTB.hideResponding(chatEl); }
        var badge = scope.querySelector('#rgd-status-badge');
        if (badge) {
          if (data.error) {
            badge.textContent = 'error';
            badge.className = 'rgd-status-badge rgd-error';
          } else {
            badge.textContent = 'done';
            badge.className = 'rgd-status-badge rgd-done';
          }
        }
        break;
    }

    // Update counters from buffer length
    if (_groupDetailKey) {
      var buf = _groupJobBuffers[_groupDetailKey] || [];
      var mc = 0, tc = 0;
      buf.forEach(function (e) {
        if (e.type === 'student_message' || e.type === 'tutor_response') mc++;
        if (e.type === 'tool_start') tc++;
      });
      if (msgCountEl) msgCountEl.textContent = 'Messages: ' + mc;
      if (toolCountEl) toolCountEl.textContent = 'Tools: ' + tc;
      if (turnCountEl) turnCountEl.textContent = Math.ceil(mc / 2);
    }
  }

  // ── Page: Results (Source → Agent → Model → Category → Items) ──

  var SOURCE_LABELS = { 'run-single': 'Single', 'run-group': 'Group' };
  var SOURCE_SVG = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">' +
    '<rect x="2" y="3" width="20" height="18" rx="2"/><path d="M2 9h20"/><path d="M9 21V9"/></svg>';

  function showResultSources(app) {
    renderTemplate(app, 'tpl-results');
    var listEl = app.querySelector('#results-list');
    api('/results').then(function (results) {
      if (results.length === 0) { listEl.innerHTML = '<div class="empty-state">No saved results</div>'; return; }
      var bySource = {};
      results.forEach(function (r) {
        var s = r.source || 'run-single';
        if (!bySource[s]) bySource[s] = [];
        bySource[s].push(r);
      });
      var sources = Object.keys(bySource).sort();
      listEl.className = 'folder-grid';
      listEl.innerHTML = sources.map(function (s) {
        var count = bySource[s].length;
        var scored = bySource[s].filter(function (r) { return r.has_scores; }).length;
        var unscored = count - scored;
        var countText = count + ' result' + (count !== 1 ? 's' : '');
        if (scored > 0) countText += ' \u00B7 ' + scored + ' scored';
        if (unscored > 0) countText += ' \u00B7 <span style="color:var(--accent)">' + unscored + ' unscored</span>';
        return '<div class="folder-card" data-source="' + QTB.escapeHtml(s) + '">' +
          '<div class="folder-icon">' + SOURCE_SVG + '</div>' +
          '<div class="folder-info">' +
            '<div class="folder-name">' + QTB.escapeHtml(SOURCE_LABELS[s] || s) + '</div>' +
            '<div class="folder-count">' + countText + '</div>' +
          '</div>' +
        '</div>';
      }).join('');
      listEl.querySelectorAll('.folder-card').forEach(function (card) {
        card.addEventListener('click', function () {
          window.location.hash = '#/results/s/' + card.getAttribute('data-source');
        });
      });
    }).catch(function (err) {
      listEl.innerHTML = '<div class="empty-state">Error: ' + err.message + '</div>';
    });
  }

  function showResultAgents(app, source) {
    renderTemplate(app, 'tpl-results');
    var listEl = app.querySelector('#results-list');
    var header = app.querySelector('.page-header');
    var sourceLabel = SOURCE_LABELS[source] || source;
    if (header) {
      header.innerHTML = buildBreadcrumb([
        {label: 'Results', href: '#/results'},
        {label: sourceLabel},
      ]);
    }
    api('/results').then(function (results) {
      var filtered = results.filter(function (r) { return (r.source || 'run-single') === source; });
      if (filtered.length === 0) { listEl.innerHTML = '<div class="empty-state">No results in ' + sourceLabel + '</div>'; return; }
      var byAgent = {};
      filtered.forEach(function (r) {
        var a = r.agent || 'unknown';
        if (!byAgent[a]) byAgent[a] = [];
        byAgent[a].push(r);
      });
      var agents = Object.keys(byAgent).sort();
      listEl.className = 'folder-grid';
      listEl.innerHTML = agents.map(function (a) {
        var count = byAgent[a].length;
        var scored = byAgent[a].filter(function (r) { return r.has_scores; }).length;
        var unscored = count - scored;
        var countText = count + ' result' + (count !== 1 ? 's' : '');
        if (scored > 0) countText += ' \u00B7 ' + scored + ' scored';
        if (unscored > 0) countText += ' \u00B7 <span style="color:var(--accent)">' + unscored + ' unscored</span>';
        return '<div class="folder-card" data-agent="' + QTB.escapeHtml(a) + '">' +
          '<div class="folder-icon">' + FOLDER_SVG + '</div>' +
          '<div class="folder-info">' +
            '<div class="folder-name">' + QTB.escapeHtml(a) + '</div>' +
            '<div class="folder-count">' + countText + '</div>' +
          '</div>' +
        '</div>';
      }).join('');
      listEl.querySelectorAll('.folder-card').forEach(function (card) {
        card.addEventListener('click', function () {
          window.location.hash = '#/results/s/' + source + '/a/' + encodeURIComponent(card.getAttribute('data-agent'));
        });
      });
    }).catch(function (err) {
      listEl.innerHTML = '<div class="empty-state">Error: ' + err.message + '</div>';
    });
  }

  function showResultModels(app, source, agent) {
    renderTemplate(app, 'tpl-results');
    var listEl = app.querySelector('#results-list');
    var header = app.querySelector('.page-header');
    var sourceLabel = SOURCE_LABELS[source] || source;
    if (header) {
      header.innerHTML = buildBreadcrumb([
        {label: 'Results', href: '#/results'},
        {label: sourceLabel, href: '#/results/s/' + source},
        {label: agent},
      ]);
    }
    api('/results').then(function (results) {
      var filtered = results.filter(function (r) {
        return (r.source || 'run-single') === source && r.agent === agent;
      });
      if (filtered.length === 0) { listEl.innerHTML = '<div class="empty-state">No results for ' + agent + '</div>'; return; }
      var byModel = {};
      filtered.forEach(function (r) {
        var m = r.model || 'unknown';
        if (!byModel[m]) byModel[m] = [];
        byModel[m].push(r);
      });
      var models = Object.keys(byModel).sort();
      listEl.className = 'folder-grid';
      listEl.innerHTML = models.map(function (m) {
        var count = byModel[m].length;
        var scored = byModel[m].filter(function (r) { return r.has_scores; }).length;
        var unscored = count - scored;
        var countText = count + ' result' + (count !== 1 ? 's' : '');
        if (scored > 0) countText += ' \u00B7 ' + scored + ' scored';
        if (unscored > 0) countText += ' \u00B7 <span style="color:var(--accent)">' + unscored + ' unscored</span>';
        return '<div class="folder-card" data-model="' + QTB.escapeHtml(m) + '">' +
          '<div class="folder-icon">' + FOLDER_SVG + '</div>' +
          '<div class="folder-info">' +
            '<div class="folder-name">' + QTB.escapeHtml(m) + '</div>' +
            '<div class="folder-count">' + countText + '</div>' +
          '</div>' +
        '</div>';
      }).join('');
      listEl.querySelectorAll('.folder-card').forEach(function (card) {
        card.addEventListener('click', function () {
          window.location.hash = '#/results/s/' + source + '/a/' + encodeURIComponent(agent) + '/m/' + encodeURIComponent(card.getAttribute('data-model'));
        });
      });
    }).catch(function (err) {
      listEl.innerHTML = '<div class="empty-state">Error: ' + err.message + '</div>';
    });
  }

  function showResultCategories(app, source, agent, model) {
    renderTemplate(app, 'tpl-results');
    var listEl = app.querySelector('#results-list');
    var header = app.querySelector('.page-header');
    var sourceLabel = SOURCE_LABELS[source] || source;
    if (header) {
      header.innerHTML = buildBreadcrumb([
        {label: 'Results', href: '#/results'},
        {label: sourceLabel, href: '#/results/s/' + source},
        {label: agent, href: '#/results/s/' + source + '/a/' + encodeURIComponent(agent)},
        {label: model},
      ]);
    }
    api('/results').then(function (results) {
      var filtered = results.filter(function (r) {
        return (r.source || 'run-single') === source && r.agent === agent && r.model === model;
      });
      if (filtered.length === 0) { listEl.innerHTML = '<div class="empty-state">No results for ' + model + '</div>'; return; }
      var groups = {};
      filtered.forEach(function (r) {
        var cat = r.category || 'other';
        if (!groups[cat]) groups[cat] = [];
        groups[cat].push(r);
      });
      var cats = Object.keys(groups).sort();
      listEl.className = 'folder-grid';
      listEl.innerHTML = cats.map(function (cat) {
        var label = CATEGORY_LABELS[cat] || cat.replace(/_/g, ' ');
        var count = groups[cat].length;
        var scored = groups[cat].filter(function (r) { return r.has_scores; }).length;
        var unscored = count - scored;
        var countText = count + ' result' + (count !== 1 ? 's' : '');
        if (scored > 0) countText += ' \u00B7 ' + scored + ' scored';
        if (unscored > 0) countText += ' \u00B7 <span style="color:var(--accent)">' + unscored + ' unscored</span>';
        return '<div class="folder-card" data-cat="' + QTB.escapeHtml(cat) + '">' +
          '<div class="folder-icon">' + FOLDER_SVG + '</div>' +
          '<div class="folder-info">' +
            '<div class="folder-name">' + QTB.escapeHtml(label) + '</div>' +
            '<div class="folder-count">' + countText + '</div>' +
          '</div>' +
        '</div>';
      }).join('');
      listEl.querySelectorAll('.folder-card').forEach(function (card) {
        card.addEventListener('click', function () {
          window.location.hash = '#/results/s/' + source + '/a/' + encodeURIComponent(agent) + '/m/' + encodeURIComponent(model) + '/c/' + card.getAttribute('data-cat');
        });
      });
    }).catch(function (err) {
      listEl.innerHTML = '<div class="empty-state">Error: ' + err.message + '</div>';
    });
  }

  function showResultsInCategory(app, source, agent, model, category) {
    renderTemplate(app, 'tpl-results');
    var listEl = app.querySelector('#results-list');
    var header = app.querySelector('.page-header');
    var sourceLabel = SOURCE_LABELS[source] || source;
    var catLabel = CATEGORY_LABELS[category] || category.replace(/_/g, ' ');

    if (header) {
      header.innerHTML = buildBreadcrumb([
        {label: 'Results', href: '#/results'},
        {label: sourceLabel, href: '#/results/s/' + source},
        {label: agent, href: '#/results/s/' + source + '/a/' + encodeURIComponent(agent)},
        {label: model, href: '#/results/s/' + source + '/a/' + encodeURIComponent(agent) + '/m/' + encodeURIComponent(model)},
        {label: catLabel},
      ]);
    }

    Promise.all([api('/results'), api('/tasks')]).then(function (res) {
      var results = res[0], tasks = res[1];
      var taskMap = {};
      tasks.forEach(function (t) { taskMap[t.task_id] = t; });
      var filtered = results.filter(function (r) {
        return (r.source || 'run-single') === source && r.agent === agent && r.model === model && r.category === category;
      });
      if (filtered.length === 0) { listEl.innerHTML = '<div class="empty-state">No results in this category</div>'; return; }
      listEl.className = 'result-list';
      listEl.innerHTML = filtered.map(function (r) { return buildResultItemHTML(r, taskMap); }).join('');
    }).catch(function (err) {
      listEl.innerHTML = '<div class="empty-state">Error: ' + err.message + '</div>';
    });
  }

  function buildResultItemHTML(r, taskMap) {
    var path = [r.source || 'run-single', r.agent, r.model, r.category, r.task_id, r.persona_id].map(encodeURIComponent).join('/');
    var sourceTag = r.source === 'run-group' ? '<span class="detail-tag detail-tag-source">group</span>' : '';
    var personaTag = r.persona_id ? '<span class="detail-tag ' + _personaTagClass(r.persona_id) + '">' + QTB.escapeHtml(r.persona_id) + '</span>' : '';
    var task = taskMap ? taskMap[r.task_id] : null;
    var desc = task ? (task.description || '') : '';
    return '<a class="result-item" href="#/results/' + path + '">' +
      '<div class="ri-top">' +
        '<span class="ri-task">' + QTB.escapeHtml(r.task_id) + '</span>' +
        '<span class="ri-tags">' + personaTag + sourceTag + '</span>' +
        '<span class="ri-meta">' +
          '<span>' + r.turn_count + ' turns</span>' +
          '<span>' + r.tool_count + ' tools</span>' +
          '<span>' + Math.round(r.duration_seconds || 0) + 's</span>' +
        '</span>' +
        (r.has_scores
          ? '<span class="ri-badge">scored</span>'
          : '<span class="ri-badge ri-badge-unscored">unscored</span>') +
      '</div>' +
      (desc ? '<div class="ri-desc">' + QTB.escapeHtml(desc) + '</div>' : '') +
    '</a>';
  }

  // ── Page: Evaluate Column View (Finder-style) ────────────

  /**
   * Filter Bar + Item List for evaluate results.
   * 4 dropdown filters (Source, Agent, Model, Category) + full-width item list.
   */
  function showEvalColumnView(app, route) {
    // Parse pre-selected filters from URL
    var parts = route.replace('/evaluate/results', '').split('/').filter(Boolean);
    var preSelected = {};
    for (var pi = 0; pi < parts.length - 1; pi += 2) {
      preSelected[parts[pi]] = decodeURIComponent(parts[pi + 1]);
    }

    renderTemplate(app, 'tpl-evaluate');
    var header = app.querySelector('.page-header');
    if (header) {
      header.innerHTML = buildBreadcrumb([
        {label: 'Evaluate', href: '#/evaluate'},
        {label: 'Results'},
      ]);
    }

    // Remove the template's #evaluate-list, build our layout instead
    var oldList = app.querySelector('#evaluate-list');
    var pageEl = oldList ? oldList.parentNode : app;
    if (oldList) oldList.remove();

    // Filter bar
    var filterBar = document.createElement('div');
    filterBar.className = 'eval-filter-bar';
    filterBar.innerHTML =
      '<span class="eval-filter-label">Source</span>' +
      '<div class="eval-filter"><select id="ecv-f-source"><option value="">All</option></select><span class="eval-filter-arrow">\u25BE</span></div>' +
      '<span class="eval-filter-label">Agent</span>' +
      '<div class="eval-filter"><select id="ecv-f-agent"><option value="">All</option></select><span class="eval-filter-arrow">\u25BE</span></div>' +
      '<span class="eval-filter-label">Model</span>' +
      '<div class="eval-filter"><select id="ecv-f-model"><option value="">All</option></select><span class="eval-filter-arrow">\u25BE</span></div>' +
      '<span class="eval-filter-label">Category</span>' +
      '<div class="eval-filter"><select id="ecv-f-category"><option value="">All</option></select><span class="eval-filter-arrow">\u25BE</span></div>';

    // Toolbar
    var toolbar = document.createElement('div');
    toolbar.className = 'eval-browse-toolbar';
    toolbar.innerHTML =
      '<label class="eval-select-all-label">' +
        '<input type="checkbox" id="ecv-select-all"> Select all' +
      '</label>' +
      '<div class="eval-toolbar-actions">' +
        '<button class="btn btn-sm btn-primary" id="ecv-eval-unscored">Evaluate All Unscored</button>' +
        '<button class="btn btn-sm btn-primary" id="ecv-eval-selected">Evaluate Selected</button>' +
        '<button class="btn btn-sm btn-danger" id="ecv-stop-selected">Stop Selected</button>' +
      '</div>' +
      '<span class="eval-toolbar-status" id="ecv-status"></span>';

    // Item list
    var listEl = document.createElement('div');
    listEl.className = 'eval-browse-list';

    pageEl.appendChild(filterBar);
    pageEl.appendChild(toolbar);
    pageEl.appendChild(listEl);

    // State
    var _allResults = [];
    var selSource = filterBar.querySelector('#ecv-f-source');
    var selAgent = filterBar.querySelector('#ecv-f-agent');
    var selModel = filterBar.querySelector('#ecv-f-model');
    var selCategory = filterBar.querySelector('#ecv-f-category');

    api('/results').then(function (results) {
      _allResults = results;
      if (results.length === 0) {
        listEl.innerHTML = '<div class="empty-state">No saved results to evaluate</div>';
        return;
      }

      // Populate filter options from data
      _populateFilters();

      // Apply pre-selected filters from URL
      if (preSelected.s) selSource.value = preSelected.s;
      if (preSelected.a) selAgent.value = preSelected.a;
      if (preSelected.m) selModel.value = preSelected.m;
      if (preSelected.c) selCategory.value = preSelected.c;

      // Cascade: update dependent filters after pre-selection
      _updateDependentOptions();
      _renderItems();
    }).catch(function (err) {
      listEl.innerHTML = '<div class="empty-state">Error: ' + QTB.escapeHtml(err.message) + '</div>';
    });

    // ── Filter logic ──

    function _unique(arr, keyFn) {
      var seen = {};
      var out = [];
      arr.forEach(function (r) {
        var k = keyFn(r);
        if (!seen[k]) { seen[k] = true; out.push(k); }
      });
      return out.sort();
    }

    function _setOptions(sel, values, labelFn, keepValue) {
      var prev = keepValue || sel.value;
      sel.innerHTML = '<option value="">All</option>';
      values.forEach(function (v) {
        var opt = document.createElement('option');
        opt.value = v;
        opt.textContent = labelFn ? labelFn(v) : v;
        sel.appendChild(opt);
      });
      // Restore selection if still valid
      if (prev && Array.from(sel.options).some(function (o) { return o.value === prev; })) {
        sel.value = prev;
      } else {
        sel.value = '';
      }
    }

    function _populateFilters() {
      _setOptions(selSource, _unique(_allResults, function (r) { return r.source || 'run-single'; }), function (v) { return SOURCE_LABELS[v] || v; });
      _updateDependentOptions();
    }

    function _getFilteredByUpstream(level) {
      // level: 0=none, 1=source, 2=source+agent, 3=source+agent+model
      var arr = _allResults;
      if (level >= 1 && selSource.value) arr = arr.filter(function (r) { return (r.source || 'run-single') === selSource.value; });
      if (level >= 2 && selAgent.value) arr = arr.filter(function (r) { return r.agent === selAgent.value; });
      if (level >= 3 && selModel.value) arr = arr.filter(function (r) { return r.model === selModel.value; });
      return arr;
    }

    function _updateDependentOptions() {
      var afterSource = _getFilteredByUpstream(1);
      _setOptions(selAgent, _unique(afterSource, function (r) { return r.agent || 'unknown'; }));

      var afterAgent = _getFilteredByUpstream(2);
      _setOptions(selModel, _unique(afterAgent, function (r) { return r.model || 'unknown'; }));

      var afterModel = _getFilteredByUpstream(3);
      _setOptions(selCategory, _unique(afterModel, function (r) { return r.category || 'other'; }), function (v) { return CATEGORY_LABELS[v] || v.replace(/_/g, ' '); });
    }

    function _getFiltered() {
      var arr = _allResults;
      if (selSource.value) arr = arr.filter(function (r) { return (r.source || 'run-single') === selSource.value; });
      if (selAgent.value) arr = arr.filter(function (r) { return r.agent === selAgent.value; });
      if (selModel.value) arr = arr.filter(function (r) { return r.model === selModel.value; });
      if (selCategory.value) arr = arr.filter(function (r) { return r.category === selCategory.value; });
      return arr;
    }

    function _updateHash() {
      var h = '#/evaluate/results';
      if (selSource.value) h += '/s/' + encodeURIComponent(selSource.value);
      if (selAgent.value) h += '/a/' + encodeURIComponent(selAgent.value);
      if (selModel.value) h += '/m/' + encodeURIComponent(selModel.value);
      if (selCategory.value) h += '/c/' + encodeURIComponent(selCategory.value);
      history.replaceState(null, '', h);
    }

    // Filter change handlers — cascade downstream options
    selSource.addEventListener('change', function () {
      _updateDependentOptions();
      _renderItems();
      _updateHash();
    });
    selAgent.addEventListener('change', function () {
      _updateDependentOptions();
      _renderItems();
      _updateHash();
    });
    selModel.addEventListener('change', function () {
      _updateDependentOptions();
      _renderItems();
      _updateHash();
    });
    selCategory.addEventListener('change', function () {
      _renderItems();
      _updateHash();
    });

    // ── Render items ──

    function _renderItems() {
      var filtered = _getFiltered();
      listEl.innerHTML = '';

      // Reset select-all
      var selectAllCb = toolbar.querySelector('#ecv-select-all');
      if (selectAllCb) { selectAllCb.checked = false; selectAllCb.indeterminate = false; }

      if (filtered.length === 0) {
        listEl.innerHTML = '<div class="empty-state">No results match current filters</div>';
        _updateToolbarCounts(0, 0);
        return;
      }

      filtered.forEach(function (r) {
        var scored = r.has_scores;
        var detailHash = '#/results/' + [r.source || 'run-single', r.agent, r.model, r.category, r.task_id, r.persona_id].map(encodeURIComponent).join('/');
        var item = document.createElement('div');
        item.className = 'result-item eval-item';
        item.innerHTML =
          '<input type="checkbox" class="eval-item-checkbox">' +
          '<a href="' + detailHash + '" class="ri-task ri-task-link">' + QTB.escapeHtml(r.task_id) + '</a>' +
          '<span class="ri-meta">' +
            '<span>' + QTB.escapeHtml(r.agent + ' / ' + r.model) + '</span>' +
            '<span>' + r.turn_count + ' turns</span>' +
            '<span>' + r.tool_count + ' tools</span>' +
          '</span>' +
          '<span class="ri-badge ' + (scored ? '' : 'ri-badge-unscored') + '">' + QTB.escapeHtml(r.persona_id) + '</span>' +
          '<button class="btn btn-sm ' + (scored ? 'btn-secondary' : 'btn-primary') + ' eval-btn"' +
            ' data-source="' + QTB.escapeHtml(r.source || 'run-single') + '"' +
            ' data-agent="' + QTB.escapeHtml(r.agent) + '"' +
            ' data-model="' + QTB.escapeHtml(r.model) + '"' +
            ' data-category="' + QTB.escapeHtml(r.category) + '"' +
            ' data-task-id="' + QTB.escapeHtml(r.task_id) + '"' +
            ' data-persona-id="' + QTB.escapeHtml(r.persona_id) + '"' +
            ' data-scored="' + (scored ? 'true' : 'false') + '">' +
            (scored ? 'Re-evaluate' : 'Evaluate') +
          '</button>';
        listEl.appendChild(item);
      });

      // Bind eval buttons
      listEl.querySelectorAll('.eval-btn').forEach(function (btn) {
        btn.addEventListener('click', function (e) {
          e.preventDefault();
          e.stopPropagation();
          triggerEval({
            source: btn.dataset.source || 'run-single',
            agent: btn.dataset.agent,
            model: btn.dataset.model,
            category: btn.dataset.category,
            task_id: btn.dataset.taskId,
            persona_id: btn.dataset.personaId,
          }, btn);
        });
      });

      var unscored = filtered.filter(function (r) { return !r.has_scores; });
      _updateToolbarCounts(filtered.length, unscored.length);
    }

    function _updateToolbarCounts(total, unscoredCount) {
      var statusEl = toolbar.querySelector('#ecv-status');
      var unscoredBtn = toolbar.querySelector('#ecv-eval-unscored');
      if (statusEl) statusEl.textContent = total + ' items, ' + unscoredCount + ' unscored';
      if (unscoredBtn) unscoredBtn.textContent = 'Evaluate All Unscored (' + unscoredCount + ')';
    }

    // ── Toolbar event handlers ──

    // Select all
    toolbar.querySelector('#ecv-select-all').addEventListener('change', function () {
      var checked = this.checked;
      listEl.querySelectorAll('.eval-item-checkbox').forEach(function (cb) { cb.checked = checked; });
    });
    listEl.addEventListener('change', function (e) {
      if (e.target.classList.contains('eval-item-checkbox')) {
        var all = Array.from(listEl.querySelectorAll('.eval-item-checkbox'));
        var selectAllCb = toolbar.querySelector('#ecv-select-all');
        var allChecked = all.length > 0 && all.every(function (cb) { return cb.checked; });
        selectAllCb.checked = allChecked;
        selectAllCb.indeterminate = !allChecked && all.some(function (cb) { return cb.checked; });
      }
    });

    // Evaluate All Unscored
    toolbar.querySelector('#ecv-eval-unscored').addEventListener('click', function () {
      var btns = Array.from(listEl.querySelectorAll('.eval-btn')).filter(function (b) {
        return b.dataset.scored === 'false' || b.textContent.trim() === 'Evaluate';
      });
      if (btns.length === 0) { showModal('Notice', '<p>No unscored items to evaluate.</p>'); return; }
      var statusEl = toolbar.querySelector('#ecv-status');
      var started = 0;
      btns.forEach(function (btn) {
        triggerEval({
          source: btn.dataset.source || 'run-single',
          agent: btn.dataset.agent,
          model: btn.dataset.model,
          category: btn.dataset.category,
          task_id: btn.dataset.taskId,
          persona_id: btn.dataset.personaId,
        }, btn);
        started++;
        if (statusEl) statusEl.textContent = started + '/' + btns.length + ' started';
      });
    });

    // Evaluate Selected
    toolbar.querySelector('#ecv-eval-selected').addEventListener('click', function () {
      var checked = Array.from(listEl.querySelectorAll('.eval-item-checkbox:checked'));
      if (checked.length === 0) { showModal('Notice', '<p>Please select tasks to evaluate.</p>'); return; }
      checked.forEach(function (cb) {
        var item = cb.closest('.eval-item');
        var btn = item && item.querySelector('.eval-btn');
        if (!btn) return;
        triggerEval({
          source: btn.dataset.source || 'run-single',
          agent: btn.dataset.agent,
          model: btn.dataset.model,
          category: btn.dataset.category,
          task_id: btn.dataset.taskId,
          persona_id: btn.dataset.personaId,
        }, btn);
      });
    });

    // Stop Selected
    toolbar.querySelector('#ecv-stop-selected').addEventListener('click', function () {
      var checked = Array.from(listEl.querySelectorAll('.eval-item-checkbox:checked'));
      if (checked.length === 0) { showModal('Notice', '<p>Please select tasks to stop.</p>'); return; }
      var keys = checked.map(function (cb) {
        var item = cb.closest('.eval-item');
        var btn = item && item.querySelector('.eval-btn');
        if (!btn) return null;
        return btn.dataset.taskId + '__' + btn.dataset.personaId;
      }).filter(function (k) {
        if (!k || !_evalState.jobs[k]) return false;
        var s = _evalState.jobs[k].status;
        return s === 'running' || s === 'pending';
      });
      if (keys.length === 0) { showModal('Notice', '<p>No running evaluations among selected.</p>'); return; }
      confirmDialog('Stop ' + keys.length + ' running evaluation(s)?').then(function (yes) {
        if (!yes) return;
        keys.forEach(function (key) {
          var job = _evalState.jobs[key];
          if (!job || !job.req) return;
          job.status = 'stopping';
          _updateEvalJobRow(key);
          apiPost('/eval/stop', { task_id: job.req.task_id, persona_id: job.req.persona_id }).catch(function () {
            if (_evalState.jobs[key]) _evalState.jobs[key].status = 'running';
            _updateEvalJobRow(key);
          });
        });
      });
    });
  }

  // ── Page: Result Detail ───────────────────────────────────

  function showResultDetail(app, pathStr) {
    var parts = pathStr.split('/').map(decodeURIComponent);
    if (parts.length < 6) { app.innerHTML = '<div class="page"><div class="empty-state">Invalid result path</div></div>'; return; }

    var source = parts[0], agent = parts[1], model = parts[2], category = parts[3], taskId = parts[4], personaId = parts[5];

    app.innerHTML =
      '<div class="page-run">' +
        '<div class="detail-header">' +
          '<a href="#/results/s/' + encodeURIComponent(source) + '/a/' + encodeURIComponent(agent) + '/m/' + encodeURIComponent(model) + '/c/' + encodeURIComponent(category) + '" class="detail-back">\u2190 Back</a>' +
          '<div class="detail-title-block">' +
            '<div class="detail-task-id">' + QTB.escapeHtml(taskId) + '</div>' +
            '<div class="detail-tags">' +
              '<span class="detail-tag detail-tag-agent">' + QTB.escapeHtml(agent) + '</span>' +
              '<span class="detail-tag detail-tag-model">' + QTB.escapeHtml(model) + '</span>' +
              '<span class="detail-tag ' + _personaTagClass(personaId) + '">' + QTB.escapeHtml(personaId) + '</span>' +
              (source === 'run-group' ? '<span class="detail-tag detail-tag-source">group</span>' : '') +
            '</div>' +
          '</div>' +
          '<button class="btn btn-sm btn-primary detail-eval-btn" id="detail-eval-btn">' +
            'Evaluate' +
          '</button>' +
          '<button class="detail-score-btn" id="detail-score-btn" style="display:none">' +
            '<svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5">' +
              '<rect x="2" y="2" width="12" height="12" rx="1.5"/><path d="M5 8h6M5 5.5h6M5 10.5h4"/>' +
            '</svg> Score Report' +
          '</button>' +
          '<button class="detail-cost-btn" id="detail-cost-btn" style="display:none">' +
            '<svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5">' +
              '<circle cx="8" cy="8" r="6.5"/><path d="M8 4v8M6 5.5h3.5a1.5 1.5 0 010 3H6m0 0h4a1.5 1.5 0 010 3H6"/>' +
            '</svg> Cost Report' +
          '</button>' +
        '</div>' +
        '<div class="run-layout">' +
          '<aside class="run-config">' +
            '<div class="panel-header">Info</div>' +
            '<div class="panel-body" id="detail-meta"></div>' +
          '</aside>' +
          '<div class="run-main">' +
            '<div class="run-chat-panel">' +
              '<div class="panel-header">Conversation</div>' +
              '<div id="detail-chat" class="chat-area"></div>' +
            '</div>' +
            '<div class="run-tool-panel">' +
              '<div class="panel-header">Tool Calls</div>' +
              '<div id="detail-tools" class="tool-area"></div>' +
            '</div>' +
          '</div>' +
        '</div>' +
      '</div>';

    // Add reopen tabs for detail page
    var detailLayout = app.querySelector('.run-layout');
    if (detailLayout) {
      var tabInfo = document.createElement('div');
      tabInfo.className = 'panel-reopen-tab tab-left';
      tabInfo.id = 'reopen-detail-info';
      tabInfo.innerHTML = '<span class="tab-arrow">\u25C0</span><span class="tab-label">Info</span>';
      detailLayout.appendChild(tabInfo);

      var tabTools = document.createElement('div');
      tabTools.className = 'panel-reopen-tab tab-right';
      tabTools.id = 'reopen-detail-tools';
      tabTools.innerHTML = '<span class="tab-arrow">\u25B6</span><span class="tab-label">Tools</span>';
      detailLayout.appendChild(tabTools);
    }

    bindSideTab(app, 'reopen-detail-info', '.run-config', true);
    bindSideTab(app, 'reopen-detail-tools', '.run-tool-panel', false);

    api('/results/' + pathStr).then(function (data) {
      var metaEl = app.querySelector('#detail-meta');
      if (metaEl) {
        var ts = data._timestamp ? new Date(data._timestamp * 1000).toLocaleString() : '-';
        metaEl.innerHTML = [
          metaItem('Agent', agent), metaItem('Model', model),
          metaItem('Category', category), metaItem('Persona', personaId),
          metaItem('Source', source),
          metaItem('Time', ts),
          metaItem('Duration', Math.round(data.duration_seconds || 0) + 's'),
          metaItem('Turns', (data.conversation || []).length),
          metaItem('Tools', (data.tool_logs || []).length),
        ].join('');
      }

      // Score Report button
      var scoreBtn = app.querySelector('#detail-score-btn');
      if (scoreBtn && data._scores_md) {
        scoreBtn.style.display = '';
        scoreBtn.addEventListener('click', function () {
          var scoreTs = data._score_timestamp ? new Date(data._score_timestamp * 1000).toLocaleString() : null;
          var headerHtml = scoreTs ? '<div style="font-size:12px;color:var(--text-muted);margin-bottom:8px">Scored: ' + scoreTs + '</div>' : '';
          showModal('Score Report', headerHtml + QTB.renderMarkdown(data._scores_md));
        });
      }

      // Cost Report button
      var costBtn = app.querySelector('#detail-cost-btn');
      if (costBtn && data._cost_md) {
        costBtn.style.display = '';
        costBtn.addEventListener('click', function () {
          showModal('Cost Report', QTB.renderMarkdown(data._cost_md));
        });
      }

      // Bind evaluate button
      var evalBtn = app.querySelector('#detail-eval-btn');
      if (evalBtn) {
        if (data._scores_md) {
          evalBtn.textContent = 'Re-evaluate';
          evalBtn.className = 'btn btn-sm btn-secondary detail-eval-btn';
        }
        evalBtn.addEventListener('click', function () {
          var body = { source: source, agent: agent, model: model, category: category, task_id: taskId, persona_id: personaId };
          triggerEval(body, evalBtn);
        });
      }

      var chatEl = app.querySelector('#detail-chat');
      var toolLogs = data.tool_logs || [];
      var hasBlocks = false;
      if (chatEl) {
        hasBlocks = QTB.buildConversationReplay(chatEl, data.conversation || [], toolLogs);
        rewriteImages(chatEl, pathStr);
      }
      var toolsEl = app.querySelector('#detail-tools');
      if (toolsEl) {
        QTB.buildToolReplay(toolsEl, toolLogs);
        rewriteImages(toolsEl, pathStr);
      }
      // Hide side tool panel when content_blocks provide inline tool display
      if (hasBlocks) {
        var toolPanel = app.querySelector('.run-tool-panel');
        if (toolPanel) toolPanel.classList.add('collapsed');
      }
    }).catch(function (err) {
      var m = app.querySelector('#detail-meta');
      if (m) m.innerHTML = '<div class="empty-state">Error: ' + err.message + '</div>';
    });
  }

  function metaItem(label, value) {
    return '<div class="detail-meta-item"><span class="dm-label">' + label + '</span><span class="dm-value">' + QTB.escapeHtml(String(value)) + '</span></div>';
  }

  function rewriteImages(container, pathStr) {
    container.querySelectorAll('img').forEach(function (img) {
      var src = img.getAttribute('src') || '';
      if (src.startsWith('http://') || src.startsWith('https://') || src.startsWith('/api/')) return;
      var filename = src.split('/').pop();
      if (filename) img.src = '/api/results/' + pathStr + '/files/' + encodeURIComponent(filename);
    });
  }

  /** Rewrite bare-filename img.src to /api/files/live/ (for live run mode). */
  function rewriteLiveImages(container) {
    container.querySelectorAll('img').forEach(function (img) {
      var src = img.getAttribute('src') || '';
      if (src.startsWith('http://') || src.startsWith('https://') || src.startsWith('/api/') || src.startsWith('data:')) return;
      var filename = src.split('/').pop();
      if (filename) img.src = '/api/files/live/' + encodeURIComponent(filename);
    });
  }

  // ── Persona level → CSS class mapping ─────────────────────

  var PERSONA_LEVEL = {
    beginner_no_finance: 'beginner',
    intermediate_developer: 'intermediate',
    advanced_quant: 'advanced',
  };

  function _personaTagClass(personaId) {
    var level = PERSONA_LEVEL[personaId] || 'beginner';
    return 'detail-tag-persona-' + level;
  }

  // ── Page: Tasks (folder view) ─────────────────────────────

  var FOLDER_SVG = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">' +
    '<path d="M2 6a2 2 0 012-2h5l2 2h9a2 2 0 012 2v10a2 2 0 01-2 2H4a2 2 0 01-2-2V6z"/></svg>';

  var CATEGORY_LABELS = {
    data_analysis: 'Data Analysis',
    implementation: 'Implementation',
    debug: 'Debug',
    end_to_end: 'End to End',
    adversarial: 'Adversarial',
    strategy: 'Strategy',
    conceptual_qa: 'Conceptual Q&A',
    backtest: 'Backtest',
  };

  var CATEGORY_META = {
    adversarial:    { icon: '\uD83D\uDEE1\uFE0F', desc: 'Tests agent robustness against misleading prompts, trick questions, and adversarial inputs' },
    backtest:       { icon: '\uD83D\uDD04',       desc: 'Evaluates understanding and execution of backtesting engines, metrics interpretation, and walk-forward validation' },
    data_analysis:  { icon: '\uD83D\uDCCA',       desc: 'Assesses ability to load, inspect, transform, and extract insights from financial market data' },
    debug:          { icon: '\uD83D\uDC1B',       desc: 'Challenges the agent to identify and fix bugs in quantitative finance code' },
    end_to_end:     { icon: '\uD83D\uDD17',       desc: 'Full pipeline tasks from data loading through strategy implementation to performance evaluation' },
    implementation: { icon: '\u2699\uFE0F',        desc: 'Strategy coding tasks: SMA, trend-following, mean-reversion, multi-asset, parameter optimization' },
    strategy:       { icon: '\uD83C\uDFAF',       desc: 'Research and design of trading strategies including alpha models, signal generation, and portfolio construction' },
  };

  function showTaskFolders(app) {
    renderTemplate(app, 'tpl-tasks');
    var listEl = app.querySelector('#tasks-list');

    api('/tasks').then(function (tasks) {
      if (tasks.length === 0) { listEl.innerHTML = '<div class="empty-state">No tasks found</div>'; return; }

      // Group by category
      var groups = {};
      tasks.forEach(function (t) {
        var cat = t.category || 'other';
        if (!groups[cat]) groups[cat] = [];
        groups[cat].push(t);
      });

      var cats = Object.keys(groups).sort();
      listEl.className = 'task-folder-list';
      listEl.innerHTML = cats.map(function (cat) {
        var label = CATEGORY_LABELS[cat] || cat.replace(/_/g, ' ');
        var count = groups[cat].length;
        var meta = CATEGORY_META[cat] || {};
        var icon = meta.icon || '\uD83D\uDCC1';
        var desc = meta.desc || '';
        return '<div class="folder-card" data-cat="' + QTB.escapeHtml(cat) + '">' +
          '<div class="folder-icon">' + icon + '</div>' +
          '<div class="folder-info">' +
            '<div class="folder-name">' + QTB.escapeHtml(label) + '</div>' +
            '<div class="folder-count"><span class="count-num">' + count + '</span> task' + (count !== 1 ? 's' : '') + '</div>' +
          '</div>' +
          '<div class="folder-desc">' + QTB.escapeHtml(desc) + '</div>' +
        '</div>';
      }).join('');

      listEl.querySelectorAll('.folder-card').forEach(function (card) {
        card.addEventListener('click', function () {
          window.location.hash = '#/tasks/' + card.getAttribute('data-cat');
        });
      });
    }).catch(function (err) {
      listEl.innerHTML = '<div class="empty-state">Error: ' + err.message + '</div>';
    });
  }

  function showTasksInCategory(app, category) {
    renderTemplate(app, 'tpl-tasks');
    var listEl = app.querySelector('#tasks-list');
    var header = app.querySelector('.page-header');
    var label = CATEGORY_LABELS[category] || category.replace(/_/g, ' ');

    if (header) {
      header.innerHTML = buildBreadcrumb([
        {label: 'Tasks', href: '#/tasks'},
        {label: label},
      ]);
    }

    api('/tasks').then(function (tasks) {
      var filtered = tasks.filter(function (t) { return t.category === category; });
      if (filtered.length === 0) { listEl.innerHTML = '<div class="empty-state">No tasks in this category</div>'; return; }

      listEl.className = 'task-grid';
      listEl.innerHTML = filtered.map(function (t) {
        var tags = [];
        if (t.difficulty) tags.push(t.difficulty);
        if (t.requires_code) tags.push('code');
        if (t.max_turns) tags.push(t.max_turns + ' turns');
        return '<div class="task-card">' +
          '<div class="tc-id">' + QTB.escapeHtml(t.task_id) + '</div>' +
          '<div class="tc-desc">' + QTB.escapeHtml((t.description || '').slice(0, 180)) + '</div>' +
          '<div class="tc-tags">' + tags.map(function (tag) { return '<span class="tc-tag">' + QTB.escapeHtml(tag) + '</span>'; }).join('') + '</div>' +
        '</div>';
      }).join('');
    }).catch(function (err) {
      listEl.innerHTML = '<div class="empty-state">Error: ' + err.message + '</div>';
    });
  }

  // ── Template helpers ──────────────────────────────────────

  /**
   * Build a breadcrumb with a back button.
   * @param {Array} crumbs — [{label, href}] where the last item has no href (current page).
   * @returns {string} HTML string
   */
  function buildBreadcrumb(crumbs) {
    // The back link = the second-to-last crumb (immediate parent)
    var backHref = crumbs.length >= 2 ? crumbs[crumbs.length - 2].href : null;
    var parts = crumbs.map(function (c, i) {
      var sep = i > 0 ? '<span class="bc-sep">/</span>' : '';
      if (c.href) {
        return sep + '<a href="' + c.href + '">' + QTB.escapeHtml(c.label) + '</a>';
      }
      return sep + '<span>' + QTB.escapeHtml(c.label) + '</span>';
    }).join('');
    var back = backHref ? '<a href="' + backHref + '" class="bc-back">\u2190</a>' : '';
    return '<div class="folder-breadcrumb">' + back + parts + '</div>';
  }

  function renderTemplate(app, templateId) {
    var tpl = document.getElementById(templateId);
    if (tpl) { app.innerHTML = ''; app.appendChild(tpl.content.cloneNode(true)); }
    else app.innerHTML = '<div class="page"><div class="empty-state">Template not found</div></div>';
  }

  // ── Init ──────────────────────────────────────────────────

  function init() {
    connectSSE();
    window.addEventListener('hashchange', onRouteChange);
    onRouteChange();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();

})(window);
