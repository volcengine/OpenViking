#!/usr/bin/env bash
# Write the Kimi Code plugin marketplace the host reads when
# KIMI_CODE_PLUGIN_MARKETPLACE_URL points at this tree.
#
# Kimi resolves a marketplace entry to a ZIP it downloads and verifies, so the
# entry carries the archive's sha256 and its semver. Both come from the file the
# caller already built; nothing here re-derives a version the manifest owns.

set -euo pipefail

USAGE="usage: generate-kimi-marketplace-json.sh <plugin-zip-url> <plugin-zip> <out>"
ZIP_URL="${1:?${USAGE}}"
PLUGIN_ZIP="${2:?${USAGE}}"
OUT="${3:?${USAGE}}"

sha256() { sha256sum "$1" | cut -d' ' -f1; }

# The marketplace entry is built from the manifest the ZIP ships, not from the
# source tree: a version or display name that differs between the two would show
# the user one plugin and install another.
TMP="$(mktemp -d "${TMPDIR:-/tmp}/kimi-marketplace.XXXXXX")"
trap 'rm -rf "${TMP}"' EXIT
unzip -q -o "${PLUGIN_ZIP}" kimi.plugin.json locales/* -d "${TMP}" 2>/dev/null \
  || unzip -q -o "${PLUGIN_ZIP}" kimi.plugin.json -d "${TMP}"
# A ZIP without locales/* (the fallback just above) still needs a readable file
# for the slurpfile below; an empty object degrades to "no localized strings".
mkdir -p "${TMP}/locales"
[ -f "${TMP}/locales/zh-CN.json" ] || echo '{}' > "${TMP}/locales/zh-CN.json"

jq -n \
  --arg url "${ZIP_URL}" \
  --arg sha256 "$(sha256 "${PLUGIN_ZIP}")" \
  --slurpfile manifest "${TMP}/kimi.plugin.json" \
  --slurpfile locale "${TMP}/locales/zh-CN.json" \
  '($manifest[0]) as $m
   | ($m.interface // {}) as $i
   | {
       version: "2",
       plugins: [
         {
           id: $m.name,
           type: "plugin",
           version: $m.version,
           displayName: $i.displayName,
           name: $i.displayName,
           description: $m.description,
           shortDescription: $i.shortDescription,
           longDescription: $i.longDescription,
           homepage: $m.homepage,
           keywords: ($m.keywords // []),
           source: $url,
           sha256sum: $sha256,
           iconUrl: $i.iconUrl,
           category: $i.category,
           platforms: ["darwin", "linux", "win32"],
           distribution: {
             category: "third_party",
             region: "global",
             defaultInstall: false
           },
           locale: {
             defaultLocale: ($i.defaultLocale // "en-US"),
             "zh-CN": ($locale[0] // {})
           }
         }
       ]
     }' > "${OUT}"

# The host rejects an entry whose digest is not 64 hex characters, and it will
# not install one without a digest at all, so a malformed output must fail here
# rather than in the user's terminal.
node -e '
const fs = require("node:fs");
const doc = JSON.parse(fs.readFileSync(process.argv[1], "utf8"));
if (!Array.isArray(doc.plugins) || doc.plugins.length !== 1) throw new Error("exactly one plugin entry is expected");
// The host reads the catalog-level `version` (stringField(parsed, 'version'));
// it ignores a `schema` key. The published example uses "2".
if (doc.version !== "2") throw new Error("catalog version must be \"2\"");
const entry = doc.plugins[0];
for (const field of ["id", "version", "source"]) {
  if (typeof entry[field] !== "string" || entry[field].length === 0) throw new Error(`entry.${field} is missing`);
}
if (!/^[A-Fa-f0-9]{64}$/.test(entry.sha256sum || "")) throw new Error("entry.sha256sum must be a SHA-256 digest");
if (!/^https?:\/\//.test(entry.source)) throw new Error("entry.source must be an HTTP(S) ZIP URL");
if (!["official", "third_party"].includes(entry.distribution?.category)) throw new Error("distribution.category must be official or third_party");
console.log(`kimi marketplace: ${entry.id} ${entry.version} (${entry.sha256sum.slice(0, 12)}…)`);
' "${OUT}"
