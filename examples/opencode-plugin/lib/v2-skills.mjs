import { readFile, readdir } from "node:fs/promises"
import { join } from "node:path"
import { openVikingSkillsDir } from "./mcp-config.mjs"

// Load files before registering the synchronous, replayable skill transform.
export async function loadBundledV2Skills(pluginRoot) {
  const { parse: parseYaml } = await import("yaml")
  const directory = openVikingSkillsDir(pluginRoot)
  return Promise.all((await readdir(directory, { withFileTypes: true }))
    .filter((entry) => entry.isDirectory())
    .map(async (entry) => {
      const path = join(directory, entry.name, "SKILL.md")
      const text = await readFile(path, "utf8")
      const frontmatter = text.match(/^---\r?\n([\s\S]*?)\r?\n---\r?\n/)
      if (!frontmatter) throw new Error(`Missing skill metadata: ${path}`)
      const { name, description } = parseYaml(frontmatter[1]) ?? {}
      const content = text.slice(frontmatter[0].length).trimStart()
      if (typeof name !== "string" || !name.trim() ||
          typeof description !== "string" || !description.trim() || !content.trim()) {
        throw new Error(`Invalid bundled skill: ${path}`)
      }
      return { id: name, name, description, path, content }
    }))
}
