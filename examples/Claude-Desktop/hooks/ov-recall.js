// ov-recall.js  v2.2  — Auto-recall for Claude Code
// Hook: UserPromptSubmit
//
// Fires before every user prompt. Searches OpenViking semantically
// and injects the top results as system context for Claude Code.
//
// Response structure (OpenViking v0.3.16):
//   result.resources[]  -- stored documents and files
//   result.memories[]   -- extracted knowledge from past sessions
//
// Configuration: edit OV_KEY, RECALL_N, MIN_SCORE below.
// No domain-specific keywords or scoping — searches all your data.

'use strict';
var http = require('http');
var fs   = require('fs');
var path = require('path');
var os   = require('os');

// ── Config ────────────────────────────────────────────────────────────────
var OV_HOST   = '127.0.0.1';
var OV_PORT   = 1933;
var OV_KEY    = process.env.OV_API_KEY || 'YOUR_LOCAL_API_KEY';
var OV_USER   = 'default';
var OV_ACCT   = 'default';
var RECALL_N  = 6;      // max results to inject per prompt
var MIN_SCORE = 0.15;   // ignore results below this relevance score
var LOG_FILE  = path.join(os.homedir(), '.claude-memory', 'logs', 'ov-recall.log');
var STATE_F   = path.join(os.homedir(), '.claude-memory', '.session_state.json');

// ── Helpers ───────────────────────────────────────────────────────────────
function log(msg) {
    try { fs.appendFileSync(LOG_FILE, '[' + new Date().toISOString() + '] ' + msg + '\n'); } catch (_) {}
}

function loadState() {
    try { return JSON.parse(fs.readFileSync(STATE_F, 'utf8')); } catch (_) { return {}; }
}

function saveState(s) {
    try { fs.writeFileSync(STATE_F, JSON.stringify(s, null, 2), 'utf8'); } catch (_) {}
}

function request(method, urlPath, body, timeoutMs) {
    return new Promise(function(resolve, reject) {
        var data = body ? JSON.stringify(body) : null;
        var headers = {
            'Authorization':        'Bearer ' + OV_KEY,
            'x-api-key':            OV_KEY,
            'x-openviking-user':    OV_USER,
            'x-openviking-account': OV_ACCT
        };
        if (data) {
            headers['Content-Type']   = 'application/json';
            headers['Content-Length'] = Buffer.byteLength(data);
        }
        var opts = {
            hostname: OV_HOST, port: OV_PORT,
            path: urlPath, method: method,
            headers: headers, timeout: timeoutMs || 8000
        };
        var req = http.request(opts, function(res) {
            var buf = '';
            res.on('data', function(c) { buf += c; });
            res.on('end', function() {
                try { resolve(JSON.parse(buf)); }
                catch (e) { resolve({ _raw: buf }); }
            });
        });
        req.on('error', reject);
        req.on('timeout', function() { req.destroy(); reject(new Error('timeout')); });
        if (data) req.write(data);
        req.end();
    });
}

function isUp() {
    return request('GET', '/health', null, 3000)
        .then(function() { return true; })
        .catch(function() { return false; });
}

function readStdin() {
    return new Promise(function(resolve) {
        if (process.stdin.isTTY) { resolve(''); return; }
        var data = '';
        process.stdin.setEncoding('utf8');
        process.stdin.on('data', function(c) { data += c; });
        process.stdin.on('end', function() { resolve(data.trim()); });
        setTimeout(function() { resolve(data.trim()); }, 2500);
    });
}

// Extract items from confirmed response: result.resources[] + result.memories[]
function extractItems(res) {
    var items = [];
    if (!res) return items;
    var result = (res && res.result) ? res.result : res;
    var resources = (result && result.resources) ? result.resources : [];
    var memories  = (result && result.memories)  ? result.memories  : [];
    var i;
    for (i = 0; i < resources.length; i++) { items.push(resources[i]); }
    for (i = 0; i < memories.length;  i++) { items.push(memories[i]); }
    return items;
}

// ── Main ──────────────────────────────────────────────────────────────────
(function() {
    readStdin().then(function(raw) {
        var prompt = '';
        if (raw) {
            try {
                var p = JSON.parse(raw);
                prompt = ((p.prompt || p.message || p.text) || '').trim();
            } catch (_) {
                prompt = raw.slice(0, 600);
            }
        }

        // Skip slash commands, empty prompts
        if (!prompt || prompt.length < 6 || prompt.charAt(0) === '/') {
            process.exit(0); return;
        }

        isUp().then(function(up) {
            if (!up) { log('Server down - recall skipped'); process.exit(0); return; }

            // Create session if none exists
            var state = loadState();
            var sessionPromise;
            if (!state.current_session) {
                sessionPromise = request('POST', '/api/v1/sessions', {}, 8000)
                    .then(function(sess) {
                        var id = null;
                        var res = (sess && sess.result) ? sess.result : null;
                        if (res) { id = res.session_id || res.id; }
                        if (!id && sess) { id = sess.session_id || sess.id; }
                        if (id) {
                            state.current_session = id;
                            state.started_at = new Date().toISOString();
                            saveState(state);
                            log('Session created: ' + id);
                        }
                    }).catch(function(e) { log('Session create error: ' + e.message); });
            } else {
                sessionPromise = Promise.resolve();
            }

            sessionPromise.then(function() {
                // Search all data — no scoping, no keyword filtering
                var searchBody = { query: prompt.slice(0, 400), limit: RECALL_N };

                request('POST', '/api/v1/search/find', searchBody, 10000)
                    .then(function(res) {
                        var items = extractItems(res);
                        var relevant = [];
                        for (var i = 0; i < items.length; i++) {
                            var s = (items[i].score !== undefined) ? items[i].score : 1;
                            if (s >= MIN_SCORE) { relevant.push(items[i]); }
                            if (relevant.length >= RECALL_N) break;
                        }

                        if (relevant.length > 0) {
                            var lines = ['\n[OpenViking Memory - Auto-Recall]'];
                            for (var j = 0; j < relevant.length; j++) {
                                var item    = relevant[j];
                                var uri     = item.uri || '';
                                var title   = item.title || (uri ? uri.split('/').pop() : ('Result ' + (j+1)));
                                var snippet = (item.abstract || item.content || item.snippet || '')
                                    .slice(0, 350).replace(/\n+/g, ' ').trim();
                                var score   = (item.score !== undefined) ? item.score.toFixed(2) : '?';
                                lines.push('\n' + (j+1) + '. ' + title + '  [score: ' + score + ']');
                                if (uri)     lines.push('   URI: ' + uri);
                                if (snippet) lines.push('   ' + snippet);
                            }
                            lines.push('\n[End recalled context]');
                            process.stdout.write(lines.join('\n') + '\n');
                            log('Recalled ' + relevant.length + ' items for: "' + prompt.slice(0, 60) + '"');
                        } else {
                            log('No results above MIN_SCORE (' + MIN_SCORE + ') for: "' + prompt.slice(0, 60) + '"');
                        }

                        // Add user turn to active session
                        state = loadState();
                        if (state.current_session) {
                            request('POST', '/api/v1/sessions/' + state.current_session + '/messages',
                                { role: 'user', content: prompt.slice(0, 3000) }, 6000
                            ).catch(function(e) { log('Add-message error: ' + e.message); });
                        }
                    })
                    .catch(function(e) { log('Search error: ' + e.message); process.exit(0); });
            });
        });
    }).catch(function(err) {
        log('ov-recall.js fatal: ' + err.message);
        process.exit(0);
    });
})();
