import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const read = file => readFile(new URL(`../dist/${file}`, import.meta.url), 'utf8');
const error = await read('404.html');
assert.match(error, /<h1[^>]*>A page out of place\.<\/h1>/);
assert.match(error, /name="robots" content="noindex, follow"/);
assert.match(error, /href="\/"[^>]*>Back to all essays/);
assert.match(error, /class="b-not-found__essay"/);
assert.doesNotMatch(error, /rel="canonical"|application\/ld\+json/);
assert.doesNotMatch(await read('index.html'), /name="robots" content="noindex/);
assert.doesNotMatch(await read('sitemap.xml'), /404\.html/);
assert.doesNotMatch(await read('llms.txt'), /404\.html/);
console.log('Static 404: readable HTML, recovery links, noindex, and index exclusion passed.');
