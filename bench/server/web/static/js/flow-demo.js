/**
 * flow-demo.js — auto-driven walkthrough of the benchmark protocol.
 *
 * One Play button. Fixed task (I01). Client-side orchestration of the
 * existing REST endpoints, with a phase strip, conversation pane, and
 * protocol-call timeline so a non-engineer can watch the eval run
 * end-to-end without operating anything.
 */
(function () {
  'use strict';

  window.QTB = window.QTB || {};

  var escapeHtml = (window.QTB && window.QTB.escapeHtml) || function (value) {
    var div = document.createElement('div');
    div.textContent = String(value == null ? '' : value);
    return div.innerHTML;
  };

  var TASK_LABEL = 'I01';
  var TASK_DISPLAY = 'I01 · implement SMA crossover';

  var TUTOR_TURNS = [
    'Welcome! Let\'s sketch the strategy skeleton first. I\'ll use the LEAN `QCAlgorithm` pattern — `Initialize` for setup, `OnData` for trading logic. Sound good?',
    'Great. I\'ll add a 10-day fast SMA and a 30-day slow SMA on BTCUSDT, go long when fast crosses above slow, and flatten when it crosses back below. I\'ll share the code shortly.'
  ];

  var PHASES = [
    {id: 'unregistered', label: 'Unregistered'},
    {id: 'registered',   label: 'Registered'},
    {id: 'in_session',   label: 'In Session'},
    {id: 'completed',    label: 'Completed'}
  ];

  function normalizePhase(value) {
    return String(value || '').toLowerCase();
  }

  var SUBTITLES = {
    idle:             'Press Play to watch the benchmark protocol run end-to-end against task ' + TASK_LABEL + '.',
    starting_run:     'Claiming a disposable run for task ' + TASK_LABEL + '\u2026',
    registering:      'Registering a new session with the server\u2026',
    loading_tools:    'Fetching the task-specific tool catalogue\u2026',
    starting_session: 'Starting the session \u2014 the student will open with a question\u2026',
    sending:          'Sending the tutor\u2019s reply and waiting for the student\u2026',
    evaluating:       'Queueing the evaluation job\u2026',
    done:             'Protocol complete. Press Replay to run it again.',
    failed:           'Something went wrong. See the banner below for details.'
  };

  var state = null;
  var _root = null;

  function reset() {
    state = {
      phase: 'unregistered',
      status: 'idle',
      runId: null,
      token: null,
      sessionId: null,
      messages: [],
      timeline: [],
      error: null,
      running: false
    };
  }

  reset();

  window.QTB.renderFlowDemoPage = function (app) {
    _root = app;
    render();
  };

  // ── Render ──────────────────────────────────────────────────────────

  function render() {
    if (!_root) return;
    var expanded = captureExpandedTimeline();
    _root.innerHTML = pageHtml();
    restoreExpandedTimeline(expanded);
    bind();
  }

  function captureExpandedTimeline() {
    if (!_root) return {};
    var opened = {};
    var nodes = _root.querySelectorAll('details.flow-timeline-card');
    Array.prototype.forEach.call(nodes, function (node) {
      if (node.open) opened[node.getAttribute('data-idx') || ''] = true;
    });
    return opened;
  }

  function restoreExpandedTimeline(opened) {
    if (!_root) return;
    Object.keys(opened).forEach(function (idx) {
      var node = _root.querySelector('details.flow-timeline-card[data-idx="' + idx + '"]');
      if (node) node.open = true;
    });
  }

  function pageHtml() {
    return '' +
      '<section class="page flow-demo">' +
        '<header class="page-header">' +
          '<div class="page-title-wrap">' +
            '<p class="eyebrow">Flow demo</p>' +
            '<h1>Watch the benchmark protocol run end-to-end.</h1>' +
            '<p class="subtitle">Task <code>' + escapeHtml(TASK_DISPLAY) + '</code>. One Play button \u2014 the script claims a disposable run, registers a session, exchanges a couple of tutor turns, and queues the evaluation.</p>' +
          '</div>' +
          '<div class="flow-actions">' + actionButtonHtml() + '</div>' +
        '</header>' +
        phaseStripHtml() +
        nowBannerHtml() +
        errorBannerHtml() +
        '<div class="flow-body">' +
          '<section class="panel flow-conversation">' +
            '<h2>Conversation</h2>' +
            conversationHtml() +
          '</section>' +
          '<section class="panel flow-timeline">' +
            '<h2>Protocol timeline</h2>' +
            timelineHtml() +
          '</section>' +
        '</div>' +
      '</section>';
  }

  function actionButtonHtml() {
    if (state.running) {
      return '<button class="btn btn-primary" disabled>Running\u2026</button>';
    }
    if (state.status === 'done' || state.status === 'failed') {
      return '<button class="btn btn-primary" id="flow-play-btn" type="button">Replay</button>';
    }
    return '<button class="btn btn-primary" id="flow-play-btn" type="button">Play</button>';
  }

  function phaseStripHtml() {
    var currentIdx = phaseIndex(state.phase);
    var done = state.status === 'done';
    var items = PHASES.map(function (phase, idx) {
      var cls = 'flow-phase-pill';
      if (done && idx === PHASES.length - 1) cls += ' active';
      else if (idx < currentIdx) cls += ' done';
      else if (idx === currentIdx) cls += (state.running ? ' active' : (done ? ' done' : ' active'));
      else cls += ' pending';
      return '' +
        '<span class="' + cls + '" data-phase="' + escapeHtml(phase.id) + '">' +
          '<span class="flow-phase-index">' + (idx + 1) + '</span>' +
          '<span class="flow-phase-label">' + escapeHtml(phase.label) + '</span>' +
        '</span>';
    });
    return '<div class="flow-phase-strip">' + items.join('<span class="flow-phase-arrow">\u2192</span>') + '</div>';
  }

  function phaseIndex(id) {
    var target = normalizePhase(id);
    for (var i = 0; i < PHASES.length; i++) if (PHASES[i].id === target) return i;
    return 0;
  }

  function nowBannerHtml() {
    var text = SUBTITLES[state.status] || '';
    return '<div class="flow-now-banner">' + escapeHtml(text) + '</div>';
  }

  function errorBannerHtml() {
    if (!state.error) return '';
    return '<div class="flow-fail-banner"><strong>Failed.</strong> ' + escapeHtml(state.error) + '</div>';
  }

  function conversationHtml() {
    if (!state.messages.length) {
      return '<p class="detail-empty-note">The student\u2019s opening and tutor replies will appear here.</p>';
    }
    return state.messages.map(messageHtml).join('');
  }

  function messageHtml(msg) {
    var role = msg.role === 'tutor' ? 'tutor' : 'student';
    var label = role === 'tutor' ? 'Tutor' : 'Student';
    var body = (window.QTB && typeof window.QTB.renderMarkdown === 'function')
      ? window.QTB.renderMarkdown(msg.content || '')
      : '<p>' + escapeHtml(msg.content || '') + '</p>';
    return '' +
      '<article class="run-message ' + role + '">' +
        '<div class="run-message-label">' + label + '</div>' +
        '<div class="run-message-bubble">' + body + '</div>' +
      '</article>';
  }

  function timelineHtml() {
    if (!state.timeline.length) {
      return '<p class="detail-empty-note">Each HTTP call appears here as the script runs.</p>';
    }
    return state.timeline.map(function (entry, idx) {
      var statusClass = entry.ok === true ? 'ok' : (entry.ok === false ? 'err' : 'pending');
      var statusText = entry.statusCode != null ? String(entry.statusCode) : '\u2026';
      var durationText = entry.durationMs != null ? entry.durationMs + ' ms' : '\u2014';
      var bodyParts = [];
      if (entry.requestBody != null) {
        bodyParts.push('<h4>Request</h4><pre>' + escapeHtml(formatJson(entry.requestBody)) + '</pre>');
      }
      if (entry.response != null) {
        bodyParts.push('<h4>Response</h4><pre>' + escapeHtml(formatJson(entry.response)) + '</pre>');
      }
      if (entry.errorText) {
        bodyParts.push('<h4>Error</h4><pre>' + escapeHtml(entry.errorText) + '</pre>');
      }
      return '' +
        '<details class="flow-timeline-card ' + statusClass + '" data-idx="' + idx + '">' +
          '<summary>' +
            '<span class="flow-tl-method">' + escapeHtml(entry.method) + '</span>' +
            '<span class="flow-tl-path">' + escapeHtml(entry.path) + '</span>' +
            '<span class="flow-tl-status">' + escapeHtml(statusText) + '</span>' +
            '<span class="flow-tl-duration">' + escapeHtml(durationText) + '</span>' +
          '</summary>' +
          '<div class="flow-tl-body">' + bodyParts.join('') + '</div>' +
        '</details>';
    }).join('');
  }

  function formatJson(value) {
    if (value == null) return '';
    try { return JSON.stringify(value, null, 2); }
    catch (e) { return String(value); }
  }

  var SECRET_KEYS = {token: true, control_token: true, Authorization: true, authorization: true};

  function redactSecrets(value) {
    if (value == null || typeof value !== 'object') return value;
    if (Array.isArray(value)) return value.map(redactSecrets);
    var out = {};
    Object.keys(value).forEach(function (key) {
      if (SECRET_KEYS[key] && value[key]) out[key] = '<redacted>';
      else out[key] = redactSecrets(value[key]);
    });
    return out;
  }

  // ── Interaction ─────────────────────────────────────────────────────

  function bind() {
    var btn = document.getElementById('flow-play-btn');
    if (btn) btn.addEventListener('click', onPlay);
  }

  function onPlay() {
    if (state.running) return;
    reset();
    state.running = true;
    state.status = 'starting_run';
    render();
    runScript().then(function () {
      state.status = 'done';
      state.running = false;
      render();
    }).catch(function (err) {
      state.error = err && err.message ? err.message : String(err);
      state.status = 'failed';
      state.running = false;
      render();
    });
  }

  // ── Scripted flow ───────────────────────────────────────────────────

  function runScript() {
    return stepStartRun()
      .then(stepRegister)
      .then(stepListTools)
      .then(stepStartSession)
      .then(function () { return stepSend(TUTOR_TURNS[0]); })
      .then(function () { return stepSend(TUTOR_TURNS[1]); })
      .then(stepEvaluate);
  }

  function stepStartRun() {
    state.status = 'starting_run';
    render();
    return call('POST', '/client/runs/start', {task: TASK_LABEL, mode: 'agent'}).then(function (resp) {
      state.runId = resp.run_id;
      state.token = resp.token;
    });
  }

  function stepRegister() {
    state.status = 'registering';
    render();
    return call(
      'POST',
      '/session/register',
      {},
      {Authorization: 'Bearer ' + state.token}
    ).then(function (resp) {
      state.sessionId = resp.session_id;
      state.phase = normalizePhase(resp.current_phase) || 'registered';
      render();
    });
  }

  function stepListTools() {
    state.status = 'loading_tools';
    render();
    return call('GET', '/session/' + encodeURIComponent(state.sessionId) + '/tools');
  }

  function stepStartSession() {
    state.status = 'starting_session';
    render();
    return call(
      'POST',
      '/session/' + encodeURIComponent(state.sessionId) + '/start',
      {}
    ).then(function (resp) {
      state.phase = normalizePhase(resp.current_phase) || 'in_session';
      if (resp.student_message) {
        state.messages.push({role: 'student', content: resp.student_message});
      }
      render();
    });
  }

  function stepSend(text) {
    state.status = 'sending';
    state.messages.push({role: 'tutor', content: text});
    render();
    return call(
      'POST',
      '/session/' + encodeURIComponent(state.sessionId) + '/send',
      {text: text}
    ).then(function (resp) {
      var next = normalizePhase(resp.current_phase);
      if (next) state.phase = next;
      if (resp.student_message) {
        state.messages.push({role: 'student', content: resp.student_message});
      }
      render();
    });
  }

  function stepEvaluate() {
    state.status = 'evaluating';
    render();
    return call(
      'POST',
      '/session/' + encodeURIComponent(state.sessionId) + '/evaluate',
      {}
    ).then(function () {
      state.phase = 'completed';
    });
  }

  // ── HTTP + timeline ─────────────────────────────────────────────────

  function call(method, path, body, extraHeaders) {
    var entry = {
      method: method,
      path: path,
      requestBody: (method === 'GET' ? null : redactSecrets(body || {})),
      statusCode: null,
      ok: null,
      response: null,
      errorText: null,
      durationMs: null
    };
    state.timeline.push(entry);
    render();

    var headers = {};
    if (method !== 'GET') headers['Content-Type'] = 'application/json';
    if (extraHeaders) {
      Object.keys(extraHeaders).forEach(function (key) { headers[key] = extraHeaders[key]; });
    }

    var fetchOpts = {method: method, headers: headers};
    if (method !== 'GET') fetchOpts.body = JSON.stringify(body || {});

    var started = (window.performance && performance.now) ? performance.now() : Date.now();
    return fetch(path, fetchOpts).then(function (resp) {
      entry.statusCode = resp.status;
      entry.durationMs = Math.round(((window.performance && performance.now) ? performance.now() : Date.now()) - started);
      return resp.text().then(function (text) {
        var parsed = null;
        if (text) {
          try { parsed = JSON.parse(text); }
          catch (e) { parsed = {raw: text}; }
        }
        entry.response = redactSecrets(parsed);
        if (!resp.ok) {
          entry.ok = false;
          entry.errorText = (parsed && parsed.error) || text || ('HTTP ' + resp.status);
          render();
          throw new Error(method + ' ' + path + ' \u2192 ' + entry.errorText);
        }
        entry.ok = true;
        render();
        return parsed;
      });
    }, function (networkErr) {
      entry.durationMs = Math.round(((window.performance && performance.now) ? performance.now() : Date.now()) - started);
      entry.ok = false;
      entry.errorText = networkErr && networkErr.message ? networkErr.message : String(networkErr);
      render();
      throw networkErr;
    });
  }
})();
