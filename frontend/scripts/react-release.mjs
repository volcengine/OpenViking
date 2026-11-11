import { appendFileSync, readFileSync, writeFileSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const stableVersion = /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$/;

export function resolveRelease(env, packageVersion, latestStableVersion) {
  const isTag = env.GITHUB_REF_TYPE === 'tag';
  const manual = env.GITHUB_EVENT_NAME === 'workflow_dispatch';
  const channel = isTag ? 'latest' : manual ? env.RELEASE_CHANNEL : 'beta';
  if (!['latest', 'beta'].includes(channel)) {
    throw new Error('Release channel must be latest or beta');
  }
  if (channel === 'beta' && manual && env.RELEASE_VERSION) {
    throw new Error('The version input is only used for stable releases');
  }
  const version = isTag
    ? env.GITHUB_REF_NAME?.replace(/^react@/, '')
    : manual && env.RELEASE_VERSION
      ? env.RELEASE_VERSION
      : channel === 'beta'
        ? latestStableVersion
        : '';
  if (!stableVersion.test(version) || version.trim() !== version) {
    throw new Error(
      channel === 'beta'
        ? 'npm latest must resolve to an exact stable version'
        : 'Supply an exact stable version, e.g. 0.1.8',
    );
  }
  if (isTag && env.GITHUB_REF_NAME !== `react@${packageVersion}`) {
    throw new Error('Release tag must match the package manifest');
  }
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
  const { version: packageVersion } = JSON.parse(
    readFileSync(new URL('../packages/react/package.json', import.meta.url)),
  );
  const beta =
    process.env.GITHUB_REF_TYPE !== 'tag' &&
    (process.env.GITHUB_EVENT_NAME !== 'workflow_dispatch' ||
      process.env.RELEASE_CHANNEL === 'beta');
  const latestStableVersion = beta
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
  stageRelease(fileURLToPath(new URL('../', import.meta.url)), release);
  for (const [key, value] of Object.entries(release)) {
    appendFileSync(process.env.GITHUB_OUTPUT, `${key}=${value}\n`);
  }
  console.log(
    `Resolved @openviking/react@${release.version} for ${release.channel}`,
  );
}
