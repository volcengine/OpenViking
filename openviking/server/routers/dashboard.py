# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
OpenViking SaaS Dashboard.

Provides a single-page HTML visualization tool for testing the SaaS database
migration. The page communicates with existing OpenViking REST API endpoints.

Access at: GET /dashboard
"""

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["dashboard"])

_DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>OpenViking SaaS Dashboard</title>
  <style>
    :root {
      --primary: #6366f1;
      --primary-dark: #4f46e5;
      --success: #22c55e;
      --warning: #f59e0b;
      --danger: #ef4444;
      --bg: #0f172a;
      --surface: #1e293b;
      --surface2: #334155;
      --text: #f1f5f9;
      --text-muted: #94a3b8;
      --border: #334155;
      --radius: 12px;
      --radius-sm: 8px;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: 'Segoe UI', system-ui, sans-serif;
      background: var(--bg);
      color: var(--text);
      min-height: 100vh;
    }
    /* Header */
    .header {
      background: var(--surface);
      border-bottom: 1px solid var(--border);
      padding: 16px 24px;
      display: flex;
      align-items: center;
      gap: 16px;
      position: sticky;
      top: 0;
      z-index: 100;
    }
    .logo { font-size: 22px; font-weight: 700; color: var(--primary); }
    .logo span { color: var(--text); }
    .badge {
      font-size: 11px; padding: 3px 10px; border-radius: 99px;
      font-weight: 600; letter-spacing: 0.5px;
    }
    .badge-saas { background: #312e81; color: #a5b4fc; }
    .badge-ok  { background: #14532d; color: #86efac; }
    .badge-err { background: #7f1d1d; color: #fca5a5; }
    .header-right { margin-left: auto; display: flex; align-items: center; gap: 12px; }
    .status-dot {
      width: 10px; height: 10px; border-radius: 50%;
      background: #374151; display: inline-block;
    }
    .status-dot.ok  { background: var(--success); box-shadow: 0 0 6px var(--success); }
    .status-dot.err { background: var(--danger);  box-shadow: 0 0 6px var(--danger); }
    /* Layout */
    .container { max-width: 1400px; margin: 0 auto; padding: 24px; }
    /* Tabs */
    .tabs { display: flex; gap: 4px; border-bottom: 1px solid var(--border); margin-bottom: 24px; }
    .tab {
      padding: 10px 20px; cursor: pointer; border-radius: var(--radius-sm) var(--radius-sm) 0 0;
      color: var(--text-muted); font-size: 14px; font-weight: 500;
      border: 1px solid transparent; border-bottom: none; transition: all .15s;
    }
    .tab:hover { color: var(--text); background: var(--surface); }
    .tab.active {
      color: var(--primary); background: var(--surface);
      border-color: var(--border); border-bottom-color: var(--surface);
      margin-bottom: -1px;
    }
    .tab-panel { display: none; }
    .tab-panel.active { display: block; }
    /* Cards */
    .card {
      background: var(--surface); border: 1px solid var(--border);
      border-radius: var(--radius); padding: 20px; margin-bottom: 16px;
    }
    .card-title {
      font-size: 13px; text-transform: uppercase; letter-spacing: 0.8px;
      color: var(--text-muted); font-weight: 600; margin-bottom: 16px;
    }
    .grid-2 { display: grid; grid-template-columns: repeat(2, 1fr); gap: 16px; }
    .grid-3 { display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; }
    .grid-4 { display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; }
    @media (max-width: 900px) { .grid-4, .grid-3 { grid-template-columns: repeat(2, 1fr); } }
    @media (max-width: 600px) { .grid-4, .grid-3, .grid-2 { grid-template-columns: 1fr; } }
    /* Stat cards */
    .stat-card {
      background: var(--surface2); border: 1px solid var(--border);
      border-radius: var(--radius-sm); padding: 20px; text-align: center;
    }
    .stat-val { font-size: 32px; font-weight: 700; color: var(--primary); }
    .stat-lbl { font-size: 12px; color: var(--text-muted); margin-top: 4px; text-transform: uppercase; }
    /* Forms */
    .form-row { display: flex; gap: 8px; margin-bottom: 16px; }
    input[type=text], input[type=number], select, textarea {
      background: var(--surface2); border: 1px solid var(--border);
      color: var(--text); padding: 10px 14px; border-radius: var(--radius-sm);
      font-size: 14px; outline: none; transition: border-color .15s;
    }
    input[type=text]:focus, select:focus, textarea:focus {
      border-color: var(--primary);
    }
    input[type=text].full, textarea.full { width: 100%; }
    textarea { resize: vertical; min-height: 80px; }
    /* Buttons */
    .btn {
      padding: 10px 20px; border: none; border-radius: var(--radius-sm);
      cursor: pointer; font-size: 14px; font-weight: 600; transition: all .15s;
      white-space: nowrap;
    }
    .btn-primary { background: var(--primary); color: white; }
    .btn-primary:hover { background: var(--primary-dark); }
    .btn-danger { background: var(--danger); color: white; }
    .btn-sm { padding: 6px 12px; font-size: 12px; }
    .btn:disabled { opacity: .5; cursor: not-allowed; }
    /* Table */
    .tbl { width: 100%; border-collapse: collapse; font-size: 13px; }
    .tbl th {
      text-align: left; padding: 10px 12px; font-size: 11px;
      text-transform: uppercase; letter-spacing: 0.6px; color: var(--text-muted);
      border-bottom: 1px solid var(--border); font-weight: 600;
    }
    .tbl td {
      padding: 10px 12px; border-bottom: 1px solid var(--border);
      vertical-align: top; word-break: break-word; max-width: 400px;
    }
    .tbl tr:hover td { background: rgba(99,102,241,.05); }
    /* URI chip */
    .uri {
      font-family: monospace; font-size: 12px; background: rgba(99,102,241,.15);
      color: #a5b4fc; padding: 2px 8px; border-radius: 4px;
      display: inline-block; max-width: 350px; overflow: hidden;
      text-overflow: ellipsis; white-space: nowrap; vertical-align: middle;
    }
    /* Score badge */
    .score-badge {
      font-size: 11px; padding: 2px 8px; border-radius: 99px;
      background: rgba(99,102,241,.2); color: #a5b4fc; font-weight: 600;
    }
    /* Loading */
    .spinner {
      width: 20px; height: 20px; border: 2px solid var(--border);
      border-top-color: var(--primary); border-radius: 50%;
      animation: spin .7s linear infinite; display: inline-block;
    }
    @keyframes spin { to { transform: rotate(360deg); } }
    .loading-row td { text-align: center; padding: 40px; color: var(--text-muted); }
    /* Result item */
    .result-item {
      background: var(--surface2); border: 1px solid var(--border);
      border-radius: var(--radius-sm); padding: 14px; margin-bottom: 10px;
    }
    .result-item:hover { border-color: var(--primary); }
    .result-header { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
    .result-abstract { color: var(--text-muted); font-size: 13px; margin-top: 8px; line-height: 1.5; }
    .result-meta { display: flex; gap: 12px; margin-top: 8px; font-size: 11px; color: var(--text-muted); }
    /* Context type tag */
    .ctx-tag {
      font-size: 10px; padding: 2px 7px; border-radius: 4px; font-weight: 600;
      text-transform: uppercase; letter-spacing: 0.5px;
    }
    .ctx-resource { background: #1e3a5f; color: #60a5fa; }
    .ctx-memory   { background: #3b2060; color: #c084fc; }
    .ctx-skill    { background: #1a3a2a; color: #4ade80; }
    /* Info row */
    .info-row { display: flex; gap: 24px; flex-wrap: wrap; }
    .info-item { display: flex; flex-direction: column; gap: 2px; }
    .info-label { font-size: 11px; color: var(--text-muted); text-transform: uppercase; }
    .info-val { font-size: 14px; font-weight: 600; }
    /* Alert */
    .alert {
      padding: 12px 16px; border-radius: var(--radius-sm); margin-bottom: 16px;
      font-size: 13px; border-left: 4px solid;
    }
    .alert-info  { background: rgba(99,102,241,.1); border-color: var(--primary); color: #c7d2fe; }
    .alert-err   { background: rgba(239,68,68,.1);  border-color: var(--danger);  color: #fca5a5; }
    .alert-ok    { background: rgba(34,197,94,.1);  border-color: var(--success); color: #86efac; }
    /* Pre */
    pre {
      background: #0a0f1e; border: 1px solid var(--border); border-radius: var(--radius-sm);
      padding: 16px; overflow-x: auto; font-size: 12px; color: #a5b4fc; line-height: 1.6;
    }
    /* Misc */
    .flex { display: flex; }
    .gap-8 { gap: 8px; }
    .mt-16 { margin-top: 16px; }
    .text-muted { color: var(--text-muted); }
    .text-center { text-align: center; }
    .text-sm { font-size: 12px; }
    .empty-state { text-align: center; padding: 60px 20px; color: var(--text-muted); }
    .empty-icon { font-size: 48px; margin-bottom: 16px; }
  </style>
</head>
<body>
  <!-- HEADER -->
  <div class="header">
    <div class="logo">Open<span>Viking</span></div>
    <span class="badge badge-saas">SaaS Mode</span>
    <div class="header-right">
      <span class="status-dot" id="statusDot"></span>
      <span id="statusText" style="font-size:13px;color:var(--text-muted)">Checking...</span>
      <span id="backendBadge" class="badge" style="display:none"></span>
    </div>
  </div>

  <!-- MAIN -->
  <div class="container">
    <!-- TABS -->
    <div class="tabs">
      <div class="tab active" onclick="switchTab('overview')">Overview</div>
      <div class="tab" onclick="switchTab('browse')">Context Browser</div>
      <div class="tab" onclick="switchTab('search')">Search</div>
      <div class="tab" onclick="switchTab('ingest')">Add Resource</div>
      <div class="tab" onclick="switchTab('sessions')">Sessions</div>
      <div class="tab" onclick="switchTab('debug')">Debug</div>
    </div>

    <!-- ─── OVERVIEW ──────────────────────────────────────────────── -->
    <div class="tab-panel active" id="tab-overview">
      <div id="statsGrid" class="grid-4"></div>
      <div class="grid-2">
        <div class="card">
          <div class="card-title">Storage Backend</div>
          <div id="backendInfo"></div>
        </div>
        <div class="card">
          <div class="card-title">Quick Actions</div>
          <div style="display:flex;flex-direction:column;gap:8px">
            <button class="btn btn-primary" onclick="loadStats()">↻ Refresh Stats</button>
            <button class="btn btn-primary" onclick="switchTab('ingest')">+ Add Resource</button>
            <button class="btn btn-primary" onclick="switchTab('search')">⌕ Search Contexts</button>
          </div>
        </div>
      </div>
      <div class="card">
        <div class="card-title">Recent Contexts (latest 10)</div>
        <div id="recentContexts">
          <div class="empty-state"><div class="spinner"></div></div>
        </div>
      </div>
    </div>

    <!-- ─── CONTEXT BROWSER ───────────────────────────────────────── -->
    <div class="tab-panel" id="tab-browse">
      <div class="card">
        <div class="card-title">Browse by URI</div>
        <div class="form-row">
          <input type="text" id="browseUri" value="viking://" placeholder="URI (e.g. viking://resources/)"
            style="flex:1" onkeydown="if(event.key==='Enter') browseUri()">
          <button class="btn btn-primary" onclick="browseUri()">List</button>
        </div>
        <div id="browseResult">
          <div class="alert alert-info">Enter a URI above and click List to browse.</div>
        </div>
      </div>
      <div class="card" id="contentCard" style="display:none">
        <div class="card-title" id="contentCardTitle">Content</div>
        <div id="contentDisplay"></div>
      </div>
    </div>

    <!-- ─── SEARCH ────────────────────────────────────────────────── -->
    <div class="tab-panel" id="tab-search">
      <div class="card">
        <div class="card-title">Semantic Search</div>
        <div class="form-row">
          <input type="text" id="searchQuery" placeholder="Enter search query..." style="flex:1"
            onkeydown="if(event.key==='Enter') doSearch()">
          <input type="text" id="searchUri" value="" placeholder="Target URI (optional)" style="width:240px">
          <input type="number" id="searchLimit" value="10" style="width:80px" min="1" max="50">
          <button class="btn btn-primary" onclick="doSearch()">Search</button>
        </div>
        <div id="searchResults">
          <div class="alert alert-info">Enter a query and click Search.</div>
        </div>
      </div>
    </div>

    <!-- ─── INGEST ────────────────────────────────────────────────── -->
    <div class="tab-panel" id="tab-ingest">
      <div class="card">
        <div class="card-title">Add Resource / URL</div>
        <div class="form-row" style="flex-direction:column;gap:12px">
          <div>
            <label style="font-size:12px;color:var(--text-muted);display:block;margin-bottom:6px">
              Resource Path / URL
            </label>
            <input type="text" id="ingestPath" placeholder="https://... or /local/path" class="full">
          </div>
          <div>
            <label style="font-size:12px;color:var(--text-muted);display:block;margin-bottom:6px">
              Target URI (optional, default: viking://resources/)
            </label>
            <input type="text" id="ingestTarget" placeholder="viking://resources/" class="full">
          </div>
          <div>
            <button class="btn btn-primary" onclick="doIngest()">Add Resource</button>
          </div>
        </div>
        <div id="ingestResult" style="margin-top:16px"></div>
      </div>
      <div class="card">
        <div class="card-title">Add Text Content</div>
        <div style="display:flex;flex-direction:column;gap:12px">
          <div>
            <label style="font-size:12px;color:var(--text-muted);display:block;margin-bottom:6px">
              URI (where to write)
            </label>
            <input type="text" id="writeUri" placeholder="viking://resources/my-note.md" class="full">
          </div>
          <div>
            <label style="font-size:12px;color:var(--text-muted);display:block;margin-bottom:6px">
              Content
            </label>
            <textarea id="writeContent" class="full" placeholder="Enter content here..."></textarea>
          </div>
          <button class="btn btn-primary" onclick="doWrite()">Write to Storage</button>
          <div id="writeResult"></div>
        </div>
      </div>
    </div>

    <!-- ─── SESSIONS ──────────────────────────────────────────────── -->
    <div class="tab-panel" id="tab-sessions">
      <div class="card">
        <div class="card-title">Active Sessions</div>
        <button class="btn btn-primary btn-sm" onclick="loadSessions()" style="margin-bottom:16px">
          ↻ Refresh
        </button>
        <div id="sessionList">
          <div class="empty-state"><div class="spinner"></div></div>
        </div>
      </div>
      <div class="card" id="sessionDetail" style="display:none">
        <div class="card-title" id="sessionDetailTitle">Session Messages</div>
        <div id="sessionMessages"></div>
      </div>
    </div>

    <!-- ─── DEBUG ─────────────────────────────────────────────────── -->
    <div class="tab-panel" id="tab-debug">
      <div class="card">
        <div class="card-title">API Console</div>
        <div style="display:flex;flex-direction:column;gap:12px">
          <div class="form-row">
            <select id="apiMethod" style="width:90px">
              <option>GET</option><option>POST</option>
            </select>
            <input type="text" id="apiEndpoint" placeholder="/api/v1/system/status" style="flex:1">
          </div>
          <div>
            <label style="font-size:12px;color:var(--text-muted);display:block;margin-bottom:6px">
              Request Body (JSON, for POST)
            </label>
            <textarea id="apiBody" class="full" placeholder='{"key": "value"}'></textarea>
          </div>
          <button class="btn btn-primary" onclick="callApi()">Send Request</button>
        </div>
        <div id="apiResult" style="margin-top:16px"></div>
      </div>
    </div>

  </div><!-- /container -->

  <script>
    // ─── State ───────────────────────────────────────────────────
    let activeTab = 'overview';

    // ─── Tab switching ───────────────────────────────────────────
    function switchTab(tab) {
      document.querySelectorAll('.tab').forEach((el, i) => {
        const tabs = ['overview','browse','search','ingest','sessions','debug'];
        el.classList.toggle('active', tabs[i] === tab);
      });
      document.querySelectorAll('.tab-panel').forEach(el => el.classList.remove('active'));
      document.getElementById('tab-' + tab).classList.add('active');
      activeTab = tab;
      if (tab === 'overview') loadStats();
      if (tab === 'sessions') loadSessions();
    }

    // ─── API helper ──────────────────────────────────────────────
    async function api(method, path, body) {
      const opts = { method, headers: { 'Content-Type': 'application/json' } };
      if (body) opts.body = JSON.stringify(body);
      const r = await fetch(path, opts);
      const data = await r.json().catch(() => ({ error: await r.text() }));
      return { ok: r.ok, status: r.status, data };
    }

    // ─── Health check ────────────────────────────────────────────
    async function checkHealth() {
      const dot = document.getElementById('statusDot');
      const txt = document.getElementById('statusText');
      try {
        const { ok } = await api('GET', '/health');
        dot.className = 'status-dot ' + (ok ? 'ok' : 'err');
        txt.textContent = ok ? 'Connected' : 'Error';
      } catch(e) {
        dot.className = 'status-dot err';
        txt.textContent = 'Offline';
      }
    }

    // ─── Overview: stats ─────────────────────────────────────────
    async function loadStats() {
      document.getElementById('statsGrid').innerHTML = '<div class="empty-state"><div class="spinner"></div></div>';
      document.getElementById('recentContexts').innerHTML = '<div class="empty-state"><div class="spinner"></div></div>';

      // Debug stats (includes DB info)
      const dbRes = await api('GET', '/api/v1/debug/storage/stats').catch(() => null);
      const stats = dbRes && dbRes.ok ? (dbRes.data.result || {}) : {};

      // System status
      const sysRes = await api('GET', '/api/v1/system/status').catch(() => null);
      const sys = sysRes && sysRes.ok ? (sysRes.data.result || {}) : {};

      // Build stat cards
      const items = [
        { label: 'Backend',      val: stats.backend || 'local' },
        { label: 'Collections',  val: stats.collections ?? '—' },
        { label: 'Total Contexts', val: stats.total_records ?? '—' },
        { label: 'Storage Size',  val: formatBytes(stats.storage_size) },
      ];

      // Backend badge
      const bk = stats.backend || 'local';
      const bdg = document.getElementById('backendBadge');
      bdg.style.display = '';
      bdg.className = 'badge ' + (bk === 'postgresql' ? 'badge-saas' : 'badge-ok');
      bdg.textContent = bk.toUpperCase();

      document.getElementById('statsGrid').innerHTML = items.map(i =>
        `<div class="stat-card">
          <div class="stat-val">${i.val}</div>
          <div class="stat-lbl">${i.label}</div>
         </div>`
      ).join('');

      // Backend info card
      document.getElementById('backendInfo').innerHTML = `
        <div class="info-row">
          <div class="info-item"><span class="info-label">Vector DB</span><span class="info-val">${bk}</span></div>
          <div class="info-item"><span class="info-label">Initialized</span><span class="info-val">${sys.initialized ?? '—'}</span></div>
          <div class="info-item"><span class="info-label">User</span><span class="info-val">${sys.user ?? '—'}</span></div>
        </div>
        ${bk === 'postgresql' ?
          '<div class="alert alert-ok" style="margin-top:16px">✓ Running in SaaS mode — PostgreSQL database backend active</div>' :
          '<div class="alert alert-info" style="margin-top:16px">ℹ Running in local mode. Configure postgresql backend for SaaS.</div>'
        }
      `;

      // Recent contexts via filter (list resources, sorted by time)
      loadRecentContexts();
    }

    async function loadRecentContexts() {
      const container = document.getElementById('recentContexts');
      const res = await api('GET', '/api/v1/debug/storage/list?collection=context&limit=10').catch(() => null);
      if (!res || !res.ok) {
        container.innerHTML = '<div class="text-muted text-sm text-center" style="padding:20px">Could not load recent contexts</div>';
        return;
      }
      const items = res.data.result?.items || [];
      if (!items.length) {
        container.innerHTML = '<div class="empty-state"><div class="empty-icon">📭</div><div>No contexts yet. Add a resource to get started.</div></div>';
        return;
      }
      container.innerHTML = `
        <table class="tbl">
          <thead><tr>
            <th>URI</th><th>Type</th><th>Level</th><th>Abstract</th><th>Active Count</th>
          </tr></thead>
          <tbody>
            ${items.map(r => `
              <tr>
                <td><span class="uri" title="${escHtml(r.uri||'')}">${escHtml(r.uri||'')}</span></td>
                <td>${ctxTag(r.context_type)}</td>
                <td>${levelBadge(r.level)}</td>
                <td class="text-muted text-sm">${escHtml((r.abstract||'').slice(0,120))}${(r.abstract||'').length>120?'…':''}</td>
                <td>${r.active_count ?? 0}</td>
              </tr>`).join('')}
          </tbody>
        </table>`;
    }

    // ─── Browse ───────────────────────────────────────────────────
    async function browseUri() {
      const uri = document.getElementById('browseUri').value.trim() || 'viking://';
      const container = document.getElementById('browseResult');
      container.innerHTML = '<div class="empty-state"><div class="spinner"></div></div>';
      document.getElementById('contentCard').style.display = 'none';

      const res = await api('POST', '/api/v1/fs/ls', { uri, recursive: false }).catch(() => null);
      if (!res || !res.ok) {
        container.innerHTML = `<div class="alert alert-err">Error: ${escHtml(res?.data?.error?.message || 'Request failed')}</div>`;
        return;
      }
      const items = res.data.result || [];
      if (!items.length) {
        container.innerHTML = '<div class="empty-state"><div class="empty-icon">📂</div><div>Empty directory</div></div>';
        return;
      }
      container.innerHTML = `
        <table class="tbl">
          <thead><tr><th>Name</th><th>Type</th><th>URI</th><th>Actions</th></tr></thead>
          <tbody>
            ${items.map(item => `
              <tr>
                <td>${escHtml(item.name || item.uri?.split('/').pop() || '')}</td>
                <td>${item.is_dir ? '📁 Dir' : '📄 File'}</td>
                <td><span class="uri" title="${escHtml(item.uri||'')}">${escHtml(item.uri||'')}</span></td>
                <td>
                  ${item.is_dir
                    ? `<button class="btn btn-sm btn-primary" onclick="navigate('${escJs(item.uri)}')">Open</button>`
                    : `<button class="btn btn-sm btn-primary" onclick="showContent('${escJs(item.uri)}')">View</button>`
                  }
                </td>
              </tr>`).join('')}
          </tbody>
        </table>`;
    }

    function navigate(uri) {
      document.getElementById('browseUri').value = uri;
      browseUri();
    }

    async function showContent(uri) {
      const card = document.getElementById('contentCard');
      const display = document.getElementById('contentDisplay');
      const title = document.getElementById('contentCardTitle');
      card.style.display = '';
      title.textContent = uri;
      display.innerHTML = '<div class="spinner"></div>';

      // Try abstract first
      const abRes = await api('GET', `/api/v1/content/abstract?uri=${encodeURIComponent(uri)}`).catch(() => null);
      const abstract = abRes?.ok ? (abRes.data.result?.abstract || '') : '';

      // Then content
      const res = await api('GET', `/api/v1/content/read?uri=${encodeURIComponent(uri)}&offset=0&size=4096`).catch(() => null);
      const content = res?.ok ? (res.data.result?.content || '') : '';

      display.innerHTML = `
        ${abstract ? `<div class="alert alert-info" style="margin-bottom:12px"><strong>Abstract:</strong> ${escHtml(abstract)}</div>` : ''}
        <pre>${escHtml(content || '(empty or binary)')}</pre>`;
    }

    // ─── Search ───────────────────────────────────────────────────
    async function doSearch() {
      const query = document.getElementById('searchQuery').value.trim();
      if (!query) return;
      const target_uri = document.getElementById('searchUri').value.trim();
      const limit = parseInt(document.getElementById('searchLimit').value) || 10;
      const container = document.getElementById('searchResults');
      container.innerHTML = '<div class="empty-state"><div class="spinner"></div></div>';

      const body = { query, limit };
      if (target_uri) body.target_uri = target_uri;

      const res = await api('POST', '/api/v1/search/find', body).catch(() => null);
      if (!res || !res.ok) {
        container.innerHTML = `<div class="alert alert-err">Search error: ${escHtml(res?.data?.error?.message || 'Request failed')}</div>`;
        return;
      }
      const results = res.data.result?.results || res.data.result || [];
      if (!results.length) {
        container.innerHTML = '<div class="empty-state"><div class="empty-icon">🔍</div><div>No results found</div></div>';
        return;
      }
      container.innerHTML = results.map(r => `
        <div class="result-item">
          <div class="result-header">
            <span class="uri" title="${escHtml(r.uri||'')}">
              ${escHtml((r.uri||'').split('/').slice(-2).join('/'))}
            </span>
            ${ctxTag(r.context_type)}
            ${r._score !== undefined ? `<span class="score-badge">score: ${Number(r._score).toFixed(3)}</span>` : ''}
            <button class="btn btn-sm btn-primary" onclick="showContentInBrowse('${escJs(r.uri)}')">
              View
            </button>
          </div>
          <div class="result-abstract">${escHtml(r.abstract || r.description || '—')}</div>
          <div class="result-meta">
            <span>Level: ${levelBadge(r.level)}</span>
            <span class="uri text-sm" style="font-size:10px">${escHtml(r.uri||'')}</span>
          </div>
        </div>`).join('');
    }

    function showContentInBrowse(uri) {
      switchTab('browse');
      document.getElementById('browseUri').value = uri;
      showContent(uri);
    }

    // ─── Ingest ───────────────────────────────────────────────────
    async function doIngest() {
      const path = document.getElementById('ingestPath').value.trim();
      if (!path) { alert('Enter a path or URL'); return; }
      const target = document.getElementById('ingestTarget').value.trim() || 'viking://resources/';
      const container = document.getElementById('ingestResult');
      container.innerHTML = '<div class="alert alert-info">⏳ Processing... this may take a moment.</div>';

      const res = await api('POST', '/api/v1/resources/add', {
        path,
        target_uri: target,
      }).catch(() => null);

      if (!res || !res.ok) {
        container.innerHTML = `<div class="alert alert-err">Error: ${escHtml(res?.data?.error?.message || 'Request failed')}</div>`;
        return;
      }
      const result = res.data.result || {};
      container.innerHTML = `
        <div class="alert alert-ok">✓ Resource added!
          Root URI: <span class="uri">${escHtml(result.root_uri || '—')}</span>
        </div>
        <pre>${escHtml(JSON.stringify(result, null, 2))}</pre>`;
    }

    async function doWrite() {
      const uri = document.getElementById('writeUri').value.trim();
      const content = document.getElementById('writeContent').value;
      const container = document.getElementById('writeResult');
      if (!uri) { container.innerHTML = '<div class="alert alert-err">Enter a URI</div>'; return; }
      container.innerHTML = '<div class="spinner"></div>';

      const res = await api('POST', '/api/v1/fs/write', { uri, content }).catch(() => null);
      if (!res || !res.ok) {
        container.innerHTML = `<div class="alert alert-err">Error: ${escHtml(res?.data?.error?.message||'Failed')}</div>`;
        return;
      }
      container.innerHTML = '<div class="alert alert-ok">✓ Written to storage</div>';
    }

    // ─── Sessions ─────────────────────────────────────────────────
    async function loadSessions() {
      const container = document.getElementById('sessionList');
      container.innerHTML = '<div class="empty-state"><div class="spinner"></div></div>';

      const res = await api('GET', '/api/v1/sessions/list?limit=20').catch(() => null);
      if (!res || !res.ok) {
        container.innerHTML = '<div class="alert alert-err">Could not load sessions</div>';
        return;
      }
      const sessions = res.data.result?.sessions || res.data.result || [];
      if (!sessions.length) {
        container.innerHTML = '<div class="empty-state"><div class="empty-icon">💬</div><div>No sessions yet</div></div>';
        return;
      }
      container.innerHTML = `
        <table class="tbl">
          <thead><tr><th>Session ID</th><th>Messages</th><th>Created</th><th>Actions</th></tr></thead>
          <tbody>
            ${sessions.map(s => `
              <tr>
                <td><code>${escHtml(s.session_id||s.id||'')}</code></td>
                <td>${s.message_count ?? s.messages?.length ?? '—'}</td>
                <td>${s.created_at ? new Date(s.created_at).toLocaleString() : '—'}</td>
                <td><button class="btn btn-sm btn-primary" onclick="viewSession('${escJs(s.session_id||s.id)}')">View</button></td>
              </tr>`).join('')}
          </tbody>
        </table>`;
    }

    async function viewSession(sessionId) {
      document.getElementById('sessionDetail').style.display = '';
      document.getElementById('sessionDetailTitle').textContent = 'Session: ' + sessionId;
      const container = document.getElementById('sessionMessages');
      container.innerHTML = '<div class="spinner"></div>';

      const res = await api('GET', `/api/v1/sessions/messages?session_id=${encodeURIComponent(sessionId)}`).catch(() => null);
      if (!res || !res.ok) {
        container.innerHTML = '<div class="alert alert-err">Could not load messages</div>';
        return;
      }
      const msgs = res.data.result?.messages || [];
      if (!msgs.length) {
        container.innerHTML = '<div class="text-muted text-center">No messages</div>';
        return;
      }
      container.innerHTML = msgs.map(m => `
        <div style="margin-bottom:12px; padding:12px; background:var(--surface2); border-radius:8px;
             border-left:3px solid ${m.role==='user'?'var(--primary)':'var(--success)'}">
          <div style="font-size:11px;color:var(--text-muted);margin-bottom:4px;text-transform:uppercase;">
            ${escHtml(m.role)}
          </div>
          <div style="font-size:13px;white-space:pre-wrap">${escHtml(m.content?.text||JSON.stringify(m.content)||'')}</div>
        </div>`).join('');
    }

    // ─── Debug/API console ────────────────────────────────────────
    async function callApi() {
      const method = document.getElementById('apiMethod').value;
      const endpoint = document.getElementById('apiEndpoint').value.trim();
      const bodyStr = document.getElementById('apiBody').value.trim();
      const container = document.getElementById('apiResult');
      if (!endpoint) return;

      let body = undefined;
      if (method === 'POST' && bodyStr) {
        try { body = JSON.parse(bodyStr); } catch(e) {
          container.innerHTML = '<div class="alert alert-err">Invalid JSON body</div>';
          return;
        }
      }
      container.innerHTML = '<div class="spinner"></div>';
      const res = await api(method, endpoint, body).catch(e => ({ ok: false, data: { error: e.message } }));
      container.innerHTML = `
        <div class="alert ${res.ok ? 'alert-ok' : 'alert-err'}" style="margin-bottom:8px">
          HTTP ${res.status} ${res.ok ? '✓ OK' : '✗ Error'}
        </div>
        <pre>${escHtml(JSON.stringify(res.data, null, 2))}</pre>`;
    }

    // ─── Helpers ─────────────────────────────────────────────────
    function escHtml(s) {
      return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
    }
    function escJs(s) {
      return String(s||'').replace(/'/g,"\\'").replace(/\\\\/g,'\\\\');
    }
    function formatBytes(b) {
      if (!b) return '—';
      const units = ['B','KB','MB','GB','TB'];
      let i = 0; b = Number(b);
      while (b >= 1024 && i < 4) { b /= 1024; i++; }
      return b.toFixed(1) + ' ' + units[i];
    }
    function ctxTag(t) {
      const cls = t === 'memory' ? 'ctx-memory' : t === 'skill' ? 'ctx-skill' : 'ctx-resource';
      return `<span class="ctx-tag ${cls}">${escHtml(t||'resource')}</span>`;
    }
    function levelBadge(l) {
      const labels = { 0: 'L0·Abstract', 1: 'L1·Overview', 2: 'L2·Detail' };
      return labels[l] || `L${l}`;
    }

    // ─── Init ─────────────────────────────────────────────────────
    checkHealth();
    loadStats();
    setInterval(checkHealth, 30000);
  </script>
</body>
</html>"""


@router.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
async def dashboard():
    """OpenViking SaaS visualization dashboard."""
    return HTMLResponse(content=_DASHBOARD_HTML, status_code=200)
