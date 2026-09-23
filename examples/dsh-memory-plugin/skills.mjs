import { fileURLToPath } from "node:url";
import * as skillFilesystem from "@deepseek-ai/dsh-skill-filesystem";

/** Provider name on `ctx.skills`; must not collide with DSH's own `filesystem`. */
export const SKILL_PROVIDER_NAME = "openviking";

/** The shared `openviking-memory` and `openviking-skills` skills, vendored from examples/skills. */
export const SKILLS_DIR = fileURLToPath(new URL("./skills", import.meta.url));

export function buildSkillsConfig() {
  return {
    providerName: SKILL_PROVIDER_NAME,
    includeDefaultRoots: false,
    bundledSkillDir: SKILLS_DIR,
    // Packaged skills change only on upgrade; watchers can block replacement on Windows.
    watch: false,
  };
}

export function mountOpenVikingSkills(ctx) {
  return ctx.plugin(skillFilesystem, buildSkillsConfig());
}
