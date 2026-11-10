/** Preserve unmanaged skills and edits made after an installer-owned copy. */
import fs from "node:fs";
import { createHash, randomUUID } from "node:crypto";
import { dirname, join } from "node:path";
import { pathToFileURL } from "node:url";

function stat(path) {
  try { return fs.lstatSync(path); } catch (error) {
    if (error.code === "ENOENT") return null;
    throw error;
  }
}

function fingerprint(root) {
  const hash = createHash("sha256");
  function visit(path, relative) {
    const entry = fs.lstatSync(path);
    hash.update(JSON.stringify([relative, entry.mode]));
    if (entry.isSymbolicLink()) hash.update(fs.readlinkSync(path));
    else if (entry.isDirectory()) {
      for (const name of fs.readdirSync(path).sort()) visit(join(path, name), `${relative}/${name}`);
    } else if (entry.isFile()) hash.update(fs.readFileSync(path));
    else throw new Error(`Unsupported skill entry: ${path}`);
  }
  visit(root, "");
  return hash.digest("hex");
}

export function manageSkill(action, source, destination, statePath) {
  fs.mkdirSync(dirname(statePath), { recursive: true });
  const lock = `${statePath}.lock`;
  try { fs.mkdirSync(lock); } catch (error) {
    if (error.code !== "EEXIST") throw error;
    console.warn(`Preserved skill while another installation is active: ${destination}`);
    return false;
  }
  try {
    const current = stat(destination);
    let ownership;
    try { ownership = JSON.parse(fs.readFileSync(statePath, "utf8")); } catch (error) {
      if (error.code !== "ENOENT" && !(error instanceof SyntaxError)) throw error;
    }
    const owned = current?.isDirectory() && !current.isSymbolicLink()
      && ownership?.destination === destination
      && ownership?.dev === current.dev && ownership?.ino === current.ino
      && ownership?.digest === fingerprint(destination);
    if (current && !owned) {
      console.warn(`Preserved unmanaged or modified skill: ${destination}`);
      return false;
    }
    if (action === "uninstall") {
      if (owned) fs.rmSync(destination, { recursive: true });
      fs.rmSync(statePath, { force: true });
      return true;
    }
    if (action !== "install") throw new Error(`Unknown skill action: ${action}`);
    fs.mkdirSync(dirname(destination), { recursive: true });
    const staging = `${destination}.install-${randomUUID()}`;
    const backup = `${destination}.backup-${randomUUID()}`;
    const stateTmp = `${statePath}.${randomUUID()}.tmp`;
    let backedUp = false;
    let published = false;
    try {
      fs.cpSync(source, staging, { recursive: true, dereference: false });
      if (current) { fs.renameSync(destination, backup); backedUp = true; }
      fs.renameSync(staging, destination);
      published = true;
      const installed = fs.lstatSync(destination);
      fs.writeFileSync(stateTmp, JSON.stringify({
        destination, dev: installed.dev, ino: installed.ino, digest: fingerprint(destination),
      }), { mode: 0o600 });
      fs.renameSync(stateTmp, statePath);
    } catch (error) {
      if (published) fs.rmSync(destination, { recursive: true, force: true });
      if (backedUp) fs.renameSync(backup, destination);
      throw error;
    } finally {
      fs.rmSync(staging, { recursive: true, force: true });
      fs.rmSync(stateTmp, { force: true });
    }
    if (backedUp) fs.rmSync(backup, { recursive: true });
    return true;
  } finally {
    fs.rmdirSync(lock);
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  manageSkill(...process.argv.slice(2));
}
