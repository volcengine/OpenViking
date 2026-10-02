// Shared by the browser router and route contract tests.
function queryObject(queryPart = '') {
  const search = new URLSearchParams(queryPart.replace(/^\?/, ''));
  const query = {};
  for (const [k, v] of search.entries()) query[k] = v;
  return query;
}

export function parsePath(pathname = '/', search = '') {
  const raw = pathname || '/';
  const pathPart = raw.startsWith('/') ? raw : `/${raw}`;
  const segs = pathPart.split('/').filter(Boolean);
  const query = queryObject(search);
  let route = { name: 'notFound', path: pathPart };
  if (pathPart === '/' || pathPart === '/index.html') route = { name: 'index' };
  if (/^\/post\/[^/]+(?:\/(?:index\.html)?)?$/.test(pathPart)) route = { name: 'post', slug: segs[1] };
  return { route, query, raw: `${pathPart}${search || ''}` };
}

export function parseHash(hash) {
  const raw = (hash || '').replace(/^#/, '') || '/';
  const [pathPart, queryPart = ''] = raw.split('?');
  return parsePath(pathPart || '/', queryPart ? `?${queryPart}` : '');
}

export function parseBrowserLocation(loc = window.location) {
  if (loc.hash?.startsWith('#/')) return parseHash(loc.hash);
  return parsePath(loc.pathname, loc.search);
}

export function buildPath(route, query = {}) {
  let path = route.name === 'notFound' ? route.path || '/404.html' : '/';
  if (route.name === 'post') path = `/post/${route.slug}/`;
  const search = new URLSearchParams();
  Object.entries(query || {}).forEach(([k, v]) => {
    if (v != null && v !== '') search.set(k, v);
  });
  const qs = search.toString();
  return `${path}${qs ? '?' + qs : ''}`;
}

export function postPath(slug, query) {
  return buildPath({ name: 'post', slug }, query);
}
