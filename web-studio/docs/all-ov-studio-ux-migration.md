# OV Workbench → Studio 局部 UX 样式迁移

记录日期：2026-11-02；登录组合及 STG 预览更新：2026-11-03。状态：样式、公共认证页面与路由已交付；真实账号服务及邀请完整流程待联调。

## 范围与来源

本次按用户要求，把现有 OV Workbench 的登录页与上下文工作区样式移入 Studio，范围为登录/连接相关页面及文件系统，不是 Studio 全站改版。历史阶段 0 计划继续保留在 `all-ov-studio-stage-0.md`，本文不替代它。

参考仓库：`/Users/bytedance/VscodeProjects/vikingdb-fe/apps/ov-workbench`。权威参考文件为 `src/layouts/auth.module.less`、`src/layouts/AuthLayout.tsx`、`src/layouts/root.module.less`、`src/features/navigation/navigation.module.less`。

主题由 `src/app/layouts/app-shell.tsx` 中的 `data-studio-ux="workbench"` 标记控制，先移除尾部斜杠，再匹配 `auth-routes.ts` 中的全部公共认证路径、兼容入口 `/onboarding`、`/filesystem`、`/settings` 和 `/connections/*`。标记存在时，根节点变量也供门户弹层继承；离开目标路由后不应用此主题。文件系统专属布局另由 `data-ux-filesystem` 限定。

## 已实现

| 区域 | 结果 | 代码入口（相对 web-studio） |
| --- | --- | --- |
| 主题 | Workbench 紫蓝主色、浅色画布、字体、边框与弹层圆角；独立深色 token 保留 Studio 深色模式 | `src/shared/styles/workbench-theme.css` |
| 认证 | 共用左侧表单/右侧插画及表单层级；保留 API Key 认证，补齐公共邮箱、密码恢复与邀请页面，账号服务待联调 | `src/features/auth/components/auth-layout.tsx`、`auth-layout.module.css`、`api-key-login.tsx`、`public-auth-page.tsx`；`src/features/auth/lib/auth-routes.ts` |
| 文件系统 | 目录、文档、助手/终端面板及上下文顶栏样式；目录列表与操作入口适配 | `src/features/workbench/filesystem/filesystem.module.css`、`page.tsx`、`components/context-explorer.tsx` |
| 布局 | 文件系统专属外壳、响应式顶栏及连接页面内容宽度 | `src/app/layouts/studio-layout.tsx`、`studio-header.tsx` |
| 资产 | `team.png` 复用原图像素，附带来源元数据及说明 | `src/assets/workbench/README.md` |
| 设计记录 | 实际 token、继承边界、响应式约束与组件片段 | `DESIGN.md`、`.impeccable/design.json` |

源参考没有深色模式，深色映射属于 Studio 适配。后续已补齐邮箱、密码恢复与邀请的公共页面，未迁入参考项目的模拟会话、邀请演示成功分支或 Arco 依赖；已有角色、权限与 API Key 连接语义继续生效。

认证插画在宽度不超过 760px 时隐藏。文件系统在 1280px 及以上可显示三栏，768–1279px 默认两栏，手机上下排列目录与文档；窄屏的终端/助手通过全屏面板进入。

## 初次样式迁移验证与证据边界（2026-11-02）

| 检查 | 结果与限制 |
| --- | --- |
| Vitest | 样式迁移后 57 个文件、485 个测试通过，日志 `/tmp/ov-ux-tests.log`。最终路由尾斜杠归一化和焦点覆盖修正后，针对性重跑 2 个文件、25 个测试通过，日志 `/tmp/ov-ux-tests-final.log`；没有把后者表述为再次全量运行。 |
| 构建 | 初次及最终 `build` 均通过，包含 lint/i18n 检查；最终日志 `/tmp/ov-ux-build-final.log`。仍有 8 条 lint warning 和构建体积提示，不称为零告警。 |
| 类型检查 | `tsc` 仍失败；`/tmp/ov-ux-tsc.log` 与干净上游基线 `/tmp/ov-stage0-baseline-tsc.log` 逐字节一致。结论是未新增该基线中的类型诊断，不是类型检查通过。 |
| 认证 | 本次执行记录为生产认证路由的 15 项检查通过。浏览器使用隔离的内存数据夹具完成 Admin 登录，未声称连接了生产部署。 |
| 浏览器 | 11 张实际页面截图保存在 `/tmp/ov-ux-review/`；文件系统列表及文件内容为合成夹具，不能据此推断真实存储服务端到端通过。 |
| 样式检测 | 改动样式目标的检测结果 `/tmp/ov-ux-detector.json` 为 `[]`，仅说明该检测未报告发现。 |
| 收尾评审 | 初次为 `recapture`：移除阴影覆盖了键盘焦点环，修正为仅对 `:not(:focus-visible)` 去阴影后，浏览器暂时无法补拍。随后在修复登录入口时补拍 `login-route-focus.jpg`，复核判定此项 `resolved`、`ship`。该结论只覆盖登录主按钮焦点修正，不代表新增路由或整页重新审查。 |

截图清单：

- 认证：`login-desktop.jpg`、`login-tablet.jpg`、`login-mobile.jpg`。
- 文件系统：`filesystem-desktop.jpg`、`filesystem-tablet.jpg`、`filesystem-mobile.jpg`、`filesystem-dark.jpg`。
- 移动终端：`terminal-mobile.jpg`。
- 连接：`connections-desktop.jpg`、`connection-form-desktop.jpg`、`connection-form-mobile.jpg`。

截图另存于本机 `/Users/bytedance/.codex/visualizations/2026/11/02/01a19b5c-50e4-7cb2-a38b-486561fa6c2f/studio-ux/`；补拍的登录主按钮焦点截图也保存在此处。这些均为本次工作机上的证据，不是仓库附件。上述证据不等同于 CI、部署验收或真实服务全链路验证。

## 登录入口修正

用户反馈访问 `/login` 看不到登录页。原实现仅在首次初始化的 `/onboarding` 渲染，且已有连接时会跳过，没有注册正式 `/login`。

- 注册 `/login`，无论是否已有连接都显示独立登录页，不挂载已连接工作区，不清空保存的连接，也不回填已有 Key。
- 首次进入跳到 `/login`；旧 `/onboarding` 链接兼容重定向，`/config` 继续按是否已有连接分别转到登录或设置。
- ROOT、Admin、User 的右上角菜单均提供“使用其他 Key 登录”；登录成功继续按原角色规则进入后台或工作台。
- 修正后构建通过；8 个相关测试文件、108 条测试通过，涵盖真实路由注册、`/studio` 子路径、旧链接、已有连接、离线设置及角色入口。
- 浏览器已直接打开 `/login`，并用 Tab 聚焦登录按钮确认焦点环可见；没有使用真实服务凭据或提交登录请求。

## 公共认证页面补齐

用户确认先交付页面和路由，真实账号接口后续联调。下表按 2026-11-03 已确认组合更新；本节末尾保留初次公共页面交付时的验证，前述样式迁移及登录入口修正的验证范围保持不变。

| 路径 | 当前行为 |
| --- | --- |
| `/login` | 官方为企业登录 / API Key 选择页；私有化为 API Key，默认火山托管服务，自部署服务排第二。 |
| `/login/api-key` | 独立 API Key 登录；官方隐藏服务选择与固定地址，提供返回选择页；“使用其他 Key 登录”菜单指向此处。 |
| `/login/email` | 邮箱、密码前端校验及服务暂不可用提示；官方提供返回选择页。 |
| `/forgot-password` | 邮箱前端校验及服务暂不可用提示，不发送邮件。 |
| `/reset-password?token=…` | 有 token 时提供新密码与确认密码校验；缺少 token 时提示链接不完整。 |
| `/invite#token=…` | 有 token 时仅显示待验证及服务暂不可用状态；缺少 token 时提示链接不完整。 |

全部路径均由 AppShell 独立渲染，不挂载已连接业务 provider；已有或离线连接不阻止访问。邮箱登录与忘记密码通过 `history.state` 传递邮箱，不写入 URL。密码仅保存在组件内，离开页面或通过前端校验提交后清空。邮箱、密码恢复与邀请页面不发送账号请求、不创建 session、不伪造成功；邀请注册、接受、拒绝分支尚未实现，不能视为邀请全链路交付。

初次公共页面交付验证（2026-11-02）：

- `npm run build` 通过（`/tmp/ov-auth-pages-build.log`），包含 lint 与 `test:i18n`（5 个文件、48 个测试）；仍有 lint warning 与构建体积提示。
- 9 个文件、109 个测试通过（`/tmp/ov-auth-pages-tests.log`），覆盖公共页面、路由、版本默认入口及 ROOT/Admin/User 角色入口与权限回归。其中 `api-key-login.test.tsx` 使用本机 HTTP fixture、真实 HTTP client 与 onboarding validator，检查三种角色的 health/admin/fs 请求范围；这是本地协议回归，不是真实部署验收。该轮测试不是全量重跑。
- `/tmp/ov-auth-pages-tsc.log` 与 `/tmp/ov-ux-tsc.log` 逐字节一致，无新增诊断；`tsc` 基线仍失败。
- 浏览器走查桌面（1280px）、手机（390px）、平板（820px），本次走查 console error 为空。截图保存在前述 `studio-ux/`：`email-login-desktop.jpg`、`email-login-mobile.jpg`、`forgot-password-mobile.jpg`、`reset-password-mobile.jpg`、`invite-tablet.jpg`。

该验证覆盖本地前端与已有 API Key 权限行为，不证明托管账号服务、生产部署或完整邀请流程已经可用。

## 登录组合与 STG 预览（2026-11-03）

官方入口选择页沿用认证分栏、插画与现有按钮层级；企业登录为主要入口，API Key 为次要入口。私有化 API Key 服务选择先火山托管、后自部署；托管地址只读且要求协议勾选，自部署地址可编辑且不显示协议勾选。切换服务会清空 Key、显隐状态、协议勾选和旧错误。API Key 继续走真实验证与连接保存，不改变 ROOT/Admin/User 的现有行为。

- 官方邮箱/API Key 使用[企业服务协议](https://docs.volcengine.com/docs/vector-database-vikingdb/viking-product-service-terms)。
- 私有化连接火山使用[个人服务协议](https://docs.volcengine.com/docs/vector-database-vikingdb/openviking-personal-product-service-terms)，用户说明该文档尚待发布；URL 已配置不等于文档已上线。
- 隐私政策 URL 待用户提供，页面显示待补充文字，不设置虚构链接。

STG 浮动控件切换整个入口组合，选择仅保存在当前标签页 `sessionStorage`；切换后整页重载到保留 Vite base 的 `/login`。STG 默认官方，两种预览的托管地址均为 `https://api.vikingdb-stg.cn-beijing.volces.com/openviking`。生产不显示该控件并忽略预览选择，源码默认私有化；官方生产需显式配置版本。构建及检查步骤见[部署配置](all-ov-studio-stage-0.md#部署配置)。

本轮验证与证据：

| 检查 | 结果与限制 |
| --- | --- |
| Vitest | 全量 227 个文件、1991 个测试通过，日志 `/tmp/ov-auth-full-tests.log`；含协议勾选、服务切换、STG base 与生产忽略预览、ROOT/Admin/User 本地 HTTP fixture 回归。 |
| 构建 | `npm run build` 通过，日志 `/tmp/ov-auth-build-production.log`，包含 lint/i18n 检查；STG base 构建通过，日志 `/tmp/ov-auth-build-stg.log`。仍保留 8 条既有 lint warning 与大 chunk 提示。 |
| 类型检查 | `/tmp/ov-auth-tsc-final.log` 与 `/tmp/ov-stage0-baseline-tsc.log` 逐字节一致；未新增基线诊断，`tsc` 仍失败。 |
| 浏览器 | `.impeccable/review/` 保存实际页面截图：官方选择、邮箱、API Key 和私有化托管/自部署的桌面及手机页面，以及桌面 STG 控件。截图不含真实服务登录验收。 |
| 构建产物浏览器检查 | 本地 3018 端口的正式官方构建 `/login` 不显示 STG 控件；3019 端口的 STG 构建 `/workbench-stg/login` 默认官方，点击切为私有化后 URL 保持 `/workbench-stg/login`，刷新仍为私有化，托管地址保持 STG。未提交真实凭据，不代表线上部署验收。 |

上述结果是本地实现、测试和页面证据，不等同于 CI、线上部署、托管账号服务或真实 Key 全链路验收。初次迁移的截图与测试数量属于历史记录，不与本轮数量合并。

## 后续验收

账号 API 联调后验证邮箱登录、重置邮件、重置 token 与邀请身份；补齐邀请注册、接受、拒绝及各异常分支，再进行真实服务端到端验收。后续若扩展样式范围，需显式更新路由边界及设计记录。
