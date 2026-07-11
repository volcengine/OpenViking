import { zipSync } from "fflate";
import { OpenVikingError } from "./errors.js";

const bytesToDataURI = (bytes: Uint8Array, mimeType: string): string => {
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return `data:${mimeType};base64,${btoa(binary)}`;
};

const imageMimeType = (path: string): string => {
  const extension = path.toLowerCase().match(/\.[^.\\/]+$/)?.[0];
  const mimeTypes: Record<string, string> = {
    ".gif": "image/gif",
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
  };
  return (extension && mimeTypes[extension]) || "image/png";
};

/** Convert an existing Node.js image path to a request-safe data URI. */
export async function nodeImagePathToDataURI(
  path: string,
): Promise<string | undefined> {
  const fsSpecifier = "node:fs/promises";
  const fs = await import(fsSpecifier);
  try {
    const stat = await fs.stat(path);
    if (!stat.isFile()) return undefined;
    return bytesToDataURI(await fs.readFile(path), imageMimeType(path));
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return undefined;
    throw error;
  }
}

/** Read a Node.js file or zip a directory for temporary upload. */
export async function nodePathToBlob(
  path: string,
  allowDirectory = true,
): Promise<{ blob: Blob; filename: string; sourceName?: string } | undefined> {
  // Keep built-in specifiers dynamic so bundled ESM and CommonJS outputs share
  // this implementation without eager filesystem initialization.
  const fsSpecifier = "node:fs/promises";
  const pathSpecifier = "node:path";
  const fs = await import(fsSpecifier);
  const nodePath = await import(pathSpecifier);
  let stat;
  try {
    stat = await fs.stat(path);
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return undefined;
    throw error;
  }
  if (stat.isFile())
    return {
      blob: new Blob([await fs.readFile(path)]),
      filename: nodePath.basename(path),
      sourceName: nodePath.basename(path),
    };
  if (!stat.isDirectory()) return undefined;
  if (!allowDirectory)
    throw new TypeError(
      `OpenViking: ${path} is a directory, expected an OVPack file`,
    );
  const files: Record<string, Uint8Array> = {};
  const walk = async (directory: string, prefix = ""): Promise<void> => {
    for (const entry of await fs.readdir(directory, { withFileTypes: true })) {
      if (entry.isSymbolicLink()) continue;
      const fullPath = nodePath.join(directory, entry.name);
      const archivePath = prefix ? `${prefix}/${entry.name}` : entry.name;
      if (entry.isDirectory()) await walk(fullPath, archivePath);
      else if (entry.isFile()) files[archivePath] = await fs.readFile(fullPath);
    }
  };
  await walk(path);
  return {
    blob: new Blob([zipSync(files)]),
    filename: `${nodePath.basename(path)}.zip`,
    sourceName: nodePath.basename(path),
  };
}

/** Resolve the Python/Go-compatible local destination for an OVPack. */
export async function packOutputPath(
  to: string,
  uri: string | undefined,
  fallback: string,
): Promise<string> {
  const fsSpecifier = "node:fs/promises";
  const pathSpecifier = "node:path";
  const fs = await import(fsSpecifier);
  const nodePath = await import(pathSpecifier);
  let output = to || ".";
  try {
    if ((await fs.stat(output)).isDirectory()) {
      const name = uri
        ? nodePath.basename(uri.trim().replace(/\/+$/, "")) || fallback
        : fallback;
      output = nodePath.join(output, `${name}.ovpack`);
    }
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
  }
  if (!output.endsWith(".ovpack")) output += ".ovpack";
  await fs.mkdir(nodePath.dirname(output), { recursive: true });
  return output;
}

/** Stream a web response body into a Node.js local file. */
export async function writeResponseToFile(
  response: Response,
  output: string,
): Promise<string> {
  if (!response.body)
    throw new OpenVikingError("Download response did not include a body", {
      code: "INTERNAL",
    });
  const fsSpecifier = "node:fs/promises";
  const fs = await import(fsSpecifier);
  const file = await fs.open(output, "w");
  try {
    for await (const chunk of response.body as unknown as AsyncIterable<Uint8Array>)
      await file.write(chunk);
  } finally {
    await file.close();
  }
  return output;
}
