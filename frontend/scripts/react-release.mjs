import { appendFileSync, readFileSync, writeFileSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const stableVersion = /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$/;
const releaseRequestPath = '.changeset/react-stable-release.json';

export function resolveReleaseTarget(env) {
  const manual = env.GITHUB_EVENT_NAME === 'workflow_dispatch';
  if (!manual && env.GITHUB_EVENT_NAME !== 'push') {
    throw new Error('Unsupported release event');
  }
  const tagPush = !manual && env.GITHUB_REF_TYPE === 'tag';
  const automatic = Boolean(env.PREPARED_RELEASE_VERSION);
  if (automatic && (manual || env.GITHUB_REF !== 'refs/heads/main'))
    throw new Error('Automatic stable releases must come from a main push');
  const channel = manual
    ? env.RELEASE_CHANNEL
    : tagPush || automatic
      ? 'latest'
      : 'beta';
  if (!['latest', 'beta'].includes(channel)) {
    throw new Error('Release channel must be latest or beta');
  }
  return { manual, tagPush, channel };
}

export function resolveRelease(env, packageVersion, latestStableVersion) {
  const { manual, tagPush, channel } = resolveReleaseTarget(env);
  if (channel === 'beta' && manual && env.RELEASE_VERSION) {
    throw new Error('The version input is only used for stable releases');
  }
  const version =
    env.PREPARED_RELEASE_VERSION ||
    (tagPush
      ? env.GITHUB_REF_NAME?.replace(/^react@/, '')
      : manual && env.RELEASE_VERSION
        ? env.RELEASE_VERSION
        : channel === 'beta'
          ? latestStableVersion
          : '');
  if (!stableVersion.test(version) || version.trim() !== version) {
    throw new Error(
      channel === 'beta'
        ? 'npm latest must resolve to an exact stable version'
        : 'Supply an exact stable version, e.g. 0.1.8',
    );
  }
  if (tagPush && env.GITHUB_REF_NAME !== `react@${packageVersion}`) {
    throw new Error('Release tag must match the package manifest');
  }
  if (env.PREPARED_RELEASE_VERSION && version !== packageVersion)
    throw new Error('Prepared release version must match the package manifest');
  if (channel === 'latest') return { channel, version };

  // Always target the patch after npm's last stable release, not the manifest.
  const base = version.replace(/\d+$/, (patch) => String(BigInt(patch) + 1n));
  if (
    !/^[1-9]\d*$/.test(env.GITHUB_RUN_NUMBER ?? '') ||
    !/^[1-9]\d*$/.test(env.GITHUB_RUN_ATTEMPT ?? '') ||
    !/^[0-9a-f]{40}$/.test(env.GITHUB_SHA ?? '')
  ) {
    throw new Error(
      'Beta releases require a GitHub run number, attempt and SHA',
    );
  }
  return {
    channel,
    version: `${base}-beta.${env.GITHUB_RUN_NUMBER}.${env.GITHUB_RUN_ATTEMPT}.g${env.GITHUB_SHA.slice(0, 12)}`,
  };
}

export function planRelease(env, changelog, preparedVersion) {
  const archiveHas = (version) =>
    changelog.split(/\r?\n/).some((line) => line.trim() === `## ${version}`);
  if (preparedVersion !== undefined) {
    resolveReleaseTarget({ ...env, PREPARED_RELEASE_VERSION: preparedVersion });
    if (!stableVersion.test(preparedVersion) || !archiveHas(preparedVersion))
      throw new Error(
        'The merged release request needs archived release notes',
      );
    return { mode: 'publish', preparedVersion };
  }
  const { channel, manual } = resolveReleaseTarget(env);
  if (!manual || channel !== 'latest') return { mode: 'publish' };
  const { version } = resolveRelease(env);
  if (archiveHas(version)) return { mode: 'publish' };
  if (env.GITHUB_REF !== 'refs/heads/main')
    throw new Error('Prepare a new stable release from main');
  return { mode: 'prepare', version };
}

function readMergedReleaseRequest(env, root) {
  if (
    env.GITHUB_EVENT_NAME !== 'push' ||
    env.GITHUB_REF !== 'refs/heads/main' ||
    !/^[a-f0-9]{40}$/.test(env.PUSH_BEFORE ?? '') ||
    /^0+$/.test(env.PUSH_BEFORE)
  )
    return undefined;
  const git = (...args) =>
    execFileSync('git', args, { cwd: root, encoding: 'utf8' }).trim();
  // The request file persists on main. Only the push that changes it can publish.
  const path = `frontend/${releaseRequestPath}`;
  if (
    !git(
      'diff',
      '--name-only',
      '--diff-filter=AM',
      env.PUSH_BEFORE,
      'HEAD',
      '--',
      `:(top)${path}`,
    )
  )
    return undefined;
  const { version } = JSON.parse(readFileSync(join(root, releaseRequestPath)));
  if (typeof version !== 'string') throw new Error('Invalid release request');
  return version;
}

export function stageRelease(root, { version }) {
  const read = (path) => JSON.parse(readFileSync(join(root, path), 'utf8'));
  const manifest = read('packages/react/package.json');
  const docs = read('docs/package.json');
  const lock = read('package-lock.json');
  manifest.version = version;
  docs.dependencies['@openviking/react'] = version;
  lock.packages['packages/react'].version = version;
  lock.packages.docs.dependencies['@openviking/react'] = version;
  for (const [path, value] of [
    ['packages/react/package.json', manifest],
    ['docs/package.json', docs],
    ['package-lock.json', lock],
  ]) {
    writeFileSync(join(root, path), `${JSON.stringify(value, null, 2)}\n`);
  }
}

if (
  process.argv[1] &&
  import.meta.url === pathToFileURL(process.argv[1]).href
) {
  const root = fileURLToPath(new URL('../', import.meta.url));
  const writeOutputs = (values) => {
    for (const [key, value] of Object.entries(values))
      appendFileSync(process.env.GITHUB_OUTPUT, `${key}=${value}\n`);
  };
  if (process.argv.includes('--plan')) {
    writeOutputs(
      planRelease(
        process.env,
        readFileSync(join(root, 'packages/react/CHANGELOG.md'), 'utf8'),
        readMergedReleaseRequest(process.env, root),
      ),
    );
  } else {
    const { version: packageVersion } = JSON.parse(
      readFileSync(new URL('../packages/react/package.json', import.meta.url)),
    );
    const { channel } = resolveReleaseTarget(process.env);
    const latestStableVersion =
      channel === 'beta'
        ? JSON.parse(
            execFileSync(
              'npm',
              [
                'view',
                '@openviking/react@latest',
                'version',
                '--json',
                '--registry=https://registry.npmjs.org',
              ],
              { encoding: 'utf8' },
            ),
          )
        : undefined;
    const release = resolveRelease(
      process.env,
      packageVersion,
      latestStableVersion,
    );
    // Keep docs on the workspace package, including prereleases, before npm ci.
    stageRelease(root, release);
    writeOutputs(release);
    console.log(
      `Resolved @openviking/react@${release.version} for ${release.channel}`,
    );
  }
}
