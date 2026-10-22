# 参与 Web Studio 国际化维护

Web Studio 支持英文和简体中文。本文面向在 `web-studio` 中新增或修改用户可见文本的贡献者。两种语言都能正常使用，这项改动才算完成。

先确认文本来自哪里：

| 文本来源                                    | 处理方式                                                   |
| ------------------------------------------- | ---------------------------------------------------------- |
| 固定界面文案                                | 同时添加英文和简体中文键，再通过 `t()` 或 `<Trans>` 渲染。 |
| 稳定的服务端枚举、状态、指标或显示表头      | 在本地化适配器中把已知值映射到 i18n 键。                   |
| 模型名称、路径、URI、标识符、命令或用户内容 | 除非产品定义了显示名称，否则保留原值。                     |
| 服务端原始错误或诊断数据                    | 翻译面向用户的错误摘要，同时保留可供排查的原始详情。       |

## 翻译资源

从仓库根目录看，语言资源位于：

```text
web-studio/src/i18n/locales/en/
web-studio/src/i18n/locales/zh-CN/
```

`web-studio/src/i18n/locales/en.ts` 和 `web-studio/src/i18n/locales/zh-CN.ts` 负责汇总各模块。新增文案应放入负责该页面或功能的现有命名空间。确实需要拆分模块时，应同时建立对应的英文和中文文件，并在两个语言入口中注册。

内部键名应表达所属模块和用途：

```ts
settings.connection.userHint
monitoringPage.detail.columns.status
resources.retrieval.emptyTitle
```

不要直接把英文句子作为键名。同一个短词在不同位置含义不同时，也不要为了复用而共用一个含糊的键。

## 新增界面文案

组件使用文案前，先在两种语言资源中添加相同的键：

```ts
// web-studio/src/i18n/locales/en/workspace.ts
refresh: 'Refresh'

// web-studio/src/i18n/locales/zh-CN/workspace.ts
refresh: '刷新'
```

普通文本和组件属性使用 `t()`：

```tsx
const { t } = useTranslation('monitoringPage')

<Button aria-label={t('refresh')}>{t('refresh')}</Button>
```

只有句子中包含嵌套 React 元素时才使用 `<Trans>`。所有语言中的插值名称和含义必须一致：

```ts
updatedAt: 'Updated at {{time}}'
updatedAt: '更新于 {{time}}'
```

不要用语言判断和写死的字符串选择界面文案：

```tsx
// 不要新增这种写法。
i18n.language.startsWith('zh') ? '刷新' : 'Refresh'
```

语言判断可以用于不同语言的文档链接或日期格式，但不应代替语言包。

## 服务端返回的文本

不要直接翻译任意服务端输出。服务端值可能是模型名称、Provider 值、路径、URI、标识符、命令或原始错误详情。

优先使用结构化字段。在界面边界将稳定的枚举值或协议标签映射到 i18n 键，请求、比较、日志和错误处理仍使用原始值。

接口返回 ASCII 表格等面向显示的文本时，按以下方式处理：

1. 将传输格式解析成有类型的界面数据。
2. 只转换明确登记的表头、指标、状态和枚举值。
3. 由组件渲染转换后的数据。
4. 未登记的值保持原样；只有产品已经定义安全显示名称时才转换。

监控页面已经采用这一结构：

- [`parse-status.ts`](./src/routes/monitoring/-lib/parse-status.ts) 负责解析传输格式。
- [`localize-observer-status.ts`](./src/routes/monitoring/-lib/localize-observer-status.ts) 负责把稳定的服务端文本映射到 i18n 键。
- [`observer-status-content.tsx`](./src/routes/monitoring/-components/observer-status-content.tsx) 负责渲染本地化后的数据。

不要把本地化映射重新写进路由组件。

## 翻译范围

| 应翻译                                     | 除非产品定义了显示名称，否则保持原样 |
| ------------------------------------------ | ------------------------------------ |
| 页面标题、按钮、表单标签、帮助文字、空状态 | API 密钥值、协议字段名称、命令       |
| 面向用户的表头和状态                       | 模型名称、Provider 值、集合名称      |
| 已知队列、角色、指标和枚举的显示名称       | ID、路径、URI、文件名、操作标识符    |
| 面向用户的校验提示和错误摘要               | 原始错误详情和诊断数据               |

`Agent`、`Root`、`Trusted`、`VikingBot`、`VLM` 和 `Embedding` 等词在表示产品角色或技术概念时可以保留英文，但各页面必须保持一致。

## PR 审查清单

提交审查前逐项确认：

- 每条新增的用户可见文本都有英文和简体中文。
- 组件使用 `t()` 或 `<Trans>`，没有新增写死的语言判断。
- 不同语言中的占位符、复数变量、链接和技术标识符保持一致。
- 服务端标签通过带上下文的白名单转换，未登记的值保持原样。
- 已在受影响的界面切换并查看两种语言，同时检查功能涉及的空、加载、成功和错误状态。
- 较长的中文在支持的页面宽度下不会遮挡数值或控件。
- 解析器或本地化适配器会影响运行结果时，有对应的目标测试。

国际化检查以错误级别运行，覆盖所有业务 TypeScript/TSX 和 `components/ui`。测试、生成代码及语言资源不参与硬编码检查。

- `i18next/no-literal-string` 检查 JSX 文本以及标题、占位符、帮助文字和无障碍标签。
- `studio-i18n/no-literal-message` 检查 `Error`、`OvClientError`、非取消型 `DOMException`、Toast、错误状态 setter，以及 JSX 中模板字符串的固定文案；也会追踪消息参数引用的本地 `const` 常量。
- `studio-i18n/explicit-locale` 禁止显示日期和数字时省略 locale。组件可使用当前 `i18n.resolvedLanguage`，公共工具可使用 `#/i18n/format`。独立组件还应通过 `useTranslation()` 订阅语言变化。
- 全量语言包测试检查键集合、空翻译和插值变量一致性；编辑器测试检查切换语言后文档、选区和撤销历史不丢失。

`npm run build` 会先运行 Lint 和 `npm run test:i18n`，检查失败时构建终止。`npm run lint` 同样会执行上述规则。

命令、文件名、技术标识符和开发者诊断信息确实需要保留原样时，应在对应行使用 `eslint-disable-next-line`，指明具体规则并用 `--` 写明原因；不要整文件禁用。`AbortError` 是取消控制流信号，可保留原值。

静态检查无法判断任意服务端输出、跨模块数据流和第三方组件的全部显示文案。仍须审查动态标签和服务端枚举，并进行双语界面验收。原始诊断可保留，但面向用户的摘要必须本地化。

## 验证

按改动范围运行检查：

```bash
cd web-studio
npm run format
npm run lint
npm run test:i18n
npm test -- <relevant-test-files>
```

改动涉及共享本地化代码、解析逻辑、路由或多个页面时，再运行 `npm test` 和 `npm run build`。未执行的检查及原因应如实说明。

## 相关文档

- [OpenViking 贡献指南](../docs/repository/CONTRIBUTING_CN.md)：仓库通用的贡献流程和要求。
