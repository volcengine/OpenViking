// Public, deterministic fixture; no backend or credentials are used.
export const image =
  'data:image/svg+xml;charset=utf-8,' +
  encodeURIComponent(
    '<svg xmlns="http://www.w3.org/2000/svg" width="618" height="835" viewBox="0 0 618 835"><rect width="618" height="835" fill="#f7f8fd"/><g fill="#3f3f51" font-family="sans-serif"><text x="32" y="80" font-size="26">OpenViking CLI</text><text x="32" y="112" font-size="15">Context management</text><text x="32" y="178" font-size="18">Quick start</text><text x="32" y="232" font-size="16">add-resource</text><text x="240" y="232" font-size="14">Import documents</text><text x="32" y="284" font-size="16">add-skill</text><text x="240" y="284" font-size="14">Connect reusable skills</text><text x="32" y="336" font-size="16">add-memory</text><text x="240" y="336" font-size="14">Save context</text><text x="32" y="388" font-size="16">find</text><text x="240" y="388" font-size="14">Search resources</text></g><rect x="24" y="716" width="570" height="80" rx="12" fill="white" stroke="#e1e4f2"/><text x="42" y="762" font-family="monospace" font-size="16" fill="#6e718c">viking://</text></svg>',
  );
export const root = 'viking://resources/manual/chapter';
export const resolveImage = (src: string): Promise<string> => {
  if (
    [
      './images/cli.svg',
      '../images/cli.svg',
      'viking://resources/manual/cli.svg',
    ].includes(src)
  )
    return Promise.resolve(image);
  return Promise.reject(new Error('Unknown demo resource'));
};
