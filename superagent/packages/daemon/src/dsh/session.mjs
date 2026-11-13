// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

// Session boundary: the lifetime of the DSH child process and the root session it
// owns. This module starts and reconnects that process (version probe, spawn,
// initialize with its two downgrades, bridge hello, resume or fresh create),
// records which pair the handshake really applied, persists and rotates the
// session, handles the exit that invalidates it, and shuts it down. What a turn
// does and which model it runs are decided elsewhere: this module only establishes
// the process those decisions land in.
import { randomUUID } from 'node:crypto';
import { safeError as safe } from '../logger.mjs';
import { terminateProcessTree } from '../process-utils.mjs';
import { agentCliCommands } from '../agent-clis.mjs';
import { agentToolEnv, agentToolServer } from '../actions/api.mjs';
import { captureStartupStderr } from '../runtime-diagnostics.mjs';
import { spawnDetached } from '../platform/index.mjs';
import {
  DSH_BRIDGE_DAEMON_METHODS,
  DSH_BRIDGE_PATH,
  DSH_BRIDGE_VERSION,
  DSH_DEFAULT_MODEL,
  assertDshNodeVersion,
  assertSupportedDshVersion,
  bridgeHelloSupports,
  dshCommand,
  dshProfilePatchHash,
  dshProfilePatchPath,
  parseDshModel,
  prepareDshEnvironment,
  retryDshNotReady,
  sanitizeDshEnv,
  writeDshPatch,
} from '../dsh-runtime.mjs';
import { readDshExtensions } from '../dsh-extensions.mjs';
import { dshHostHome, dshSkillDirectories } from '../dsh-config.mjs';
import { number } from './shared.mjs';
import {
  appliedSelection, initializeFailure, modelSelection, modelUnavailable,
} from './models.mjs';

// Consecutive handshake failures before queued input is failed.
const RECONNECT_QUEUE_FAILURES = 3;
// How many catalogue routes one handshake may try before it gives up. Each
// attempt is a failed initialize round trip, so the bound keeps a catalogue
// where every route refuses from turning a start into a long stall.
const MAX_ADOPTED_ROUTES = 3;

export default {
  activateSession() {
    this.sessionPersistence = true;
    this.autoReconnect = true;
  },

  _larkProfile() {
    return this.config.connectors?.includes('lark') ? this.config.profile : '';
  },

  // A handshake is several awaits long (version probe, spawn, initialize,
  // catalogue) and stop() can land in any of them. Every point that is about to
  // take ownership of a child asks this first, so a stopped generation cannot
  // adopt one after its own shutdown already ran.
  _throwIfStopping() {
    if (this.stopping) throw new Error('dsh stopped during startup');
  },

  _childEnv() {
    return sanitizeDshEnv(this._environment, {
      profile: this._larkProfile(),
      actions: agentToolEnv(this.config, this.authorityToken),
      cliBins: agentCliCommands(this.config).map(({ bin }) => bin),
      dataDir: this.config.dataDir,
      dshHomeMode: this.config.dshHomeMode,
      homeDir: dshHostHome(this._environment),
    });
  },

  async start() {
    this.stopping = false;
    this.autoReconnect = this.sessionPersistence;
    this.starting = null;
    // The handshake is tracked so stop() can wait for one that is already in
    // flight. It outlives a stop that arrives before the child exists (the
    // version probe alone is a second `node --import tsx` launch, seconds
    // wide), and the child it spawns afterwards would belong to a generation
    // that has already been shut down.
    const handshake = this._handshake();
    this.starting = handshake;
    try {
      await handshake;
    } finally {
      if (this.starting === handshake) this.starting = null;
    }
  },

  async _probeVersion(env) {
    assertDshNodeVersion();
    if (this._versionProbe) {
      const text = await this._versionProbe();
      this.dshVersion = assertSupportedDshVersion(text);
      return this.dshVersion;
    }
    const bin = this.config.dshBin || 'dsh';
    const output = await new Promise((resolve, reject) => {
      let child;
      try {
        child = this._spawn(bin, ['--version'], {
          cwd: this.config.workspace, env, shell: false,
          detached: spawnDetached, stdio: ['ignore', 'pipe', 'pipe'],
        });
      } catch (error) { reject(error); return; }
      let stdout = '';
      let stderr = '';
      let settled = false;
      const finish = (error) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        if (error) reject(error);
        else resolve(stdout.trim());
      };
      const timer = setTimeout(() => {
        terminateProcessTree(child, 'SIGKILL');
        finish(new Error('dsh --version timed out'));
      }, this.versionTimeoutMs);
      timer.unref?.();
      child.stdout?.setEncoding('utf8');
      child.stderr?.setEncoding('utf8');
      child.stdout?.on('data', (chunk) => { stdout = (stdout + chunk).slice(-4096); });
      child.stderr?.on('data', (chunk) => { stderr = (stderr + chunk).slice(-4096); });
      child.once('error', finish);
      child.once('exit', (code, signal) => {
        if (code === 0) finish();
        else finish(new Error(`dsh --version failed (code ${code}, signal ${signal})${stderr ? `; stderr: ${stderr}` : ''}`));
      });
    });
    this.dshVersion = assertSupportedDshVersion(output);
    return this.dshVersion;
  },

  _resumeCandidate() {
    const global = this.store.getGlobalSession?.() ?? {};
    const resumable = Boolean(this.allowResume
      && !this.forceFreshSession
      && global.sessionId
      && global.sessionReady !== false
      && global.policyHash === this.policyHash
      && global.runtime === this.runtime
      && global.epoch === this.cognitionEpoch);
    return { global, resumable };
  },

  async _handshake() {
    // Snapshot the settings this process is actually started with. A setModel or
    // setEffort that lands mid-handshake stays desired-but-not-applied, so the
    // first turn boundary applies it instead of this process claiming it: it is
    // newer than this snapshot, and the downgrade below compares against it
    // rather than overwriting it.
    const settings = Object.freeze({ model: this.model, effort: this.effort });
    // A fresh process reads the settings document again, so a pair the previous
    // one refused gets one more chance: the document may have gained its route
    // since. A refusal during this handshake records it again.
    this.rejectedSelection = null;
    const env = this._childEnv();
    await this._probeVersion(env);
    this._throwIfStopping();
    const { global, resumable } = this._resumeCandidate();
    this.sessionId = resumable ? global.sessionId : randomUUID();
    this.sessionReady = false;
    this.syncedThroughSeq = resumable ? global.syncedThroughSeq ?? 0 : 0;
    this.deliveredThroughSeq = this.syncedThroughSeq;
    this.needsHistoryPrefix = !resumable;
    // No credential pre-check: a route's key may live in DSH's own
    // <DSH_HOME>/.credentials.yaml (written by `ov-superagent dsh model key set`), which
    // this process cannot read, and a process without any key still starts and
    // answers the first turn with MISSING_CREDENTIAL -- naming the credentials
    // service and the export route. Refusing to spawn would block a working
    // configuration on a premise that does not hold.
    const dshHomeMode = this.config.dshHomeMode;
    const homeDir = dshHostHome(this._environment);
    prepareDshEnvironment(this.config.dataDir, { dshHomeMode, homeDir });
    const tools = agentToolServer(this.config, this.authorityToken);
    const extensions = readDshExtensions({
      dataDir: this.config.dataDir,
      workspace: this.config.workspace,
      warn: message => this.logger.warn('dsh_extensions_invalid', { message: String(message) }),
    });
    const skillDirs = dshSkillDirectories({
      workspace: this.config.workspace,
      dataDir: this.config.dataDir,
      mode: dshHomeMode,
      selected: this.config.dshSkillRoots,
      homeDir,
    });
    for (const directory of extensions.skillDirs) {
      if (!skillDirs.includes(directory)) skillDirs.push(directory);
    }
    const patchPath = writeDshPatch({
      dataDir: this.config.dataDir,
      policy: this.systemPrompt,
      bridgePath: DSH_BRIDGE_PATH,
      mcp: {
        ...tools,
        toolCallTimeoutMs: Math.max(10_000, number(this.config.sendTimeoutMs) + 5_000),
      },
      mcpServers: extensions.mcpServers,
      skillDirs,
      includeDefaultSkillRoots: false,
      dshHomeMode,
      homeDir,
    });
    const command = dshCommand({ bin: this.config.dshBin || 'dsh', patchPath });
    // The change-detection baseline is the document this child is started from,
    // sampled before it can read the file: DSH reads the settings document in its
    // own start-up, so sampling it once the handshake finished would record a
    // write that landed mid-handshake as applied and silently swallow it (§4.4).
    // A write that still lands between here and DSH's own read only costs one
    // extra restart, which is the safe direction.
    const startedFrom = dshProfilePatchHash({ dataDir: this.config.dataDir, dshHomeMode, homeDir });

    return new Promise((resolve, reject) => {
      let child;
      try {
        child = this._spawn(command.command, command.args, {
          cwd: this.config.workspace,
          env,
          shell: false,
          detached: spawnDetached,
          stdio: ['pipe', 'pipe', 'pipe'],
        });
      } catch (error) { reject(error); return; }
      // A stop that landed while this handshake was still in the probe window
      // has already run `_terminateCurrent` against `child === null`. Adopting
      // this child now would leave it unowned: the stopped generation would
      // finish its handshake, publish a session, and hold DSH_HOME while the
      // next one starts. Kill it here instead and fail the handshake.
      if (this.stopping) {
        terminateProcessTree(child, 'SIGKILL');
        reject(new Error('dsh stopped during startup'));
        return;
      }
      this.child = child;
      this.connected = false;
      this.stdoutBuffer = '';
      this.stdoutDiscarding = false;
      this.stderrBuffer = '';
      const startupStderr = captureStartupStderr(child.stderr);
      let ready = false;
      let settled = false;
      const failStart = (cause) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        const diagnostic = startupStderr.text();
        startupStderr.stop();
        if (diagnostic && !cause.message.includes(diagnostic)) {
          cause.message = `${safe(cause)}; stderr: ${diagnostic}`;
        }
        if (this.child === child) {
          this.child = null;
          this.connected = false;
        }
        this._rejectPendingRpc(new Error('dsh startup failed'), child);
        terminateProcessTree(child, 'SIGKILL');
        reject(cause);
      };
      const timer = setTimeout(() => failStart(new Error('dsh SDK startup timed out')), this.startTimeoutMs);
      timer.unref?.();

      child.stdin?.on('error', (error) => {
        if (this.child === child) this.logger.warn('dsh_stdin_error', { error: safe(error) });
      });
      child.stdout?.setEncoding('utf8');
      child.stderr?.setEncoding('utf8');
      child.stdout?.on('data', (chunk) => {
        if (this.child === child) this._onStdout(chunk, child);
      });
      child.stderr?.on('data', (chunk) => {
        if (this.child !== child) return;
        this._onStderr(chunk);
      });
      child.once('error', (error) => {
        this._handleExit(child, null, null, error);
        if (!ready) failStart(error);
      });
      child.once('exit', (code, signal) => {
        this._handleExit(child, code, signal);
        if (!ready) failStart(new Error(`dsh exited during startup (code ${code}, signal ${signal})`));
      });

      (async () => {
        // initialize always carries the reasoning effort (N2); the two
        // downgrades below are the only exceptions, and neither rewrites the
        // persisted choice.
        const attempt = await this._initializeChild(child, settings);
        const hello = await this._requestOn(child, 'superagent/hello', {}, this.controlTimeoutMs);
        if (!bridgeHelloSupports(hello, DSH_BRIDGE_DAEMON_METHODS)) {
          throw new Error(
            `dsh superagent/hello did not expose bridge v${DSH_BRIDGE_VERSION}`
            + ` (${DSH_BRIDGE_DAEMON_METHODS.join(', ')})`,
          );
        }
        // `llm-pi-ai` is an optional route layer: DSH starts without it even when
        // its document is unusable. Report that once, never as a startup failure.
        await this._noteModelSettings(child);
        let resumed = false;
        if (resumable) {
          try {
            await this._requestOn(child, 'session/resume', { sessionId: this.sessionId }, this.controlTimeoutMs);
            this.sessionReady = true;
            this.needsHistoryPrefix = false;
            resumed = true;
          } catch (error) {
            this.logger.warn('dsh_resume_failed_fresh_start', { error: safe(error) });
            this.sessionId = randomUUID();
            this.sessionReady = false;
            this.syncedThroughSeq = 0;
            this.deliveredThroughSeq = 0;
            this.needsHistoryPrefix = true;
            await this.store.prepareOVContext?.(this.cognitionEpoch);
            await this._requestOn(child, 'session/working-directory/get', {
              sessionId: this.sessionId,
            }, this.controlTimeoutMs);
            this.sessionReady = true;
          }
        } else {
          await this.store.prepareOVContext?.(this.cognitionEpoch);
          await this._requestOn(child, 'session/working-directory/get', {
            sessionId: this.sessionId,
          }, this.controlTimeoutMs);
          this.sessionReady = true;
        }
        this.forceFreshSession = false;
        // D4: a resumed session restores its own recorded selection, so the
        // route this process initialized with is re-asserted before the first
        // turn. initialize is what this process really runs, so that pair is
        // recorded as applied even when the re-assertion is refused: recording
        // null there left `_selectionPending()` true forever, with no applied
        // pair to roll back to.
        const initialized = {
          model: `${attempt.provider}/${attempt.model}`,
          effort: attempt.effort || null,
        };
        const reselected = resumed ? await this._reselectSession(child, attempt) : null;
        const applied = reselected ?? initialized;
        // The applied pair belongs to this session, and to this process: a
        // session created later in the same process inherits the initialize
        // route, so that pair is recorded separately (P1-4 ①).
        this._recordApplied({ ...applied, sessionId: this.sessionId });
        this.processSelection = Object.freeze({ ...initialized });
        this.selectionUnresolved = false;
        // A refused re-assertion keeps the selection stale: the next boundary
        // asks once more, and its own failure path records the pair as refused.
        this.selectionStale = resumed && !reselected;
        this._recordHandshakeSettings(settings, attempt);
        // The baseline is the document this child was started from, not a re-read
        // now: a write that landed during the handshake is still unread.
        this.profilePatchHash = startedFrom;
        if (this.child !== child) throw new Error('dsh process was replaced during startup');
        ready = true;
        settled = true;
        clearTimeout(timer);
        startupStderr.stop();
        this.connected = true;
        this.startedOk = true;
        this.backoffMs = this.reconnectBaseMs;
        this._persistSession();
        this._recordActivity('agent.ready', {
          metadata: {
            runtime: this.runtime,
            version: this.dshVersion,
            bridgeVersion: DSH_BRIDGE_VERSION,
            ...(attempt.downgraded
              ? { modelDowngrade: {
                requested: settings.model,
                requestedEffort: settings.effort,
                model: applied.model,
                effort: attempt.effort,
              } }
              : {}),
          },
        });
        resolve();
        this._drain();
        this._notifyAvailable();
      })().catch(failStart);
    });
  },

  // The model settings document is an optional layer: a broken one leaves DSH
  // running with the built-in routes only. Its descriptor is the one readable
  // signal, so a missing one is reported here instead of guessed from stderr.
  async _noteModelSettings(child) {
    let descriptor;
    try {
      const reply = await retryDshNotReady(
        () => this._requestOn(child, 'superagent/settings/describe', {}, this.controlTimeoutMs),
      );
      descriptor = reply?.descriptor ?? null;
    } catch (error) {
      this.logger.warn('dsh_model_settings_probe_failed', { error: safe(error) });
      return;
    }
    if (descriptor) return;
    this.logger.warn('dsh_model_settings_invalid', {
      path: dshProfilePatchPath({
        dataDir: this.config.dataDir,
        dshHomeMode: this.config.dshHomeMode,
        homeDir: dshHostHome(this._environment),
      }),
      detail: 'llm-pi-ai did not activate; only the built-in routes are available',
    });
  },

  // initialize with the design's two downgrades: an effort the route cannot
  // serve is retried without one, and a route or model DSH does not know falls
  // back to the built-in default. A stored choice is never rewritten on that
  // path -- the downgrade is this process's own, and the next start retries the
  // stored one.
  //
  // A successful initialize is NOT evidence that the route can run: DSH registers
  // a built-in provider unconditionally and answers initialize from the adapter,
  // while the route serves ZERO models until its credential exists. Measured on
  // dsh 0.2.2-rc.1: `initialize deepseek-official/deepseek-flash` succeeds with no
  // DEEPSEEK_API_KEY while `superagent/llm/models` lists that route with no models. So
  // after a route starts, the live catalogue is what decides whether it is
  // servable, and a route it cannot serve is replaced -- when the coordinator
  // allows a substitution (the caller named no model) -- with one it can. Both
  // shapes lead there: a refused initialize, and an accepted one on a route the
  // catalogue says serves nothing.
  async _initializeChild(child, settings) {
    const route = parseDshModel(settings.model);
    const builtIn = parseDshModel(DSH_DEFAULT_MODEL);
    const routes = [route];
    if (route.provider !== builtIn.provider || route.model !== builtIn.model) routes.push(builtIn);
    let lastError = null;
    for (const candidate of routes) {
      try {
        const applied = await this._initializeRoute(child, { ...candidate, effort: settings.effort });
        const started = {
          ...applied,
          downgraded: applied.effort !== settings.effort
            || candidate.provider !== route.provider || candidate.model !== route.model,
        };
        // The catalogue is only consulted when a substitution is allowed at all:
        // an explicitly requested model must fail loudly at request time rather
        // than be quietly replaced here.
        if (this.allowModelFallback !== true) return started;
        const catalogue = await this._catalogueRoutes(child);
        // No answer is not an answer: a probe that failed (an older bridge, a
        // request the SDK refused) must not be read as "nothing is servable" and
        // move a healthy start onto another route.
        if (catalogue.length === 0 || this._servable(catalogue, applied)) return started;
        // The catalogue answered and does not list this route: exactly the shape a
        // built-in default without its credential has. Replace it.
        this.logger.warn('dsh_model_unservable', {
          model: `${applied.provider}/${applied.model}`,
          catalogue: catalogue.reduce((count, entry) => count + entry.models.length, 0),
        });
        const adopted = await this._adoptCatalogueRoute(child, {
          settings, route: applied, catalogue,
        });
        return adopted ?? started;
      } catch (error) {
        lastError = error;
      }
    }
    // No route started. A route/model refusal is the one verdict that says another
    // route might work, so the catalogue gets the last word -- but only when a
    // substitution is allowed at all.
    if (this.allowModelFallback === true) {
      const kind = initializeFailure(lastError);
      if (kind === 'model' || kind === 'route') {
        const adopted = await this._adoptCatalogueRoute(child, { settings, route });
        if (adopted) return adopted;
      }
    }
    throw lastError ?? new Error('dsh initialize failed');
  },

  // Whether a route this process initialized on can actually serve work: the live
  // catalogue lists that provider AND that exact model. A provider that is listed
  // with no models (a route without its credential) serves nothing, and a
  // provider that lists other models does not serve this one either.
  _servable(catalogue, applied) {
    const provider = applied?.provider ?? '';
    const model = applied?.model ?? '';
    return catalogue.some((entry) => entry.provider === provider && entry.models.includes(model));
  },

  // One route's initialize with the effort downgrade that belongs to that route:
  // an effort it cannot serve is retried without one. Any other refusal is
  // rethrown, so the caller decides whether the route itself is unusable.
  async _initializeRoute(child, { provider, model, effort }) {
    let level = effort;
    for (;;) {
      try {
        await this._requestOn(child, 'initialize', {
          cwd: this.config.workspace,
          provider,
          model,
          ...(level ? { reasoningEffort: level } : {}),
          approvalRequests: true,
        }, this.startTimeoutMs);
        return { provider, model, effort: level };
      } catch (raw) {
        const error = modelUnavailable(raw);
        if (initializeFailure(error) === 'effort' && level) {
          level = '';
          continue;
        }
        throw error;
      }
    }
  },

  // The routes DSH reports right now, in the order it lists them, each with the
  // model ids it can serve (`models` empty means the route exists but serves
  // nothing -- no credential, not signed in). Best effort: a bridge that cannot
  // answer (an older bridge answers method-not-found, a request this SDK refuses
  // before initialize) leaves the caller with the error it already has, so this
  // probe never becomes a new failure mode of its own.
  //
  // Deliberately no `superagent/hello` here: the handshake sends it once, right after
  // initialize, and a second one would be a second probe of the same surface.
  // The same per-request bound as every other handshake RPC: measured at ~20ms
  // on a real dsh 0.2.2-rc.1, since a route's model list is catalog-driven and
  // never touches the provider endpoint.
  async _catalogueRoutes(child) {
    try {
      const reply = await retryDshNotReady(
        () => this._requestOn(child, 'superagent/llm/models', {}, this.controlTimeoutMs),
      );
      const routes = [];
      for (const provider of Array.isArray(reply?.providers) ? reply.providers : []) {
        if (!provider?.provider) continue;
        const models = (Array.isArray(provider.models) ? provider.models : [])
          .filter((model) => model?.id)
          .map((model) => String(model.id));
        routes.push({ provider: String(provider.provider), models });
      }
      return routes;
    } catch (error) {
      this.logger.warn('dsh_model_catalogue_unavailable', { error: safe(error) });
      return [];
    }
  },

  // Replace a route the catalogue proved it cannot serve with the first one it
  // can. Another provider is preferred: the route that just failed is the least
  // likely to answer, and on the measured machine the operator route lives
  // elsewhere entirely. A different model on the SAME provider is still tried
  // afterwards -- a built-in route that lost only one model is not a reason to
  // abandon the provider.
  //
  // The pair is persisted only for this instance's own first start that has no
  // stored choice -- the design's rule ("only on a first start with nothing
  // set"). A stored choice belongs to the user and stays, and a staging attempt
  // for a runtime switch must not write either: its commit records the pair.
  async _adoptCatalogueRoute(child, { settings, route, catalogue = null }) {
    const routes = catalogue ?? await this._catalogueRoutes(child);
    // Only a route that lists something can be adopted, and never the exact pair
    // this process already tried.
    const failed = `${route?.provider ?? ''}/${route?.model ?? ''}`;
    const candidates = routes.flatMap((entry) => entry.models
      .map((model) => ({ provider: entry.provider, model }))
      .filter((candidate) => `${candidate.provider}/${candidate.model}` !== failed));
    if (candidates.length === 0) return null;
    const ordered = [
      ...candidates.filter((candidate) => candidate.provider !== route?.provider),
      ...candidates.filter((candidate) => candidate.provider === route?.provider),
    ];
    let attempts = 0;
    for (const candidate of ordered) {
      if (attempts >= MAX_ADOPTED_ROUTES) break;
      attempts += 1;
      const name = `${candidate.provider}/${candidate.model}`;
      try {
        const applied = await this._initializeRoute(child, {
          provider: candidate.provider, model: candidate.model, effort: settings.effort,
        });
        const adopted = `${applied.provider}/${applied.model}`;
        this.logger.warn('dsh_model_adopted', {
          requested: settings.model, model: adopted, effort: applied.effort,
          candidates: candidates.length,
        });
        // A start that silently runs a different model than the manifest names is
        // the one thing an operator cannot discover from the conversation: the
        // answer looks normal because a model did answer. Record it where the
        // other runtimes record their automatic switches, so it reaches the chat
        // instead of only the daemon log.
        this._recordActivity('agent.fallback', {
          status: 'SWITCHED',
          summary: `dsh model route ${settings.model} serves nothing; started on ${adopted}`,
          metadata: {
            runtime: this.runtime,
            requested: settings.model,
            model: adopted,
            candidates: candidates.length,
            trigger: 'dsh-startup',
          },
        });
        if (this.sessionPersistence !== false && !this.store.getSetting?.('dsh_model')) {
          try {
            this.store.setSetting?.('dsh_model', adopted);
          } catch (error) {
            this.logger.warn('dsh_model_adopt_persist_failed', { model: adopted, error: safe(error) });
          }
        }
        return { ...applied, downgraded: true, adopted };
      } catch (error) {
        this.logger.warn('dsh_model_adopt_failed', { model: name, error: safe(error) });
      }
    }
    return null;
  },

  // D4: re-assert this process's route on a resumed session, which restores the
  // selection it recorded when it was last used. Returns the applied pair, or
  // null when DSH refused it: the handshake then keeps the initialize pair as
  // applied and the selection stale, so the next boundary asks once more.
  async _reselectSession(child, attempt) {
    const model = `${attempt.provider}/${attempt.model}`;
    try {
      const result = await retryDshNotReady(() => this._requestOn(child, 'superagent/session/model/select', {
        sessionId: this.sessionId,
        ...modelSelection(model, attempt.effort),
      }, this.controlTimeoutMs));
      return appliedSelection(result, { model, effort: attempt.effort });
    } catch (error) {
      this.logger.warn('dsh_resume_model_select_failed', { model, error: safe(error) });
      return null;
    }
  },

  // A downgraded initialize runs a route the user did not ask for: log it once
  // and adopt the fallback as this process's desired pair. A stored choice stays
  // what the user picked, so the next start tries it again -- except when the
  // handshake adopted a catalogue route for a first start that had no setting at
  // all, which `_adoptCatalogueRoute` already saved as this instance's model.
  //
  // A selection made while this handshake was in flight is newer than the
  // snapshot it started from, so the downgrade must not overwrite it: the
  // boundary then applies that newer pair instead of this process quietly
  // running the fallback forever.
  _recordHandshakeSettings(settings, attempt) {
    if (!attempt.downgraded) return;
    const model = `${attempt.provider}/${attempt.model}`;
    this.logger.warn('dsh_model_downgraded', {
      requested: settings.model,
      requestedEffort: settings.effort,
      model,
      effort: attempt.effort,
    });
    const desired = this._desiredSelection();
    const superseded = desired.model !== settings.model
      || desired.effort !== this._normalizedEffort(settings.effort);
    if (superseded) {
      this.logger.info('dsh_model_downgrade_superseded', {
        startedFrom: settings.model, downgradedTo: model, desired: desired.model,
      });
      return;
    }
    this.model = model;
    // The ladder's effort drop is '' on the wire; the desired field speaks null.
    this.effort = this._normalizedEffort(attempt.effort);
    // One-time notice for the model itself: the user's stored choice was not
    // applied, so its next prompt must know which route it is really running.
    this.turnNotice ??= '[daemon notice] The configured DSH model'
      + ` ${settings.model} could not be started with`
      + ` (effort ${settings.effort || 'default'}); this process runs`
      + ` ${model} at effort ${attempt.effort || 'default'} instead.`
      + (this.store.getSetting?.('dsh_model') === model
        ? ' That route is now the saved model for this instance.'
        : ' The saved model choice is unchanged; fix it with /model or `ov-superagent dsh model list`.');
  },

  /**
   * Restart the SDK process between turns. Model and effort switches never use
   * this; it exists for a profile patch edited outside this process (a new
   * route, a changed endpoint), which only a fresh process reads.
   */
  async restartWhenIdle(reason = 'model-settings-changed') {
    if (this.stopping || this.restarting || this.settingsUpdate || this.sessionRotation) return false;
    if (!this.connected || this.turn || this.inflight.size > 0) return false;
    this.restarting = true;
    // This restart is the answer to the content read just now, so a failed
    // attempt cannot loop on it. The child is spawned much later, so `_handshake`
    // samples the document again right before spawning it and records that value
    // as the baseline (a write landing in between is then still a change).
    this.profilePatchHash = dshProfilePatchHash({
      dataDir: this.config.dataDir,
      dshHomeMode: this.config.dshHomeMode,
      homeDir: dshHostHome(this._environment),
    });
    this.settingsUpdate = (async () => {
      try {
        this.logger.info('dsh_restart_for_settings', { reason });
        await this._terminateCurrent();
        if (this.stopping) return;
        await this._handshake();
        return true;
      } catch (error) {
        // The waiting batch stays queued: this restart is itself the answer to a
        // settings change, and the input behind it is what the user just said. A
        // single transient failure (a 30s start timeout, an OOM kill) must not
        // fail it, while the reconnect loop below owns the retry budget and
        // fails the queue only when its own consecutive-failure limit is spent.
        this.logger.warn('dsh_restart_for_settings_failed', {
          reason, error: safe(error), queued: this.queue.length,
        });
        if (!this.stopping) void this._reconnectLoop();
        return false;
      } finally {
        this.restarting = false;
        this.settingsUpdate = null;
        this._drain();
        this._notifyAvailable();
      }
    })();
    return this.settingsUpdate;
  },

  async prepareSessionRotation() {
    while (this.settingsUpdate) await this.settingsUpdate;
    if (this.turn || this.inflight.size > 0) throw new Error('dsh session rotation requires an idle runtime');
    if (this.sessionRotation) throw new Error('dsh session rotation is already prepared');
    if (!this.connected) {
      const id = `pending-${randomUUID()}`;
      this.sessionRotation = { id, deferred: true };
      return Object.freeze({ id, sessionReady: false, deferred: true });
    }
    const id = randomUUID();
    this.sessionRotation = { id, deferred: false };
    try {
      await this._request('session/working-directory/get', { sessionId: id });
      return Object.freeze({ id, sessionReady: true });
    } catch (error) {
      this.sessionRotation = null;
      throw error;
    }
  },

  commitSessionRotation(prepared, { persist = true, preserveContext = false } = {}) {
    if (!prepared?.id || this.sessionRotation?.id !== prepared.id) {
      throw new Error('prepared dsh session is no longer current');
    }
    if (prepared.deferred) {
      this.forceFreshSession = true;
      this.sessionId = null;
      this.sessionReady = false;
    } else {
      this.sessionId = prepared.id;
      this.sessionReady = true;
    }
    // A new session inherits the initialize route, not the selection this one
    // carried: the applied pair is bound to a session, so the pair the retired
    // session ran is not evidence about this one. Recording the inherited pair
    // is what keeps a refused re-selection from looking applied (P1-4 ①).
    const inherited = this.processSelection ?? null;
    this._recordApplied({
      model: inherited?.model ?? null,
      effort: inherited?.effort ?? null,
      sessionId: this.sessionId,
    });
    // A refusal recorded for the retired session says nothing about the new
    // one: it must be asked again rather than suppressed by a stale record.
    this.rejectedSelection = null;
    // A session created here inherits the initialize route, so the route is
    // named again: an unconfirmed selection of the retired session no longer
    // leaves this one unknown.
    if (typeof inherited?.model === 'string') this.selectionUnresolved = false;
    // The turn boundary re-asserts the selection before the next input.
    this.selectionStale = true;
    this.needsHistoryPrefix = preserveContext;
    this.deliveredThroughSeq = this.syncedThroughSeq;
    this.sessionRotation = null;
    if (persist) this._persistSession();
    this._recordActivity('agent.session_rotated', {
      summary: '已开启新会话',
      metadata: { runtime: this.runtime, resume: false },
    });
    this._drain();
    this._notifyAvailable();
    return this.sessionId;
  },

  cancelSessionRotation() {
    this.sessionRotation = null;
    this._drain();
    this._notifyAvailable();
  },

  async rotateSession({ resume = false } = {}) {
    while (this.settingsUpdate) await this.settingsUpdate;
    if (this.turn || this.inflight.size > 0) throw new Error('dsh session rotation requires an idle runtime');
    if (resume) return this.sessionId;
    const prepared = await this.prepareSessionRotation();
    return this.commitSessionRotation(prepared, { preserveContext: false });
  },

  _markSessionUnresumable() {
    if (!this.sessionPersistence || !this.sessionId) return;
    try {
      const marked = this.store.markOwnedSessionUnresumable?.({
        runtime: this.runtime,
        authorityToken: this.authorityToken,
        sessionId: this.sessionId,
        epoch: this.cognitionEpoch,
      });
      if (marked === false) this.logger.info('dsh_session_unresumable_stale');
    } catch (error) {
      this.logger.warn('dsh_session_unresumable_failed', { error: safe(error) });
    }
  },

  _persistSession() {
    if (!this.sessionPersistence || !this.sessionId) return;
    try {
      if (this.store.publishOwnedSession) {
        const published = this.store.publishOwnedSession({
          runtime: this.runtime,
          authorityToken: this.authorityToken,
          sessionId: this.sessionId,
          policyHash: this.policyHash,
          epoch: this.cognitionEpoch,
          acceptedThroughSeq: this.syncedThroughSeq,
          sessionReady: this.sessionReady,
          nativeSessionId: null,
        });
        if (!published) this.logger.info('dsh_session_publish_stale');
        return;
      }
      this.store.setGlobalSession?.(this.sessionId, this.policyHash, {
        runtime: this.runtime,
        epoch: this.cognitionEpoch,
        syncedThroughSeq: this.syncedThroughSeq,
        sessionReady: this.sessionReady,
        nativeSessionId: null,
      });
    } catch (error) {
      this.logger.warn('dsh_session_persist_failed', { error: safe(error) });
    }
  },

  // Crash recovery contract, by what DSH confirmed before the process died:
  //   written, never acknowledged (delivery state `pending`)
  //     -> requeued into a fresh session with a history prefix: DSH has no record
  //        of accepting it, and a new session cannot double-apply it.
  //   acknowledged (`turn.items`, observed or not)
  //     -> failed once with 'dsh connection lost' and NEVER replayed: DSH holds it
  //        in its own session/inbox, so replaying it could run the same tool
  //        calls and replies twice. Whether the model saw it only changes the
  //        journal cursor, not whether it may be replayed.
  _handleExit(child, code, signal, processError = null) {
    if (this.child !== child) return;
    this._exitedChildren.add(child);
    const error = processError ?? this._connectionError();
    const turn = this.turn;
    // Deliveries written to this child but never acknowledged: DSH may or may
    // not have them, so they are requeued for a fresh session, never counted.
    const unacked = [...this.deliveries.values()]
      .filter((delivery) => delivery.state === 'pending' && delivery.child === child);
    const hadUnacked = unacked.length > 0;
    // Snapshot acceptance state before rejecting RPC promises runs their closures.
    if (hadUnacked) {
      for (const delivery of [...unacked].reverse()) {
        this._requeueDelivery(delivery, { drain: false });
      }
      this.forceFreshSession = true;
      this._markSessionUnresumable();
      this.sessionReady = false;
      this.needsHistoryPrefix = true;
      this.syncedThroughSeq = 0;
      this.deliveredThroughSeq = 0;
    }
    this.child = null;
    this.connected = false;
    // The session this process owned is about to be replaced or re-established;
    // until the next handshake records a pair, nothing names its route.
    this.appliedSessionId = null;
    this.modelWorking = false;
    this.writeBlocked = null;
    this.generation += 1;
    this.turn = null;
    this.toolStarts.clear();
    this.inflight.clear();
    this._rejectPendingRpc(error, child);
    this._rejectPendingRpc(error);
    if (!this.stopping && !this.restarting) terminateProcessTree(child, 'SIGKILL');
    // Acknowledged input is terminal here: reported (once per conversation, so
    // every chat that fed this turn is told) and never written again.
    const acknowledgedItems = turn?.items ?? [];
    if (acknowledgedItems.length > 0) {
      this._failItems(acknowledgedItems, new Error('dsh connection lost'), { endReason: 'connection-lost' });
    }
    for (const delivery of [...this.deliveries.values()]) {
      if (delivery.turnGen !== null && turn && delivery.turnGen === turn.gen) {
        this._closeDelivery(delivery, 'failed');
      }
    }
    this.deliveredThroughSeq = this.syncedThroughSeq;
    if (this.stopping || this.restarting || !this.startedOk || !this.autoReconnect) {
      this._resolveIdleWaiters();
      return;
    }
    this.logger.warn('dsh_disconnected', { code, signal, error: safe(error), unacknowledged: hadUnacked });
    void this._reconnectLoop();
  },

  async _reconnectLoop() {
    if (this.reconnecting || this.stopping) return;
    this.reconnecting = true;
    let attempts = 0;
    let queueFailures = 0;
    try {
      while (!this.stopping && !this.connected) {
        if (this.reconnectMaxAttempts > 0 && attempts >= this.reconnectMaxAttempts) {
          this.logger.warn('dsh_reconnect_exhausted', {
            attempts, backoffMs: this.backoffMs, autoReconnect: false,
          });
          this.autoReconnect = false;
          if (this.queue.length > 0) {
            this._failItems(this.queue.splice(0).flat(),
              new Error('dsh reconnect attempts exhausted'));
          }
          break;
        }
        await this._delay(this.backoffMs);
        if (this.stopping) break;
        try {
          await this._handshake();
          this.backoffMs = this.reconnectBaseMs;
          break;
        } catch (error) {
          attempts += 1;
          queueFailures += 1;
          this.logger.warn('dsh_reconnect_failed', {
            error: safe(error), attempts, queueFailures, backoffMs: this.backoffMs,
          });
          if (queueFailures >= RECONNECT_QUEUE_FAILURES && this.queue.length > 0) {
            this._failItems(this.queue.splice(0).flat(), error);
            queueFailures = 0;
          }
          this.backoffMs = Math.min(this.backoffMs * 2, this.reconnectCapMs);
        }
      }
    } finally {
      this.reconnecting = false;
      this._resolveIdleWaiters();
      this._drain();
      this._notifyAvailable();
    }
  },

  async _terminateCurrent() {
    const child = this.child;
    if (!child) {
      this.connected = false;
      return;
    }
    const exited = () => this._exitedChildren.has(child)
      || child.exitCode !== null || child.signalCode !== null;
    const waitForExit = () => exited()
      ? Promise.resolve(true)
      : new Promise((resolve) => child.once('exit', () => resolve(true)));
    if (this.connected && child.stdin?.writable && !exited()) {
      // DSH writes the reply before disposal and exit. An exit without the reply
      // is still a successful transport shutdown, not a reason to resurrect it.
      await this._requestOn(child, 'shutdown', undefined, this.shutdownTimeoutMs).catch(() => {});
    }
    if (!exited()) await Promise.race([waitForExit(), this._delay(this.processExitTimeoutMs)]);
    if (!exited()) {
      terminateProcessTree(child, 'SIGTERM');
      await Promise.race([waitForExit(), this._delay(this.processExitTimeoutMs)]);
    }
    if (!exited()) {
      terminateProcessTree(child, 'SIGKILL');
      await Promise.race([waitForExit(), this._delay(Math.min(this.processExitTimeoutMs, 1_000))]);
    }
    if (this.child === child) {
      this.child = null;
      this.connected = false;
    }
    this._rejectPendingRpc(new Error('dsh process replaced'), child);
  },

  async stop() {
    this.stopping = true;
    this.autoReconnect = false;
    // A handshake that is already in flight is waited for, not raced: it may be
    // about to assign `this.child`, and terminating before that leaves the child
    // unowned. It now sees `stopping` and fails instead of publishing a session.
    // Captured once: a second handshake cannot start while `stopping` is set.
    // Bounded, because an initialize that is already waiting out its own timer
    // must not hold a shutdown past it.
    const starting = this.starting;
    if (starting) {
      await Promise.race([
        starting.catch(() => {}),
        this._delay(this.startTimeoutMs + this.processExitTimeoutMs),
      ]);
    }
    while (this.settingsUpdate) await this.settingsUpdate;
    const queued = this.queue.splice(0).flat();
    if (queued.length > 0) this._failItems(queued, new Error('dsh stopped before queued work started'));
    const turn = this.turn;
    const open = [...this.deliveries.values()]
      .filter((delivery) => !turn || delivery.turnGen === turn.gen);
    const pendingItems = open
      .filter((delivery) => delivery.state === 'pending')
      .flatMap((delivery) => delivery.items);
    const items = [...(turn?.items ?? []), ...pendingItems];
    for (const delivery of open) this._closeDelivery(delivery, 'stopped');
    this.generation += 1;
    this.turn = null;
    this.inflight.clear();
    this.writeBlocked = null;
    this.modelWorking = false;
    this.toolStarts.clear();
    if (items.length > 0) this._failItems(items, new Error('dsh stopped during an active turn'));
    this._resolveIdleWaiters();
    await this._terminateCurrent();
    this.startedOk = false;
  }
};
