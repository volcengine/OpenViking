import { readFileSync, writeFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';
import { locales, localizeChangelog } from './bilingual-changelog.mjs';

export function githubReleaseNotes(source, version) {
  const entries = source
    .split(/(?=^## \d)/m)
    .filter((entry) => entry.split('\n')[0].trim() === `## ${version}`);
  if (entries.length !== 1)
    throw new Error(`Expected one CHANGELOG entry for ${version}`);
  return (
    locales
      .map((locale) => {
        const notes = localizeChangelog(entries[0], locale)
          .replace(/^## [^\n]+\n+/, '')
          .trim();
        if (!notes.replace(/^### .*$/gm, '').trim())
          throw new Error(`${version}: ${locale} release note is empty`);
        return `## ${locale}\n\n${notes}`;
      })
      .join('\n\n') + '\n'
  );
}

export async function publishGitHubRelease(
  version,
  body,
  { GITHUB_REPOSITORY, GITHUB_SHA, GH_TOKEN },
  request = fetch,
) {
  if (!GITHUB_REPOSITORY || !GITHUB_SHA || !GH_TOKEN)
    throw new Error('GitHub repository, commit and token are required');
  const base = `https://api.github.com/repos/${GITHUB_REPOSITORY}/releases`;
  const tag = `react@${version}`;
  const headers = {
    Accept: 'application/vnd.github+json',
    Authorization: `Bearer ${GH_TOKEN}`,
    'Content-Type': 'application/json',
    'X-GitHub-Api-Version': '2026-03-10',
  };
  const lookup = await request(`${base}/tags/${encodeURIComponent(tag)}`, {
    headers,
  });
  if (!lookup.ok && lookup.status !== 404)
    throw new Error(
      `GitHub Release lookup failed: ${lookup.status} ${await lookup.text()}`,
    );
  const existing = lookup.ok ? await lookup.json() : undefined;
  const release = {
    name: `@openviking/react ${version}`,
    body,
    draft: false,
    prerelease: false,
    make_latest: 'false',
  };
  if (!existing)
    Object.assign(release, { tag_name: tag, target_commitish: GITHUB_SHA });
  const response = await request(existing ? `${base}/${existing.id}` : base, {
    method: existing ? 'PATCH' : 'POST',
    headers,
    body: JSON.stringify(release),
  });
  if (!response.ok)
    throw new Error(
      `GitHub Release write failed: ${response.status} ${await response.text()}`,
    );
  return (await response.json()).html_url;
}

if (
  process.argv[1] &&
  import.meta.url === pathToFileURL(process.argv[1]).href
) {
  const [mode, version, file] = process.argv.slice(2);
  if (!['prepare', 'publish'].includes(mode) || !version || !file)
    throw new Error(
      'Usage: react-github-release.mjs prepare|publish VERSION FILE',
    );
  if (mode === 'prepare') {
    const source = readFileSync(
      new URL('../packages/react/CHANGELOG.md', import.meta.url),
      'utf8',
    );
    writeFileSync(file, githubReleaseNotes(source, version));
  } else {
    console.log(
      await publishGitHubRelease(
        version,
        readFileSync(file, 'utf8'),
        process.env,
      ),
    );
  }
}
