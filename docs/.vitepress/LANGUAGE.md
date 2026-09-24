# Site language behavior / 站点语言规则

The interface follows the browser's **first** preferred language. Chinese tags use Chinese; the website additionally supports Traditional Chinese, Japanese, Korean and Spanish. Other sites use English for unsupported languages. IP location, top-level domain and secondary browser languages do not affect this decision.

Choose a language in the language menu to remember it, including when it is already displayed. Choose **Follow browser** to resume automatic selection and remove the language query override. Only menu choices persist. `?lang=en` and other supported language tags control the current page without changing the saved choice; Studio also accepts `?lng=`. On Docs, `/en/` and `/zh/` paths take precedence over queries and saved preferences. The root opens the localized documentation homepage automatically; the localized homepages remain stable URLs.

The shared cookie and each origin's fallback localStorage key are `openviking-language-preference`. Values are `auto`, `en`, `zh`, `zh-TW`, `ja`, `ko`, or `es`. No value means `auto`. The cookie wins over localStorage, including when its value is `auto`. Production cookies use the matching `.openviking.ai` or `.openviking.net` parent domain, `Path=/`, a one-year lifetime, `SameSite=Lax`, and `Secure` on HTTPS. The two parent domains remain independent. A bilingual site may display English for a saved `ja` choice without changing that original choice. Old language records (`openviking-preferences.lang`, `i18nextLng`, `blog.lang`, sessionStorage and window.name) are ignored; theme behavior is retained. Disabled storage still permits changes for the current page.

Each repository contains the same self-contained `language-preference.js` and `language-preference.contract.js`; keep their copies and test cases aligned when changing the contract. There is no remote configuration or runtime dependency between repositories. Focus, history restoration and browser-language changes refresh the preference. Explicit page languages continue to win.

Publish the compatible widget before releasing host integrations. Hosts mount it with their current locale, then call `VikingBotWidget.setLocale('en' | 'zh')` to update UI copy in place. Older widget bundles remain mounted when this method is absent. Language changes preserve drafts, panel state, history and connections; they do not change the model's reply policy. Widget assets at `/studio/embed-widget/vikingbot-widget.js` must be routed to the widget's static build even while the Studio page is closed. Verify a JavaScript MIME type and bundle response, not a homepage HTML fallback, before production widget acceptance. These changes do not reopen Studio.

## 中文说明

界面默认只读取浏览器的首选语言；中文标签使用中文。官网还支持繁体中文、日语、韩语和西班牙语，其他站点遇到未支持语言时显示英语。自动判断不使用 IP、所在地区、顶级域名或浏览器第二语言。

在语言菜单中点击具体语言会保存选择，即使当前已经显示该语言。选择“跟随浏览器”会恢复自动判断，并清除 URL 的语言覆盖。只有菜单操作保存偏好；`?lang=` 只决定当前页面，Studio 兼容 `?lng=`。Docs 的 `/en/`、`/zh/` 路径优先于查询参数和保存的偏好；根入口自动打开对应语言的文档首页，两个语言首页都是稳定地址。Docs 手动切换尽量保留当前文档，缺少译文时回退到目标语言简介页，不存在的锚点回到页首。

Cookie 和本站 localStorage 统一使用新键 `openviking-language-preference`，取值为 `auto` 或上述六种官网语言代码，无记录等同于自动。Cookie 优先于 localStorage，显式 `auto` 也优先。正式域名在各自父域共享 Cookie，一年有效，`Path=/`、`SameSite=Lax`，HTTPS 下启用 `Secure`；`.ai` 与 `.net` 相互独立。不支持的语言只在显示时回退，不覆盖原始选择。旧语言记录不迁移，主题偏好保留；存储不可用时仍可在本页切换。重新获得焦点、历史恢复和浏览器语言变化时重读偏好，显式页面语言继续优先。

四个仓库内的解析模块和契约测试应同步维护，不引入运行时跨仓依赖。发布时先发布兼容旧调用的新 widget，再发布各宿主接入。`setLocale()` 原地更新组件文案，保留面板、草稿、历史与连接；不改变模型回复语言策略。旧 widget 缺少此方法时保留现有实例。组件静态资源路由须与 Studio 页面关闭状态分开，上线验收要确认脚本地址返回 JavaScript；本改动不恢复 Studio 的开放状态。

## Verification / 验证

The shared contract runs 30 cases in each repository. The Docs route suite covers root-to-homepage routing and document language switching; widget locale tests preserve the mounted view, inputs, selection, message history and connection, and verify the translated suggestion payload. The built section-link check also verifies that homepage section anchors remain valid. No coverage threshold is configured for this change.

Chromium checks use local production bundles served under the real `.ai`/`.net` hostnames through request interception, with a local widget and mocked chat transport. They cover both parent-domain cookie scopes and isolation, focus refresh, explicit URLs, legacy language records with retained theme, Blog legacy URLs, desktop/mobile keyboard controls, old widget compatibility, drafts across language changes, and Docs `DOCS_BASE=/guide/`. These are local integration checks, not production chat/backend validation.

Known environment limits: the deployed widget URL currently returns `text/html` and needs its static route restored before release. Studio's whole-project `tsc --noEmit` reports existing errors identical to a clean target-branch checkout; widget typechecking passes. With all storage APIs forced to throw, VitePress 1.6.4's built-in `check-dark-mode` inline script reports a storage error, while documentation and language switching remain usable. Website lint has 11 existing warnings and no errors.

共享契约在各仓运行 30 项；Docs 路由新增 11 项，widget 原地切换新增 4 项。各站构建和 widget 类型检查通过。浏览器验收使用本地生产构建、真实域名 Cookie 规则和模拟聊天传输，不代表线上后端验收已完成。Studio 全量类型检查的已有错误与干净目标分支一致；VitePress 内置深色模式脚本在强制禁用所有存储 API 时会报错，但正文和语言切换可用。上线前仍需恢复 widget 的 JavaScript 静态资源响应。
