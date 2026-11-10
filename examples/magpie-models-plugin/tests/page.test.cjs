// Optional browser checks: NODE_PATH must contain Playwright. No live config/API.
const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const { chromium, webkit } = require("playwright");
const root = path.resolve(__dirname, "../page");
const bridge = `let serial=0;const pending=new Map();addEventListener('message',e=>{const p=pending.get(e.data.id);if(p){pending.delete(e.data.id);e.data.error?p.reject(new Error(e.data.error)):p.resolve(e.data.value)}});const call=(method,options)=>new Promise((resolve,reject)=>{const id=++serial;pending.set(id,{resolve,reject});parent.postMessage({id,method,options},'*')});window.magpie={ready:Promise.resolve(),context:{},theme:{lang:new URLSearchParams(location.search).get('lang')},options:{get:()=>call('get'),set:options=>call('set',options)}};`;
const parent = `<!doctype html><title>OpenViking settings test</title><style>body{margin:0}iframe{width:100%;height:1800px;border:0}</style><iframe sandbox="allow-scripts" src="/panel/index.html?lang=LANG"></iframe><script>let options=INITIAL;addEventListener('message',e=>{if(e.source!==document.querySelector('iframe').contentWindow)return;const {id,method}=e.data;if(method==='set'&&window.holdSave){window.pendingSave={source:e.source,id,options:e.data.options};return}if(method==='set')options=e.data.options;e.source.postMessage({id,value:options},'*')});</script>`;
for (const engine of (process.env.BROWSER ? [process.env.BROWSER] : ["chromium", "webkit"])) {
  test(`${engine}: VLM and Bot forms save independently in a sandbox`, async (t) => {
    const browser = await (engine === "webkit" ? webkit.launch() : chromium.launch({ channel: process.env.PLAYWRIGHT_CHANNEL || "chromium" }));
    t.after(() => browser.close());
    for (const lang of ["zh", "en"]) for (const width of [900, 360]) {
      const page = await browser.newPage({ viewport: { width, height: 1000 } });
      const errors = []; page.on("pageerror", (e) => errors.push(e.message));
      const initial = { manageVlm: true, botMode: "independent", botModel: "vendor/bot", restartOnChange: true, serverUrl: "https://ov.example", rootApiKeyFile: "/private/key", untouched: "keep", vlmParameters: { max_tokens: 8192, temperature: 0.2, thinking: false, timeout: 600 }, botParameters: { max_tokens: 4096, thinking: true, timeout: 120 } };
      await page.route("**/*", async (route) => {
        const url = new URL(route.request().url());
        if (url.pathname === "/") return route.fulfill({ contentType: "text/html", body: parent.replace("LANG", lang).replace("INITIAL", JSON.stringify(initial)) });
        const file = url.pathname.slice("/panel/".length);
        const headers = { "Access-Control-Allow-Origin": "*" };
        if (file === "_magpie/bridge.js") return route.fulfill({ contentType: "text/javascript", headers, body: bridge });
        const contentType = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".png": "image/png" }[path.extname(file)];
        try { await route.fulfill({ contentType, headers, body: await fs.readFile(path.join(root, file)) }); }
        catch { await route.fulfill({ status: 404, body: "" }); }
      });
      await page.goto("http://ov.test/");
      const f = page.frameLocator("iframe");
      await f.locator("#saveSettings:enabled").waitFor();
      assert.equal(await f.locator("#vlmParameters-thinking-mode").inputValue(), "false");
      await f.locator("#vlmParameters-max_concurrent-mode").selectOption("custom");
      await f.locator("#vlmParameters-max_concurrent").fill("4");
      assert.equal(await f.locator("#botParameters-max_concurrent-mode").count(), 0);
      await f.locator("#vlmParameters-max_tokens").fill("16384");
      await f.locator("#vlmParameters-temperature-mode").selectOption("null");
      await f.locator("#botParameters-thinking-mode").selectOption("false");
      await page.evaluate(() => { window.holdSave = true; });
      await f.locator("#saveSettings").click();
      await page.waitForFunction(() => !!window.pendingSave, null, { polling: 50 });
      assert.ok(await f.locator("#vlmParameters-max_concurrent").isDisabled());
      assert.ok(await f.locator("#botMode").isDisabled());
      await page.evaluate(() => {
        window.holdSave = false;
        const pending = window.pendingSave; options = pending.options;
        pending.source.postMessage({ id: pending.id, value: options }, "*");
        window.pendingSave = null;
      });
      await f.locator("#saveSettings:enabled").waitFor();
      await f.locator("#message").filter({ hasText: lang === "zh" ? "设置已保存" : "Settings saved" }).waitFor();
      const saved = await page.evaluate(() => options);
      assert.deepEqual(saved.vlmParameters, { max_concurrent: 4, max_tokens: 16384, temperature: null, thinking: false, timeout: 600 });
      assert.deepEqual(saved.botParameters, { max_tokens: 4096, thinking: false, timeout: 120 });
      for (const key of ["serverUrl", "rootApiKeyFile", "untouched"]) assert.equal(saved[key], initial[key]);
      const frame = page.frames()[1];
      assert.ok(await frame.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
      await f.locator("#botMode").selectOption("preserve");
      assert.ok(await f.locator("#botParameters-thinking-mode").isDisabled());
      await f.locator("#vlmParameters-max_tokens-mode").selectOption("preserve");
      await f.locator("#saveSettings").click();
      await page.waitForFunction(() => !Object.hasOwn(options.vlmParameters, "max_tokens"));
      await f.locator("#vlmParameters-timeout").fill("0");
      await f.locator("#saveSettings").click();
      assert.equal(await page.evaluate(() => options.vlmParameters.timeout), 600, "invalid timeout is not saved");
      await f.locator("#vlmParameters-timeout").fill("45");
      await f.locator("#vlmParameters-timeout").press("Enter");
      await page.waitForFunction(() => options.vlmParameters.timeout === 45);
      assert.deepEqual(errors, []);
      await page.close();
    }
  });
}
