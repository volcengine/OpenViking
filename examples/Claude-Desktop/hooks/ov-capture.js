// ov-capture.js  v2.1  — Auto-capture for Claude Code
// Hook: Stop
//
// Fires at end of every Claude Code session.
// Commits the session so OpenViking extracts and stores memories.
// Written in ES5 for compatibility with all Node.js versions.

'use strict';
var http = require('http');
var fs   = require('fs');
var path = require('path');
var os   = require('os');

var OV_HOST  = '127.0.0.1';
var OV_PORT  = 1933;
var OV_KEY   = process.env.OV_API_KEY || 'YOUR_LOCAL_API_KEY';
var OV_USER  = 'default';
var OV_ACCT  = 'default';
var LOG_FILE = path.join(os.homedir(), '.claude-memory', 'logs', 'ov-capture.log');
var STATE_F  = path.join(os.homedir(), '.claude-memory', '.session_state.json');

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
            headers: headers, timeout: timeoutMs || 45000
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
    return request('GET', '/health', null, 4000)
        .then(function() { return true; })
        .catch(function() { return false; });
}

function safeGet(obj, prop) {
    if (obj && typeof obj === 'object' && obj.hasOwnProperty(prop)) { return obj[prop]; }
    return undefined;
}

(function() {
    var state     = loadState();
    var sessionId = safeGet(state, 'current_session');

    if (!sessionId) {
        log('No active session -- Stop hook done (nothing to commit)');
        process.exit(0); return;
    }

    isUp().then(function(up) {
        if (!up) { log('Server down -- capture skipped'); process.exit(0); return; }

        log('Committing session ' + sessionId + '...');

        return request('POST', '/api/v1/sessions/' + sessionId + '/commit', {}, 45000)
            .then(function(result) {
                var r         = safeGet(result, 'result') || result || {};
                var extracted = safeGet(r, 'memories_extracted');
                var updated   = safeGet(r, 'active_count_updated');
                var archived  = safeGet(r, 'archived');

                if (extracted === undefined) { extracted = 0; }
                if (updated   === undefined) { updated   = 0; }
                if (archived  === undefined) { archived  = false; }

                log('Committed: ' + extracted + ' memories extracted, ' + updated + ' updated, archived=' + archived);

                state.last_commit = {
                    session_id:           sessionId,
                    committed_at:         new Date().toISOString(),
                    memories_extracted:   extracted,
                    active_count_updated: updated
                };
                delete state.current_session;
                delete state.started_at;
                saveState(state);

                if (extracted > 0) {
                    process.stdout.write('[OpenViking] Session captured: ' + extracted + ' memories stored.\n');
                }
                process.exit(0);
            });
    }).catch(function(err) {
        log('ov-capture.js error: ' + err.message);
        process.exit(0);
    });
})();
