(function () {
  'use strict';

  var app = document.getElementById('app');
  if (!app) return;
  window.QTB = window.QTB || {};

  var state = {
    auth: {
      loaded: false,
      authMode: 'disabled',
      user: null
    },
    results: null,
    tasksPayload: null,
    detailCache: {},
    workspaceIndexCache: {},
    workspacePreviewCache: {},
    activeSessionId: null,
    review: {
      bundles: null,
      bundleCache: {},
      search: '',
      opinionFilter: 'all'
    },
    run: {
      mode: '',
      taskId: '',
      personaId: 'auto',
      sessionId: null,
      phase: 'idle',
      action: 'idle',
      background: '',
      messages: [],
      tools: [],
      toolEvents: [],
      error: '',
      busy: false,
      agentMaxSteps: 200,
      startedAt: null,
      completedAt: null
    },
    filters: {
      category: 'all',
      evalStatus: 'all',
      model: 'all',
      search: ''
    }
  };

  function escapeHtml(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function safeRenderMarkdown(markdown) {
    if (!markdown) return '<p>No report available.</p>';
    if (window.QTB && typeof window.QTB.renderMarkdown === 'function') {
      return window.QTB.renderMarkdown(markdown);
    }
    return '<pre>' + escapeHtml(markdown) + '</pre>';
  }

  function loginUrl() {
    return '/auth/login?next=' + encodeURIComponent(
      window.location.pathname + window.location.search + window.location.hash
    );
  }

  function redirectToLogin() {
    window.location.href = loginUrl();
  }

  function authFetch(path, options) {
    return fetch(path, options).then(function (response) {
      if (response.status === 401) {
        redirectToLogin();
      }
      return response;
    });
  }

  window.QTB.authFetch = authFetch;

  function api(path) {
    return authFetch('/ui' + path).then(function (response) {
      if (!response.ok) {
        return response.text().then(function (text) {
          throw new Error(text || ('HTTP ' + response.status));
        });
      }
      return response.json();
    });
  }

  function restApi(path, options) {
    options = options || {};
    options.headers = Object.assign(
      {'Content-Type': 'application/json'},
      options.headers || {}
    );
    return authFetch(path, options).then(function (response) {
      return response.text().then(function (text) {
        var payload = {};
        if (text) {
          try {
            payload = JSON.parse(text);
          } catch (error) {
            payload = {raw: text};
          }
        }
        if (!response.ok) {
          var message = payload.error || payload.raw || ('HTTP ' + response.status);
          throw new Error(message);
        }
        return payload;
      });
    });
  }

  function loadMe() {
    return fetch('/ui/me')
      .then(function (response) { return response.json(); })
      .then(function (payload) {
        state.auth.loaded = true;
        state.auth.authMode = payload.auth_mode || 'disabled';
        state.auth.user = payload.user || null;
        renderNavUser();
        if (state.auth.authMode === 'github' && !payload.authenticated) {
          redirectToLogin();
        }
        return payload;
      })
      .catch(function () {
        state.auth.loaded = true;
        renderNavUser();
      });
  }

  function renderNavUser() {
    var target = document.getElementById('nav-user');
    if (!target) return;
    var user = state.auth.user;
    if (!user) {
      if (state.auth.authMode === 'github') {
        target.innerHTML = '<a class="btn btn-secondary btn-small" href="' + loginUrl() + '">Log in</a>';
      } else {
        target.innerHTML = '<span class="badge">Auth disabled</span>';
      }
      return;
    }
    var avatar = user.avatar_url
      ? '<img class="nav-avatar" src="' + escapeHtml(user.avatar_url) + '" alt="">'
      : '<span class="nav-avatar-fallback">' + escapeHtml((user.github_login || 'U').slice(0, 1).toUpperCase()) + '</span>';
    target.innerHTML =
      '<div class="nav-user-card">' +
        avatar +
        '<span class="nav-user-name">' + escapeHtml(user.github_login || user.display_name || 'User') + '</span>' +
        (user.role === 'admin' ? '<span class="badge">Admin</span>' : '') +
        (state.auth.authMode === 'github'
          ? '<button class="btn btn-secondary btn-small" id="api-key-btn" type="button">API Key</button>' +
            '<button class="btn btn-secondary btn-small" id="auth-logout-btn" type="button">Logout</button>'
          : '<span class="badge">Auth disabled</span>') +
      '</div>';
    var apiKey = document.getElementById('api-key-btn');
    if (apiKey) {
      apiKey.addEventListener('click', openApiKeyModal);
    }
    var logout = document.getElementById('auth-logout-btn');
    if (logout) {
      logout.addEventListener('click', function () {
        fetch('/auth/logout', {method: 'POST'}).then(function () {
          state.auth.user = null;
          redirectToLogin();
        });
      });
    }
  }

  function formatDuration(value) {
    if (typeof value !== 'number' || !isFinite(value) || value <= 0) return '0s';
    if (value >= 3600) return (value / 3600).toFixed(1) + 'h';
    if (value >= 60) return (value / 60).toFixed(1) + 'm';
    return Math.round(value) + 's';
  }

  function formatTimestamp(value) {
    if (!value) return 'Unknown time';
    var date = new Date(value);
    if (isNaN(date.getTime())) return String(value);
    return date.toLocaleString();
  }

  function formatScore(value) {
    if (typeof value !== 'number' || !isFinite(value)) return '—';
    return value.toFixed(2);
  }

  function formatBytes(value) {
    if (typeof value !== 'number' || !isFinite(value) || value < 0) return '—';
    if (value >= 1024 * 1024 * 1024) return (value / (1024 * 1024 * 1024)).toFixed(1) + ' GB';
    if (value >= 1024 * 1024) return (value / (1024 * 1024)).toFixed(1) + ' MB';
    if (value >= 1024) return (value / 1024).toFixed(1) + ' KB';
    return value + ' B';
  }

  function formatInteger(value) {
    if (typeof value !== 'number' || !isFinite(value)) return '—';
    return Math.round(value).toLocaleString();
  }

  function formatCost(value) {
    if (typeof value !== 'number' || !isFinite(value)) return '—';
    return '$' + value.toFixed(4);
  }

  function renderJsonBlock(payload) {
    if (!payload) return '<p class="detail-empty-note">No JSON payload available.</p>';
    return '<pre class="detail-json-block">' + escapeHtml(JSON.stringify(payload, null, 2)) + '</pre>';
  }

  function titleCase(value) {
    return String(value || '')
      .split(/[_\s-]+/)
      .filter(Boolean)
      .map(function (part) {
        return part.charAt(0).toUpperCase() + part.slice(1);
      })
      .join(' ');
  }

  function sortedUnique(values) {
    var map = {};
    values.forEach(function (value) {
      if (value == null || value === '') return;
      map[String(value)] = true;
    });
    return Object.keys(map).sort();
  }

  function routeFromHash() {
    var hash = location.hash.slice(1);
    if (hash) return hash;
    var path = window.location.pathname || '/';
    if (path.indexOf('/review') === 0) {
      return path.replace(/\/$/, '') || '/review';
    }
    return '/results';
  }

  function setAppDetailMode(enabled) {
    if (enabled) app.classList.add('app-detail');
    else app.classList.remove('app-detail');
  }

  function setActiveNav(route) {
    var current;
    if (route.indexOf('/flow-demo') === 0) current = 'flow';
    else if (route.indexOf('/review') === 0) current = 'review';
    else if (route.indexOf('/tasks') === 0) current = 'tasks';
    else if (route === '/runs') current = 'runs';
    else if (route.indexOf('/run') === 0) current = 'run';
    else current = 'results';
    var links = document.querySelectorAll('.nav-link');
    Array.prototype.forEach.call(links, function (link) {
      if (link.getAttribute('data-route') === current) link.classList.add('active');
      else link.classList.remove('active');
    });
  }

  function renderLoading(title, description) {
    app.innerHTML =
      '<section class="loading-state">' +
        '<p class="eyebrow">Loading</p>' +
        '<h1>' + escapeHtml(title) + '</h1>' +
        '<p>' + escapeHtml(description) + '</p>' +
      '</section>';
  }

  function renderError(title, error) {
    app.innerHTML =
      '<section class="error-state">' +
        '<p class="eyebrow">Error</p>' +
        '<h1>' + escapeHtml(title) + '</h1>' +
        '<p><strong>Unable to load data.</strong></p>' +
        '<p>' + escapeHtml(error && error.message ? error.message : String(error || 'Unknown error')) + '</p>' +
      '</section>';
  }

  function ensureResults() {
    if (state.results) return Promise.resolve(state.results);
    return api('/results').then(function (payload) {
      state.results = payload.results || [];
      return state.results;
    });
  }

  function ensureTasks() {
    if (state.tasksPayload) return Promise.resolve(state.tasksPayload);
    return api('/tasks').then(function (payload) {
      state.tasksPayload = payload;
      return payload;
    });
  }

  function ensureDetail(sessionId) {
    if (state.detailCache[sessionId]) return Promise.resolve(state.detailCache[sessionId]);
    return api('/results/' + encodeURIComponent(sessionId)).then(function (payload) {
      state.detailCache[sessionId] = payload;
      return payload;
    });
  }

  function ensureWorkspaceIndex(sessionId) {
    if (state.workspaceIndexCache[sessionId]) return Promise.resolve(state.workspaceIndexCache[sessionId]);
    return api('/results/' + encodeURIComponent(sessionId) + '/workspace').then(function (payload) {
      state.workspaceIndexCache[sessionId] = payload;
      return payload;
    });
  }

  function ensureWorkspacePreview(sessionId, relativePath) {
    var cacheKey = sessionId + '::' + relativePath;
    if (state.workspacePreviewCache[cacheKey]) return Promise.resolve(state.workspacePreviewCache[cacheKey]);
    return api(
      '/results/' + encodeURIComponent(sessionId) + '/workspace/preview/' +
      encodePathPreservingSlashes(relativePath)
    ).then(function (payload) {
      state.workspacePreviewCache[cacheKey] = payload;
      return payload;
    });
  }

  function ensureReviewBundles(force) {
    if (state.review.bundles && !force) return Promise.resolve(state.review.bundles);
    return api('/review/bundles').then(function (payload) {
      state.review.bundles = payload.bundles || [];
      return state.review.bundles;
    });
  }

  function ensureReviewBundle(bundleId, force) {
    if (state.review.bundleCache[bundleId] && !force) {
      return Promise.resolve(state.review.bundleCache[bundleId]);
    }
    return api('/review/bundles/' + encodeURIComponent(bundleId)).then(function (payload) {
      state.review.bundleCache[bundleId] = payload;
      return payload;
    });
  }

  function closeModal() {
    var el = document.getElementById('qtb-modal');
    if (el) el.remove();
    document.removeEventListener('keydown', modalEscHandler);
  }

  function modalEscHandler(event) {
    if (event.key === 'Escape') closeModal();
  }

  function showModal(title, htmlContent, options) {
    options = options || {};
    closeModal();
    var overlay = document.createElement('div');
    overlay.className = 'modal-overlay' + (options.overlayClass ? ' ' + options.overlayClass : '');
    overlay.id = 'qtb-modal';
    overlay.innerHTML =
      '<div class="modal-content' + (options.contentClass ? ' ' + options.contentClass : '') + '">' +
        '<div class="modal-header' + (options.headerClass ? ' ' + options.headerClass : '') + '">' +
          '<span class="modal-title">' + escapeHtml(title) + '</span>' +
          '<button class="modal-close" id="modal-close-btn" aria-label="Close dialog">&times;</button>' +
        '</div>' +
        '<div class="modal-body' + (options.bodyClass ? ' ' + options.bodyClass : '') + '">' + htmlContent + '</div>' +
      '</div>';
    overlay.addEventListener('click', function (event) {
      if (event.target === overlay) closeModal();
    });
    document.body.appendChild(overlay);
    var closeBtn = document.getElementById('modal-close-btn');
    if (closeBtn) closeBtn.addEventListener('click', closeModal);
    document.addEventListener('keydown', modalEscHandler);
    if (state.activeSessionId) rewriteImages(overlay, state.activeSessionId);
  }

  window._qtbShowModal = showModal;

  function openApiKeyModal() {
    showModal('REST API Key', '<p class="detail-empty-note">Loading API key status...</p>');
    authFetch('/ui/api-key')
      .then(function (response) { return response.json(); })
      .then(renderApiKeyModalBody)
      .catch(function (error) {
        showModal('REST API Key', '<p class="detail-empty-note">' + escapeHtml(error && error.message ? error.message : String(error || 'Unable to load API key status.')) + '</p>');
      });
  }

  function renderApiKeyModalBody(status) {
    var created = status && status.created_at ? formatTimestamp(status.created_at * 1000) : '';
    var skillUrl = '/skills/quanttutorbench-rest-agent';
    var body =
      '<section class="info-section">' +
        '<h3>External REST Access</h3>' +
        '<p class="detail-empty-note">Use this key as <code>Authorization: Bearer &lt;api_key&gt;</code> when creating runs through <code>/client/runs/start</code>. The raw key is shown once after generation. See the <a href="' + skillUrl + '" target="_blank" rel="noreferrer">REST agent skill</a> for the full platform workflow.</p>' +
        (status && status.has_key
          ? '<div class="info-grid">' +
              metaItem('Current Key', status.key_hint ? status.key_hint + '...' : 'Active') +
              metaItem('Created', created || 'Unknown') +
            '</div>'
          : '<p class="detail-empty-note">No active API key.</p>') +
        '<div id="api-key-result" class="run-connect-section"></div>' +
        '<div class="run-actions">' +
          '<button class="btn btn-primary" id="api-key-generate-btn" type="button">' + (status && status.has_key ? 'Regenerate Key' : 'Generate Key') + '</button>' +
          (status && status.has_key ? '<button class="btn btn-secondary" id="api-key-revoke-btn" type="button">Revoke Key</button>' : '') +
        '</div>' +
      '</section>';
    showModal('REST API Key', body);
    var generate = document.getElementById('api-key-generate-btn');
    if (generate) generate.addEventListener('click', rotateApiKey);
    var revoke = document.getElementById('api-key-revoke-btn');
    if (revoke) revoke.addEventListener('click', revokeApiKey);
  }

  function rotateApiKey() {
    authFetch('/ui/api-key', {method: 'POST'})
      .then(function (response) { return response.json(); })
      .then(function (payload) {
        var target = document.getElementById('api-key-result');
        if (target) {
          target.innerHTML =
            '<h3>New Key</h3>' +
            '<code class="run-connect-cmd">' + escapeHtml(payload.api_key || '') + '</code>' +
            '<button class="btn btn-small run-copy-btn" id="api-key-copy-btn" type="button">Copy</button>' +
            '<p class="detail-empty-note">Use this key with the <a href="/skills/quanttutorbench-rest-agent" target="_blank" rel="noreferrer">REST agent skill</a> to connect your agent to the benchmark service.</p>';
          var copy = document.getElementById('api-key-copy-btn');
          if (copy) {
            copy.addEventListener('click', function () {
              if (navigator.clipboard && payload.api_key) {
                navigator.clipboard.writeText(payload.api_key);
                copy.textContent = 'Copied';
              }
            });
          }
        }
      });
  }

  function revokeApiKey() {
    authFetch('/ui/api-key', {method: 'DELETE'})
      .then(function (response) { return response.json(); })
      .then(renderApiKeyModalBody);
  }

  function buildSummaryPill(label, value) {
    return '<span class="summary-pill"><strong>' + escapeHtml(label) + '</strong> ' + escapeHtml(value) + '</span>';
  }

  function filteredResults(results) {
    return results.filter(function (item) {
      if (state.filters.category !== 'all' && item.category !== state.filters.category) return false;
      if (state.filters.evalStatus !== 'all' && item.evaluation_status !== state.filters.evalStatus) return false;
      if (state.filters.model !== 'all' && (item.model || '') !== state.filters.model) return false;
      if (state.filters.search) {
        var haystack = [item.task_id, item.session_id, item.persona_id, item.model || '']
          .join(' ')
          .toLowerCase();
        if (haystack.indexOf(state.filters.search) === -1) return false;
      }
      return true;
    });
  }

  function renderResultsList(results) {
    var categories = sortedUnique(results.map(function (item) { return item.category; }));
    var statuses = sortedUnique(results.map(function (item) { return item.evaluation_status; }));
    var models = sortedUnique(results.map(function (item) { return item.model; }).filter(Boolean));
    var visible = filteredResults(results);

    app.innerHTML =
      '<section class="page">' +
        '<header class="page-header">' +
          '<div class="page-title-wrap">' +
            '<p class="eyebrow">Results</p>' +
            '<h1>Archived sessions, rebuilt around the new session model.</h1>' +
            '<p class="subtitle">This view reads from the isolated <code>bench/server/web/</code> stack, not the legacy <code>bench/web/</code> app.</p>' +
          '</div>' +
          '<div class="summary-strip">' +
            buildSummaryPill('Sessions', String(results.length)) +
            buildSummaryPill('Client traces', String(results.filter(function (item) { return item.has_client_trace; }).length)) +
            buildSummaryPill('Models', String(models.length || 0)) +
          '</div>' +
        '</header>' +
        buildFilterBar(categories, statuses, models) +
        '<div class="results-meta">' +
          '<div class="results-count">' + escapeHtml(String(visible.length)) + ' result(s) shown</div>' +
        '</div>' +
        (visible.length ? '<div class="results-grid">' + visible.map(renderResultCard).join('') + '</div>' : renderEmptyInline('No sessions match the current filters.')) +
      '</section>';

    bindResultsFilters();
    setFilterControlValues();
  }

  function buildFilterBar(categories, statuses, models) {
    return '' +
      '<section class="panel filter-bar">' +
        buildSelectField('Category', 'filter-category', categories, state.filters.category) +
        buildSelectField('Eval Status', 'filter-eval-status', statuses, state.filters.evalStatus) +
        buildSelectField('Model', 'filter-model', models, state.filters.model) +
        '<label class="filter-field">' +
          '<span class="filter-label">Search</span>' +
          '<input id="filter-search" class="filter-input" type="search" placeholder="task_id, session_id, persona..." value="' + escapeHtml(state.filters.search) + '">' +
        '</label>' +
      '</section>';
  }

  function buildSelectField(label, id, values, selected) {
    var options = ['<option value="all">All</option>'].concat(values.map(function (value) {
      var isSelected = value === selected ? ' selected' : '';
      return '<option value="' + escapeHtml(value) + '"' + isSelected + '>' + escapeHtml(titleCase(value)) + '</option>';
    }));

    return '' +
      '<label class="filter-field">' +
        '<span class="filter-label">' + escapeHtml(label) + '</span>' +
        '<select id="' + escapeHtml(id) + '" class="filter-select">' + options.join('') + '</select>' +
      '</label>';
  }

  function renderResultCard(item) {
    var scoreChip = item.overall_score == null
      ? '<span class="meta-chip unknown-chip">OAS —</span>'
      : '<span class="meta-chip score-chip">OAS ' + escapeHtml(formatScore(item.overall_score)) + '</span>';
    var model = item.model || 'Unknown';

    return '' +
      '<a class="session-card" href="#/results/' + encodeURIComponent(item.session_id) + '">' +
        '<div class="session-top">' +
          '<div>' +
            '<h2 class="session-title">' +
              '<span>' + escapeHtml(item.task_id) + '</span>' +
              '<span class="badge">' + escapeHtml(titleCase(item.category)) + '</span>' +
              '<span class="badge">' + escapeHtml(titleCase(item.difficulty)) + '</span>' +
            '</h2>' +
            '<p class="session-subtitle">' +
              'Persona <code>' + escapeHtml(item.persona_id) + '</code> · Model <code>' + escapeHtml(model) + '</code> · ' + escapeHtml(formatTimestamp(item.timestamp)) +
            '</p>' +
          '</div>' +
          '<span class="status-pill ' + escapeHtml(item.evaluation_status) + '">' + escapeHtml(titleCase(item.evaluation_status)) + '</span>' +
        '</div>' +
        '<div class="session-meta-row">' +
          '<span class="meta-chip">' + escapeHtml(item.turn_count + ' turns') + '</span>' +
          '<span class="meta-chip">' + escapeHtml(item.tool_count + ' tools') + '</span>' +
          '<span class="meta-chip">' + escapeHtml(item.step_count + ' steps') + '</span>' +
          '<span class="meta-chip">' + escapeHtml(formatDuration(item.duration_seconds)) + '</span>' +
          '<span class="meta-chip">' + escapeHtml(item.has_content_blocks ? 'content blocks' : 'plain replay') + '</span>' +
          scoreChip +
        '</div>' +
      '</a>';
  }

  function bindResultsFilters() {
    bindFilter('filter-category', 'category');
    bindFilter('filter-eval-status', 'evalStatus');
    bindFilter('filter-model', 'model');

    var searchInput = document.getElementById('filter-search');
    if (searchInput) {
      searchInput.addEventListener('input', function (event) {
        state.filters.search = String(event.target.value || '').trim().toLowerCase();
        showResultsList();
      });
    }
  }

  function bindFilter(id, key) {
    var el = document.getElementById(id);
    if (!el) return;
    el.addEventListener('change', function (event) {
      state.filters[key] = event.target.value || 'all';
      showResultsList();
    });
  }

  function setFilterControlValues() {
    var category = document.getElementById('filter-category');
    var evalStatus = document.getElementById('filter-eval-status');
    var model = document.getElementById('filter-model');
    var search = document.getElementById('filter-search');
    if (category) category.value = state.filters.category;
    if (evalStatus) evalStatus.value = state.filters.evalStatus;
    if (model) model.value = state.filters.model;
    if (search) search.value = state.filters.search;
  }

  function renderTasksPage(payload) {
    var tasks = payload.tasks || [];
    var grouped = {};

    tasks.forEach(function (task) {
      var category = task.category || 'unknown';
      if (!grouped[category]) grouped[category] = [];
      grouped[category].push(task);
    });

    var categories = Object.keys(grouped).sort();
    var groupsHtml = categories.map(function (category) {
      var items = grouped[category];
      return '' +
        '<section class="tasks-group">' +
          '<header class="tasks-group-header">' +
            '<h2 class="tasks-group-title">' + escapeHtml(titleCase(category)) + '</h2>' +
            '<span class="summary-pill"><strong>Tasks</strong> ' + escapeHtml(String(items.length)) + '</span>' +
          '</header>' +
          '<div class="tasks-grid">' + items.map(renderTaskCard).join('') + '</div>' +
        '</section>';
    }).join('');

    app.innerHTML =
      '<section class="page">' +
        '<header class="page-header">' +
          '<div class="page-title-wrap">' +
            '<p class="eyebrow">Tasks</p>' +
            '<h1>Task inventory for the new session-centric UI.</h1>' +
            '<p class="subtitle">Grouped by benchmark category and served from the new isolated API layer.</p>' +
          '</div>' +
          '<div class="summary-strip">' +
            buildSummaryPill('Tasks', String(tasks.length)) +
            buildSummaryPill('Personas', String((payload.personas || []).length)) +
            buildSummaryPill('Categories', String(categories.length)) +
          '</div>' +
        '</header>' +
        groupsHtml +
      '</section>';
  }

  function renderTaskCard(task) {
    var personaCount = (task.persona_ids || []).length;
    var requiresCode = task.requires_code ? 'Requires code' : 'No code required';

    return '' +
      '<article class="task-card">' +
        '<h3>' + escapeHtml(task.task_id) + '</h3>' +
        '<div class="task-meta-row">' +
          '<span class="badge">' + escapeHtml(titleCase(task.difficulty || 'unknown')) + '</span>' +
          '<span class="meta-chip">' + escapeHtml((task.max_turns || 0) + ' max turns') + '</span>' +
          '<span class="meta-chip">' + escapeHtml(personaCount + ' persona' + (personaCount === 1 ? '' : 's')) + '</span>' +
        '</div>' +
        '<p class="task-description">' + escapeHtml(task.description || 'No description available.') + '</p>' +
        '<div class="task-meta-row">' +
          '<span class="meta-chip">' + escapeHtml(requiresCode) + '</span>' +
        '</div>' +
      '</article>';
  }

  function getRunTask(tasks) {
    if (!tasks || !tasks.length) return null;
    var taskId = state.run.taskId || tasks[0].task_id;
    for (var index = 0; index < tasks.length; index += 1) {
      if (tasks[index].task_id === taskId) return tasks[index];
    }
    return tasks[0];
  }

  function personasForTask(task, personas) {
    var allowed = task && task.persona_ids ? task.persona_ids : [];
    if (!allowed.length) return personas || [];
    return (personas || []).filter(function (persona) {
      return allowed.indexOf(persona.persona_id) !== -1;
    });
  }

  function publicTaskLabel(taskId) {
    var value = String(taskId || '').trim();
    var match = value.match(/^[A-Za-z]\d{2}/);
    return match ? match[0].toUpperCase() : (value || 'Task');
  }

  function runIsBusy() {
    return !!state.run.busy || !!(state.run.action && state.run.action !== 'idle');
  }

  function runCanSend() {
    return state.run.mode === 'human' &&
      state.run.sessionId &&
      state.run.phase === 'in_session' &&
      !runIsBusy();
  }

  function runStatusLabel() {
    if (state.run.action && state.run.action !== 'idle') return titleCase(state.run.action);
    if (!state.run.mode) return 'Choose Mode';
    if (!state.run.sessionId) return 'Not Started';
    return titleCase(state.run.phase || 'unknown');
  }

  function runModeLabel() {
    if (state.run.mode === 'agent') return 'Agent Test';
    if (state.run.mode === 'human') return 'Human Test';
    return 'Unselected';
  }

  function setRunAction(action) {
    state.run.action = action || 'idle';
    state.run.busy = state.run.action !== 'idle';
  }

  function addRunToolEvent(name, status, detail) {
    state.run.toolEvents = state.run.toolEvents || [];
    state.run.toolEvents.push({
      name: name,
      status: status || 'ok',
      detail: detail || '',
      timestamp: new Date().toISOString()
    });
    if (state.run.toolEvents.length > 20) {
      state.run.toolEvents = state.run.toolEvents.slice(state.run.toolEvents.length - 20);
    }
  }

  function renderRunMessage(message) {
    var role = message.role === 'tutor' ? 'tutor' : 'student';
    var label = role === 'tutor' ? 'Tutor' : 'Student';
    var body = window.QTB && typeof window.QTB.renderMarkdown === 'function'
      ? window.QTB.renderMarkdown(message.content || '')
      : '<pre>' + escapeHtml(message.content || '') + '</pre>';
    var attachments = message.attachments && message.attachments.length
      ? '<div class="run-message-attachments">' + message.attachments.map(function (item) {
        return '<span class="meta-chip">' + escapeHtml(item) + '</span>';
      }).join('') + '</div>'
      : '';

    return '' +
      '<article class="run-message ' + role + '">' +
        '<div class="run-message-label">' + escapeHtml(label) + '</div>' +
        '<div class="run-message-bubble">' + body + attachments + '</div>' +
      '</article>';
  }

  function renderRunTools() {
    if (!state.run.tools || !state.run.tools.length) {
      return '<p class="detail-empty-note">Visible tools will appear after the session starts.</p>';
    }
    return '<div class="run-tool-list">' + state.run.tools.map(function (tool) {
      var isProtocol = tool.name === 'send_message' || tool.name === 'get_background';
      return '' +
        '<div class="run-tool-chip ' + (isProtocol ? 'protocol' : 'domain') + '">' +
          '<span>' + escapeHtml(tool.name || 'unknown') + '</span>' +
          '<small>' + escapeHtml(isProtocol ? 'protocol' : 'domain') + '</small>' +
        '</div>';
    }).join('') + '</div>';
  }

  function renderRunToolEvents() {
    var events = state.run.toolEvents || [];
    if (!events.length) {
      return '<p class="detail-empty-note">Live tool activity will appear here while this browser-driven session runs.</p>';
    }
    return '<div class="run-event-list">' + events.slice().reverse().map(function (event) {
      return '' +
        '<article class="run-event ' + escapeHtml(event.status || 'ok') + '">' +
          '<div class="run-event-head">' +
            '<strong>' + escapeHtml(event.name || 'event') + '</strong>' +
            '<span>' + escapeHtml(formatTimestamp(event.timestamp)) + '</span>' +
          '</div>' +
          (event.detail ? '<p>' + escapeHtml(event.detail) + '</p>' : '') +
        '</article>';
    }).join('') + '</div>';
  }

  // ── Run list / history page ──

  var runsState = {
    runs: null,
    statusFilter: 'all'
  };

  function ensureRuns(force) {
    if (runsState.runs && !force) return Promise.resolve(runsState.runs);
    return authFetch('/ui/runs').then(function (r) { return r.json(); })
      .then(function (data) {
        runsState.runs = data.runs || [];
        return runsState.runs;
      });
  }

  function filteredRuns(runs) {
    if (runsState.statusFilter === 'all') return runs;
    return runs.filter(function (r) { return r.status === runsState.statusFilter; });
  }

  function renderRunCard(run) {
    var statusLabels = {
      waiting: '● Waiting',
      claimed: '● Claimed',
      active: '● Active',
      completed: '✓ Completed',
      failed: '✗ Failed',
      cancelled: '— Cancelled'
    };
    var label = run.public_task_label || '—';
    var statusText = statusLabels[run.status] || run.status;
    var isTerminal = run.status === 'completed' || run.status === 'failed' || run.status === 'cancelled';
    var href;
    if (run.status === 'completed' && run.session_id) {
      href = '#/results/' + encodeURIComponent(run.session_id);
    } else if (!isTerminal) {
      href = '#/run';
    } else {
      href = 'javascript:void(0)';
    }

    return '' +
      '<a class="run-card" href="' + href + '">' +
        '<div class="run-card-top">' +
          '<span class="run-card-label">' + escapeHtml(label) + '</span>' +
          '<span class="summary-pill run-status-' + escapeHtml(run.status) + '">' + escapeHtml(statusText) + '</span>' +
        '</div>' +
        '<div class="run-card-mode">' + escapeHtml(run.mode || 'agent') + '</div>' +
        '<div class="run-card-meta">' +
          (run.persona_id ? '<span class="meta-chip">' + escapeHtml(run.persona_id) + '</span>' : '') +
          (run.eval_status && run.eval_status !== 'pending' ? '<span class="meta-chip">Eval: ' + escapeHtml(run.eval_status) + '</span>' : '') +
        '</div>' +
        '<div class="run-card-time">' +
          escapeHtml(formatTimestamp(run.created_at)) +
          (run.completed_at ? ' → ' + escapeHtml(formatTimestamp(run.completed_at)) : '') +
        '</div>' +
      '</a>';
  }

  function renderRunsListPage(runs) {
    var statuses = sortedUnique(runs.map(function (r) { return r.status; }));
    var visible = filteredRuns(runs);

    var statusOptions = ['<option value="all">All</option>'].concat(statuses.map(function (s) {
      var sel = s === runsState.statusFilter ? ' selected' : '';
      return '<option value="' + escapeHtml(s) + '"' + sel + '>' + escapeHtml(titleCase(s)) + '</option>';
    }));

    app.innerHTML =
      '<section class="page">' +
        '<header class="page-header">' +
          '<div class="page-title-wrap">' +
            '<p class="eyebrow">Runs</p>' +
            '<h1>Run history and active sessions.</h1>' +
            '<p class="subtitle">All runs created through the Run layer, with real-time status tracking.</p>' +
          '</div>' +
          '<div class="summary-strip">' +
            buildSummaryPill('Total', String(runs.length)) +
            buildSummaryPill('Active', String(runs.filter(function (r) { return r.status === 'active'; }).length)) +
            buildSummaryPill('Completed', String(runs.filter(function (r) { return r.status === 'completed'; }).length)) +
          '</div>' +
        '</header>' +
        '<section class="panel filter-bar">' +
          '<label class="filter-field">' +
            '<span class="filter-label">Status</span>' +
            '<select id="runs-status-filter" class="filter-select">' + statusOptions.join('') + '</select>' +
          '</label>' +
        '</section>' +
        '<div class="results-meta">' +
          '<div class="results-count">' + escapeHtml(String(visible.length)) + ' run(s) shown</div>' +
        '</div>' +
        (visible.length
          ? '<div class="runs-grid">' + visible.map(renderRunCard).join('') + '</div>'
          : renderEmptyInline('No runs match the current filter.')) +
      '</section>';

    var filterEl = document.getElementById('runs-status-filter');
    if (filterEl) {
      filterEl.addEventListener('change', function (e) {
        runsState.statusFilter = e.target.value || 'all';
        renderRunsListPage(runs);
      });
    }
  }

  function showRuns() {
    state.activeSessionId = null;
    setAppDetailMode(false);
    renderLoading('Loading runs', 'Fetching run history from the /ui/runs endpoint.');
    ensureRuns(true).then(renderRunsListPage).catch(function (error) {
      renderError('Runs unavailable', error);
    });
  }

  function renderRunPage(payload) {
    var tasks = payload.tasks || [];
    var selectedTask = getRunTask(tasks);
    if (selectedTask && !state.run.taskId) state.run.taskId = selectedTask.task_id;

    if (!state.run.mode) {
      renderRunModePicker(payload);
      return;
    }

    if (state.run.mode === 'agent') {
      if (window.QTB && typeof window.QTB.renderMyAgentPage === 'function') {
        window.QTB.renderMyAgentPage(app, state, payload);
      } else {
        renderAgentRunPage(payload);
      }
      return;
    }

    renderHumanRunPage(payload);
  }

  function renderRunModePicker(payload) {
    var tasks = payload.tasks || [];
    app.innerHTML =
      '<section class="page run-page">' +
        '<header class="page-header">' +
          '<div class="page-title-wrap">' +
            '<p class="eyebrow">Run</p>' +
            '<h1>Choose how to run the benchmark.</h1>' +
            '<p class="subtitle">Connect your own agent, try it yourself in the browser, or watch the baseline agent run.</p>' +
          '</div>' +
          '<div class="summary-strip">' +
            buildSummaryPill('Mode', runModeLabel()) +
            buildSummaryPill('Tasks', String(tasks.length)) +
          '</div>' +
        '</header>' +
        '<div class="run-mode-grid">' +
          '<article class="panel run-mode-card">' +
            '<p class="eyebrow">Your Agent</p>' +
            '<h2>My Agent</h2>' +
            '<p>Create a run and get connection details (MCP URL + token or REST endpoints). Connect your own agent to execute the benchmark.</p>' +
            '<button class="btn btn-primary" id="run-mode-agent" type="button">My Agent</button>' +
          '</article>' +
          '<article class="panel run-mode-card">' +
            '<p class="eyebrow">Manual</p>' +
            '<h2>Try it myself</h2>' +
            '<p>Use the browser-driven REST harness to manually tutor the student. Useful for understanding the benchmark tasks.</p>' +
            '<button class="btn btn-secondary" id="run-mode-human" type="button">Try it myself</button>' +
          '</article>' +
          '<article class="panel run-mode-card">' +
            '<p class="eyebrow">Demo</p>' +
            '<h2>Watch Baseline</h2>' +
            '<p>Watch the baseline agent (Claude) complete the task automatically. Server starts the agent — just observe.</p>' +
            '<button class="btn btn-secondary" id="run-mode-baseline" type="button" disabled title="Coming soon">Watch Baseline</button>' +
          '</article>' +
        '</div>' +
      '</section>';
    bindRunModeControls();
  }

  function renderAgentRunPage(payload) {
    var tasks = payload.tasks || [];
    var personas = payload.personas || [];
    var selectedTask = getRunTask(tasks);
    var visiblePersonas = personasForTask(selectedTask, personas);
    var selectedPersona = state.run.personaId || 'auto';
    var isBusy = runIsBusy();
    var taskOptions = tasks.map(function (task) {
      return '<option value="' + escapeHtml(task.task_id) + '"' + (task.task_id === state.run.taskId ? ' selected' : '') + '>' +
        escapeHtml(publicTaskLabel(task.task_id)) +
      '</option>';
    }).join('');
    var personaOptions =
      '<option value="auto"' + (selectedPersona === 'auto' ? ' selected' : '') + '>Auto select</option>' +
      visiblePersonas.map(function (persona) {
        return '<option value="' + escapeHtml(persona.persona_id) + '"' + (persona.persona_id === selectedPersona ? ' selected' : '') + '>' +
          escapeHtml(persona.persona_id) +
        '</option>';
      }).join('');

    app.innerHTML =
      '<section class="page run-page">' +
        '<header class="page-header run-sticky-header">' +
          '<div class="page-title-wrap">' +
            '<p class="eyebrow">Run · Agent Test</p>' +
            '<h1>Automated client benchmark flow.</h1>' +
            '<p class="subtitle">Agent run module (run-agent.js) failed to load. This is a fallback page — refresh to retry.</p>' +
          '</div>' +
          '<div class="summary-strip">' +
            buildSummaryPill('Mode', runModeLabel()) +
            buildSummaryPill('Status', 'Module Missing') +
            buildSummaryPill('Task', publicTaskLabel(state.run.taskId || (selectedTask && selectedTask.task_id))) +
          '</div>' +
        '</header>' +
        '<div class="run-agent-grid">' +
          '<aside class="panel run-control-panel">' +
            '<h2>Agent Setup</h2>' +
            '<label class="filter-field">' +
              '<span class="filter-label">Task</span>' +
              '<select id="run-task-select" class="filter-select"' + (isBusy ? ' disabled' : '') + '>' + taskOptions + '</select>' +
            '</label>' +
            '<label class="filter-field">' +
              '<span class="filter-label">Persona Control</span>' +
              '<select id="run-persona-select" class="filter-select"' + (isBusy ? ' disabled' : '') + '>' + personaOptions + '</select>' +
            '</label>' +
            '<label class="filter-field">' +
              '<span class="filter-label">Max Agent Steps</span>' +
              '<input id="run-agent-steps-input" class="filter-input" type="number" min="0" value="' + escapeHtml(state.run.agentMaxSteps || 200) + '"' + (isBusy ? ' disabled' : '') + '>' +
            '</label>' +
            '<div class="run-actions">' +
              '<button class="btn btn-primary" type="button" disabled>Start Agent Run</button>' +
              '<button class="btn btn-secondary" id="run-mode-reset-btn" type="button">Change Mode</button>' +
            '</div>' +
          '</aside>' +
          '<section class="panel run-conversation-panel run-agent-main">' +
            '<div class="run-panel-header">' +
              '<div>' +
                '<h2>Execution Boundary</h2>' +
                '<p>The automated flow is not a chat form. It should be a backend job that launches the client runner, polls job state, captures stdout/stderr, and links to the archived result when the client trace is written.</p>' +
              '</div>' +
            '</div>' +
            '<ol class="run-flow-list">' +
              '<li><strong>register_session</strong> with the internal full task id and optional persona id.</li>' +
              '<li><strong>start_session</strong> returns the client-visible background and student opening.</li>' +
              '<li><strong>list_tools</strong> exposes <code>send_message</code>, <code>get_background</code>, and domain tools to the agent.</li>' +
              '<li><strong>adapter.generate_response</strong> runs once; the tool runner handles domain tools and student communication.</li>' +
              '<li><strong>save_client_trace</strong> writes <code>results/client/{session_id}/client_trace.json</code>, then Results can render the merged replay.</li>' +
            '</ol>' +
            '<div class="run-status-note">' +
              '<strong>Required before enabling this button:</strong> add server endpoints for creating/cancelling agent jobs, safe subprocess lifecycle, result-dir wiring, live log/tool polling, and completion mapping back to Results.' +
            '</div>' +
          '</section>' +
          '<aside class="panel run-tools-panel">' +
            '<h2>Live Tools</h2>' +
            '<p class="detail-empty-note">Agent tool events cannot be streamed here until the backend job layer exposes them. Result detail already renders them after archive creation.</p>' +
          '</aside>' +
        '</div>' +
      '</section>';
    bindRunControls(tasks);
  }

  function renderHumanRunPage(payload) {
    var tasks = payload.tasks || [];
    var personas = payload.personas || [];
    var selectedTask = getRunTask(tasks);
    var visiblePersonas = personasForTask(selectedTask, personas);
    var selectedPersona = state.run.personaId || 'auto';
    var isBusy = runIsBusy();
    var isLocked = !!state.run.sessionId || isBusy;
    var taskOptions = tasks.map(function (task) {
      return '<option value="' + escapeHtml(task.task_id) + '"' + (task.task_id === state.run.taskId ? ' selected' : '') + '>' +
        escapeHtml(publicTaskLabel(task.task_id)) +
      '</option>';
    }).join('');
    var personaOptions =
      '<option value="auto"' + (selectedPersona === 'auto' ? ' selected' : '') + '>Auto select</option>' +
      visiblePersonas.map(function (persona) {
        return '<option value="' + escapeHtml(persona.persona_id) + '"' + (persona.persona_id === selectedPersona ? ' selected' : '') + '>' +
          escapeHtml(persona.persona_id) +
        '</option>';
      }).join('');
    var messagesHtml = state.run.messages.length
      ? state.run.messages.map(renderRunMessage).join('')
      : '<div class="run-empty-conversation">Start a human test session to see the student opening message here.</div>';
    var sessionMeta = state.run.sessionId
      ? [
        metaItem('Session ID', state.run.sessionId),
        metaItem('Phase', runStatusLabel()),
        metaItem('Task', publicTaskLabel(state.run.taskId)),
        metaItem('Client Context', state.run.background ? 'Loaded' : 'Waiting')
      ].join('')
      : '<p class="detail-empty-note">No active run yet. This panel will only show client-visible runtime context after start_session.</p>';
    var actionButton = state.run.sessionId
      ? '<button class="btn btn-secondary" id="run-refresh-btn" type="button"' + (isBusy ? ' disabled' : '') + '>Refresh</button>'
      : '<button class="btn btn-primary" id="run-start-btn" type="button"' + (isBusy || !selectedTask ? ' disabled' : '') + '>Create & Start Session</button>';
    var openResult = state.run.sessionId && state.run.phase === 'completed'
      ? '<a class="btn btn-secondary" href="#/results/' + encodeURIComponent(state.run.sessionId) + '">Open Result</a>'
      : '';
    var cancelButton = state.run.sessionId && state.run.phase !== 'completed'
      ? '<button class="btn btn-secondary" id="run-cancel-btn" type="button"' + (isBusy ? ' disabled' : '') + '>Cancel</button>'
      : '';

    app.innerHTML =
      '<section class="page run-page">' +
        '<header class="page-header run-sticky-header">' +
          '<div class="page-title-wrap">' +
            '<p class="eyebrow">Run · Human Test</p>' +
            '<h1>Browser-driven REST session harness.</h1>' +
            '<p class="subtitle">Manual mode is kept for human testing. The visible task label and info panel avoid exposing hidden task metadata during execution.</p>' +
          '</div>' +
          '<div class="summary-strip">' +
            buildSummaryPill('Mode', runModeLabel()) +
            buildSummaryPill('Status', runStatusLabel()) +
            buildSummaryPill('Task', publicTaskLabel(state.run.taskId || (selectedTask && selectedTask.task_id))) +
            buildSummaryPill('Tools', String(state.run.tools.length)) +
          '</div>' +
        '</header>' +
        (state.run.error ? '<section class="run-error">' + escapeHtml(state.run.error) + '</section>' : '') +
        '<div class="run-grid">' +
          '<aside class="panel run-control-panel">' +
            '<h2>Info</h2>' +
            '<label class="filter-field">' +
              '<span class="filter-label">Task</span>' +
              '<select id="run-task-select" class="filter-select"' + (isLocked ? ' disabled' : '') + '>' + taskOptions + '</select>' +
            '</label>' +
            '<label class="filter-field">' +
              '<span class="filter-label">Persona Control</span>' +
              '<select id="run-persona-select" class="filter-select"' + (isLocked ? ' disabled' : '') + '>' + personaOptions + '</select>' +
            '</label>' +
            '<div class="run-actions">' + actionButton + openResult + cancelButton +
              '<button class="btn btn-secondary" id="run-reset-btn" type="button"' + (isBusy ? ' disabled' : '') + '>Reset UI</button>' +
              '<button class="btn btn-secondary" id="run-mode-reset-btn" type="button"' + (isBusy ? ' disabled' : '') + '>Change Mode</button>' +
            '</div>' +
            '<section class="run-meta-block">' +
              '<h3>Client-Visible State</h3>' +
              sessionMeta +
            '</section>' +
            '<section class="run-meta-block">' +
              '<h3>Client Context</h3>' +
              (state.run.background
                ? '<div class="run-background-text">' + safeRenderMarkdown(state.run.background) + '</div>'
                : '<p class="detail-empty-note">The background returned by start_session appears here. Hidden task metadata is not shown.</p>') +
            '</section>' +
          '</aside>' +
          '<section class="panel run-conversation-panel">' +
            '<div class="run-panel-header">' +
              '<div>' +
                '<h2>Conversation</h2>' +
                '<p>Student messages returned by <code>start_session</code> and <code>send_message</code>. This column intentionally takes most of the width.</p>' +
              '</div>' +
            '</div>' +
            '<div id="run-conversation" class="run-conversation">' + messagesHtml + '</div>' +
            '<form id="run-send-form" class="run-send-form">' +
              '<label class="filter-field">' +
                '<span class="filter-label">Tutor Message</span>' +
                '<textarea id="run-message-input" class="run-textarea" rows="5" placeholder="Write a message that will be delivered through send_message..."' + (!runCanSend() ? ' disabled' : '') + '></textarea>' +
              '</label>' +
              '<label class="filter-field">' +
                '<span class="filter-label">Attachments</span>' +
                '<input id="run-attachments-input" class="filter-input" type="text" placeholder="Optional: strategy.py, plots/chart.png" ' + (!runCanSend() ? ' disabled' : '') + '>' +
              '</label>' +
              '<button class="btn btn-primary" type="submit"' + (!runCanSend() ? ' disabled' : '') + '>' + (state.run.action === 'sending' ? 'Sending...' : 'Send Message') + '</button>' +
            '</form>' +
          '</section>' +
          '<aside class="panel run-tools-panel">' +
            '<h2>Tools</h2>' +
            '<section class="run-meta-block compact">' +
              '<h3>Visible To Client</h3>' +
              renderRunTools() +
            '</section>' +
            '<section class="run-meta-block compact">' +
              '<h3>Live Activity</h3>' +
              renderRunToolEvents() +
            '</section>' +
          '</aside>' +
        '</div>' +
      '</section>';

    bindRunControls(tasks);
  }

  function bindRunModeControls() {
    var agentBtn = document.getElementById('run-mode-agent');
    var humanBtn = document.getElementById('run-mode-human');
    var baselineBtn = document.getElementById('run-mode-baseline');

    // Allow run-agent.js to reset back to mode picker
    if (window.QTB) {
      window.QTB._resetRunMode = function () {
        state.run.mode = '';
        state.run.error = '';
        renderRunPage(state.tasksPayload || {tasks: [], personas: []});
      };
    }
    if (agentBtn) {
      agentBtn.addEventListener('click', function () {
        state.run.mode = 'agent';
        state.run.error = '';
        renderRunPage(state.tasksPayload || {tasks: [], personas: []});
      });
    }
    if (humanBtn) {
      humanBtn.addEventListener('click', function () {
        state.run.mode = 'human';
        state.run.error = '';
        renderRunPage(state.tasksPayload || {tasks: [], personas: []});
      });
    }
  }

  function bindRunControls(tasks) {
    var taskSelect = document.getElementById('run-task-select');
    var personaSelect = document.getElementById('run-persona-select');
    var agentStepsInput = document.getElementById('run-agent-steps-input');
    var startBtn = document.getElementById('run-start-btn');
    var refreshBtn = document.getElementById('run-refresh-btn');
    var cancelBtn = document.getElementById('run-cancel-btn');
    var resetBtn = document.getElementById('run-reset-btn');
    var modeResetBtn = document.getElementById('run-mode-reset-btn');
    var sendForm = document.getElementById('run-send-form');

    if (taskSelect) {
      taskSelect.addEventListener('change', function (event) {
        state.run.taskId = event.target.value;
        state.run.personaId = 'auto';
        renderRunPage(state.tasksPayload || {tasks: tasks, personas: []});
      });
    }
    if (personaSelect) {
      personaSelect.addEventListener('change', function (event) {
        state.run.personaId = event.target.value || 'auto';
      });
    }
    if (agentStepsInput) {
      agentStepsInput.addEventListener('change', function (event) {
        var value = parseInt(event.target.value, 10);
        state.run.agentMaxSteps = isFinite(value) && value >= 0 ? value : 200;
      });
    }
    if (startBtn) startBtn.addEventListener('click', startRunSession);
    if (refreshBtn) refreshBtn.addEventListener('click', refreshRunStatus);
    if (cancelBtn) cancelBtn.addEventListener('click', cancelRunSession);
    if (resetBtn) resetBtn.addEventListener('click', resetRunState);
    if (modeResetBtn) modeResetBtn.addEventListener('click', resetRunMode);
    if (sendForm) {
      sendForm.addEventListener('submit', function (event) {
        event.preventDefault();
        sendRunMessage();
      });
    }

    var conversationEl = document.getElementById('run-conversation');
    if (conversationEl) conversationEl.scrollTop = conversationEl.scrollHeight;
  }

  function setRunBusy(busy) {
    setRunAction(busy ? 'working' : 'idle');
    renderRunPage(state.tasksPayload || {tasks: [], personas: []});
  }

  function startRunSession() {
    var taskId = state.run.taskId;
    if (!taskId || state.run.mode !== 'human' || runIsBusy()) return;
    setRunAction('registering');
    state.run.error = '';
    renderRunPage(state.tasksPayload || {tasks: [], personas: []});

    var registerBody = {task_id: taskId};
    if (state.run.personaId && state.run.personaId !== 'auto') {
      registerBody.persona_id = state.run.personaId;
    }

    restApi('/session/register', {
      method: 'POST',
      body: JSON.stringify(registerBody)
    }).then(function (registered) {
      if (!registered.accepted) {
        throw new Error(registered.error || 'Session registration rejected.');
      }
      state.run.sessionId = registered.session_id;
      state.run.phase = 'registered';
      state.run.startedAt = new Date().toISOString();
      addRunToolEvent('register_session', 'ok', 'REST session registered.');
      setRunAction('starting');
      renderRunPage(state.tasksPayload || {tasks: [], personas: []});
      return restApi('/session/' + encodeURIComponent(registered.session_id) + '/start', {
        method: 'POST',
        body: JSON.stringify({})
      });
    }).then(function (started) {
      state.run.phase = 'in_session';
      state.run.background = started.background || '';
      state.run.messages = [];
      if (started.student_message) {
        state.run.messages.push({role: 'student', content: started.student_message});
      }
      addRunToolEvent('start_session', 'ok', started.student_message ? 'Student opening received.' : 'Session started.');
      return refreshRunStatus({silent: true, preserveAction: true});
    }).catch(function (error) {
      state.run.error = error && error.message ? error.message : String(error || 'Unknown error');
      addRunToolEvent('run_start', 'error', state.run.error);
    }).then(function () {
      setRunAction('idle');
      renderRunPage(state.tasksPayload || {tasks: [], personas: []});
    });
  }

  function refreshRunStatus(options) {
    options = options || {};
    if (!state.run.sessionId) return Promise.resolve();
    if (!options.silent) setRunAction('refreshing');
    if (!options.silent) renderRunPage(state.tasksPayload || {tasks: [], personas: []});

    return restApi('/session/' + encodeURIComponent(state.run.sessionId), {
      method: 'GET',
      headers: {}
    }).then(function (status) {
      state.run.phase = status.phase || state.run.phase;
      state.run.personaId = status.persona_id || state.run.personaId;
      return restApi('/session/' + encodeURIComponent(state.run.sessionId) + '/tools', {
        method: 'GET',
        headers: {}
      }).catch(function () {
        return {tools: []};
      });
    }).then(function (toolsPayload) {
      state.run.tools = toolsPayload.tools || [];
      state.run.error = '';
      if (!options.silent) addRunToolEvent('list_tools', 'ok', state.run.tools.length + ' visible tools.');
    }).catch(function (error) {
      state.run.error = error && error.message ? error.message : String(error || 'Unknown error');
      if (!options.silent) addRunToolEvent('refresh', 'error', state.run.error);
    }).then(function () {
      if (!options.preserveAction) setRunAction('idle');
      if (!options.silent) renderRunPage(state.tasksPayload || {tasks: [], personas: []});
    });
  }

  function parseRunAttachments(value) {
    return String(value || '')
      .split(/[,\n]/)
      .map(function (item) { return item.trim(); })
      .filter(Boolean)
      .slice(0, 3);
  }

  function sendRunMessage() {
    if (!runCanSend()) return;
    var messageInput = document.getElementById('run-message-input');
    var attachmentsInput = document.getElementById('run-attachments-input');
    var text = messageInput ? String(messageInput.value || '').trim() : '';
    var attachments = attachmentsInput ? parseRunAttachments(attachmentsInput.value) : [];
    if (!text) {
      state.run.error = 'Message text is required.';
      renderRunPage(state.tasksPayload || {tasks: [], personas: []});
      return;
    }

    setRunAction('sending');
    state.run.error = '';
    renderRunPage(state.tasksPayload || {tasks: [], personas: []});

    restApi('/session/' + encodeURIComponent(state.run.sessionId) + '/send', {
      method: 'POST',
      body: JSON.stringify({text: text, attachments: attachments})
    }).then(function (reply) {
      if (reply.error) {
        throw new Error(reply.error);
      }
      state.run.messages.push({role: 'tutor', content: text, attachments: attachments});
      if (reply.student_message) {
        state.run.messages.push({role: 'student', content: reply.student_message});
      }
      state.run.phase = reply.status === 'completed' ? 'completed' : 'in_session';
      addRunToolEvent(
        'send_message',
        'ok',
        (attachments.length ? attachments.length + ' attachment(s). ' : '') +
          'Student status: ' + (reply.status || state.run.phase) + '.'
      );
      if (reply.status === 'completed') {
        state.run.completedAt = new Date().toISOString();
        state.results = null;
        state.detailCache = {};
      }
      return refreshRunStatus({silent: true, preserveAction: true});
    }).catch(function (error) {
      state.run.error = error && error.message ? error.message : String(error || 'Unknown error');
      addRunToolEvent('send_message', 'error', state.run.error);
    }).then(function () {
      setRunAction('idle');
      renderRunPage(state.tasksPayload || {tasks: [], personas: []});
    });
  }

  function cancelRunSession() {
    if (!state.run.sessionId || runIsBusy()) return;
    setRunAction('cancelling');
    state.run.error = '';
    renderRunPage(state.tasksPayload || {tasks: [], personas: []});
    restApi('/session/' + encodeURIComponent(state.run.sessionId), {
      method: 'DELETE',
      headers: {}
    }).then(function () {
      state.run.phase = 'cancelled';
      state.run.tools = [];
      addRunToolEvent('cancel_session', 'ok', 'Session cancelled by UI.');
    }).catch(function (error) {
      state.run.error = error && error.message ? error.message : String(error || 'Unknown error');
      addRunToolEvent('cancel_session', 'error', state.run.error);
    }).then(function () {
      setRunAction('idle');
      renderRunPage(state.tasksPayload || {tasks: [], personas: []});
    });
  }

  function resetRunState() {
    state.run = {
      mode: state.run.mode,
      taskId: state.run.taskId,
      personaId: 'auto',
      sessionId: null,
      phase: 'idle',
      action: 'idle',
      background: '',
      messages: [],
      tools: [],
      toolEvents: [],
      error: '',
      busy: false,
      agentMaxSteps: state.run.agentMaxSteps || 200,
      startedAt: null,
      completedAt: null
    };
    renderRunPage(state.tasksPayload || {tasks: [], personas: []});
  }

  function resetRunMode() {
    if (runIsBusy()) return;
    resetRunState();
    state.run.mode = '';
    renderRunPage(state.tasksPayload || {tasks: [], personas: []});
  }

  function metaItem(label, value) {
    return '' +
      '<div class="detail-meta-item">' +
        '<span class="dm-label">' + escapeHtml(label) + '</span>' +
        '<span class="dm-value">' + escapeHtml(value) + '</span>' +
      '</div>';
  }

  function encodePathPreservingSlashes(path) {
    return String(path || '')
      .split('/')
      .filter(Boolean)
      .map(function (segment) {
        return encodeURIComponent(segment);
      })
      .join('/');
  }

  function decodePathPreservingSlashes(path) {
    return String(path || '')
      .split('/')
      .map(function (segment) {
        try {
          return decodeURIComponent(segment);
        } catch (error) {
          return segment;
        }
      })
      .join('/');
  }

  function normalizeAssetPath(src) {
    if (!src) return '';
    var clean = String(src).trim().split('#')[0].split('?')[0];
    if (!clean) return '';
    if (/^(https?:|data:|blob:)/i.test(clean)) return '';
    if (clean.indexOf('/ui/results/') === 0) return '';
    if (clean.indexOf('/api/results/') === 0) return '';
    clean = clean.replace(/^\/api\/files\/live\//, '');
    clean = clean.replace(/^api\/files\/live\//, '');
    clean = clean.replace(/^\/workspace\//, '');
    clean = clean.replace(/^workspace\//, '');
    clean = clean.replace(/^\.\//, '');
    clean = clean.replace(/^\/+/, '');
    return decodePathPreservingSlashes(clean);
  }

  function buildSessionAssetUrl(sessionId, src) {
    var relativePath = normalizeAssetPath(src);
    if (!relativePath) return null;
    return '/ui/results/' + encodeURIComponent(sessionId) + '/files/' + encodePathPreservingSlashes(relativePath);
  }

  function rewriteImages(container, sessionId) {
    if (!container || !sessionId) return;
    var images = container.querySelectorAll('img');
    Array.prototype.forEach.call(images, function (img) {
      var rawSrc = img.getAttribute('src') || '';
      var nextSrc = buildSessionAssetUrl(sessionId, rawSrc);
      if (!nextSrc) return;
      img.setAttribute('src', nextSrc);
      img.setAttribute('loading', 'lazy');
    });
  }

  function buildInfoSection(title, bodyHtml) {
    return '<section class="info-section"><h2>' + escapeHtml(title) + '</h2>' + bodyHtml + '</section>';
  }

  function summarizeWorkspaceTypes(paths) {
    var counts = {};
    (paths || []).forEach(function (path) {
      var normalized = String(path || '').trim();
      if (!normalized) return;
      var slash = normalized.lastIndexOf('/');
      var name = slash >= 0 ? normalized.slice(slash + 1) : normalized;
      var dot = name.lastIndexOf('.');
      var ext = dot >= 0 ? name.slice(dot).toLowerCase() : '[no_ext]';
      counts[ext] = (counts[ext] || 0) + 1;
    });
    return Object.keys(counts).sort(function (left, right) {
      if (counts[right] !== counts[left]) return counts[right] - counts[left];
      return left.localeCompare(right);
    }).slice(0, 4);
  }

  function buildWorkspaceSummarySection(detail) {
    var files = detail.workspace_files || [];
    if (!files.length) {
      return buildInfoSection(
        'Workspace',
        '<p class="detail-empty-note">No archived workspace files were recorded for this session.</p>'
      );
    }

    var types = summarizeWorkspaceTypes(files).map(function (ext) {
      return ext === '[no_ext]' ? 'no extension' : ext.replace(/^\./, '');
    });

    return buildInfoSection('Workspace', [
      metaItem('File Count', String(files.length)),
      metaItem('Primary Types', types.length ? types.join(' / ') : 'mixed'),
      '<p class="detail-empty-note">Use the Workspace button in the top-right corner to browse archived artifacts without leaving this result page.</p>'
    ].join(''));
  }

  function extractEvalHistoryScore(entry) {
    if (!entry || typeof entry !== 'object') return null;
    if (typeof entry.overall_score === 'number') return entry.overall_score;
    if (typeof entry.oas === 'number') return entry.oas;
    if (typeof entry.score === 'number') return entry.score;
    return null;
  }

  function renderEvalHistoryItem(entry) {
    var score = extractEvalHistoryScore(entry);
    var label = entry.score_id || entry.eval_dir || entry.created_at || 'Evaluation';
    var timestamp = formatTimestamp(entry.completed_at || entry.created_at || entry.timestamp);
    var status = entry.status || entry.score_status || 'unknown';
    return '' +
      '<div class="detail-history-item">' +
        '<strong>' + escapeHtml(label) + '</strong>' +
        '<div class="detail-history-meta">' +
          escapeHtml(titleCase(status)) + ' · ' + escapeHtml(timestamp) +
          (score == null ? '' : ' · OAS ' + escapeHtml(formatScore(score))) +
        '</div>' +
      '</div>';
  }

  function renderScoreJsonReport(detail) {
    if (!detail.score_json) return '<p class="detail-empty-note">No score JSON available.</p>';
    var score = detail.score_json;
    var judgeReliability = score.judge_reliability || {};
    var validationRunId = judgeReliability.validation_run_id || '—';
    var validationMatch = judgeReliability.current_eval_model_matches_reference;
    var validationMatchText = validationMatch == null ? '—' : (validationMatch ? 'Yes' : 'No');
    var summary = [
      metaItem('Score ID', score.score_id || '—'),
      metaItem('Status', titleCase(score.score_status || 'unknown')),
      metaItem('Mode', titleCase(score.eval_mode || 'full')),
      metaItem('Overall Score', score.overall_score == null ? '—' : formatScore(score.overall_score)),
      metaItem('Judge Validation Run', validationRunId),
      metaItem('Judge Model Match', validationMatchText),
      metaItem('Completed', formatTimestamp(score.completed_at))
    ].join('');
    return buildInfoSection('Score Summary', summary) +
      buildInfoSection('Score JSON', renderJsonBlock(score));
  }

  function renderCostReport(detail) {
    var summary = [
      metaItem('Student Simulator Cost', detail.simulator_cost != null ? formatCost(detail.simulator_cost) : '—'),
      metaItem('Evaluation Cost', detail.evaluation_cost != null ? formatCost(detail.evaluation_cost) : '—'),
      metaItem('Displayed Total', detail.total_cost != null ? formatCost(detail.total_cost) : '—')
    ].join('');
    return buildInfoSection('Cost Summary', summary) +
      buildInfoSection('Evaluation Cost JSON', renderJsonBlock(detail.cost_json));
  }

  function buildInfoPanel(detail) {
    var summary = [
      metaItem('Session ID', detail.session_id),
      metaItem('Task ID', detail.task_id),
      metaItem('Persona', detail.persona_id),
      metaItem('Category', titleCase(detail.category)),
      metaItem('Difficulty', titleCase(detail.difficulty)),
      metaItem('Timestamp', formatTimestamp(detail.timestamp)),
      metaItem('Duration', formatDuration(detail.duration_seconds)),
      metaItem('Turns', String(detail.turn_count)),
      metaItem('Tool Calls', String(detail.tool_count)),
      metaItem('Send Messages', String(detail.send_message_count || 0)),
      metaItem('Steps', String(detail.step_count)),
      metaItem('Evaluation Status', titleCase(detail.evaluation_status)),
      metaItem('Overall Score', detail.overall_score == null ? '—' : formatScore(detail.overall_score)),
      metaItem('Student + Eval Cost', detail.total_cost == null ? '—' : formatCost(detail.total_cost))
    ].join('');

    return buildInfoSection('Summary', summary);
  }

  function buildDetailActions(detail) {
    var buttons = [];
    var exportUrl = '/ui/results/' + encodeURIComponent(detail.session_id) + '/export';
    buttons.push(
      '<a class="detail-report-btn" href="' + escapeHtml(exportUrl) + '" download="' +
      escapeHtml((detail.session_id || 'session') + '_run_state.json') +
      '">Export JSON</a>'
    );
    // Server / Client / Eval History
    buttons.push('<button class="detail-report-btn" id="detail-server-btn">Server</button>');
    buttons.push(
      '<button class="detail-report-btn" id="detail-client-btn"' +
      (detail.has_client_trace ? '' : ' disabled title="No client trace"') +
      '>Client</button>'
    );
    if (detail.eval_history && detail.eval_history.length) {
      buttons.push('<button class="detail-report-btn" id="detail-eval-history-btn">Eval History</button>');
    }
    if (detail.score_json) {
      buttons.push('<button class="detail-report-btn" id="detail-score-btn">Score JSON</button>');
    }
    if (detail.cost_json || detail.simulator_cost != null || detail.evaluation_cost != null) {
      buttons.push('<button class="detail-report-btn" id="detail-cost-btn">Cost</button>');
    }
    return buttons.join('');
  }

  function bindPanelControl(scope, options) {
    var panel = scope.querySelector(options.panelSelector);
    var tab = scope.querySelector('#' + options.tabId);
    if (!panel) return;

    var openArrow = options.isLeft ? '\u25C0' : '\u25B6';
    var closedArrow = options.isLeft ? '\u25B6' : '\u25C0';

    function rememberExpandedWidth() {
      var width = panel.getBoundingClientRect().width;
      if (width > 40) {
        panel.dataset.expandedWidth = String(width);
      }
    }

    function getExpandedWidth() {
      var remembered = parseFloat(panel.dataset.expandedWidth || '');
      if (isFinite(remembered) && remembered > 0) return remembered;
      var liveWidth = panel.getBoundingClientRect().width || panel.offsetWidth;
      return liveWidth > 0 ? liveWidth : 0;
    }

    function sync() {
      var collapsed = panel.classList.contains('collapsed');
      if (!collapsed) rememberExpandedWidth();
      if (tab) {
        var tabArrow = tab.querySelector('.tab-arrow');
        if (tabArrow) tabArrow.textContent = collapsed ? closedArrow : openArrow;
        tab.classList.toggle('is-collapsed', collapsed);
        tab.classList.toggle('is-expanded', !collapsed);
        if (options.isLeft) {
          tab.style.left = (collapsed ? 0 : getExpandedWidth()) + 'px';
          tab.style.right = 'auto';
          tab.style.transform = 'translateY(-50%)';
        } else {
          tab.style.left = 'auto';
          tab.style.right = (collapsed ? 0 : getExpandedWidth()) + 'px';
          tab.style.transform = 'translateY(-50%)';
        }
        tab.setAttribute('aria-expanded', String(!collapsed));
        tab.setAttribute('title', (collapsed ? 'Expand ' : 'Collapse ') + options.label);
        tab.setAttribute('aria-label', (collapsed ? 'Expand ' : 'Collapse ') + options.label);
      }
    }

    function toggle() {
      if (!panel.classList.contains('collapsed')) {
        rememberExpandedWidth();
      }
      panel.classList.toggle('collapsed');
      sync();
    }

    if (tab) tab.addEventListener('click', toggle);
    panel.addEventListener('transitionend', function () {
      rememberExpandedWidth();
      sync();
    });
    window.addEventListener('resize', sync);
    sync();
  }

  function buildWorkspaceModalShell() {
    return '' +
      '<div class="workspace-explorer">' +
        '<aside class="workspace-sidebar">' +
          '<div class="workspace-sidebar-head">' +
            '<input id="workspace-search-input" class="workspace-search-input" type="search" placeholder="Search files...">' +
            '<div id="workspace-summary" class="workspace-summary">Loading workspace…</div>' +
          '</div>' +
          '<div id="workspace-file-list" class="workspace-file-list">' +
            '<div class="workspace-empty">Loading archived files…</div>' +
          '</div>' +
        '</aside>' +
        '<section class="workspace-preview-pane">' +
          '<div id="workspace-preview-header" class="workspace-preview-header"></div>' +
          '<div id="workspace-preview-body" class="workspace-preview-body">' +
            '<div class="workspace-empty">Select a file to inspect its archived contents.</div>' +
          '</div>' +
        '</section>' +
      '</div>';
  }

  function buildWorkspacePreviewHeader(file) {
    var actionLinks = '';
    if (file.raw_url) {
      actionLinks =
        '<div class="workspace-preview-actions">' +
          '<a class="workspace-preview-link" href="' + escapeHtml(file.raw_url) + '" target="_blank" rel="noreferrer">Open Raw</a>' +
          '<a class="workspace-preview-link" href="' + escapeHtml(file.raw_url) + '" download="' + escapeHtml(file.name || 'artifact') + '">Download</a>' +
        '</div>';
    }

    return '' +
      '<div class="workspace-preview-meta">' +
        '<div class="workspace-preview-path">' + escapeHtml(file.path || file.name || 'workspace file') + '</div>' +
        '<div class="workspace-preview-submeta">' +
          '<span>' + escapeHtml(titleCase(file.kind || 'file')) + '</span>' +
          '<span>' + escapeHtml(formatBytes(file.size_bytes)) + '</span>' +
          (file.mime_type ? '<span>' + escapeHtml(file.mime_type) + '</span>' : '') +
          (file.truncated ? '<span class="workspace-truncate-chip">Preview truncated</span>' : '') +
        '</div>' +
      '</div>' +
      actionLinks;
  }

  function renderWorkspacePreview(preview, sessionId) {
    var headerEl = document.getElementById('workspace-preview-header');
    var bodyEl = document.getElementById('workspace-preview-body');
    if (!headerEl || !bodyEl) return;

    headerEl.innerHTML = buildWorkspacePreviewHeader(preview);

    if (preview.kind === 'image') {
      bodyEl.innerHTML =
        '<div class="workspace-image-wrap">' +
          '<img src="' + escapeHtml(preview.raw_url || '') + '" alt="' + escapeHtml(preview.path || preview.name || 'workspace image') + '" class="workspace-preview-image">' +
        '</div>';
      rewriteImages(bodyEl, sessionId);
      return;
    }

    if (preview.kind === 'markdown') {
      bodyEl.innerHTML =
        (preview.truncated ? '<div class="workspace-banner">Preview truncated to the first archived segment of this file.</div>' : '') +
        '<div class="workspace-markdown">' + safeRenderMarkdown(preview.content_text || '') + '</div>';
      rewriteImages(bodyEl, sessionId);
      return;
    }

    if (preview.kind === 'csv') {
      var columns = preview.columns || [];
      var rows = preview.rows || [];
      if (!columns.length && !rows.length) {
        bodyEl.innerHTML = '<div class="workspace-empty">Preview unavailable for this tabular file.</div>';
        return;
      }
      var headerRow = columns.map(function (value) {
        return '<th>' + escapeHtml(value) + '</th>';
      }).join('');
      var bodyRows = rows.map(function (row) {
        return '<tr>' + row.map(function (cell) {
          return '<td>' + escapeHtml(cell) + '</td>';
        }).join('') + '</tr>';
      }).join('');
      bodyEl.innerHTML =
        (preview.truncated ? '<div class="workspace-banner">Preview truncated to the first archived segment of this file.</div>' : '') +
        '<div class="workspace-table-wrap">' +
          '<table class="workspace-table">' +
            (headerRow ? '<thead><tr>' + headerRow + '</tr></thead>' : '') +
            '<tbody>' + bodyRows + '</tbody>' +
          '</table>' +
        '</div>';
      return;
    }

    if (preview.kind === 'binary') {
      bodyEl.innerHTML =
        '<div class="workspace-empty">' +
          '<p>This file type is not previewed inline.</p>' +
          '<p>Use <strong>Open Raw</strong> or <strong>Download</strong> to inspect it directly.</p>' +
        '</div>';
      return;
    }

    bodyEl.innerHTML =
      (preview.truncated ? '<div class="workspace-banner">Preview truncated to the first archived segment of this file.</div>' : '') +
      '<pre class="workspace-code"><code>' + escapeHtml(preview.content_text || '') + '</code></pre>';
  }

  function openWorkspaceModal(detail) {
    showModal('Workspace', buildWorkspaceModalShell(), {
      contentClass: 'workspace-modal-content',
      bodyClass: 'workspace-modal-body'
    });

    var overlay = document.getElementById('qtb-modal');
    if (!overlay) return;

    var searchInput = overlay.querySelector('#workspace-search-input');
    var summaryEl = overlay.querySelector('#workspace-summary');
    var listEl = overlay.querySelector('#workspace-file-list');
    var previewHeaderEl = overlay.querySelector('#workspace-preview-header');
    var previewBodyEl = overlay.querySelector('#workspace-preview-body');
    var currentSelection = null;
    var workspaceIndex = null;

    function renderFileList() {
      if (!listEl) return;
      var query = searchInput ? String(searchInput.value || '').trim().toLowerCase() : '';
      var files = (workspaceIndex && workspaceIndex.files ? workspaceIndex.files : []).filter(function (file) {
        if (!query) return true;
        return String(file.path || '').toLowerCase().indexOf(query) !== -1;
      });

      if (!files.length) {
        listEl.innerHTML = '<div class="workspace-empty">No files match the current filter.</div>';
        if (previewHeaderEl) previewHeaderEl.innerHTML = '';
        if (previewBodyEl) previewBodyEl.innerHTML = '<div class="workspace-empty">Select a file to inspect its archived contents.</div>';
        return;
      }

      listEl.innerHTML = files.map(function (file) {
        var activeClass = file.path === currentSelection ? ' active' : '';
        return '' +
          '<button type="button" class="workspace-file-item' + activeClass + '" data-path="' + escapeHtml(file.path) + '">' +
            '<span class="workspace-file-main">' +
              '<span class="workspace-file-path">' + escapeHtml(file.path) + '</span>' +
              '<span class="workspace-file-meta">' + escapeHtml(titleCase(file.kind || 'file')) + ' · ' + escapeHtml(formatBytes(file.size_bytes)) + '</span>' +
            '</span>' +
          '</button>';
      }).join('');

      Array.prototype.forEach.call(listEl.querySelectorAll('.workspace-file-item'), function (button) {
        button.addEventListener('click', function () {
          loadPreview(button.getAttribute('data-path') || '');
        });
      });

      var stillVisible = files.some(function (file) { return file.path === currentSelection; });
      if (!stillVisible && files.length) {
        loadPreview(files[0].path);
      }
    }

    function loadPreview(relativePath) {
      if (!relativePath) return;
      if (relativePath === currentSelection && previewHeaderEl && previewHeaderEl.innerHTML) {
        renderFileList();
        return;
      }
      currentSelection = relativePath;
      renderFileList();
      if (previewHeaderEl) previewHeaderEl.innerHTML = '';
      if (previewBodyEl) previewBodyEl.innerHTML = '<div class="workspace-empty">Loading preview…</div>';

      ensureWorkspacePreview(detail.session_id, relativePath).then(function (preview) {
        if (currentSelection !== relativePath) return;
        renderWorkspacePreview(preview, detail.session_id);
      }).catch(function (error) {
        if (currentSelection !== relativePath) return;
        if (previewHeaderEl) previewHeaderEl.innerHTML = '';
        if (previewBodyEl) {
          previewBodyEl.innerHTML = '<div class="workspace-empty">Unable to load preview: ' +
            escapeHtml(error && error.message ? error.message : String(error || 'Unknown error')) +
            '</div>';
        }
      });
    }

    if (searchInput) {
      searchInput.addEventListener('input', renderFileList);
    }

    ensureWorkspaceIndex(detail.session_id).then(function (indexPayload) {
      workspaceIndex = indexPayload;
      if (summaryEl) {
        var topExtensions = (indexPayload.top_extensions || []).map(function (ext) {
          return ext === '[no_ext]' ? 'no extension' : ext.replace(/^\./, '');
        });
        summaryEl.innerHTML =
          '<strong>' + escapeHtml(String(indexPayload.file_count || 0)) + '</strong> archived file(s)' +
          (topExtensions.length ? '<span> · ' + escapeHtml(topExtensions.join(' / ')) + '</span>' : '');
      }
      renderFileList();
    }).catch(function (error) {
      if (summaryEl) summaryEl.textContent = 'Unable to load workspace.';
      if (listEl) {
        listEl.innerHTML = '<div class="workspace-empty">Unable to load workspace: ' +
          escapeHtml(error && error.message ? error.message : String(error || 'Unknown error')) +
          '</div>';
      }
    });
  }

  // ── Server Detail Modal ──

  function openServerModal(detail) {
    var taskConfig = [
      metaItem('Requires Code', detail.requires_code ? 'Yes' : 'No'),
      metaItem('Max Turns', detail.max_turns == null ? '—' : String(detail.max_turns))
    ].join('');

    var distractorsHtml = (detail.distractor_names && detail.distractor_names.length)
      ? '<div class="detail-chip-list">' + detail.distractor_names.map(function (name) {
        return '<span class="detail-chip">' + escapeHtml(name) + '</span>';
      }).join('') + '</div>'
      : '<p class="detail-empty-note">No distractor tools registered.</p>';

    var serverCost = [
      metaItem('Simulator Cost', detail.simulator_cost != null ? formatCost(detail.simulator_cost) : '—'),
      metaItem('TC Checker Cost', detail.tc_checker_cost != null ? formatCost(detail.tc_checker_cost) : '—'),
      metaItem('Duration', formatDuration(detail.duration_seconds))
    ].join('');

    var hasWorkspace = detail.has_agent_files && detail.workspace_files && detail.workspace_files.length;

    var html =
      buildInfoSection('Task Config', taskConfig) +
      buildInfoSection('Distractors', distractorsHtml) +
      buildInfoSection('Server Cost', serverCost) +
      (hasWorkspace
        ? buildInfoSection('Workspace (' + detail.workspace_files.length + ')', '<div id="server-workspace-mount"></div>')
        : buildInfoSection('Workspace', '<p class="detail-empty-note">No workspace files archived.</p>'));

    showModal('Server Detail', html, {
      contentClass: 'server-modal-content',
      bodyClass: 'server-modal-body'
    });

    // Mount workspace explorer into the placeholder if available
    if (hasWorkspace) {
      var mount = document.getElementById('server-workspace-mount');
      if (mount) {
        mount.innerHTML = buildWorkspaceModalShell();
        _bindWorkspaceExplorer(detail, mount);
      }
    }
  }

  function _bindWorkspaceExplorer(detail, scope) {
    var searchInput = scope.querySelector('#workspace-search-input');
    var summaryEl = scope.querySelector('#workspace-summary');
    var listEl = scope.querySelector('#workspace-file-list');
    var previewHeaderEl = scope.querySelector('#workspace-preview-header');
    var previewBodyEl = scope.querySelector('#workspace-preview-body');
    var currentSelection = null;
    var workspaceIndex = null;

    function renderFileList() {
      if (!listEl) return;
      var query = searchInput ? String(searchInput.value || '').trim().toLowerCase() : '';
      var files = (workspaceIndex && workspaceIndex.files ? workspaceIndex.files : []).filter(function (file) {
        if (!query) return true;
        return String(file.path || '').toLowerCase().indexOf(query) !== -1;
      });
      if (!files.length) {
        listEl.innerHTML = '<div class="workspace-empty">No files match the current filter.</div>';
        if (previewHeaderEl) previewHeaderEl.innerHTML = '';
        if (previewBodyEl) previewBodyEl.innerHTML = '<div class="workspace-empty">Select a file to inspect.</div>';
        return;
      }
      listEl.innerHTML = files.map(function (file) {
        var activeClass = file.path === currentSelection ? ' active' : '';
        return '<button type="button" class="workspace-file-item' + activeClass + '" data-path="' + escapeHtml(file.path) + '">' +
          '<span class="workspace-file-main"><span class="workspace-file-path">' + escapeHtml(file.path) + '</span>' +
          '<span class="workspace-file-meta">' + escapeHtml(titleCase(file.kind || 'file')) + ' · ' + escapeHtml(formatBytes(file.size_bytes)) + '</span></span></button>';
      }).join('');
      Array.prototype.forEach.call(listEl.querySelectorAll('.workspace-file-item'), function (button) {
        button.addEventListener('click', function () { loadPreview(button.getAttribute('data-path') || ''); });
      });
      var stillVisible = files.some(function (f) { return f.path === currentSelection; });
      if (!stillVisible && files.length) loadPreview(files[0].path);
    }

    function loadPreview(relativePath) {
      if (!relativePath) return;
      if (relativePath === currentSelection && previewHeaderEl && previewHeaderEl.innerHTML) { renderFileList(); return; }
      currentSelection = relativePath;
      renderFileList();
      if (previewHeaderEl) previewHeaderEl.innerHTML = '';
      if (previewBodyEl) previewBodyEl.innerHTML = '<div class="workspace-empty">Loading preview…</div>';
      ensureWorkspacePreview(detail.session_id, relativePath).then(function (preview) {
        if (currentSelection !== relativePath) return;
        renderWorkspacePreview(preview, detail.session_id);
      }).catch(function (error) {
        if (currentSelection !== relativePath) return;
        if (previewBodyEl) previewBodyEl.innerHTML = '<div class="workspace-empty">Unable to load preview: ' + escapeHtml(error && error.message ? error.message : String(error)) + '</div>';
      });
    }

    if (searchInput) searchInput.addEventListener('input', renderFileList);
    ensureWorkspaceIndex(detail.session_id).then(function (indexPayload) {
      workspaceIndex = indexPayload;
      if (summaryEl) {
        var topExt = (indexPayload.top_extensions || []).map(function (ext) { return ext === '[no_ext]' ? 'no extension' : ext.replace(/^\./, ''); });
        summaryEl.innerHTML = '<strong>' + escapeHtml(String(indexPayload.file_count || 0)) + '</strong> file(s)' +
          (topExt.length ? '<span> · ' + escapeHtml(topExt.join(' / ')) + '</span>' : '');
      }
      renderFileList();
    }).catch(function (error) {
      if (summaryEl) summaryEl.textContent = 'Unable to load workspace.';
      if (listEl) listEl.innerHTML = '<div class="workspace-empty">' + escapeHtml(error && error.message ? error.message : String(error)) + '</div>';
    });
  }

  // ── Client Detail Modal ──

  function openClientModal(detail) {
    if (!detail.has_client_trace) {
      showModal('Client Detail', '<p class="detail-empty-note">No client trace was uploaded for this session.</p>');
      return;
    }

    var traceInfo = [
      metaItem('Model', detail.model || '—'),
      metaItem('Agent Name', detail.agent_name || '—'),
      metaItem('Content Blocks', detail.has_content_blocks ? 'Yes' : 'No'),
      metaItem('Client Trace', 'Present')
    ].join('');

    var costHtml = '';
    if (detail.agent_cost) {
      costHtml = [
        metaItem('Input Tokens', formatInteger(detail.agent_cost.input_tokens)),
        metaItem('Output Tokens', formatInteger(detail.agent_cost.output_tokens)),
        metaItem('API Calls', formatInteger(detail.agent_cost.api_calls)),
        metaItem('Cost', formatCost(detail.agent_cost.cost_usd))
      ].join('');
    } else {
      costHtml = '<p class="detail-empty-note">No agent cost data in client trace.</p>';
    }

    showModal('Client Detail',
      buildInfoSection('Model & Trace', traceInfo) +
      buildInfoSection('Agent Cost', costHtml)
    );
  }

  // ── Eval History Modal ──

  function openEvalHistoryModal(detail) {
    var history = detail.eval_history || [];
    var scoresHtml = history.length
      ? '<div class="detail-history-list">' + history.map(renderEvalHistoryItem).join('') + '</div>'
      : '<p class="detail-empty-note">No evaluation history found.</p>';

    showModal('Eval History',
      buildInfoSection('Scores', scoresHtml) +
      renderCostReport(detail)
    );
  }

  // ── Bind action buttons ──

  function bindDetailActionButtons(detail) {
    var serverBtn = document.getElementById('detail-server-btn');
    var clientBtn = document.getElementById('detail-client-btn');
    var evalHistoryBtn = document.getElementById('detail-eval-history-btn');
    var scoreBtn = document.getElementById('detail-score-btn');
    var costBtn = document.getElementById('detail-cost-btn');

    if (serverBtn) serverBtn.addEventListener('click', function () { openServerModal(detail); });
    if (clientBtn) clientBtn.addEventListener('click', function () { openClientModal(detail); });
    if (evalHistoryBtn) evalHistoryBtn.addEventListener('click', function () { openEvalHistoryModal(detail); });

    if (scoreBtn) {
      scoreBtn.addEventListener('click', function () {
        showModal('Score JSON', renderScoreJsonReport(detail));
      });
    }

    if (costBtn) {
      costBtn.addEventListener('click', function () {
        showModal('Cost', renderCostReport(detail));
      });
    }
  }

  function renderDetailPage(detail) {
    var overallScoreTag = detail.overall_score == null
      ? '<span class="detail-tag detail-tag-status">OAS —</span>'
      : '<span class="detail-tag detail-tag-status">OAS ' + escapeHtml(formatScore(detail.overall_score)) + '</span>';

    app.innerHTML =
      '<section class="page-run">' +
        '<div class="detail-header">' +
          '<a class="detail-back" href="#/results">\u2190 Back</a>' +
          '<div class="detail-title-block">' +
            '<div class="detail-task-id">' + escapeHtml(detail.task_id) + '</div>' +
            '<div class="detail-tags">' +
              '<span class="detail-tag detail-tag-agent">' + escapeHtml(titleCase(detail.category)) + '</span>' +
              '<span class="detail-tag detail-tag-model">' + escapeHtml(detail.model || 'Unknown model') + '</span>' +
              '<span class="detail-tag detail-tag-persona">' + escapeHtml(detail.persona_id) + '</span>' +
              '<span class="detail-tag detail-tag-status">' + escapeHtml(titleCase(detail.evaluation_status)) + '</span>' +
              overallScoreTag +
            '</div>' +
          '</div>' +
          '<div class="detail-actions">' + buildDetailActions(detail) + '</div>' +
        '</div>' +
        '<div class="run-layout">' +
          '<aside class="run-config">' +
            '<div class="panel-header"><span class="panel-header-title">Info</span></div>' +
            '<div class="panel-body" id="detail-meta"></div>' +
          '</aside>' +
          '<div class="run-main">' +
            '<div class="run-chat-panel">' +
              '<div class="panel-header">Conversation</div>' +
              '<div id="detail-chat" class="chat-area"></div>' +
            '</div>' +
            '<div class="run-tool-panel">' +
              '<div class="panel-header"><span class="panel-header-title">Tools</span></div>' +
              '<div id="detail-tools" class="tool-area"></div>' +
            '</div>' +
          '</div>' +
          '<div class="panel-reopen-tab tab-left" id="reopen-detail-info">' +
            '<span class="tab-arrow">\u25C0</span><span class="tab-label">Info</span>' +
          '</div>' +
          '<div class="panel-reopen-tab tab-right" id="reopen-detail-tools">' +
            '<span class="tab-arrow">\u25B6</span><span class="tab-label">Tools</span>' +
          '</div>' +
        '</div>' +
      '</section>';

    var metaEl = document.getElementById('detail-meta');
    if (metaEl) {
      metaEl.innerHTML = buildInfoPanel(detail);
    }

    bindPanelControl(app, {
      panelSelector: '.run-config',
      tabId: 'reopen-detail-info',
      isLeft: true,
      label: 'Info'
    });
    bindPanelControl(app, {
      panelSelector: '.run-tool-panel',
      tabId: 'reopen-detail-tools',
      isLeft: false,
      label: 'Tools'
    });
    bindDetailActionButtons(detail);

    var toolLogs = detail.tool_logs || [];
    var sendMessageEvents = detail.send_message_events || [];
    var chatEl = document.getElementById('detail-chat');
    var toolsEl = document.getElementById('detail-tools');

    if (chatEl && window.QTB && typeof window.QTB.buildConversationReplay === 'function') {
      window.QTB.buildConversationReplay(chatEl, detail.conversation || [], toolLogs, sendMessageEvents);
      rewriteImages(chatEl, detail.session_id);
    } else if (chatEl) {
      chatEl.innerHTML = '<div class="empty-state">Replay renderer unavailable.</div>';
    }

    if (toolsEl && window.QTB && typeof window.QTB.buildToolReplay === 'function') {
      window.QTB.buildToolReplay(toolsEl, toolLogs);
      rewriteImages(toolsEl, detail.session_id);
    } else if (toolsEl) {
      toolsEl.innerHTML = '<div class="empty-state">Tool renderer unavailable.</div>';
    }
  }

  var REVIEW_SECTIONS = ['task_spec', 'conversation', 'tool_log', 'workspace', 'judge_eval', 'overall'];
  var REVIEW_SECTION_LABELS = {
    task_spec: 'Task Spec',
    conversation: 'Conversation',
    tool_log: 'Tutor Tool Log',
    workspace: 'Workspace State',
    judge_eval: 'Judge Evaluation',
    overall: 'Overall'
  };

  function reviewSectionLabel(section) {
    return REVIEW_SECTION_LABELS[section] || titleCase(section);
  }

  function filteredReviewBundles(bundles) {
    var query = String(state.review.search || '').trim().toLowerCase();
    if (!query) return bundles;
    return bundles.filter(function (item) {
      var haystack = [
        item.bundle_id,
        item.session_id,
        item.task_id,
        item.persona_id,
        item.category,
        item.model
      ].join(' ').toLowerCase();
      return haystack.indexOf(query) !== -1;
    });
  }

  function renderReviewListPage(bundles) {
    var visible = filteredReviewBundles(bundles);
    var reviewed = bundles.filter(function (item) { return item.reviewed_by_current_user; }).length;

    app.innerHTML =
      '<section class="page review-page">' +
        '<header class="page-header">' +
          '<div class="page-title-wrap">' +
            '<p class="eyebrow">Review</p>' +
            '<h1>Human reviewer console.</h1>' +
            '<p class="subtitle">Inspect archived session bundles across task, conversation, tool, workspace, and judge layers. Opinion cards are stored as structured JSON per bundle and GitHub reviewer.</p>' +
          '</div>' +
          '<div class="summary-strip">' +
            buildSummaryPill('Bundles', String(bundles.length)) +
            buildSummaryPill('Reviewed By You', String(reviewed)) +
          '</div>' +
        '</header>' +
        '<section class="panel filter-bar">' +
          '<label class="filter-field">' +
            '<span class="filter-label">Search</span>' +
            '<input id="review-search" class="filter-input" type="search" placeholder="bundle, task, persona, model" value="' + escapeHtml(state.review.search) + '">' +
          '</label>' +
          '<div class="review-refresh-wrap">' +
            '<button class="btn btn-secondary" id="review-refresh-btn" type="button">Refresh</button>' +
          '</div>' +
        '</section>' +
        '<div class="results-meta">' +
          '<div class="results-count">' + escapeHtml(String(visible.length)) + ' bundle(s) shown</div>' +
        '</div>' +
        (visible.length ? '<div class="results-grid">' + visible.map(renderReviewBundleCard).join('') + '</div>' : renderEmptyInline('Matching review bundles will appear here.')) +
      '</section>';

    var searchInput = document.getElementById('review-search');
    if (searchInput) {
      searchInput.addEventListener('input', function (event) {
        state.review.search = String(event.target.value || '').trim().toLowerCase();
        renderReviewListPage(bundles);
      });
    }
    var refresh = document.getElementById('review-refresh-btn');
    if (refresh) {
      refresh.addEventListener('click', function () {
        ensureReviewBundles(true).then(renderReviewListPage).catch(function (error) {
          renderError('Review unavailable', error);
        });
      });
    }
  }

  function renderReviewBundleCard(item) {
    var reviewed = item.reviewed_by_current_user
      ? '<span class="meta-chip score-chip">Reviewed by you</span>'
      : '<span class="meta-chip unknown-chip">Awaiting your card</span>';
    return '' +
      '<a class="session-card" href="#/review/' + encodeURIComponent(item.bundle_id || item.session_id) + '">' +
        '<div class="session-top">' +
          '<div>' +
            '<h2 class="session-title">' +
              '<span>' + escapeHtml(item.task_id || item.bundle_id || 'Bundle') + '</span>' +
              '<span class="badge">' + escapeHtml(titleCase(item.category || 'unknown')) + '</span>' +
              '<span class="badge">' + escapeHtml(titleCase(item.difficulty || 'unknown')) + '</span>' +
            '</h2>' +
            '<p class="session-subtitle">' +
              'Bundle <code>' + escapeHtml(item.bundle_id || item.session_id || '') + '</code> · Persona <code>' + escapeHtml(item.persona_id || '') + '</code> · ' + escapeHtml(formatTimestamp(item.timestamp)) +
            '</p>' +
          '</div>' +
          '<span class="status-pill ' + escapeHtml(item.evaluation_status || 'pending') + '">' + escapeHtml(titleCase(item.evaluation_status || 'pending')) + '</span>' +
        '</div>' +
        '<div class="session-meta-row">' +
          '<span class="meta-chip">' + escapeHtml((item.turn_count || 0) + ' turns') + '</span>' +
          '<span class="meta-chip">' + escapeHtml((item.tool_count || 0) + ' tools') + '</span>' +
          '<span class="meta-chip">' + escapeHtml((item.review_count || 0) + ' review file(s)') + '</span>' +
          reviewed +
        '</div>' +
      '</a>';
  }

  function reviewAddButton(section, label, targetType, targetValue) {
    return '<button class="btn btn-secondary btn-small review-add-btn" type="button" data-section="' +
      escapeHtml(section) + '" data-target-type="' + escapeHtml(targetType || '') +
      '" data-target-value="' + escapeHtml(targetValue == null ? '' : targetValue) + '">' +
      escapeHtml(label || 'Add Card') + '</button>';
  }

  function renderReviewLayerPanel(section, title, bodyHtml, metaHtml) {
    return '' +
      '<section class="panel review-layer" id="review-section-' + escapeHtml(section) + '">' +
        '<header class="review-layer-head">' +
          '<div>' +
            '<p class="eyebrow">' + escapeHtml(section.replace(/_/g, ' ')) + '</p>' +
            '<h2>' + escapeHtml(title) + '</h2>' +
          '</div>' +
          '<div class="review-layer-actions">' +
            (metaHtml || '') +
            reviewAddButton(section, 'Add Section Card') +
          '</div>' +
        '</header>' +
        bodyHtml +
      '</section>';
  }

  function renderReviewTaskSpec(layer) {
    layer = layer || {};
    var task = layer.task || {};
    var persona = layer.persona || {};
    var rubric = layer.judge_rubric || {};
    var tracks = rubric.tracks || [];
    var tracksHtml = tracks.length
      ? '<div class="review-rubric-track-list">' + tracks.map(function (track) {
        return '<span class="meta-chip">' + escapeHtml((track.track || '').toUpperCase()) + ' ' +
          escapeHtml(track.score == null ? 'pending' : formatScore(track.score)) + '</span>';
      }).join('') + '</div>'
      : '<p class="detail-empty-note">Judge rubric metadata appears after scoring completes.</p>';
    var body =
      '<div class="review-spec-grid">' +
        '<article class="review-spec-block">' +
          '<h3>Task</h3>' +
          '<div class="detail-meta-grid">' +
            metaItem('Task ID', task.task_id || '') +
            metaItem('Category', titleCase(task.category || 'unknown')) +
            metaItem('Difficulty', titleCase(task.difficulty || 'unknown')) +
            metaItem('Requires Code', task.requires_code ? 'Yes' : 'No') +
          '</div>' +
          '<div class="review-markdown">' + safeRenderMarkdown(task.description || 'Task description unavailable.') + '</div>' +
        '</article>' +
        '<article class="review-spec-block">' +
          '<h3>Student Persona</h3>' +
          '<div class="detail-meta-grid">' +
            metaItem('Persona ID', persona.persona_id || '') +
            metaItem('Knowledge Level', persona.knowledge_level || 'Unspecified') +
          '</div>' +
          '<p class="detail-empty-note">' + escapeHtml(persona.description || 'Persona description unavailable.') + '</p>' +
        '</article>' +
        '<article class="review-spec-block">' +
          '<h3>Judge Rubric</h3>' +
          '<div class="detail-meta-grid">' +
            metaItem('Score ID', rubric.score_id || 'Pending') +
            metaItem('Eval Model', rubric.eval_model || 'Pending') +
            metaItem('Eval Mode', rubric.eval_mode || 'Pending') +
            metaItem('Validation Run', rubric.judge_validation_run || 'Pending') +
          '</div>' +
          tracksHtml +
        '</article>' +
      '</div>';
    return renderReviewLayerPanel('task_spec', 'Task Spec', body);
  }

  function renderReviewConversation(layer) {
    var turns = layer && layer.turns ? layer.turns : [];
    var body = turns.length
      ? '<div class="review-turn-list">' + turns.map(function (turn, index) {
        var role = turn.role || 'message';
        var label = titleCase(role);
        var content = turn.content || '';
        return '' +
          '<article class="review-turn" id="turn-' + escapeHtml(index) + '">' +
            '<header class="review-row-head">' +
              '<div><strong>' + escapeHtml(label) + '</strong><span class="meta-chip">turn ' + escapeHtml(String(index)) + '</span></div>' +
              reviewAddButton('conversation', 'Review Turn', 'turn_index', index) +
            '</header>' +
            '<div class="review-markdown">' + safeRenderMarkdown(content) + '</div>' +
          '</article>';
      }).join('') + '</div>'
      : '<p class="detail-empty-note">Conversation turns will appear here when the bundle contains a transcript.</p>';
    return renderReviewLayerPanel('conversation', 'Conversation', body, '<span class="meta-chip">' + escapeHtml(String(turns.length)) + ' turns</span>');
  }

  function renderReviewToolLog(layer) {
    var calls = layer && layer.tool_calls ? layer.tool_calls : [];
    var body = calls.length
      ? '<div class="review-tool-list">' + calls.map(function (call, index) {
        var name = call.name || call.tool_name || 'tool';
        var duration = call.duration_ms == null ? '' : formatDuration(Number(call.duration_ms) / 1000);
        return '' +
          '<article class="review-tool-call" id="tool-call-' + escapeHtml(index) + '">' +
            '<header class="review-row-head">' +
              '<div><strong>' + escapeHtml(name) + '</strong><span class="meta-chip">call ' + escapeHtml(String(index)) + '</span>' +
                (duration ? '<span class="meta-chip">' + escapeHtml(duration) + '</span>' : '') +
              '</div>' +
              reviewAddButton('tool_log', 'Review Call', 'tool_call_index', index) +
            '</header>' +
            '<details>' +
              '<summary>Arguments</summary>' +
              '<pre class="detail-json-block">' + escapeHtml(JSON.stringify(call.args || call.input || {}, null, 2)) + '</pre>' +
            '</details>' +
            '<details>' +
              '<summary>Result</summary>' +
              '<pre class="detail-json-block">' + escapeHtml(stringifyReviewValue(call.result || call.output || call.error || '')) + '</pre>' +
            '</details>' +
          '</article>';
      }).join('') + '</div>'
      : '<p class="detail-empty-note">Tool calls will appear here when the tutor used tools.</p>';
    return renderReviewLayerPanel('tool_log', 'Tutor Tool Log', body, '<span class="meta-chip">' + escapeHtml(String(calls.length)) + ' calls</span>');
  }

  function stringifyReviewValue(value) {
    if (typeof value === 'string') return value;
    try {
      return JSON.stringify(value, null, 2);
    } catch (error) {
      return String(value == null ? '' : value);
    }
  }

  function renderReviewWorkspace(layer) {
    layer = layer || {};
    var files = layer.tree || [];
    var diffs = layer.diffs || [];
    var stdout = layer.stdout || '';
    var stderr = layer.stderr || '';
    var filesHtml = files.length
      ? '<div class="review-file-list">' + files.map(function (file) {
        var path = file.path || file.name || '';
        return '' +
          '<div class="review-file-row">' +
            '<div><strong>' + escapeHtml(path) + '</strong><span class="meta-chip">' + escapeHtml(titleCase(file.kind || 'file')) + '</span></div>' +
            reviewAddButton('workspace', 'Review File', 'file_path', path) +
          '</div>';
      }).join('') + '</div>'
      : '<p class="detail-empty-note">Archived workspace files will appear here.</p>';
    var body =
      '<details open>' +
        '<summary>Final Tree</summary>' +
        filesHtml +
      '</details>' +
      '<details>' +
        '<summary>Diffs</summary>' +
        (diffs.length ? '<pre class="detail-json-block">' + escapeHtml(JSON.stringify(diffs, null, 2)) + '</pre>' : '<p class="detail-empty-note">Diff data is empty for this bundle.</p>') +
      '</details>' +
      '<details>' +
        '<summary>Stdout</summary>' +
        (stdout ? '<pre class="detail-json-block">' + escapeHtml(stdout) + '</pre>' : '<p class="detail-empty-note">Stdout is empty for this bundle.</p>') +
      '</details>' +
      '<details>' +
        '<summary>Stderr</summary>' +
        (stderr ? '<pre class="detail-json-block">' + escapeHtml(stderr) + '</pre>' : '<p class="detail-empty-note">Stderr is empty for this bundle.</p>') +
      '</details>';
    return renderReviewLayerPanel('workspace', 'Workspace State', body, '<span class="meta-chip">' + escapeHtml(String(files.length)) + ' files</span>');
  }

  function renderReviewJudgeEval(layer) {
    layer = layer || {};
    var rows = layer.rows || [];
    var rowsHtml = rows.length
      ? '<div class="review-judge-table">' + rows.map(function (row) {
        var score = row.score == null ? 'pending' : formatScore(Number(row.score));
        return '' +
          '<article class="review-judge-row" id="criterion-' + escapeHtml(row.criterion_id || '') + '">' +
            '<header class="review-row-head">' +
              '<div><strong>' + escapeHtml(row.criterion || row.criterion_id || 'criterion') + '</strong>' +
                '<span class="meta-chip">' + escapeHtml((row.track || '').toUpperCase()) + '</span>' +
                '<span class="meta-chip">score ' + escapeHtml(score) + '</span>' +
                (row.verdict ? '<span class="meta-chip">' + escapeHtml(titleCase(row.verdict)) + '</span>' : '') +
              '</div>' +
              reviewAddButton('judge_eval', 'Review Criterion', 'criterion_id', row.criterion_id || '') +
            '</header>' +
            (row.reasoning ? '<p class="detail-empty-note">' + escapeHtml(row.reasoning) + '</p>' : '<p class="detail-empty-note">Reasoning field is empty.</p>') +
          '</article>';
      }).join('') + '</div>'
      : '<p class="detail-empty-note">Judge criteria will appear here after scoring completes.</p>';
    var body =
      '<div class="detail-meta-grid">' +
        metaItem('Status', titleCase(layer.status || 'pending')) +
        metaItem('Overall Score', layer.overall_score == null ? 'Pending' : formatScore(layer.overall_score)) +
      '</div>' +
      rowsHtml +
      '<details>' +
        '<summary>Raw Score JSON</summary>' +
        renderJsonBlock(layer.score_json) +
      '</details>';
    return renderReviewLayerPanel('judge_eval', 'Judge Evaluation', body, '<span class="meta-chip">' + escapeHtml(String(rows.length)) + ' rows</span>');
  }

  function reviewOpinionCounts(opinions) {
    var counts = {};
    REVIEW_SECTIONS.forEach(function (section) { counts[section] = 0; });
    (opinions || []).forEach(function (opinion) {
      var section = opinion.section || 'overall';
      counts[section] = (counts[section] || 0) + 1;
    });
    return counts;
  }

  function renderReviewOpinionPanel(bundle) {
    var review = bundle.review || {};
    var opinions = review.opinions || [];
    var counts = reviewOpinionCounts(opinions);
    var filter = state.review.opinionFilter || 'all';
    var visible = opinions.filter(function (opinion) {
      return filter === 'all' || opinion.section === filter;
    });
    var options = ['<option value="all">All sections</option>'].concat(REVIEW_SECTIONS.map(function (section) {
      return '<option value="' + escapeHtml(section) + '"' + (filter === section ? ' selected' : '') + '>' +
        escapeHtml(reviewSectionLabel(section)) + '</option>';
    })).join('');
    var slots = REVIEW_SECTIONS.map(function (section) {
      return '<div class="review-slot">' +
        '<span>' + escapeHtml(reviewSectionLabel(section)) + '</span>' +
        '<strong>' + escapeHtml(String(counts[section] || 0)) + '</strong>' +
      '</div>';
    }).join('');
    return '' +
      '<aside class="panel review-opinion-panel">' +
        '<header class="review-layer-head">' +
          '<div>' +
            '<p class="eyebrow">Opinion Cards</p>' +
            '<h2>Structured Feedback</h2>' +
          '</div>' +
          reviewAddButton('overall', 'Add Overall Card') +
        '</header>' +
        '<div class="review-slot-grid">' + slots + '</div>' +
        '<label class="filter-field">' +
          '<span class="filter-label">Section Filter</span>' +
          '<select id="review-opinion-filter" class="filter-select">' + options + '</select>' +
        '</label>' +
        '<div class="review-opinion-list">' +
          (visible.length ? visible.map(renderReviewOpinionCard).join('') : '<p class="detail-empty-note">Cards for the selected section will appear here.</p>') +
        '</div>' +
      '</aside>';
  }

  function renderReviewOpinionCard(opinion) {
    var tags = opinion.tags && opinion.tags.length
      ? '<div class="detail-chip-list">' + opinion.tags.map(function (tag) {
        return '<span class="detail-chip">' + escapeHtml(tag) + '</span>';
      }).join('') + '</div>'
      : '';
    return '' +
      '<article class="review-opinion-card severity-' + escapeHtml(opinion.severity || 'info') + '">' +
        '<header class="review-row-head">' +
          '<div><strong>' + escapeHtml(reviewSectionLabel(opinion.section)) + '</strong>' +
            '<span class="meta-chip">' + escapeHtml(titleCase(opinion.severity || 'info')) + '</span></div>' +
          '<span class="review-card-time">' + escapeHtml(formatTimestamp(opinion.created_at || opinion.timestamp)) + '</span>' +
        '</header>' +
        '<p>' + escapeHtml(opinion.comment || '') + '</p>' +
        '<p class="detail-empty-note">' + escapeHtml(describeOpinionTarget(opinion.target || {})) + '</p>' +
        tags +
      '</article>';
  }

  function describeOpinionTarget(target) {
    if (target.turn_index != null) return 'Target: turn ' + target.turn_index;
    if (target.tool_call_index != null) return 'Target: tool call ' + target.tool_call_index;
    if (target.file_path) return 'Target: ' + target.file_path;
    if (target.criterion_id) return 'Target: ' + target.criterion_id;
    return 'Target: section-level';
  }

  function renderReviewBundlePage(bundle) {
    var layers = bundle.layers || {};
    var detail = bundle.detail || {};
    app.innerHTML =
      '<section class="page review-page review-bundle-page">' +
        '<header class="page-header">' +
          '<div class="page-title-wrap">' +
            '<a class="detail-back" href="#/review">Back</a>' +
            '<p class="eyebrow">Review Bundle</p>' +
            '<h1>' + escapeHtml(detail.task_id || bundle.bundle_id || 'Session Bundle') + '</h1>' +
            '<p class="subtitle">Bundle <code>' + escapeHtml(bundle.bundle_id || '') + '</code> · Persona <code>' + escapeHtml(detail.persona_id || '') + '</code> · ' + escapeHtml(formatTimestamp(detail.timestamp)) + '</p>' +
          '</div>' +
          '<div class="summary-strip">' +
            buildSummaryPill('Turns', String(detail.turn_count || 0)) +
            buildSummaryPill('Tools', String(detail.tool_count || 0)) +
            buildSummaryPill('Score', detail.overall_score == null ? 'Pending' : formatScore(detail.overall_score)) +
          '</div>' +
        '</header>' +
        '<div class="review-layout">' +
          '<main class="review-layer-stack">' +
            renderReviewTaskSpec(layers.task_spec) +
            renderReviewConversation(layers.conversation) +
            renderReviewToolLog(layers.tool_log) +
            renderReviewWorkspace(layers.workspace) +
            renderReviewJudgeEval(layers.judge_eval) +
          '</main>' +
          renderReviewOpinionPanel(bundle) +
        '</div>' +
      '</section>';
    rewriteImages(app.querySelector('.review-layer-stack'), bundle.bundle_id || detail.session_id);
    bindReviewBundleControls(bundle);
  }

  function bindReviewBundleControls(bundle) {
    Array.prototype.forEach.call(document.querySelectorAll('.review-add-btn'), function (button) {
      button.addEventListener('click', function () {
        var targetType = button.getAttribute('data-target-type') || '';
        var targetValue = button.getAttribute('data-target-value') || '';
        openReviewOpinionModal(bundle, {
          section: button.getAttribute('data-section') || 'overall',
          targetType: targetType,
          targetValue: targetValue
        });
      });
    });
    var filter = document.getElementById('review-opinion-filter');
    if (filter) {
      filter.addEventListener('change', function (event) {
        state.review.opinionFilter = event.target.value || 'all';
        renderReviewBundlePage(bundle);
      });
    }
  }

  function openReviewOpinionModal(bundle, preset) {
    preset = preset || {};
    var sectionOptions = REVIEW_SECTIONS.map(function (section) {
      return '<option value="' + escapeHtml(section) + '"' + (preset.section === section ? ' selected' : '') + '>' +
        escapeHtml(reviewSectionLabel(section)) + '</option>';
    }).join('');
    var targetOptions = [
      ['none', 'Section-level'],
      ['turn_index', 'Turn'],
      ['tool_call_index', 'Tool Call'],
      ['file_path', 'File Path'],
      ['criterion_id', 'Criterion']
    ].map(function (item) {
      return '<option value="' + item[0] + '"' + (preset.targetType === item[0] ? ' selected' : '') + '>' + item[1] + '</option>';
    }).join('');
    var html =
      '<form id="review-opinion-form" class="review-opinion-form">' +
        '<label class="filter-field">' +
          '<span class="filter-label">Section</span>' +
          '<select id="review-card-section" class="filter-select" required>' + sectionOptions + '</select>' +
        '</label>' +
        '<div class="review-form-grid">' +
          '<label class="filter-field">' +
            '<span class="filter-label">Target Type</span>' +
            '<select id="review-card-target-type" class="filter-select">' + targetOptions + '</select>' +
          '</label>' +
          '<label class="filter-field">' +
            '<span class="filter-label">Target</span>' +
            '<input id="review-card-target-value" class="filter-input" type="text" value="' + escapeHtml(preset.targetValue || '') + '">' +
          '</label>' +
        '</div>' +
        '<label class="filter-field">' +
          '<span class="filter-label">Severity</span>' +
          '<select id="review-card-severity" class="filter-select">' +
            '<option value="info">Info</option>' +
            '<option value="concern">Concern</option>' +
            '<option value="blocker">Blocker</option>' +
          '</select>' +
        '</label>' +
        '<label class="filter-field">' +
          '<span class="filter-label">Tags</span>' +
          '<input id="review-card-tags" class="filter-input" type="text" placeholder="rubric_mismatch, persona_break">' +
        '</label>' +
        '<div id="review-judge-disagreement" class="review-form-grid">' +
          '<label class="filter-field">' +
            '<span class="filter-label">Judge Score</span>' +
            '<input id="review-card-judge-score" class="filter-input" type="number" step="0.01">' +
          '</label>' +
          '<label class="filter-field">' +
            '<span class="filter-label">Human Score</span>' +
            '<input id="review-card-human-score" class="filter-input" type="number" step="0.01">' +
          '</label>' +
        '</div>' +
        '<label class="filter-field">' +
          '<span class="filter-label">Comment</span>' +
          '<textarea id="review-card-comment" class="run-textarea" rows="5" required></textarea>' +
        '</label>' +
        '<div id="review-card-error" class="run-error" hidden></div>' +
        '<div class="run-actions">' +
          '<button class="btn btn-primary" type="submit">Save Card</button>' +
        '</div>' +
      '</form>';
    showModal('Opinion Card', html);
    bindReviewOpinionForm(bundle);
  }

  function bindReviewOpinionForm(bundle) {
    var form = document.getElementById('review-opinion-form');
    var section = document.getElementById('review-card-section');
    var judgeBlock = document.getElementById('review-judge-disagreement');

    function syncJudgeFields() {
      if (!judgeBlock || !section) return;
      judgeBlock.style.display = section.value === 'judge_eval' ? 'grid' : 'none';
    }

    if (section) section.addEventListener('change', syncJudgeFields);
    syncJudgeFields();
    if (!form) return;
    form.addEventListener('submit', function (event) {
      event.preventDefault();
      submitReviewOpinion(bundle);
    });
  }

  function submitReviewOpinion(bundle) {
    var section = document.getElementById('review-card-section');
    var targetType = document.getElementById('review-card-target-type');
    var targetValue = document.getElementById('review-card-target-value');
    var severity = document.getElementById('review-card-severity');
    var tags = document.getElementById('review-card-tags');
    var comment = document.getElementById('review-card-comment');
    var judgeScore = document.getElementById('review-card-judge-score');
    var humanScore = document.getElementById('review-card-human-score');
    var errorEl = document.getElementById('review-card-error');

    var payload = {
      section: section ? section.value : 'overall',
      target: parseReviewTarget(targetType ? targetType.value : '', targetValue ? targetValue.value : ''),
      severity: severity ? severity.value : 'info',
      tags: String(tags ? tags.value : '').split(',').map(function (item) { return item.trim(); }).filter(Boolean),
      comment: String(comment ? comment.value : '').trim()
    };
    if (payload.section === 'judge_eval') {
      payload.judge_disagreement = {
        judge_score: judgeScore ? judgeScore.value : '',
        human_score: humanScore ? humanScore.value : ''
      };
    }

    restApi('/ui/review/bundles/' + encodeURIComponent(bundle.bundle_id) + '/opinions', {
      method: 'POST',
      body: JSON.stringify({opinion: payload})
    }).then(function (updated) {
      state.review.bundleCache[updated.bundle_id] = updated;
      state.review.bundles = null;
      closeModal();
      renderReviewBundlePage(updated);
    }).catch(function (error) {
      if (errorEl) {
        errorEl.hidden = false;
        errorEl.textContent = error && error.message ? error.message : String(error || 'Unable to save card');
      }
    });
  }

  function parseReviewTarget(type, value) {
    var cleanType = String(type || '');
    var cleanValue = String(value || '').trim();
    var target = {};
    if (!cleanValue || cleanType === 'none') return target;
    if (cleanType === 'turn_index' || cleanType === 'tool_call_index') {
      var index = parseInt(cleanValue, 10);
      if (isFinite(index) && index >= 0) target[cleanType] = index;
      return target;
    }
    if (cleanType === 'file_path' || cleanType === 'criterion_id') {
      target[cleanType] = cleanValue;
    }
    return target;
  }

  function showReviewList() {
    state.activeSessionId = null;
    setAppDetailMode(false);
    renderLoading('Loading review bundles', 'Fetching archived session bundles and review-card counts.');
    ensureReviewBundles(false).then(renderReviewListPage).catch(function (error) {
      renderError('Review unavailable', error);
    });
  }

  function showReviewBundle(bundleId) {
    state.activeSessionId = bundleId;
    setAppDetailMode(false);
    renderLoading('Loading review bundle', 'Fetching the bundle layers and current reviewer cards.');
    ensureReviewBundle(bundleId, true).then(renderReviewBundlePage).catch(function (error) {
      renderError('Review bundle unavailable', error);
    });
  }

  function renderEmptyInline(message) {
    return '<section class="empty-state"><p class="eyebrow">Empty</p><h1>No results to show.</h1><p>' + escapeHtml(message) + '</p></section>';
  }

  function showResultsList() {
    state.activeSessionId = null;
    setAppDetailMode(false);
    renderLoading('Loading archived sessions', 'Fetching merged session summaries from the isolated /ui/results endpoint.');
    ensureResults().then(renderResultsList).catch(function (error) {
      renderError('Results unavailable', error);
    });
  }

  function showTasks() {
    state.activeSessionId = null;
    setAppDetailMode(false);
    renderLoading('Loading task catalog', 'Fetching task and persona metadata from the isolated /ui/tasks endpoint.');
    ensureTasks().then(renderTasksPage).catch(function (error) {
      renderError('Tasks unavailable', error);
    });
  }

  function showRun() {
    state.activeSessionId = null;
    setAppDetailMode(false);
    renderLoading('Loading run harness', 'Fetching task and persona metadata before choosing Human or Agent test mode.');
    ensureTasks().then(renderRunPage).catch(function (error) {
      renderError('Run unavailable', error);
    });
  }

  function showResultDetail(sessionId) {
    state.activeSessionId = sessionId;
    setAppDetailMode(true);
    renderLoading('Loading session detail', 'Fetching merged detail JSON, then rebuilding conversation replay and tool history.');
    ensureDetail(sessionId).then(renderDetailPage).catch(function (error) {
      renderError('Detail unavailable', error);
    });
  }

  function onRouteChange() {
    var route = routeFromHash();
    setActiveNav(route);
    closeModal();

    if (route === '/' || route === '/results') {
      showResultsList();
      return;
    }

    if (route === '/runs') {
      showRuns();
      return;
    }

    if (route === '/run') {
      showRun();
      return;
    }

    if (route === '/tasks') {
      showTasks();
      return;
    }

    if (route === '/review') {
      showReviewList();
      return;
    }

    if (route === '/flow-demo') {
      if (window.QTB && typeof window.QTB.renderFlowDemoPage === 'function') {
        window.QTB.renderFlowDemoPage(app, state);
      } else {
        renderError('Flow demo unavailable', new Error('flow-demo.js not loaded'));
      }
      return;
    }

    if (route.indexOf('/results/') === 0) {
      showResultDetail(decodeURIComponent(route.slice('/results/'.length)));
      return;
    }

    if (route.indexOf('/review/') === 0) {
      showReviewBundle(decodeURIComponent(route.slice('/review/'.length)));
      return;
    }

    location.hash = '#/results';
  }

  window.addEventListener('hashchange', onRouteChange);
  window.addEventListener('load', function () {
    loadMe().then(onRouteChange);
  });
})();
