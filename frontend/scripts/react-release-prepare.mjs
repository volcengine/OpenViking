import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { githubReleaseNotes } from './react-github-release.mjs';
import { stageRelease } from './react-release.mjs';
import { checkChangesets, syncReleaseNotes } from './release-notes.mjs';

const reactPackage = '@openviking/react';
const stableVersion = /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$/;
const manifestPath = 'packages/react/package.json';
const changelogPath = 'packages/react/CHANGELOG.md';

function assertStableVersion(version) {
  if (!stableVersion.test(version) || version.trim() !== version)
    throw new Error('Supply an exact stable version, e.g. 0.1.13');
}

function validatePlan(plan, currentVersion, version) {
  if (plan.preState)
    throw new Error('Exit Changesets prerelease mode before a stable release');
  const releases = plan.releases.filter(({ type }) => type !== 'none');
  if (releases.length !== 1 || releases[0].name !== reactPackage)
    throw new Error('Expected one pending Changesets release for React UI');

  const release = releases[0];
  const [major, minor, patch] = currentVersion.split('.');
  const nextPatch = `${major}.${minor}.${BigInt(patch) + 1n}`;
  const allowed =
    version !== currentVersion &&
    (version === release.newVersion ||
      (major === '0' && release.type === 'minor' && version === nextPatch));
  if (!allowed)
    throw new Error(
      `Version ${version} is incompatible with the Changesets ${release.type} release ${release.newVersion} from ${currentVersion}`,
    );
  return {
    ...plan,
    releases: plan.releases.map((item) =>
      item === release ? { ...item, newVersion: version } : item,
    ),
  };
}

export async function prepareReactRelease(root, version) {
  assertStableVersion(version);
  checkChangesets(root);
  const read = (path) => readFileSync(join(root, path), 'utf8');
  const manifest = () => JSON.parse(read(manifestPath));
  const { version: currentVersion } = manifest();
  assertStableVersion(currentVersion);
  if (
    read(changelogPath)
      .split('\n')
      .some((line) => line.trim() === `## ${version}`)
  )
    throw new Error(`CHANGELOG already contains ${version}`);

  // Use the same release primitives and versions as the installed Changesets CLI.
  const require = createRequire(import.meta.url);
  const changesets = createRequire(
    require.resolve('@changesets/cli/package.json'),
  );
  const { getPackages } = changesets('@manypkg/get-packages');
  const { read: readConfig } = changesets('@changesets/config');
  const { default: getReleasePlan } = changesets(
    '@changesets/get-release-plan',
  );
  const { default: applyReleasePlan } = changesets(
    '@changesets/apply-release-plan',
  );
  const packages = await getPackages(root);
  const config = await readConfig(root, packages);
  const plan = validatePlan(
    await getReleasePlan(root),
    currentVersion,
    version,
  );
  await applyReleasePlan(plan, packages, config);
  if (manifest().version !== version)
    throw new Error(`Changesets did not prepare React UI ${version}`);

  githubReleaseNotes(read(changelogPath), version);
  stageRelease(root, { version });
  await syncReleaseNotes(root);
}

if (
  process.argv[1] &&
  import.meta.url === pathToFileURL(process.argv[1]).href
) {
  const [version, ...extra] = process.argv.slice(2);
  if (!version || extra.length)
    throw new Error('Usage: react-release-prepare.mjs VERSION');
  await prepareReactRelease(
    fileURLToPath(new URL('../', import.meta.url)),
    version,
  );
  console.log(`Prepared ${reactPackage}@${version}`);
}
