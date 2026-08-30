import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const canonicalSkill = join(ROOT, "examples", "skills", "repo-wiki");

function git(cwd, ...args) {
  return execFileSync("git", ["-C", cwd, ...args], { encoding: "utf8" }).trim();
}

function createRepo() {
  const root = mkdtempSync(join(tmpdir(), "openviking-repo-wiki-"));
  git(root, "init");
  git(root, "config", "user.email", "test@example.com");
  git(root, "config", "user.name", "OpenViking Test");
  writeFileSync(join(root, "README.md"), "# Test repository\n");
  git(root, "add", "README.md");
  git(root, "commit", "-m", "initial");
  return root;
}

function runPython(script, args) {
  return execFileSync("python3", [script, ...args], { encoding: "utf8" });
}

test("canonical repo-wiki Skill is complete and self-contained", () => {
  const required = [
    "SKILL.md",
    "defaults.json",
    "references/openviking-read.md",
    "references/repo-build.md",
    "references/repo-read.md",
    "references/repo-templates.md",
    "references/repo-update.md",
    "scripts/collect_all.py",
    "scripts/detect_updates.py",
    "scripts/prepare_repo_memory.py",
    "scripts/validate_memory.py",
  ];
  for (const file of required) {
    assert.ok(readFileSync(join(canonicalSkill, file), "utf8").length > 0, `${file} must not be empty`);
  }
  const skill = readFileSync(join(canonicalSkill, "SKILL.md"), "utf8");
  assert.match(skill, /OpenViking Resource subtree/u);
  assert.match(readFileSync(join(canonicalSkill, "references", "openviking-read.md"), "utf8"), /grep/u);
});

test("repo-wiki helper scripts collect local evidence and validate an authored bundle", () => {
  const root = createRepo();
  try {
    const collect = join(canonicalSkill, "scripts", "collect_all.py");
    const validate = join(canonicalSkill, "scripts", "validate_memory.py");
    const collected = JSON.parse(runPython(collect, ["--repo-path", root, "--commit-limit", "5", "--pretty"]));
    assert.equal(collected.ok, true);
    assert.equal(collected.effective_settings.history.mode, "local-only");
    assert.match(readFileSync(join(root, ".gitignore"), "utf8"), /\.repo_memory/u);

    const wiki = join(root, ".repo_memory");
    const head = git(root, "rev-parse", "HEAD");
    const tree = git(root, "rev-parse", "HEAD^{tree}");
    execFileSync("mkdir", ["-p", join(wiki, "resources")]);
    writeFileSync(join(wiki, "PROFILE.md"), [
      "---",
      'schema: "repo_memory_profile.v0.2"',
      `local_head: "${head}"`,
      `source_tree: "${tree}"`,
      "---",
      "# Test Wiki",
      "[Architecture](architecture.md)",
      "",
    ].join("\n"));
    writeFileSync(join(wiki, "architecture.md"), [
      "---",
      'schema: "repo_memory_wiki_page.v0.1"',
      "---",
      "# Architecture",
      "",
    ].join("\n"));
    const resourceSchemas = {
      "commits.md": "repo_memory_commit_resource.v0.1",
      "prs.md": "repo_memory_pr_resource.v0.1",
      "issues.md": "repo_memory_issue_resource.v0.1",
    };
    for (const [file, schema] of Object.entries(resourceSchemas)) {
      writeFileSync(join(wiki, "resources", file), [
        "---", `schema: "${schema}"`, 'source: "provider_skipped_local_only"',
        "resource_count: 0", 'trust_state: "unavailable_local_only"', 'raw_source: ""',
        "---", `# ${file}`, "",
      ].join("\n"));
    }
    const result = JSON.parse(runPython(validate, [root, "--pretty"]));
    assert.equal(result.ok, true);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
