// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import http from 'node:http';
import path from 'node:path';

// A scripted OpenViking HTTP server with the content, upload, resource,
// search, filesystem, session, task and fs routes the daemon uses: the mail
// archive pipeline and the Core `openviking_*` tools. `files` is what was
// written or parsed (uri -> text); `requests` every call; a parse task lands
// on its second poll, fails for a file named in `failParse`, and stays parsing
// for one in `slowParse` until it is taken out. An import without no_split is
// split at once, like a Markdown file without headings: <stem>_N.md pieces of
// at most 6000 characters, cut between paragraphs. `down` answers 503.
// `errors` maps one route ("POST /api/v1/search/find") to an HTTP status to
// answer once... or until deleted; `searchResult` is what find/search return;
// `listings` maps a directory uri to { entries, has_more } for fs/ls;
// `sessions` holds what the session routes did. GET /content/read pages by
// line for a numeric limit and returns the whole text for limit=-1.
function split(text, max = 6000) {
  const pieces = [''];
  for (const paragraph of text.split('\n\n')) {
    const last = pieces.length - 1;
    if (pieces[last] && pieces[last].length + paragraph.length + 2 > max) pieces.push(paragraph);
    else pieces[last] = pieces[last] ? `${pieces[last]}\n\n${paragraph}` : paragraph;
  }
  return pieces;
}

export async function fakeOV(t, { failParse = [], slowParse = [], apiKeys = {} } = {}) {
  const state = {
    files: new Map(), uploads: new Map(), tasks: new Map(), requests: [], down: false, slowParse: new Set(slowParse),
    errors: new Map(), searchResult: { memories: [], resources: [], skills: [], total: 0 },
    listings: new Map(), sessions: new Map(),
  };
  let next = 0;
  const server = http.createServer(async (request, response) => {
    const chunks = [];
    for await (const chunk of request) chunks.push(chunk);
    const raw = Buffer.concat(chunks);
    const url = new URL(request.url, 'http://ov');
    const route = `${request.method} ${url.pathname}`;
    const reply = (status, result, extra = {}) => {
      response.writeHead(status, { 'content-type': 'application/json' });
      response.end(JSON.stringify(status < 400 ? { status: 'ok', result, ...extra } : { status: 'error', error: { message: result } }));
    };
    const body = raw.length && String(request.headers['content-type']).startsWith('application/json') ? JSON.parse(raw.toString('utf8')) : null;
    state.requests.push({ route, query: Object.fromEntries(url.searchParams), body, headers: request.headers });
    if (state.down) return reply(503, 'unavailable');
    const failing = state.errors.get(route);
    if (failing !== undefined) {
      state.errors.delete(route);
      return reply(failing, `injected ${failing}`);
    }
    // Model OV's authenticated home alias: an API key overrides the user
    // header, and stored files and returned URIs are always canonical.
    const user = apiKeys[request.headers['x-api-key']] ?? request.headers['x-openviking-user'] ?? 'owner';
    const canonical = (uri) => uri?.replace(/^viking:\/\/~(?=\/|$)/, `viking://user/${user}`);
    const uri = canonical(url.searchParams.get('uri'));
    if (route === 'GET /api/v1/system/status') return reply(200, { initialized: true, user });
    if (route === 'POST /api/v1/content/write') {
      const target = canonical(body.uri);
      // Like OV 0.5: replace writes whether or not the file exists, create
      // refuses one that does.
      if (body.mode === 'create' && state.files.has(target)) return reply(409, 'already exists');
      state.files.set(target, body.content);
      return reply(200, { uri: target, semantic_status: 'skipped', vector_status: 'queued' });
    }
    if (route === 'POST /api/v1/search/find' || route === 'POST /api/v1/search/search') {
      return reply(200, state.searchResult);
    }
    if (route === 'GET /api/v1/fs/ls') {
      const listing = state.listings.get(uri);
      // The preference tree lists what is stored, directories first-level.
      if (!listing && /\/memories\/preferences(?:\/|$)/.test(uri ?? '')) {
        const children = new Map();
        for (const key of state.files.keys()) {
          if (!key.startsWith(`${uri}/`)) continue;
          const [first, ...rest] = key.slice(uri.length + 1).split('/');
          children.set(`${uri}/${first}`, rest.length > 0);
        }
        if (!children.size) return reply(404, 'not found');
        const offset = Number(url.searchParams.get('offset') ?? 0);
        const limit = Number(url.searchParams.get('node_limit') ?? 1000);
        const all = [...children].map(([child, isDir]) => ({ uri: child, isDir }));
        return reply(200, all.slice(offset, offset + limit), { has_more: offset + limit < all.length });
      }
      if (!listing) return reply(200, [], { has_more: false });
      const offset = Number(url.searchParams.get('offset') ?? 0);
      const limit = Number(url.searchParams.get('node_limit') ?? 1000);
      return reply(200, listing.entries.slice(offset, offset + limit), { has_more: Boolean(listing.has_more) });
    }
    if (route === 'POST /api/v1/resources/temp_upload') {
      const form = await new Response(raw, { headers: { 'content-type': request.headers['content-type'] } }).formData();
      const file = form.get('file');
      const id = `upload_${next += 1}${path.extname(file.name)}`;
      state.uploads.set(id, { name: file.name, type: file.type, data: Buffer.from(await file.arrayBuffer()) });
      return reply(200, { temp_file_id: id });
    }
    if (route === 'POST /api/v1/resources') {
      const upload = state.uploads.get(body.temp_file_id);
      const source = body.path || upload?.name || body.url || 'resource';
      const leaf = String(source).split('/').filter(Boolean).pop() || 'resource';
      const target = canonical(body.to || body.parent);
      const root = canonical(body.to) || `${target ?? `viking://user/${user}/resources/inbox`}/${leaf}`;
      const destination = target ?? root;
      const id = `task-${next += 1}`;
      state.tasks.set(id, { polls: 0, to: destination, upload: upload ?? { name: source }, root });
      if (body.temp_file_id && !upload) return reply(400, 'unknown upload');
      if (body.temp_file_id && body.args?.parse_mode !== 'no_split') {
        const stem = path.basename(upload.name, path.extname(upload.name));
        const pieces = split(upload.data.toString('utf8'));
        pieces.forEach((piece, index) => state.files.set(`${destination}/${pieces.length > 1 ? `${stem}_${index + 1}` : stem}.md`, piece));
        state.tasks.get(id).landed = true;
      }
      return reply(200, { status: 'success', task_id: id, root_uri: root });
    }
    if (request.method === 'GET' && url.pathname.startsWith('/api/v1/tasks/')) {
      const task = state.tasks.get(decodeURIComponent(url.pathname.slice('/api/v1/tasks/'.length)));
      if (!task) return reply(404, 'no such task');
      task.polls += 1;
      if (task.landed) return reply(200, { status: 'completed', stage: 'completed' });
      if (failParse.includes(task.upload.name)) return reply(200, { status: 'failed', stage: 'failed' });
      if (task.polls < 2 || state.slowParse.has(task.upload.name)) return reply(200, { status: 'running', stage: 'parsing' });
      const stem = path.basename(task.upload.name, path.extname(task.upload.name));
      state.files.set(`${task.to}/${stem}.md`, `parsed ${task.upload.name}: ${task.upload.data.toString('latin1')}`);
      return reply(200, { status: 'running', stage: 'processing_queue' });
    }
    if (route === 'DELETE /api/v1/fs') {
      if (!state.files.delete(uri)) return reply(404, 'not found');
      return reply(200, { uri, estimated_deleted_count: 1 });
    }
    if (route === 'GET /api/v1/fs/stat') {
      if (state.files.has(uri)) return reply(200, { uri, isDir: false });
      if ([...state.files.keys()].some((key) => key.startsWith(`${uri}/`))) return reply(200, { uri, isDir: true });
      return reply(404, 'not found');
    }
    if (route === 'GET /api/v1/fs/tree') {
      return reply(200, [...state.files.keys()].filter((key) => key.startsWith(`${uri}/`))
        .map((key) => ({ uri: key, isDir: false, rel_path: key.slice(uri.length + 1) })));
    }
    if (route === 'GET /api/v1/content/read') {
      if (!state.files.has(uri)) return reply(404, 'not found');
      const text = state.files.get(uri);
      const offset = Number(url.searchParams.get('offset') ?? 0);
      const limit = url.searchParams.get('limit');
      if (limit === '-1' || limit === null) return reply(200, text);
      const size = Number(limit);
      return reply(200, text.split('\n').slice(offset, offset + size).join('\n'));
    }
    if (route === 'POST /api/v1/sessions') {
      const id = body?.session_id ?? `session-${next += 1}`;
      state.sessions.set(id, { policy: body?.auto_commit_policy ?? null, messages: [], commits: 0 });
      return reply(200, { session_id: id, uri: `viking://user/testuser/sessions/${id}` });
    }
    const session = url.pathname.match(/^\/api\/v1\/sessions\/([^/]+)\/(messages|commit)$/);
    if (session && request.method === 'POST') {
      const record = state.sessions.get(decodeURIComponent(session[1]));
      if (!record) return reply(404, 'no such session');
      if (session[2] === 'messages') {
        record.messages.push({ role: body.role, content: body.content });
        return reply(200, { session_id: session[1], message_count: record.messages.length });
      }
      record.commits += 1;
      return reply(200, { status: 'completed', task_id: `commit-${next += 1}` });
    }
    return reply(404, `no route ${route}`);
  });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  t.after(() => { server.closeAllConnections(); server.close(); });
  return { state, url: `http://127.0.0.1:${server.address().port}` };
}
