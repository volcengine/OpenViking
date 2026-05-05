import fs from "fs"
import path from "path"
import { fileURLToPath } from "url"
import {
  ensureRemoteUrl,
  makeMultipartRequest,
  makeRequest,
  unwrapResponse,
} from "./utils.mjs"

export const MEMADD_LOCAL_FILE_ONLY_ERROR = "Error: memadd local upload currently supports files only."

const ADD_RESOURCE_KEYS = [
  "to",
  "parent",
  "reason",
  "instruction",
  "wait",
  "timeout",
  "watch_interval",
]

export function resolveMemaddSource(inputPath, projectDirectory = process.cwd()) {
  if (ensureRemoteUrl(inputPath)) {
    return { kind: "remote", path: inputPath }
  }

  let filePath
  try {
    filePath = resolveLocalPath(inputPath, projectDirectory)
  } catch (error) {
    return { kind: "error", error: `Error: ${error.message}` }
  }

  let stat
  try {
    stat = fs.statSync(filePath)
  } catch (error) {
    if (error?.code === "ENOENT" || error?.code === "ENOTDIR") {
      return { kind: "error", error: `Error: Local file not found: ${filePath}` }
    }
    return { kind: "error", error: `Error: Unable to access local file: ${filePath}: ${error.message}` }
  }

  if (!stat.isFile()) {
    return { kind: "error", error: MEMADD_LOCAL_FILE_ONLY_ERROR }
  }

  return { kind: "local", path: filePath, filename: path.basename(filePath) }
}

export function resolveLocalPath(inputPath, projectDirectory = process.cwd()) {
  if (typeof inputPath !== "string" || inputPath.trim() === "") {
    throw new Error("memadd path is required.")
  }

  let localPath = inputPath
  if (isFileUrl(inputPath)) {
    localPath = fileURLToPath(inputPath)
  }

  if (path.isAbsolute(localPath)) return path.normalize(localPath)
  return path.resolve(projectDirectory || process.cwd(), localPath)
}

export function buildAddResourceBody(args, source, tempFileId) {
  const body = source.kind === "remote" ? { path: source.path } : { temp_file_id: tempFileId }
  for (const key of ADD_RESOURCE_KEYS) {
    if (args[key] !== undefined) body[key] = args[key]
  }
  return body
}

export async function uploadLocalResource(config, source, abortSignal) {
  const bytes = await fs.promises.readFile(source.path)
  const form = new FormData()
  form.append("file", new Blob([bytes], { type: "application/octet-stream" }), source.filename)

  const uploadResponse = await makeMultipartRequest(config, {
    method: "POST",
    endpoint: "/api/v1/resources/temp_upload",
    body: form,
    abortSignal,
  })
  const tempFileId = unwrapResponse(uploadResponse)?.temp_file_id
  if (!tempFileId) {
    throw new Error("OpenViking temp upload did not return temp_file_id")
  }
  return tempFileId
}

export async function addMemaddResource(config, args, projectDirectory, abortSignal) {
  const source = resolveMemaddSource(args.path, projectDirectory)
  if (source.kind === "error") return { error: source.error }

  const tempFileId = source.kind === "local" ? await uploadLocalResource(config, source, abortSignal) : undefined
  const body = buildAddResourceBody(args, source, tempFileId)
  const addResponse = await makeRequest(config, {
    method: "POST",
    endpoint: "/api/v1/resources",
    body,
    abortSignal,
    timeoutMs: args.wait ? Math.max(config.timeoutMs, (args.timeout ?? 300) * 1000) : config.timeoutMs,
  })
  return { addResponse, source }
}

function isFileUrl(value) {
  try {
    return new URL(value).protocol === "file:"
  } catch {
    return false
  }
}
