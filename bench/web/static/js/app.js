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

  // Active eval progress tracking: key → {el, startTime, timerInterval}
  var _activeEvals = {};

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

  // ── SSE connection ────────────────────────────────────────

  var _sseRetryCount = 0;
  var _MAX_SSE_RETRIES = 3;

  function connectSSE() {
    if (state.sse) state.sse.close();
    _sseRetryCount = 0;
    var es = new EventSource('/api/events');
    state.sse = es;
    es.onopen = function () {
      _sseRetryCount = 0;
      state.connected = true;
      // Only update indicator if a run is active (don't show on idle connect)
      if (state.running || _groupState.running) updateConnectionUI('connected');
    };
    es.onmessage = function (e) {
      var data;
      try { data = JSON.parse(e.data); } catch (_) { return; }
      handleSSEEvent(data);
    };
    es.onerror = function () {
      _sseRetryCount++;
      state.connected = false;
      if (_sseRetryCount >= _MAX_SSE_RETRIES) {
        es.close();
        state.sse = null;
        updateConnectionUI('disconnected');
        if (state.running) {
          state.running = false;
          updateRunButtons(false);
          var rscope = _getRunScope();
          var chatEl = rscope.querySelector('#run-chat');
          if (chatEl) QTB.hideThinking(chatEl);
          var sb = rscope.querySelector('#run-status-bar');
          if (sb) {
            sb.textContent = 'Server disconnected \u2014 session may have ended.';
            sb.style.color = 'var(--error)';
          }
        }
      } else {
        updateConnectionUI('reconnecting');
      }
    };
  }

  function updateConnectionUI(status) {
    var nav = document.getElementById('nav-status');
    var dot = document.getElementById('connection-dot');
    var text = document.getElementById('connection-text');
    // Only show connection indicator while a run/group is active or just completed
    var visible = state.running || _groupState.running || status === 'completed';
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

  function handleSSEEvent(data) {
    switch (data.type) {
      case 'session_start':
        if (data.mode === 'eval') onEvalStart(data);
        else onSessionStart(data);
        break;
      case 'student_message': onStudentMessage(data); break;
      case 'tutor_response':  onTutorResponse(data); break;
      case 'tool_start':      onToolStart(data); break;
      case 'tool_result':     onToolResult(data); break;
      case 'session_end':
        if (data.mode === 'eval') onEvalEnd(data);
        else onSessionEnd(data);
        break;
      case 'eval_step':       onEvalStep(data); break;
      case 'group_start':     onGroupStart(data); break;
      case 'group_task_start': onGroupTaskStart(data); break;
      case 'group_task_end':  onGroupTaskEnd(data); break;
      case 'group_end':       onGroupEnd(data); break;
    }
  }

  // ── SSE event handlers (Run) ────────────────────────────────

  /** Get the run-single sub-page scope for SSE handlers. */
  function _getRunScope() {
    return _subPages['/run/single'] || document;
  }

  function onSessionStart(data) {
    state.running = true; state.startTime = Date.now();
    state.msgCount = 0; state.toolCount = 0;
    var scope = _getRunScope();
    var chatEl = scope.querySelector('#run-chat');
    var toolsEl = scope.querySelector('#run-tools');
    if (chatEl) QTB.clearChat(chatEl);
    if (toolsEl) QTB.clearTools(toolsEl);
    updateRunCounters(); updateRunButtons(true);
    updateConnectionUI('connected');
    var sb = scope.querySelector('#run-status-bar');
    if (sb) { sb.textContent = 'Running: ' + (data.task_id || ''); sb.style.color = ''; }
    // Collapse tool panel at start (will auto-expand on first tool_start)
    var toolPanel = scope.querySelector('.run-tool-panel');
    if (toolPanel) {
      toolPanel.classList.add('collapsed');
      var reopenTab = scope.querySelector('#reopen-tools');
      if (reopenTab) {
        var arrow = reopenTab.querySelector('.tab-arrow');
        if (arrow) arrow.textContent = '\u25C0';
      }
    }
  }

  function onStudentMessage(data) {
    state.msgCount++;
    var scope = _getRunScope();
    var chatEl = scope.querySelector('#run-chat');
    if (chatEl) {
      QTB.hideResponding(chatEl);
      QTB.addChatMessage(chatEl, 'student', data.content || '');
      QTB.showThinking(chatEl, 'Thinking...');
    }
    var tc = scope.querySelector('#run-turn-count');
    if (tc && data.turn_index != null) tc.textContent = data.turn_index + 1;
    updateRunCounters();
  }

  function onTutorResponse(data) {
    state.msgCount++;
    var scope = _getRunScope();
    var chatEl = scope.querySelector('#run-chat');
    if (chatEl) {
      var blocks = data.content_blocks || null;
      QTB.addChatMessage(chatEl, 'tutor', data.content || '', blocks, null);
      QTB.showResponding(chatEl);
    }
    updateRunCounters();
  }

  function onToolStart(data) {
    state.toolCount++;
    var scope = _getRunScope();
    // Auto-expand tool panel on first tool call
    var toolPanel = scope.querySelector('.run-tool-panel');
    if (toolPanel && toolPanel.classList.contains('collapsed')) {
      toolPanel.classList.remove('collapsed');
      var reopenTab = scope.querySelector('#reopen-tools');
      if (reopenTab) {
        var arrow = reopenTab.querySelector('.tab-arrow');
        if (arrow) arrow.textContent = '\u25B6';
      }
    }
    var toolsEl = scope.querySelector('#run-tools');
    if (toolsEl) QTB.addToolStart(toolsEl, data);
    var chatEl = scope.querySelector('#run-chat');
    if (chatEl) QTB.updateThinking(chatEl, 'Using ' + (data.name || 'tool') + '...');
    updateRunCounters();
  }

  function onToolResult(data) {
    var scope = _getRunScope();
    var toolsEl = scope.querySelector('#run-tools');
    if (toolsEl) QTB.updateToolResult(toolsEl, data);
  }

  function onSessionEnd(data) {
    state.running = false;
    updateRunButtons(false);
    var scope = _getRunScope();
    var chatEl = scope.querySelector('#run-chat');
    if (chatEl) {
      QTB.hideThinking(chatEl);
      QTB.hideResponding(chatEl);
    }

    // Update connection status to "completed"
    updateConnectionUI('completed');

    // Clear status bar (no local path display)
    var sb = scope.querySelector('#run-status-bar');
    if (sb) { sb.textContent = ''; sb.style.color = ''; }

    // Error case: simple modal
    if (data.error) {
      showModal('Run Failed', '<p style="color:var(--error)">' + QTB.escapeHtml(data.error) + '</p>');
      return;
    }

    // Build result detail hash route
    var resultHash = '';
    if (data.source && data.agent && data.model && data.category && data.task_id && data.persona_id) {
      resultHash = '#/results/' + [
        data.source, data.agent, data.model, data.category,
        data.task_id, data.persona_id
      ].map(encodeURIComponent).join('/');
    }

    var countdown = 10;
    var modalHtml =
      '<div style="text-align:center;padding:20px 0">' +
        '<div style="font-size:40px;margin-bottom:12px">\u2713</div>' +
        '<p style="font-size:16px;font-weight:600;margin-bottom:8px">Task Completed</p>' +
        '<p style="color:var(--text-muted);margin-bottom:24px;font-family:var(--font-mono);font-size:13px">' +
          QTB.escapeHtml(data.task_id || '') + ' / ' + QTB.escapeHtml(data.persona_id || '') +
        '</p>' +
        (resultHash
          ? '<button class="btn btn-primary" id="modal-go-result">View Results</button>'
          : '') +
        '<p style="color:var(--text-muted);font-size:12px;margin-top:20px">' +
          'Auto-closing in <span id="modal-countdown">' + countdown + '</span>s' +
        '</p>' +
      '</div>';

    showModal('Session Ended', modalHtml);

    // "View Results" button — navigate and clean up
    var goBtn = document.getElementById('modal-go-result');
    if (goBtn) {
      goBtn.addEventListener('click', function () {
        clearInterval(_sessionEndTimer);
        closeModal();
        clearRunState();
        window.location.hash = resultHash;
      });
    }

    // 10s countdown → auto-close + clear
    var _sessionEndTimer = setInterval(function () {
      countdown--;
      var cdEl = document.getElementById('modal-countdown');
      if (cdEl) cdEl.textContent = countdown;
      if (countdown <= 0) {
        clearInterval(_sessionEndTimer);
        closeModal();
        clearRunState();
      }
    }, 1000);
  }

  function clearRunState() {
    var scope = _getRunScope();
    var chatEl = scope.querySelector('#run-chat');
    var toolsEl = scope.querySelector('#run-tools');
    if (chatEl) QTB.clearChat(chatEl);
    if (toolsEl) QTB.clearTools(toolsEl);
    state.msgCount = 0;
    state.toolCount = 0;
    updateRunCounters();
    var tc = scope.querySelector('#run-turn-count');
    if (tc) tc.textContent = '0';
    // Hide connection indicator (run is over)
    var nav = document.getElementById('nav-status');
    if (nav) nav.style.display = 'none';
  }

  function updateRunCounters() {
    var scope = _getRunScope();
    var mc = scope.querySelector('#run-msg-count');
    var tc = scope.querySelector('#run-tool-total');
    var toolBadge = scope.querySelector('#run-tool-count');
    if (mc) mc.textContent = 'Messages: ' + state.msgCount;
    if (tc) tc.textContent = 'Tools: ' + state.toolCount;
    if (toolBadge) toolBadge.textContent = state.toolCount;
  }

  function updateRunButtons(running) {
    var scope = _getRunScope();
    var btn = scope.querySelector('#btn-run');
    var stop = scope.querySelector('#btn-stop');
    if (btn) btn.disabled = running;
    if (stop) stop.disabled = !running;
  }

  function updateElapsed() {
    if (!state.startTime || !state.running) return;
    var s = Math.round((Date.now() - state.startTime) / 1000);
    var m = Math.floor(s / 60);
    var scope = _getRunScope();
    var el = scope.querySelector('#run-elapsed');
    if (el) el.textContent = 'Elapsed: ' + (m > 0 ? m + 'm ' : '') + (s % 60) + 's';
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
    var entry = _activeEvals[key];
    if (entry && entry.el) {
      entry.el.classList.add('active');
      entry.startTime = Date.now();
    }
  }

  function onEvalStep(data) {
    var key = evalKey(data);
    var entry = _activeEvals[key];
    if (!entry || !entry.el) return;
    var stepEl = entry.el.querySelector('[data-step="' + data.step + '"]');
    if (!stepEl) return;
    var indicator = stepEl.querySelector('.step-indicator');
    var scoreEl = stepEl.querySelector('.step-score');
    if (data.status === 'running') {
      stepEl.className = 'eval-step running';
      if (indicator) indicator.textContent = '\u25CF';
    } else if (data.status === 'done') {
      stepEl.className = 'eval-step done';
      if (indicator) indicator.textContent = '\u2713';
      if (scoreEl && data.score != null) scoreEl.textContent = data.score.toFixed(4);
    }
  }

  function onEvalEnd(data) {
    var key = evalKey(data);
    var entry = _activeEvals[key];
    if (!entry || !entry.el) return;
    if (entry.timerInterval) clearInterval(entry.timerInterval);

    var card = entry.el;
    // Hide stop button
    var stopBtn = card.querySelector('.eval-stop-btn');
    if (stopBtn) stopBtn.style.display = 'none';

    if (data.error) {
      var isCancelled = data.error === 'cancelled';
      card.classList.add(isCancelled ? 'eval-cancelled' : 'eval-error');
      var errEl = card.querySelector('.eval-final');
      if (errEl) errEl.innerHTML = isCancelled
        ? '<span class="eval-cancelled-text">Cancelled</span>'
        : '<span class="eval-error-text">Error: ' + QTB.escapeHtml(data.error).slice(0, 120) + '</span>';
    } else {
      card.classList.add('eval-complete');
      var finalEl = card.querySelector('.eval-final');
      if (finalEl && data.scores) {
        var oas = data.scores.oas != null ? data.scores.oas.toFixed(4) : '-';
        var qr = data.scores.qr != null ? data.scores.qr.toFixed(4) : '-';
        var qp = data.scores.qp != null ? data.scores.qp.toFixed(4) : '-';
        finalEl.innerHTML = '<span class="eval-oas">OAS: ' + oas + '</span>' +
          '<span class="eval-sub">QR: ' + qr + '</span>' +
          '<span class="eval-sub">QP: ' + qp + '</span>';
      }
      // Update the eval button
      var btn = entry.btn;
      if (btn) {
        btn.textContent = 'Re-evaluate';
        btn.disabled = false;
        btn.className = 'btn btn-sm btn-secondary eval-btn';
      }
    }

    // Invalidate the eval category sub-page so unscored counts refresh on next visit
    if (data.source && data.agent && data.model && data.category) {
      var evalRoute = '/evaluate/s/' + data.source + '/a/' + encodeURIComponent(data.agent) +
        '/m/' + encodeURIComponent(data.model) + '/c/' + data.category;
      invalidateSubPage(evalRoute);
      // Also invalidate the parent pages (model/agent level) so counts update
      invalidateSubPage('/evaluate/s/' + data.source + '/a/' + encodeURIComponent(data.agent) + '/m/' + encodeURIComponent(data.model));
      invalidateSubPage('/evaluate/s/' + data.source + '/a/' + encodeURIComponent(data.agent));
      invalidateSubPage('/evaluate/s/' + data.source);
      invalidateSubPage('/evaluate');
      // Invalidate corresponding results pages
      var resRoute = '/results/s/' + encodeURIComponent(data.source) + '/a/' + encodeURIComponent(data.agent) +
        '/m/' + encodeURIComponent(data.model) + '/c/' + encodeURIComponent(data.category);
      invalidateSubPage(resRoute);
    }

    // Show completion modal
    if (!data.error) {
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
  }

  function createEvalProgressCard(r, btn) {
    var key = r.task_id + '__' + r.persona_id;
    var card = document.createElement('div');
    card.className = 'eval-progress-card';
    card.innerHTML =
      '<div class="eval-progress-header">' +
        '<span class="eval-task-id">' + QTB.escapeHtml(r.task_id) + '</span>' +
        '<button class="btn btn-xs btn-danger eval-stop-btn">Stop</button>' +
        '<span class="eval-timer">0s</span>' +
      '</div>' +
      '<div class="eval-steps">' +
        EVAL_STEPS.map(function (s) {
          return '<div class="eval-step pending" data-step="' + s + '">' +
            '<span class="step-indicator">\u25CB</span>' +
            '<span class="step-name">' + EVAL_STEP_LABELS[s] + '</span>' +
            '<span class="step-score"></span>' +
          '</div>';
        }).join('') +
      '</div>' +
      '<div class="eval-final"></div>';

    // Stop button
    var stopBtn = card.querySelector('.eval-stop-btn');
    stopBtn.addEventListener('click', function () {
      stopBtn.disabled = true;
      stopBtn.textContent = 'Stopping...';
      apiPost('/eval/stop', { task_id: r.task_id, persona_id: r.persona_id })
        .catch(function () { stopBtn.textContent = 'Stop'; stopBtn.disabled = false; });
    });

    var startTime = Date.now();
    var timerEl = card.querySelector('.eval-timer');
    var timerInterval = setInterval(function () {
      var s = Math.round((Date.now() - startTime) / 1000);
      var m = Math.floor(s / 60);
      if (timerEl) timerEl.textContent = (m > 0 ? m + 'm ' : '') + (s % 60) + 's';
    }, 1000);

    _activeEvals[key] = { el: card, startTime: startTime, timerInterval: timerInterval, btn: btn };
    return card;
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
  var _lastModRoute = {};   // module-name → last active route (for module switch resume)

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
      div.style.display = 'none';
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
      _modules[_currentMod].style.display = 'none';
    }

    // Show target module
    var container = _getModContainer(newMod);
    container.style.display = '';
    _currentMod = newMod;
    _updateNavActive(route);

    // Module-specific routing
    switch (newMod) {
      case 'run':
        _routeSubPaged(container, route, _dispatchRun);
        // Restart elapsed timer if running
        if (state.timerInterval) clearInterval(state.timerInterval);
        state.timerInterval = setInterval(updateElapsed, 1000);
        break;

      case 'evaluate':
        _routeSubPaged(container, route, _dispatchEval);
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

    // Cached & not stale → just show
    if (_subPages[route] && !_staleRoutes[route]) {
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
    else if (route === '/run/group') {
      if (!target.firstElementChild || target.dataset.rendered !== 'group') {
        target.innerHTML = '';
        target.dataset.rendered = 'group';
        showRunGroup(target);
      }
    }
    else target.innerHTML = '<div class="page"><div class="empty-state">Page not found</div></div>';
  }

  function _dispatchEval(target, route) {
    if (route === '/evaluate') showEvalSources(target);
    else if (route.match(/^\/evaluate\/s\/[^/]+$/)) showEvalAgents(target, route.split('/')[3]);
    else if (route.match(/^\/evaluate\/s\/[^/]+\/a\/[^/]+$/)) { var ea = route.split('/'); showEvalModels(target, ea[3], ea[5]); }
    else if (route.match(/^\/evaluate\/s\/[^/]+\/a\/[^/]+\/m\/[^/]+$/)) { var em = route.split('/'); showEvalCategories(target, em[3], em[5], em[7]); }
    else if (route.match(/^\/evaluate\/s\/[^/]+\/a\/[^/]+\/m\/[^/]+\/c\/[^/]+$/)) { var ec = route.split('/'); showEvaluateInCategory(target, ec[3], ec[5], ec[7], ec[9]); }
    else target.innerHTML = '<div class="page"><div class="empty-state">Page not found</div></div>';
  }

  function _dispatchResults(target, route) {
    if (route === '/results') showResultSources(target);
    else if (route.match(/^\/results\/s\/[^/]+$/)) showResultAgents(target, route.split('/')[3]);
    else if (route.match(/^\/results\/s\/[^/]+\/a\/[^/]+$/)) { var ra = route.split('/'); showResultModels(target, ra[3], ra[5]); }
    else if (route.match(/^\/results\/s\/[^/]+\/a\/[^/]+\/m\/[^/]+$/)) { var rm = route.split('/'); showResultCategories(target, rm[3], rm[5], rm[7]); }
    else if (route.match(/^\/results\/s\/[^/]+\/a\/[^/]+\/m\/[^/]+\/c\/[^/]+$/)) { var rc = route.split('/'); showResultsInCategory(target, rc[3], rc[5], rc[7], rc[9]); }
    else if (route.startsWith('/results/')) showResultDetail(target, route.slice('/results/'.length));
    else target.innerHTML = '<div class="page"><div class="empty-state">Page not found</div></div>';
  }

  function _dispatchTasks(target, route) {
    if (route === '/tasks') showTaskFolders(target);
    else if (route.startsWith('/tasks/')) showTasksInCategory(target, route.slice('/tasks/'.length));
    else target.innerHTML = '<div class="page"><div class="empty-state">Page not found</div></div>';
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

  function showRun(app) {
    renderTemplate(app, 'tpl-run');

    // Inject panel toggles + wrap config body
    injectPanelStructure(app);

    // Load tasks → populate category + task cascade
    api('/tasks').then(function (tasks) {
      _allTasks = tasks;
      var catSel = app.querySelector('#run-category');
      var taskSel = app.querySelector('#run-task');
      if (!catSel || !taskSel) return;

      // Build category options
      var cats = {};
      tasks.forEach(function (t) { cats[t.category || 'other'] = true; });
      var catList = Object.keys(cats).sort();
      catSel.innerHTML = catList.map(function (c) {
        var label = CATEGORY_LABELS[c] || c.replace(/_/g, ' ');
        return '<option value="' + c + '">' + QTB.escapeHtml(label) + '</option>';
      }).join('');

      function updateTaskList() {
        var cat = catSel.value;
        var filtered = tasks.filter(function (t) { return t.category === cat; });
        taskSel.innerHTML = filtered.map(function (t) {
          return '<option value="' + t.task_id + '">' + t.task_id + '</option>';
        }).join('');
      }

      catSel.addEventListener('change', updateTaskList);
      updateTaskList();
    }).catch(function () {});

    api('/personas').then(function (personas) {
      var sel = app.querySelector('#run-persona');
      if (sel) sel.innerHTML = personas.map(function (p) {
        return '<option value="' + p.persona_id + '">' + p.persona_id + '</option>';
      }).join('');
    }).catch(function () {});

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

    var btnRun = app.querySelector('#btn-run');
    if (btnRun) {
      btnRun.addEventListener('click', function () {
        var modeEl = app.querySelector('input[name="run-mode"]:checked');
        var skipEval = !modeEl || modeEl.value === 'runonly';
        var body = {
          task_id: app.querySelector('#run-task').value,
          persona_id: app.querySelector('#run-persona').value,
          agent: app.querySelector('#run-agent').value,
          docker: app.querySelector('#run-docker').checked,
          max_turns: parseInt(app.querySelector('#run-max-turns').value) || 10,
          skip_eval: skipEval,
        };
        var sb = app.querySelector('#run-status-bar');
        apiPost('/run', body).then(function () { if (sb) sb.textContent = 'Running: ' + body.task_id + '...'; })
          .catch(function (err) { if (sb) sb.textContent = 'Error: ' + err.message; });
      });
    }

    // Timer is managed by the router (onRouteChange)
    if (state.running) updateRunButtons(true);
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
      if (!scope.querySelector('#reopen-tools')) {
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

  function showRunGroup(app) {
    renderTemplate(app, 'tpl-run-group');

    // Populate category groups
    api('/tasks').then(function (tasks) {
      var sel = app.querySelector('#rg-group');
      if (!sel) return;
      var cats = {};
      tasks.forEach(function (t) { cats[t.category || 'other'] = true; });
      var catList = Object.keys(cats).sort();
      sel.innerHTML = catList.map(function (c) {
        var label = CATEGORY_LABELS[c] || c.replace(/_/g, ' ');
        return '<option value="' + c + '">' + QTB.escapeHtml(label) + ' (' + c + ')</option>';
      }).join('');
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

    var btn = app.querySelector('#btn-run-group');
    if (btn) {
      btn.addEventListener('click', function () {
        var body = {
          group: app.querySelector('#rg-group').value,
          agent: app.querySelector('#rg-agent').value,
          persona: app.querySelector('#rg-persona').value || null,
          docker: app.querySelector('#rg-docker').checked,
          max_turns: parseInt(app.querySelector('#rg-max-turns').value) || 10,
          workers: parseInt(app.querySelector('#rg-workers').value) || 3,
        };
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
    var progress = gs.querySelector('#rg-progress');
    if (!progress) return;

    // Build header
    progress.innerHTML =
      '<div class="rg-header">' +
        '<span class="rg-title">Group: ' + QTB.escapeHtml(data.group || '') + '</span>' +
        '<span class="rg-counter" id="rg-counter">0 / ' + _groupState.totalJobs + '</span>' +
        '<span class="rg-timer" id="rg-timer">0s</span>' +
      '</div>' +
      '<div class="rg-progress-bar"><div class="rg-progress-fill" id="rg-fill" style="width:0%"></div></div>' +
      '<div class="rg-job-list" id="rg-job-list"></div>';

    // Build job rows from data.jobs
    var listEl = gs.querySelector('#rg-job-list');
    if (listEl && data.jobs) {
      listEl.innerHTML = data.jobs.map(function (j) {
        var key = j.task_id + '__' + j.persona_id;
        _groupState.jobs[key] = { status: 'pending' };
        return '<div class="rg-job" id="rg-job-' + key + '" data-key="' + key + '">' +
          '<span class="rg-job-status rg-pending">\u25CB</span>' +
          '<span class="rg-job-task">' + QTB.escapeHtml(j.task_id) + '</span>' +
          '<span class="rg-job-persona">' + QTB.escapeHtml(j.persona_id) + '</span>' +
          '<span class="rg-job-detail"></span>' +
        '</div>';
      }).join('');
    }

    // Start timer
    if (_groupState.timerInterval) clearInterval(_groupState.timerInterval);
    _groupState.timerInterval = setInterval(function () {
      if (!_groupState.startTime) return;
      var s = Math.round((Date.now() - _groupState.startTime) / 1000);
      var m = Math.floor(s / 60);
      var timerEl = gs.querySelector('#rg-timer');
      if (timerEl) timerEl.textContent = (m > 0 ? m + 'm ' : '') + (s % 60) + 's';
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
  }

  function onGroupTaskEnd(data) {
    var key = data.task_id + '__' + data.persona_id;
    _groupState.completedJobs++;
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
      if (data.error) {
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
  }

  function onGroupEnd(data) {
    _groupState.running = false;
    if (_groupState.timerInterval) { clearInterval(_groupState.timerInterval); _groupState.timerInterval = null; }
    var gs = _getGroupScope();
    var btn = gs.querySelector('#btn-run-group');
    if (btn) { btn.disabled = false; btn.textContent = 'Run Group'; }
    var sb = gs.querySelector('#rg-status-bar');
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

    api('/results').then(function (results) {
      var filtered = results.filter(function (r) {
        return (r.source || 'run-single') === source && r.agent === agent && r.model === model && r.category === category;
      });
      if (filtered.length === 0) { listEl.innerHTML = '<div class="empty-state">No results in this category</div>'; return; }
      listEl.className = 'result-list';
      listEl.innerHTML = filtered.map(buildResultItemHTML).join('');
    }).catch(function (err) {
      listEl.innerHTML = '<div class="empty-state">Error: ' + err.message + '</div>';
    });
  }

  function buildResultItemHTML(r) {
    var path = [r.source || 'run-single', r.agent, r.model, r.category, r.task_id, r.persona_id].map(encodeURIComponent).join('/');
    var sourceTag = r.source === 'run-group' ? '<span class="ri-source">group</span>' : '';
    return '<a class="result-item" href="#/results/' + path + '">' +
      '<span class="ri-task">' + QTB.escapeHtml(r.task_id) + '</span>' +
      sourceTag +
      '<span class="ri-meta">' +
        '<span>' + QTB.escapeHtml(r.agent + ' / ' + r.model) + '</span>' +
        '<span>' + r.turn_count + ' turns</span>' +
        '<span>' + r.tool_count + ' tools</span>' +
        '<span>' + Math.round(r.duration_seconds || 0) + 's</span>' +
      '</span>' +
      (r.has_scores
        ? '<span class="ri-badge">scored</span>'
        : '<span class="ri-badge ri-badge-unscored">unscored</span>') +
    '</a>';
  }

  // ── Page: Evaluate (Source → Agent → Model → Category → Items) ──

  function showEvalSources(app) {
    renderTemplate(app, 'tpl-evaluate');
    var listEl = app.querySelector('#evaluate-list');
    api('/results').then(function (results) {
      if (results.length === 0) { listEl.innerHTML = '<div class="empty-state">No saved results to evaluate</div>'; return; }
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
        var unscored = bySource[s].filter(function (r) { return !r.has_scores; }).length;
        var countText = count + ' result' + (count !== 1 ? 's' : '');
        countText += unscored > 0
          ? ' \u00B7 <span style="color:var(--accent)">' + unscored + ' unscored</span>'
          : ' \u00B7 all scored';
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
          window.location.hash = '#/evaluate/s/' + card.getAttribute('data-source');
        });
      });
    }).catch(function (err) {
      listEl.innerHTML = '<div class="empty-state">Error: ' + err.message + '</div>';
    });
  }

  function showEvalAgents(app, source) {
    renderTemplate(app, 'tpl-evaluate');
    var listEl = app.querySelector('#evaluate-list');
    var header = app.querySelector('.page-header');
    var sourceLabel = SOURCE_LABELS[source] || source;
    if (header) {
      header.innerHTML = buildBreadcrumb([
        {label: 'Evaluate', href: '#/evaluate'},
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
        var unscored = byAgent[a].filter(function (r) { return !r.has_scores; }).length;
        var countText = count + ' result' + (count !== 1 ? 's' : '');
        countText += unscored > 0
          ? ' \u00B7 <span style="color:var(--accent)">' + unscored + ' unscored</span>'
          : ' \u00B7 all scored';
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
          window.location.hash = '#/evaluate/s/' + source + '/a/' + encodeURIComponent(card.getAttribute('data-agent'));
        });
      });
    }).catch(function (err) {
      listEl.innerHTML = '<div class="empty-state">Error: ' + err.message + '</div>';
    });
  }

  function showEvalModels(app, source, agent) {
    renderTemplate(app, 'tpl-evaluate');
    var listEl = app.querySelector('#evaluate-list');
    var header = app.querySelector('.page-header');
    var sourceLabel = SOURCE_LABELS[source] || source;
    if (header) {
      header.innerHTML = buildBreadcrumb([
        {label: 'Evaluate', href: '#/evaluate'},
        {label: sourceLabel, href: '#/evaluate/s/' + source},
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
        var unscored = byModel[m].filter(function (r) { return !r.has_scores; }).length;
        var countText = count + ' result' + (count !== 1 ? 's' : '');
        countText += unscored > 0
          ? ' \u00B7 <span style="color:var(--accent)">' + unscored + ' unscored</span>'
          : ' \u00B7 all scored';
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
          window.location.hash = '#/evaluate/s/' + source + '/a/' + encodeURIComponent(agent) + '/m/' + encodeURIComponent(card.getAttribute('data-model'));
        });
      });
    }).catch(function (err) {
      listEl.innerHTML = '<div class="empty-state">Error: ' + err.message + '</div>';
    });
  }

  function showEvalCategories(app, source, agent, model) {
    renderTemplate(app, 'tpl-evaluate');
    var listEl = app.querySelector('#evaluate-list');
    var header = app.querySelector('.page-header');
    var sourceLabel = SOURCE_LABELS[source] || source;
    if (header) {
      header.innerHTML = buildBreadcrumb([
        {label: 'Evaluate', href: '#/evaluate'},
        {label: sourceLabel, href: '#/evaluate/s/' + source},
        {label: agent, href: '#/evaluate/s/' + source + '/a/' + encodeURIComponent(agent)},
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
        var unscored = groups[cat].filter(function (r) { return !r.has_scores; }).length;
        var countText = count + ' result' + (count !== 1 ? 's' : '');
        countText += unscored > 0
          ? ' \u00B7 <span style="color:var(--accent)">' + unscored + ' unscored</span>'
          : ' \u00B7 all scored';
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
          window.location.hash = '#/evaluate/s/' + source + '/a/' + encodeURIComponent(agent) + '/m/' + encodeURIComponent(model) + '/c/' + card.getAttribute('data-cat');
        });
      });
    }).catch(function (err) {
      listEl.innerHTML = '<div class="empty-state">Error: ' + err.message + '</div>';
    });
  }

  function showEvaluateInCategory(app, source, agent, model, category) {
    renderTemplate(app, 'tpl-evaluate');
    var listEl = app.querySelector('#evaluate-list');
    var header = app.querySelector('.page-header');
    var sourceLabel = SOURCE_LABELS[source] || source;
    var catLabel = CATEGORY_LABELS[category] || category.replace(/_/g, ' ');

    if (header) {
      header.innerHTML = buildBreadcrumb([
        {label: 'Evaluate', href: '#/evaluate'},
        {label: sourceLabel, href: '#/evaluate/s/' + source},
        {label: agent, href: '#/evaluate/s/' + source + '/a/' + encodeURIComponent(agent)},
        {label: model, href: '#/evaluate/s/' + source + '/a/' + encodeURIComponent(agent) + '/m/' + encodeURIComponent(model)},
        {label: catLabel},
      ]);
    }

    api('/results').then(function (results) {
      var filtered = results.filter(function (r) {
        return (r.source || 'run-single') === source && r.agent === agent && r.model === model && r.category === category;
      });
      if (filtered.length === 0) { listEl.innerHTML = '<div class="empty-state">No results in this category</div>'; return; }

      // "Evaluate All Unscored" button
      var unscored = filtered.filter(function (r) { return !r.has_scores; });
      if (unscored.length > 0) {
        var evalAllDiv = document.createElement('div');
        evalAllDiv.style.cssText = 'margin-bottom:12px;display:flex;align-items:center;gap:12px';
        evalAllDiv.innerHTML = '<button class="btn btn-primary" id="eval-all-btn">Evaluate All Unscored (' + unscored.length + ')</button>' +
          '<span id="eval-all-status" style="color:var(--text-muted);font-size:0.85em"></span>';
        listEl.parentNode.insertBefore(evalAllDiv, listEl);
      }

      listEl.className = 'result-list';
      listEl.innerHTML = filtered.map(function (r) {
        return buildEvalItemHTML(r);
      }).join('');

      // Helper to trigger eval on a single button
      function triggerEval(btn) {
        var body = {
          source: btn.dataset.source || 'run-single',
          agent: btn.dataset.agent,
          model: btn.dataset.model,
          category: btn.dataset.category,
          task_id: btn.dataset.taskId,
          persona_id: btn.dataset.personaId,
        };
        btn.disabled = true;
        btn.textContent = 'Starting...';

        var card = createEvalProgressCard(body, btn);
        var itemEl = btn.closest('.eval-item');
        if (itemEl && itemEl.parentNode) {
          itemEl.parentNode.insertBefore(card, itemEl.nextSibling);
        }

        return apiPost('/eval', body).then(function () {
          btn.textContent = 'Evaluating...';
        }).catch(function (err) {
          btn.disabled = false;
          btn.textContent = 'Error';
          card.classList.add('eval-error');
          var finalEl = card.querySelector('.eval-final');
          if (finalEl) finalEl.innerHTML = '<span class="eval-error-text">' + QTB.escapeHtml(err.message) + '</span>';
        });
      }

      // Bind individual eval buttons
      listEl.querySelectorAll('.eval-btn').forEach(function (btn) {
        btn.addEventListener('click', function (e) {
          e.preventDefault();
          e.stopPropagation();
          triggerEval(btn);
        });
      });

      // Bind "Evaluate All Unscored" button
      var evalAllBtn = app.querySelector('#eval-all-btn');
      if (evalAllBtn) {
        evalAllBtn.addEventListener('click', function () {
          evalAllBtn.disabled = true;
          evalAllBtn.textContent = 'Starting...';
          var statusEl = app.querySelector('#eval-all-status');
          var unscoredBtns = Array.from(listEl.querySelectorAll('.eval-btn')).filter(function (b) {
            return b.textContent.trim() === 'Evaluate';
          });
          var total = unscoredBtns.length;
          var started = 0;
          unscoredBtns.forEach(function (btn) {
            triggerEval(btn);
            started++;
            if (statusEl) statusEl.textContent = started + '/' + total + ' started';
          });
          evalAllBtn.textContent = 'All Started (' + total + ')';
        });
      }
    }).catch(function (err) {
      listEl.innerHTML = '<div class="empty-state">Error: ' + err.message + '</div>';
    });
  }

  function buildEvalItemHTML(r) {
    var scored = r.has_scores;
    var detailHash = '#/results/' + [r.source || 'run-single', r.agent, r.model, r.category, r.task_id, r.persona_id].map(encodeURIComponent).join('/');
    return '<div class="result-item eval-item">' +
      '<a href="' + detailHash + '" class="ri-task ri-task-link">' + QTB.escapeHtml(r.task_id) + '</a>' +
      (r.source === 'run-group' ? '<span class="ri-source">group</span>' : '') +
      '<span class="ri-meta">' +
        '<span>' + QTB.escapeHtml(r.agent + ' / ' + r.model) + '</span>' +
        '<span>' + r.turn_count + ' turns</span>' +
        '<span>' + r.tool_count + ' tools</span>' +
      '</span>' +
      (scored
        ? '<span class="ri-badge">scored</span>'
        : '<span class="ri-badge ri-badge-unscored">unscored</span>') +
      '<button class="btn btn-sm ' + (scored ? 'btn-secondary' : 'btn-primary') + ' eval-btn"' +
        ' data-source="' + QTB.escapeHtml(r.source || 'run-single') + '"' +
        ' data-agent="' + QTB.escapeHtml(r.agent) + '"' +
        ' data-model="' + QTB.escapeHtml(r.model) + '"' +
        ' data-category="' + QTB.escapeHtml(r.category) + '"' +
        ' data-task-id="' + QTB.escapeHtml(r.task_id) + '"' +
        ' data-persona-id="' + QTB.escapeHtml(r.persona_id) + '">' +
        (scored ? 'Re-evaluate' : 'Evaluate') +
      '</button>' +
    '</div>';
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
              '<span class="detail-tag detail-tag-persona">' + QTB.escapeHtml(personaId) + '</span>' +
              (source === 'run-group' ? '<span class="detail-tag detail-tag-source">group</span>' : '') +
            '</div>' +
          '</div>' +
          '<button class="btn btn-sm btn-primary detail-eval-btn" id="detail-eval-btn">' +
            'Evaluate' +
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

      // Cost / scores modal button
      var costContent = data._cost_md || data._scores_md || '';
      if (costContent) {
        var costBtn = app.querySelector('#detail-cost-btn');
        if (costBtn) {
          costBtn.style.display = '';
          costBtn.addEventListener('click', function () {
            var title = data._cost_md ? 'Cost Report' : 'Score Report';
            var html = QTB.renderMarkdown(costContent);
            if (data._cost_md && data._scores_md) {
              html = QTB.renderMarkdown(data._scores_md) + '<hr>' + QTB.renderMarkdown(data._cost_md);
              title = 'Scores & Cost';
            }
            showModal(title, html);
          });
        }
      }

      // Bind evaluate button
      var evalBtn = app.querySelector('#detail-eval-btn');
      if (evalBtn) {
        evalBtn.addEventListener('click', function () {
          evalBtn.disabled = true;
          evalBtn.textContent = 'Starting...';
          var body = {
            source: source,
            agent: agent,
            model: model,
            category: category,
            task_id: taskId,
            persona_id: personaId,
          };
          apiPost('/eval', body).then(function () {
            evalBtn.textContent = 'Evaluating...';
            // Navigate to evaluate page for this category
            window.location.hash = '#/evaluate/s/' + encodeURIComponent(source) +
              '/a/' + encodeURIComponent(agent) +
              '/m/' + encodeURIComponent(model) +
              '/c/' + encodeURIComponent(category);
          }).catch(function (err) {
            evalBtn.disabled = false;
            evalBtn.textContent = 'Evaluate';
            showModal('Eval Error', '<p>' + QTB.escapeHtml(err.message) + '</p>');
          });
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
      if (toolsEl) QTB.buildToolReplay(toolsEl, toolLogs);
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
      listEl.className = 'folder-grid';
      listEl.innerHTML = cats.map(function (cat) {
        var label = CATEGORY_LABELS[cat] || cat.replace(/_/g, ' ');
        var count = groups[cat].length;
        return '<div class="folder-card" data-cat="' + QTB.escapeHtml(cat) + '">' +
          '<div class="folder-icon">' + FOLDER_SVG + '</div>' +
          '<div class="folder-info">' +
            '<div class="folder-name">' + QTB.escapeHtml(label) + '</div>' +
            '<div class="folder-count">' + count + ' task' + (count !== 1 ? 's' : '') + '</div>' +
          '</div>' +
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
