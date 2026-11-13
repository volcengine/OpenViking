export const locales = ['en-US', 'zh-CN'];

export function parseSummary(summary, id = 'changeset') {
  const sections = [...summary.matchAll(/^## (en-US|zh-CN)\s*$/gm)];
  if (
    sections.length !== 2 ||
    new Set(sections.map((section) => section[1])).size !== 2 ||
    summary.slice(0, sections[0]?.index).trim()
  ) {
    throw new Error(`${id}: provide exactly one ## en-US and ## zh-CN section`);
  }
  return Object.fromEntries(
    sections.map((section, index) => {
      const content = summary
        .slice(section.index + section[0].length, sections[index + 1]?.index)
        .trim();
      if (!content)
        throw new Error(`${id}: ${section[1]} release note is empty`);
      return [section[1], content];
    }),
  );
}

export function bilingualBlock(notes) {
  return locales
    .map(
      (locale) =>
        `<!-- release-note:${locale} -->\n${notes[locale]}\n<!-- /release-note:${locale} -->`,
    )
    .join('\n\n');
}

export async function getReleaseLine(changeset) {
  const notes = parseSummary(changeset.summary, changeset.id);
  return bilingualBlock(
    Object.fromEntries(
      locales.map((locale) => {
        const [first, ...rest] = notes[locale].split('\n');
        const commit = changeset.commit
          ? `${changeset.commit.slice(0, 7)}: `
          : '';
        return [
          locale,
          `- ${commit}${first}\n${rest.map((line) => `  ${line}`).join('\n')}`.trimEnd(),
        ];
      }),
    ),
  );
}

export async function getDependencyReleaseLine(_changesets, dependencies) {
  if (!dependencies.length) return '';
  const list = dependencies
    .map((dependency) => `  - ${dependency.name}@${dependency.newVersion}`)
    .join('\n');
  return bilingualBlock({
    'en-US': `- Updated dependencies:\n${list}`,
    'zh-CN': `- 更新依赖：\n${list}`,
  });
}

export function localizeChangelog(source, locale) {
  const versions = source
    .replace(/^# @openviking\/react\s*/, '')
    .split(/(?=^## \d)/m);
  return versions
    .filter((version) => version.trim())
    .map((version) => {
      const counts = { 'en-US': 0, 'zh-CN': 0 };
      const blocks =
        /<!-- release-note:(en-US|zh-CN) -->\n([\s\S]*?)\n<!-- \/release-note:\1 -->/g;
      const remaining = version
        .replace(blocks, (_match, language, content) => {
          if (!content.trim())
            throw new Error(
              `${version.split('\n')[0]}: ${language} release note is empty`,
            );
          counts[language]++;
          return '';
        })
        .replace(/^## \d[^\n]*|^### (Major|Minor|Patch) Changes/gm, '')
        .trim();
      if (
        remaining ||
        !counts['en-US'] ||
        counts['en-US'] !== counts['zh-CN']
      ) {
        throw new Error(
          `${version.split('\n')[0]}: changelog requires paired bilingual notes`,
        );
      }
      let result = version.replace(blocks, (_match, language, content) =>
        language === locale ? content : '',
      );
      if (locale === 'zh-CN') {
        const headings = {
          Major: '重大变更',
          Minor: '功能更新',
          Patch: '修复与改进',
        };
        result = result.replace(
          /^### (Major|Minor|Patch) Changes$/gm,
          (_match, type) => `### ${headings[type]}`,
        );
      }
      return result.replace(/\n{3,}/g, '\n\n').trim();
    })
    .join('\n\n');
}

export function groupChangelog(source, locale, currentVersion) {
  const major = currentVersion.split('.')[0];
  const groups = new Map();
  for (const section of localizeChangelog(source, locale).split(
    /(?=^## \d)/m,
  )) {
    const version = section.match(/^## (\d+)\.(\d+)\.(\d+)/);
    if (!version || version[1] !== major) continue;
    const minor = `${major}.${version[2]}.x`;
    const releases = groups.get(minor) ?? [];
    releases.push(
      section.trim().replace(/^### /gm, '#### ').replace(/^## /gm, '### '),
    );
    groups.set(minor, releases);
  }
  if (!groups.size) throw new Error(`No release notes for major ${major}`);
  return [...groups]
    .map(([minor, releases], groupIndex) => {
      const versions = releases.map((release) =>
        release.replace(/^#### (.+)$/gm, '**$1**'),
      );
      return `<details className="ov-release-notes__group" name="ov-release-minors"${groupIndex === 0 ? ' open' : ''}>\n<summary>${minor}</summary>\n\n${versions.join('\n\n')}\n\n</details>`;
    })
    .join('\n\n');
}
