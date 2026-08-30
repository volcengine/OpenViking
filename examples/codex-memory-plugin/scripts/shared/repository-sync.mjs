// GENERATED FROM examples/memory-plugin-shared/lib. DO NOT EDIT.
import { createHash, randomUUID } from "node:crypto";
import { execFile } from "node:child_process";
import {
  chmod,
  lstat,
  mkdir,
  mkdtemp,
  readFile,
  readdir,
  rename,
  rm,
  stat,
  writeFile,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import { basename, dirname, join, posix } from "node:path";
import { promisify } from "node:util";

import { resolveOpenVikingCredentials } from "./credentials.mjs";

const execFileAsync = promisify(execFile);
const MUTATING_GIT_COMMANDS = new Set([
  "checkout",
  "commit",
  "merge",
  "pull",
  "rebase",
  "reset",
  "revert",
  "switch",
]);
const STATE_DIR_MODE = 0o700;
const STATE_FILE_MODE = 0o600;
const LOCAL_REPOSITORY_KEY_CONFIG = "openviking.repositoryKey";
const WIKI_DIR = ".repo_memory";
const WIKI_PROFILE_SCHEMA = "repo_memory_profile.v0.2";
const WIKI_PAGE_SCHEMA = "repo_memory_wiki_page.v0.1";
const WIKI_RESOURCE_FILES = new Set(["commits.md", "prs.md", "issues.md"]);
const WIKI_IGNORED_DIRS = new Set(["raw", "procedure-memory", "user-profile", "__pycache__"]);
const WIKI_IGNORED_FILES = new Set([".DS_Store"]);

function hash(value) {
  return createHash("sha256").update(String(value || "")).digest("hex");
}

function safePart(value, fallback = "unknown") {
  const normalized = String(value || "")
    .trim()
    .replace(/[^A-Za-z0-9._-]/g, "-")
    .replace(/-+/g, "-")
    .replace(/^-|-$/g, "");
  return (normalized || fallback).slice(0, 96);
}

function branchTargetSegment(branch) {
  const raw = String(branch || "").trim();
  const normalized = safePart(raw);
  if (normalized === raw && raw.length <= 96) return normalized;
  return `${normalized.slice(0, 87)}-${hash(raw).slice(0, 8)}`;
}

function shellWords(command) {
  return String(command || "").trim().split(/\s+/u);
}

function nestedInput(input = {}) {
  return input.tool_input ?? input.toolInput ?? input.input ?? input.arguments ?? input.payload ?? {};
}

function commandText(input = {}) {
  const toolInput = nestedInput(input);
  let raw = typeof toolInput === "string"
    ? toolInput
    : (toolInput.command ?? toolInput.cmd ?? toolInput.script ?? input.command ?? input.cmd ?? "");
  if (Array.isArray(raw) && raw.length >= 3 && /(?:^|\/)bash$/u.test(String(raw[0])) && raw[1] === "-lc") {
    raw = raw[2];
  }
  return Array.isArray(raw) ? raw.join(" ") : String(raw || "");
}

function toolName(input = {}) {
  const nested = nestedInput(input);
  return String(
    input.tool_name
      ?? input.toolName
      ?? input.name
      ?? input.tool?.name
      ?? input.tool
      ?? nested.tool_name
      ?? nested.toolName
      ?? nested.name
      ?? "",
  );
}

function toolResponse(input = {}) {
  const nested = nestedInput(input);
  return input.tool_response
    ?? input.toolResponse
    ?? input.response
    ?? input.tool_result
    ?? nested.tool_response
    ?? nested.toolResponse
    ?? nested.response
    ?? {
      exit_code: input.exit_code ?? nested.exit_code,
      exitCode: input.exitCode ?? nested.exitCode,
      status: input.status ?? nested.status,
      success: input.success ?? nested.success,
    };
}

export function isSuccessfulGitMutation(input = {}) {
  if (!/^(Bash|RunCommand|Shell|exec_command|codex_exec)$/u.test(toolName(input))) return false;

  const response = toolResponse(input);
  if (response && typeof response === "object") {
    const code = response.exit_code ?? response.exitCode ?? response.code;
    if (Number.isFinite(Number(code)) && Number(code) !== 0) return false;
    const status = String(response.status ?? "").toLowerCase();
    if (["error", "failed", "failure"].includes(status)) return false;
    if (response.success === false) return false;
  }

  const command = commandText(input);
  if (!command) return false;
  const invocations = command.match(/(?:^|(?:&&|;|\|\|)\s*)git\s+([A-Za-z-]+)/gu) || [];
  return invocations.some((entry) => {
    const words = shellWords(entry.replace(/^(?:&&|;|\|\|)\s*/u, ""));
    return words[0] === "git" && MUTATING_GIT_COMMANDS.has(words[1]);
  });
}

function hookCwd(input = {}) {
  const nested = nestedInput(input);
  return String(input.cwd ?? nested.cwd ?? process.cwd());
}

async function runGit(cwd, args, timeout = 15_000) {
  const { stdout } = await execFileAsync("git", ["-C", cwd, ...args], {
    encoding: "utf8",
    timeout,
    maxBuffer: 1024 * 1024,
  });
  return stdout.trim();
}

async function getLocalRepositoryKey(root) {
  try {
    const existing = await runGit(root, ["config", "--local", "--get", LOCAL_REPOSITORY_KEY_CONFIG]);
    if (existing) return `local:${existing}`;
  } catch {
    // A new repository has no OpenViking identity yet.
  }

  const value = randomUUID();
  try {
    await runGit(root, ["config", "--local", LOCAL_REPOSITORY_KEY_CONFIG, value]);
    return `local:${value}`;
  } catch {
    // The hook must never fail a completed Git command. The deterministic
    // fallback remains machine-local and never leaves the client except hashed.
    return `local:${hash(root)}`;
  }
}

function stripRemoteCredentials(value) {
  const raw = String(value || "").trim();
  if (!raw) return "";
  try {
    const parsed = new URL(raw);
    parsed.username = "";
    parsed.password = "";
    return parsed.toString().replace(/\/$/u, "");
  } catch {
    return raw.replace(/^(https?:\/\/)[^/@]+@/u, "$1");
  }
}

export async function resolveRepositoryContext(cwd) {
  const root = await runGit(cwd, ["rev-parse", "--show-toplevel"]);
  const commit = await runGit(root, ["rev-parse", "HEAD"]);
  const tree = await runGit(root, ["rev-parse", "HEAD^{tree}"]);
  let branch = await runGit(root, ["branch", "--show-current"]);
  if (!branch) branch = `detached-${commit.slice(0, 12)}`;

  let origin = "";
  try {
    origin = await runGit(root, ["remote", "get-url", "origin"]);
  } catch {
    // This is the intended local-only repository case.
  }
  const repoKey = origin ? stripRemoteCredentials(origin) : await getLocalRepositoryKey(root);
  const repoId = hash(repoKey).slice(0, 24);
  return {
    root,
    commit: commit.toLowerCase(),
    tree: tree.toLowerCase(),
    branch,
    repoKey,
    repoId,
    repoName: basename(root),
    remoteOrigin: Boolean(origin),
    targetUri: `viking://resources/local-git/${repoId}/${branchTargetSegment(branch)}`,
  };
}

function statePath(context) {
  const root = process.env.OPENVIKING_REPOSITORY_SYNC_STATE_DIR
    || join(process.env.HOME || tmpdir(), ".openviking", "repository-sync", "state");
  return join(root, `${hash(`${context.repoKey}\n${context.branch}`)}.json`);
}

function lockPath(context) {
  return `${statePath(context)}.lock`;
}

async function withRepositoryLock(context, callback) {
  const lock = lockPath(context);
  await mkdir(dirname(lock), { recursive: true, mode: STATE_DIR_MODE });
  while (true) {
    try {
      await mkdir(lock, { mode: STATE_DIR_MODE });
      break;
    } catch (error) {
      if (error?.code !== "EEXIST") throw error;
      try {
        if (Date.now() - (await stat(lock)).mtimeMs > 10 * 60_000) {
          await rm(lock, { recursive: true, force: true });
          continue;
        }
      } catch {
        continue;
      }
      return { status: "skipped", reason: "already-running", context };
    }
  }
  try {
    return await callback();
  } finally {
    await rm(lock, { recursive: true, force: true });
  }
}

async function readState(context) {
  try {
    return JSON.parse(await readFile(statePath(context), "utf8"));
  } catch {
    return {};
  }
}

async function writeState(context, value) {
  const file = statePath(context);
  await mkdir(dirname(file), { recursive: true, mode: STATE_DIR_MODE });
  const temporary = `${file}.${process.pid}.tmp`;
  await writeFile(temporary, `${JSON.stringify(value, null, 2)}\n`, {
    encoding: "utf8",
    mode: STATE_FILE_MODE,
  });
  await rename(temporary, file);
}

export async function createRepositoryArchive(context) {
  const directory = await mkdtemp(join(tmpdir(), "openviking-git-local-"));
  await chmod(directory, STATE_DIR_MODE);
  const archive = join(directory, `${safePart(context.repoName, "repository")}.zip`);
  await execFileAsync(
    "git",
    [
      "-C", context.root, "archive", "--format=zip", `--output=${archive}`, "HEAD",
      "--", ".", `:(exclude)${WIKI_DIR}`, `:(exclude)${WIKI_DIR}/**`,
    ],
    { timeout: 120_000, maxBuffer: 1024 * 1024 },
  );
  await chmod(archive, STATE_FILE_MODE);
  return { archive, cleanup: () => rm(directory, { recursive: true, force: true }) };
}

function wikiUploadEnabled(options = {}) {
  if (typeof options.wikiUploadEnabled === "boolean") return options.wikiUploadEnabled;
  const value = String(process.env.OPENVIKING_REPO_WIKI_UPLOAD_ENABLED || "").trim().toLowerCase();
  return ["1", "true", "yes", "on"].includes(value);
}

function metadataValue(text, name) {
  const prefix = `${name}:`;
  const line = String(text || "").split(/\r?\n/u).find((candidate) => candidate.startsWith(prefix));
  if (!line) return "";
  return line.slice(prefix.length).trim().replace(/^["']|["']$/gu, "").trim();
}

function validUserId(value) {
  const raw = String(value || "").trim();
  return raw && raw !== "." && raw !== ".." && /^[A-Za-z0-9._-]+$/u.test(raw) ? raw : "";
}

export async function resolveEffectiveUserId(credentials, fetchImpl = globalThis.fetch) {
  const configured = validUserId(credentials?.user);
  if (configured) return configured;
  for (const endpoint of ["/health", "/api/v1/system/status"]) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 3000);
    try {
      const response = await fetchImpl(`${credentials.baseUrl}${endpoint}`, {
        headers: authHeaders(credentials),
        signal: controller.signal,
      });
      if (!response.ok) continue;
      const body = await response.json().catch(() => ({}));
      const candidate = validUserId(body.user_id || body.result?.user);
      if (candidate) return candidate;
    } catch {
      // A missing identity skips only Wiki publication.
    } finally {
      clearTimeout(timer);
    }
  }
  return "";
}

async function publicationFiles(wikiRoot) {
  const profile = join(wikiRoot, "PROFILE.md");
  const profileStat = await lstat(profile).catch(() => null);
  if (!profileStat?.isFile() || profileStat.isSymbolicLink()) {
    throw new Error("Wiki PROFILE.md is missing or unsafe.");
  }
  const files = ["PROFILE.md"];
  for (const entry of await readdir(wikiRoot, { withFileTypes: true })) {
    if (entry.name === "PROFILE.md" || entry.name === "_plan.md") continue;
    if (entry.isSymbolicLink()) throw new Error(`Wiki contains an unsafe symlink: ${entry.name}`);
    if (WIKI_IGNORED_FILES.has(entry.name) || /\.(?:lock|log|tmp)$/u.test(entry.name)) continue;
    if (entry.isDirectory() && (entry.name === "resources" || WIKI_IGNORED_DIRS.has(entry.name))) continue;
    if (entry.isFile() && entry.name.endsWith(".md")) {
      files.push(entry.name);
      continue;
    }
    throw new Error(`Wiki contains an unsupported root entry: ${entry.name}`);
  }
  const resources = join(wikiRoot, "resources");
  const resourcesStat = await lstat(resources).catch(() => null);
  if (resourcesStat?.isSymbolicLink()) throw new Error("Wiki resources directory is a symlink.");
  if (resourcesStat?.isDirectory()) {
    for (const entry of await readdir(resources, { withFileTypes: true })) {
      if (entry.isSymbolicLink()) throw new Error(`Wiki contains an unsafe symlink: resources/${entry.name}`);
      if (entry.isFile() && WIKI_RESOURCE_FILES.has(entry.name)) {
        files.push(posix.join("resources", entry.name));
        continue;
      }
      throw new Error(`Wiki contains an unsupported resource entry: resources/${entry.name}`);
    }
  }
  return files.sort();
}

function validateWikiLinks(files, contents) {
  const available = new Set(files);
  for (const file of files) {
    for (const match of contents.get(file).matchAll(/\[[^\]]*\]\(([^)]+)\)/gu)) {
      const target = match[1].trim().split("#", 1)[0];
      if (!target || /^(?:[a-z]+:|#)/iu.test(target)) continue;
      const resolved = posix.normalize(posix.join(posix.dirname(file), target.replace(/^\.\//u, "")));
      if (resolved.startsWith("../") || !available.has(resolved)) {
        throw new Error(`Wiki link escapes or is missing: ${file} -> ${target}`);
      }
    }
  }
}

function sanitizeWikiContent(content, repositoryRoot) {
  const roots = new Set([String(repositoryRoot || "")]);
  if (repositoryRoot.startsWith("/private/")) roots.add(repositoryRoot.slice(8));
  else if (repositoryRoot.startsWith("/var/")) roots.add(`/private${repositoryRoot}`);
  let sanitized = String(content || "");
  for (const root of [...roots].filter(Boolean).sort((a, b) => b.length - a.length)) {
    sanitized = sanitized.split(root).join(".");
  }
  return sanitized;
}

export async function prepareRepositoryUploadInputs(context, credentials, options = {}) {
  if (!wikiUploadEnabled(options)) return { code: context, wiki: { status: "disabled" } };
  const wikiRoot = join(context.root, WIKI_DIR);
  const profile = await readFile(join(wikiRoot, "PROFILE.md"), "utf8").catch(() => "");
  if (!profile) return { code: context, wiki: { status: "absent", root: wikiRoot } };
  if (metadataValue(profile, "schema") !== WIKI_PROFILE_SCHEMA) {
    return { code: context, wiki: { status: "invalid", reason: "unsupported-profile-schema", root: wikiRoot } };
  }
  const sourceCommit = metadataValue(profile, "local_head").toLowerCase();
  const sourceTree = metadataValue(profile, "source_tree").toLowerCase();
  if (!/^[0-9a-f]{40}$/u.test(sourceCommit) || !/^[0-9a-f]{40}$/u.test(sourceTree)) {
    return { code: context, wiki: { status: "invalid", reason: "invalid-source-provenance", root: wikiRoot } };
  }
  const userId = await resolveEffectiveUserId(credentials, options.fetchImpl || globalThis.fetch);
  if (!userId) return { code: context, wiki: { status: "invalid", reason: "user-id-unavailable", root: wikiRoot } };
  try {
    const files = await publicationFiles(wikiRoot);
    const contents = new Map();
    const digest = createHash("sha256");
    for (const file of files) {
      let content = await readFile(join(wikiRoot, ...file.split("/")), "utf8");
      if (file !== "PROFILE.md" && !file.includes("/") && metadataValue(content, "schema") !== WIKI_PAGE_SCHEMA) {
        throw new Error(`Wiki page has unsupported schema: ${file}`);
      }
      content = sanitizeWikiContent(content, context.root);
      contents.set(file, content);
      digest.update(file).update("\0").update(content).update("\0");
    }
    validateWikiLinks(files, contents);
    return {
      code: context,
      wiki: {
        status: "ready",
        root: wikiRoot,
        targetUri: `viking://resources/wiki/${context.repoId}/${userId}`,
        repoId: context.repoId,
        userId,
        sourceCommit,
        sourceTree,
        sourceBranch: metadataValue(profile, "local_branch") || context.branch,
        triggerCommit: context.commit,
        triggerTree: context.tree,
        buildMode: metadataValue(profile, "build_mode") || "unknown",
        generatedAt: metadataValue(profile, "generated_at"),
        files,
        contents,
        contentHash: digest.digest("hex"),
      },
    };
  } catch (error) {
    return { code: context, wiki: { status: "invalid", reason: error?.message || String(error), root: wikiRoot, sourceCommit } };
  }
}

export async function createWikiArchive(context, wiki) {
  if (wiki?.status !== "ready") throw new Error("Wiki is not ready for publication.");
  const directory = await mkdtemp(join(tmpdir(), "openviking-repo-wiki-"));
  const staging = join(directory, "publication");
  const archive = join(directory, `${safePart(context.repoName, "repository")}-wiki.zip`);
  await mkdir(staging, { recursive: true, mode: STATE_DIR_MODE });
  try {
    for (const file of wiki.files) {
      const destination = join(staging, ...file.split("/"));
      await mkdir(dirname(destination), { recursive: true, mode: STATE_DIR_MODE });
      await writeFile(destination, wiki.contents.get(file), { encoding: "utf8", mode: STATE_FILE_MODE });
    }
    const manifest = {
      schema: "repo_wiki_publication.v1", repo_id: context.repoId, repo_name: context.repoName,
      repo_key: context.repoKey, wiki_owner: wiki.userId, source_commit: wiki.sourceCommit,
      source_tree: wiki.sourceTree, source_branch: wiki.sourceBranch, build_mode: wiki.buildMode,
      generated_at: wiki.generatedAt, files: wiki.files, content_hash: wiki.contentHash,
    };
    await writeFile(join(staging, "repo-wiki-manifest.json"), `${JSON.stringify(manifest, null, 2)}\n`, { encoding: "utf8", mode: STATE_FILE_MODE });
    await execFileAsync("python3", ["-m", "zipfile", "-c", archive, "."], { cwd: staging, timeout: 120_000, maxBuffer: 1024 * 1024 });
    await chmod(archive, STATE_FILE_MODE);
    return { archive, cleanup: () => rm(directory, { recursive: true, force: true }) };
  } catch (error) {
    await rm(directory, { recursive: true, force: true });
    throw error;
  }
}

function authHeaders(credentials, contentType = "") {
  const headers = {};
  if (contentType) headers["Content-Type"] = contentType;
  if (credentials.apiKey) headers.Authorization = `Bearer ${credentials.apiKey}`;
  if (credentials.account) headers["X-OpenViking-Account"] = credentials.account;
  if (credentials.user) headers["X-OpenViking-User"] = credentials.user;
  if (credentials.peerId) headers["X-OpenViking-Actor-Peer"] = credentials.peerId;
  if (credentials.userAgent) headers["User-Agent"] = credentials.userAgent;
  return headers;
}

async function responseResult(response) {
  const body = await response.json().catch(() => ({}));
  if (!response.ok || body.status === "error") {
    const message = body.error?.message || body.message || `HTTP ${response.status}`;
    throw new Error(message);
  }
  return body.result ?? body;
}

export async function uploadRepositorySnapshot(credentials, archive) {
  const form = new FormData();
  const { openAsBlob } = await import("node:fs");
  const content = typeof openAsBlob === "function"
    ? await openAsBlob(archive, { type: "application/zip" })
    : new Blob([await readFile(archive)], { type: "application/zip" });
  form.append("file", content, basename(archive));
  const response = await fetch(`${credentials.baseUrl}/api/v1/resources/temp_upload`, {
    method: "POST",
    headers: authHeaders(credentials),
    body: form,
  });
  const result = await responseResult(response);
  if (!result.temp_file_id) throw new Error("Temporary upload returned no temp_file_id.");
  return result.temp_file_id;
}

export async function submitRepositorySnapshot(credentials, tempFileId, context) {
  const response = await fetch(`${credentials.baseUrl}/api/v1/resources`, {
    method: "POST",
    headers: authHeaders(credentials, "application/json"),
    body: JSON.stringify({
      temp_file_id: tempFileId,
      to: context.targetUri,
      wait: false,
      args: {
        git_local: {
          version: 1,
          repo_key: context.repoKey,
          repo_name: context.repoName,
          branch: context.branch,
          commit: context.commit,
          archive_format: "zip",
        },
      },
    }),
  });
  return responseResult(response);
}

export async function submitWikiSnapshot(credentials, tempFileId, wiki) {
  const tags = [
    "content_kind=repo_wiki",
    `repo_id=${wiki.repoId}`,
    `wiki_owner=${wiki.userId}`,
    `source_commit=${wiki.sourceCommit}`,
    `build_mode=${wiki.buildMode}`,
    `content_hash=${wiki.contentHash}`,
  ];
  const response = await fetch(`${credentials.baseUrl}/api/v1/resources`, {
    method: "POST",
    headers: authHeaders(credentials, "application/json"),
    body: JSON.stringify({
      temp_file_id: tempFileId,
      to: wiki.targetUri,
      wait: false,
      processing_mode: "vectors_only",
      tags,
      tag_mode: "replace",
      args: { parse_mode: "no_split" },
    }),
  });
  return responseResult(response);
}

function normalizedSyncState(previous, context) {
  if (previous?.version === 2) return previous;
  return {
    version: 2,
    repoKey: context.repoKey,
    branch: context.branch,
    code: previous?.lastSubmittedCommit ? {
      lastSubmittedCommit: previous.lastSubmittedCommit,
      targetUri: previous.targetUri || context.targetUri,
      taskId: previous.taskId || "",
      status: "submitted",
    } : {},
    wiki: {},
    updatedAt: previous?.updatedAt || "",
  };
}

export async function syncRepositoryFromHook(input, options = {}) {
  if (!isSuccessfulGitMutation(input)) return { status: "skipped", reason: "not-git-mutation" };
  const context = await resolveRepositoryContext(hookCwd(input));

  return withRepositoryLock(context, async () => {
    const rawPrevious = await readState(context);
    const previous = normalizedSyncState(rawPrevious, context);
    const credentials = options.credentials || resolveOpenVikingCredentials();
    const inputs = await prepareRepositoryUploadInputs(context, credentials, options);
    const codeNeeded = !context.remoteOrigin && previous.code?.lastSubmittedCommit !== context.commit;
    const wikiNeeded = inputs.wiki.status === "ready"
      && (previous.wiki?.contentHash !== inputs.wiki.contentHash
        || previous.wiki?.status !== "submitted");

    if (!codeNeeded && !wikiNeeded) {
      const wikiReason = inputs.wiki.reason || inputs.wiki.status;
      if (rawPrevious?.version !== 2 || previous.wiki?.status !== inputs.wiki.status
          || previous.wiki?.reason !== wikiReason) {
        await writeState(context, {
          ...previous,
          wiki: { ...previous.wiki, status: inputs.wiki.status, reason: wikiReason },
          updatedAt: new Date().toISOString(),
        });
      }
      return {
        status: "skipped",
        reason: context.remoteOrigin && inputs.wiki.status === "disabled"
          ? "remote-backed"
          : (inputs.wiki.status === "disabled" ? "already-submitted" : "nothing-to-submit"),
        context,
        code: { status: "skipped", reason: context.remoteOrigin ? "remote-backed" : "already-submitted" },
        wiki: { status: "skipped", reason: wikiReason },
      };
    }

    const codePromise = codeNeeded ? (async () => {
      const bundle = await createRepositoryArchive(context);
      try {
        const tempFileId = await uploadRepositorySnapshot(credentials, bundle.archive);
        const result = await submitRepositorySnapshot(credentials, tempFileId, context);
        return { status: "submitted", result };
      } finally {
        await bundle.cleanup();
      }
    })() : Promise.resolve({ status: "skipped", reason: context.remoteOrigin ? "remote-backed" : "already-submitted" });

    const wikiPromise = wikiNeeded ? (async () => {
      const bundle = await createWikiArchive(context, inputs.wiki);
      try {
        const tempFileId = await uploadRepositorySnapshot(credentials, bundle.archive);
        const result = await submitWikiSnapshot(credentials, tempFileId, inputs.wiki);
        return { status: "submitted", result };
      } finally {
        await bundle.cleanup();
      }
    })() : Promise.resolve({ status: "skipped", reason: inputs.wiki.status });

    const [codeSettled, wikiSettled] = await Promise.allSettled([codePromise, wikiPromise]);
    const code = codeSettled.status === "fulfilled"
      ? codeSettled.value
      : { status: "failed", error: codeSettled.reason?.message || String(codeSettled.reason) };
    const wiki = wikiSettled.status === "fulfilled"
      ? wikiSettled.value
      : { status: "failed", error: wikiSettled.reason?.message || String(wikiSettled.reason) };

    const next = {
      ...previous,
      version: 2,
      repoKey: context.repoKey,
      branch: context.branch,
      code: code.status === "submitted" ? {
        lastSubmittedCommit: context.commit, targetUri: context.targetUri,
        taskId: code.result?.task_id || "", status: "submitted",
      } : code.status === "skipped" ? previous.code : {
        ...previous.code, status: code.status, ...(code.error ? { error: code.error } : {}),
      },
      wiki: wiki.status === "submitted" ? {
        contentHash: inputs.wiki.contentHash, targetUri: inputs.wiki.targetUri,
        taskId: wiki.result?.task_id || "", status: "submitted",
      } : wiki.status === "skipped" && inputs.wiki.status === "ready" ? previous.wiki : {
        ...previous.wiki,
        ...(inputs.wiki.contentHash ? { contentHash: inputs.wiki.contentHash } : {}),
        ...(inputs.wiki.targetUri ? { targetUri: inputs.wiki.targetUri } : {}),
        status: wiki.status === "skipped" ? inputs.wiki.status : wiki.status,
        ...((inputs.wiki.reason || (wiki.status === "skipped" ? inputs.wiki.status : ""))
          ? { reason: inputs.wiki.reason || inputs.wiki.status } : {}),
        ...(wiki.error ? { error: wiki.error } : {}),
      },
      updatedAt: new Date().toISOString(),
    };
    await writeState(context, next);

    const submitted = code.status === "submitted" || wiki.status === "submitted";
    const failed = code.status === "failed" || wiki.status === "failed";
    return {
      status: failed ? (submitted ? "partial" : "error") : (submitted ? "submitted" : "skipped"),
      context, code, wiki, result: code.result,
    };
  });
}
