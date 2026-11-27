// TRAE CN is TRAE under its own client id.
const DIRECTORY = { "trae-cn": "trae" };

/**
 * The host module for a client id, or null when this installation has none.
 * An installation carries only its own host's directory, so each is loaded on
 * demand and not from a list.
 */
export async function loadHost(id) {
  if (!/^[a-z][a-z0-9-]*$/.test(String(id || ""))) return null;
  let module;
  try {
    module = await import(`./${DIRECTORY[id] || id}/host.mjs`);
  } catch (error) {
    if (error?.code === "ERR_MODULE_NOT_FOUND") return null;
    throw error;
  }
  return module.hosts?.[id] || module.default;
}
