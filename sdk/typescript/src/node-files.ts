import { zipSync } from "fflate";
import { OpenVikingError } from "./errors.js";

const NODE_FS_SPECIFIER = "node:fs/promises";
const NODE_PATH_SPECIFIER = "node:path";
const IMAGE_REFERENCE_PREFIXES = [
  "data:image/",
  "http://",
  "https://",
  "viking://",
] as const;

const nodeFs = () => import(NODE_FS_SPECIFIER);
const nodePath = () => import(NODE_PATH_SPECIFIER);

const statOrUndefined = async (path: string) => {
  try {
    return await (await nodeFs()).stat(path);
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return undefined;
    throw error;
  }
};

const bytesToDataURI = (bytes: Uint8Array, mimeType: string): string => {
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return `data:${mimeType};base64,${btoa(binary)}`;
};

const imageMimeType = (path: string): string => {
  const extension = path.toLowerCase().match(/\.[^.\\/]+$/)?.[0];
  const mimeTypes: Record<string, string> = {
    ".avif": "image/avif",
    ".bmp": "image/bmp",
    ".gif": "image/gif",
    ".ico": "image/x-icon",
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".webp": "image/webp",
  };
  return (extension && mimeTypes[extension]) || "image/png";
};

/** Convert an existing Node.js image path to a request-safe data URI. */
export async function nodeImagePathToDataURI(
  path: string,
): Promise<string | undefined> {
  if (IMAGE_REFERENCE_PREFIXES.some((prefix) => path.startsWith(prefix)))
    return undefined;
  const stat = await statOrUndefined(path);
  if (!stat?.isFile()) return undefined;
  return bytesToDataURI(
    await (await nodeFs()).readFile(path),
    imageMimeType(path),
  );
}

/** Read a Node.js file or zip a directory for temporary upload. */
export async function nodePathToBlob(
  path: string,
  options: { allowDirectory?: boolean } = {},
): Promise<{ blob: Blob; filename: string; sourceName?: string } | undefined> {
  // Keep built-in specifiers dynamic so bundled ESM and CommonJS outputs share
  // this implementation without eager filesystem initialization.
  const [fs, paths, stat] = await Promise.all([
    nodeFs(),
    nodePath(),
    statOrUndefined(path),
  ]);
  if (!stat) return undefined;
  if (stat.isFile())
    return {
      blob: new Blob([await fs.readFile(path)]),
      filename: paths.basename(path),
      sourceName: paths.basename(path),
    };
  if (!stat.isDirectory()) return undefined;
  if (options.allowDirectory === false)
    throw new TypeError(
      `OpenViking: ${path} is a directory, expected an OVPack file`,
    );
  const files: Record<string, Uint8Array> = {};
  const walk = async (directory: string, prefix = ""): Promise<void> => {
    for (const entry of await fs.readdir(directory, { withFileTypes: true })) {
      if (entry.isSymbolicLink()) continue;
      const fullPath = paths.join(directory, entry.name);
      const archivePath = prefix ? `${prefix}/${entry.name}` : entry.name;
      if (entry.isDirectory()) await walk(fullPath, archivePath);
      else if (entry.isFile()) files[archivePath] = await fs.readFile(fullPath);
    }
  };
  await walk(path);
  return {
    blob: new Blob([zipSync(files)]),
    filename: `${paths.basename(path)}.zip`,
    sourceName: paths.basename(path),
  };
}

/** Resolve the Python/Go-compatible local destination for an OVPack. */
export async function packOutputPath(
  to: string,
  uri: string | undefined,
  fallback: string,
): Promise<string> {
  const [fs, paths] = await Promise.all([nodeFs(), nodePath()]);
  let output = to || ".";
  if ((await statOrUndefined(output))?.isDirectory()) {
    const name = uri
      ? paths.basename(uri.trim().replace(/\/+$/, "")) || fallback
      : fallback;
    output = paths.join(output, `${name}.ovpack`);
  }
  if (!output.endsWith(".ovpack")) output += ".ovpack";
  await fs.mkdir(paths.dirname(output), { recursive: true });
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
  const [fs, paths] = await Promise.all([nodeFs(), nodePath()]);
  const temporaryDirectory = await fs.mkdtemp(
    paths.join(paths.dirname(output), `.${paths.basename(output)}-`),
  );
  const temporaryPath = paths.join(temporaryDirectory, "download");
  let file: Awaited<ReturnType<typeof fs.open>> | undefined;
  try {
    file = await fs.open(temporaryPath, "wx");
    for await (const chunk of response.body as unknown as AsyncIterable<Uint8Array>)
      await file.writeFile(chunk);
    await file.close();
    file = undefined;
    await fs.rename(temporaryPath, output);
    return output;
  } finally {
    await file?.close().catch(() => undefined);
    await fs
      .rm(temporaryDirectory, { recursive: true, force: true })
      .catch(() => undefined);
  }
}
