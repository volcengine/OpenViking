import { fileURLToPath } from "node:url";
import * as skillFilesystem from "@deepseek-ai/dsh-skill-filesystem";

/** Provider name on `ctx.skills`; must not collide with DSH's own `filesystem`. */
export const SKILL_PROVIDER_NAME = "openviking";

/** The shared `openviking-memory` skill, vendored from examples/skills. */
export const SKILLS_DIR = fileURLToPath(new URL("./skills", import.meta.url));

export function buildSkillsConfig() {
  return {
    providerName: SKILL_PROVIDER_NAME,
    // Isolated: this provider serves only the bundle's own skill, so it never
    // shadows or duplicates what DSH's default filesystem provider discovers
    // under the project and user skill roots.
    includeDefaultRoots: false,
    customSkillDirs: [SKILLS_DIR],
  };
}

export function mountOpenVikingSkills(ctx) {
  return ctx.plugin(skillFilesystem, buildSkillsConfig());
}
