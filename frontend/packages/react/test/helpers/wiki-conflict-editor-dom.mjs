import {
  click,
  fixture,
  mount,
  ok,
  rootUri,
  target,
  tree,
} from './preview-native-dom.mjs';

const file = `${target}/approvals.md`;
const otherFile = `${target}/other.md`;
const otherBody = 'This file has no pending edits.';
const body = 'The process requires two approvals.';
const fixedSentence = 'Earlier guidance allows one approval.';
const pending = {
  match_text: 'two approvals',
  problem_description: 'Approval policy differs',
  reason: 'Two references disagree',
  severity: 'high',
  state: 'unfixed',
  begin_time: 100,
  fix_time: null,
  fix_plan: '',
  resolution_basis: '',
};
const fixed = {
  ...pending,
  match_text: 'one approval',
  problem_description: 'Earlier conflict is fixed',
  state: 'fixed',
  fix_time: 120,
};
const documentBody = `${body}\n${fixedSentence}`;
const fileContent = `${documentBody}\n<!-- CONFLICTS_CONTENT\n${JSON.stringify(pending)},\n${JSON.stringify(fixed)}\n-->`;

function wikiSaveNotice(type = 'loading') {
  const card = document.querySelector(`.ov-notification--${type}`);
  return (
    card && {
      title: card.querySelector('.ov-notification__title')?.textContent,
      content: card.querySelector('.ov-notification__content')?.textContent,
      closable: Boolean(card.querySelector('.ov-notification__close')),
    }
  );
}

async function mountWikiFile(
  props,
  pendingPreview,
  pendingWrite,
  content = fileContent,
) {
  const fetch = fixture(
    file,
    false,
    ({ path, uri, query }) => {
      if (path.endsWith('/fs/ls')) {
        if (uri === rootUri)
          return ok([
            { uri: target, isDir: true, tags: ['compile_target=true'] },
          ]);
        if (uri === target)
          return ok([
            { uri: file, isDir: false, tags: ['has_conflict=true'] },
            { uri: otherFile, isDir: false },
          ]);
        return ok([]);
      }
      if (uri === otherFile) {
        if (path.endsWith('/fs/stat')) return ok({ isDir: false });
        if (path.endsWith('/content/read')) return ok(otherBody);
      }
      if (path.endsWith('/search/find'))
        return ok({ resources: [{ uri: file, level: 2 }] });
      if (path.endsWith('/content/write')) return pendingWrite;
      if (path.endsWith('/content/read') && uri === file)
        return pendingPreview && query.limit !== '-1'
          ? pendingPreview.then(() => ok(content))
          : ok(content);
    },
    [{ to: target }],
  );
  const render = (uri) =>
    tree(fetch, uri, { defaultExpandedUri: target, ...props });
  const view = await mount(render(file));
  return {
    view,
    fetch,
    navigate: (uri) => view.render(render(uri)),
    openDashboard: () =>
      click(
        view.container.querySelector(
          `[data-file-tree-row-id="${file}"] button[aria-label="View pending Wiki conflicts"]`,
        ),
      ),
    pencil: () =>
      view.container.querySelector(
        'button[aria-label="Edit conflict: Approval policy differs"]',
      ),
    activeTab: () =>
      view.container
        .querySelector('[role="tab"][aria-selected="true"]')
        .getAttribute('data-key'),
  };
}

export {
  body,
  documentBody,
  file,
  fixedSentence,
  fixed,
  mountWikiFile,
  otherBody,
  otherFile,
  pending,
  wikiSaveNotice,
};
