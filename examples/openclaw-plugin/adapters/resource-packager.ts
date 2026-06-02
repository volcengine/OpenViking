import { randomUUID } from "node:crypto";
import { once } from "node:events";
import { createWriteStream } from "node:fs";
import { mkdtemp, readdir, readFile, rm, stat } from "node:fs/promises";
import { tmpdir } from "node:os";
import { basename, dirname, join, relative } from "node:path";

import { Zip, ZipDeflate } from "fflate";

const REMOTE_RESOURCE_PREFIXES = ["http://", "https://", "git@", "ssh://", "git://"];

export type PackagedResourceSource =
  | { kind: "remote"; path: string }
  | { kind: "upload"; uploadPath: string; sourceName?: string; cleanupPath?: string };

export type ResourcePackager = {
  prepareResourceSource(pathOrUrl: string): Promise<PackagedResourceSource>;
  prepareLocalUploadSource(path: string): Promise<PackagedResourceSource>;
  createTempUploadBody(uploadPath: string): Promise<BodyInit>;
  cleanup(source?: PackagedResourceSource): Promise<void>;
};

function toBlobPart(value: Buffer): ArrayBuffer {
  return value.buffer.slice(value.byteOffset, value.byteOffset + value.byteLength) as ArrayBuffer;
}

export function isRemoteResourceSource(source: string): boolean {
  return REMOTE_RESOURCE_PREFIXES.some((prefix) => source.startsWith(prefix));
}

export async function cleanupUploadTempPath(path?: string): Promise<void> {
  if (!path) {
    return;
  }
  await rm(path, { force: true }).catch(() => undefined);
  await rm(dirname(path), { recursive: true, force: true }).catch(() => undefined);
}

export async function zipDirectoryForUpload(dirPath: string): Promise<string> {
  const rootStats = await stat(dirPath);
  if (!rootStats.isDirectory()) {
    throw new Error(`Not a directory: ${dirPath}`);
  }

  const zipDir = await mkdtemp(join(tmpdir(), "openviking-openclaw-upload-"));
  const zipPath = join(zipDir, `${basename(dirPath).replace(/[^a-zA-Z0-9._-]/g, "_")}-${randomUUID()}.zip`);
  const output = createWriteStream(zipPath);
  const outputClosed = once(output, "close");
  const outputErrored = once(output, "error").then(([err]) => Promise.reject(err));
  const zip = new Zip((err, chunk, final) => {
    if (err) {
      output.destroy(err);
      return;
    }
    if (chunk?.length) {
      output.write(Buffer.from(chunk));
    }
    if (final) {
      output.end();
    }
  });

  const walk = async (currentDir: string) => {
    const entries = await readdir(currentDir, { withFileTypes: true });
    for (const entry of entries) {
      const fullPath = join(currentDir, entry.name);
      if (entry.isDirectory()) {
        await walk(fullPath);
        continue;
      }
      if (!entry.isFile()) {
        continue;
      }
      const relPath = relative(dirPath, fullPath).split("\\").join("/");
      if (!relPath || relPath.startsWith("../") || relPath.includes("/../")) {
        throw new Error(`Unsafe relative path while zipping: ${relPath}`);
      }
      const file = new ZipDeflate(relPath);
      zip.add(file);
      file.push(new Uint8Array(await readFile(fullPath)), true);
    }
  };
  try {
    await walk(dirPath);
    zip.end();
    await Promise.race([outputClosed, outputErrored]);
  } catch (err) {
    zip.terminate();
    output.destroy(err as Error);
    await cleanupUploadTempPath(zipPath);
    throw err;
  }
  return zipPath;
}

async function prepareLocalUploadSource(path: string, includeSourceName = false): Promise<PackagedResourceSource> {
  const localStats = await stat(path);
  if (localStats.isDirectory()) {
    const uploadPath = await zipDirectoryForUpload(path);
    return {
      kind: "upload",
      uploadPath,
      cleanupPath: uploadPath,
      ...(includeSourceName ? { sourceName: basename(path) } : {}),
    };
  }
  if (!localStats.isFile()) {
    throw new Error(`Path is not a file or directory: ${path}`);
  }
  return { kind: "upload", uploadPath: path };
}

export const defaultResourcePackager: ResourcePackager = {
  async prepareResourceSource(pathOrUrl: string): Promise<PackagedResourceSource> {
    if (isRemoteResourceSource(pathOrUrl)) {
      return { kind: "remote", path: pathOrUrl };
    }
    return prepareLocalUploadSource(pathOrUrl, true);
  },

  async prepareLocalUploadSource(path: string): Promise<PackagedResourceSource> {
    return prepareLocalUploadSource(path, false);
  },

  async createTempUploadBody(uploadPath: string): Promise<BodyInit> {
    const fileBytes = await readFile(uploadPath);
    const form = new FormData();
    form.append(
      "file",
      new Blob([toBlobPart(fileBytes)], { type: "application/octet-stream" }),
      basename(uploadPath),
    );
    return form;
  },

  async cleanup(source?: PackagedResourceSource): Promise<void> {
    if (source?.kind === "upload") {
      await cleanupUploadTempPath(source.cleanupPath);
    }
  },
};
