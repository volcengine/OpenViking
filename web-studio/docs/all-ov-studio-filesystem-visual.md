# 官方文件系统局部视觉调整

日期：2026-11-03。范围：官方版现有 `/filesystem` 控件的视觉调整；私有化继续使用既有 Studio 呈现。

参考：[Console Figma 节点](https://www.figma.com/design/ecpd1ZGHKRYibm1ODKEpM6/OpenViking-Console?node-id=8937-52805)、[产品文档](https://bytedance.larkoffice.com/wiki/GGchwdV0Qi4HNFkXEK9c6NXZnCe)。本轮按用户最终约束，只调整当前可用控件，不新增 VikingBot、导入或快速开始占位界面。

## 呈现与边界

平台配置 `presentation.filesystem` 由官方版选择 `console`、私有化版选择 `studio`。`app/composition/filesystem-entry.tsx` 注入页面呈现，`app/layouts/studio-layout.tsx` 将官方 CSS 限定在文件系统外壳；不在业务组件中判断部署版本。

- 顶栏保留账户、空间与导航入口。官方折叠导航不常驻窄轨，原导航仍由顶栏按钮展开。
- 桌面使用目录、弹性阅读区及终端三栏；初始目录约 352px、终端约 360px，保留原有宽度约束和拖动能力。沿用 1280px / 768px 断点与窄屏全屏终端入口。
- 目录采用 32px 行高、紧凑搜索与刷新入口；辅助摘要来自已有 `abstract` 数据。阅读区采用平面排版，L0/L1 仍是可分别开关的内容层级。
- CLI 欢迎区、命令卡片及输入区仅调整样式。API、认证、目录选择、L0/L1 状态与终端命令逻辑保持原有实现。

原始搜索、刷新图标与空文档插画见[资产来源说明](../src/assets/console/README.md)。中文插画只用于中文浅色模式；英文及深色模式沿用翻译文字。未增加 Iconbox 依赖，也不使用临时 Figma 资产 URL。

## 本地证据

| 检查 | 结果与限制 |
| --- | --- |
| 自动化回归 | 全量 227 个文件、1992 个测试通过：`/private/tmp/ov-filesystem-full-tests.log`。随后官方/私有化范围修正的定向回归为 3 个文件、10 个测试通过：`/private/tmp/ov-filesystem-scope-tests.log`；不是第二次全量。 |
| 构建 | 官方 `npm run build` 通过：`/private/tmp/ov-filesystem-official-build.log`；最终仅 CSS 调整后 Vite 构建通过：`/private/tmp/ov-filesystem-final-build.log`。既有 lint warning 与大 chunk 提示仍存在。 |
| 类型检查 | `/private/tmp/ov-filesystem-final-tsc.log` 与 `/private/tmp/ov-stg-tsc.log` 一致，仍有 43 条既有诊断；结论是无新增诊断，不是类型检查通过。 |
| 浏览器样式走查 | 本机 `.impeccable/review/filesystem/` 保存 `desktop.png`（1440×948）、`user-1280.png`（1280×720）、`mobile.png`、`mobile-terminal.png`（均 390×844）。真实页面路由使用隔离响应夹具，页面明确标记“样式验收示例”；目录、文档与终端结果是合成数据。 |
| 收尾评审 | 独立视觉复核要求移除阅读区点阵装饰，修正后判定该项解决、可交付；此为局部视觉评审。 |

上述截图是本机产物，日志是本次执行记录。自动化、夹具与浏览器证据不等同于真实服务联调、CI 或线上部署验收；本轮没有声称完成这些验收。

`DESIGN.md` 仅补充实际变体说明，保留现有 token 与 `.impeccable/design.json`。既有辅助文字 token 存在记录漂移（frontmatter 为 `#6e718c`，主题 CSS 为 `#787c91`）；本轮不扩展为 token 修订，也未将其规范化为新规则。
