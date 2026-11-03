---
name: OpenViking Studio — Workbench 局部样式
description: 2026-11-02 登录、连接与文件系统页面对现有 OV Workbench 视觉的限定范围继承
colors:
  primary: "#5252ff"
  primary-hover: "#3833d2"
  surface: "#ffffff"
  foreground: "#0b0b0f"
  muted-foreground: "#6e718c"
  canvas: "#f6f6fc"
  auth-visual: "#f5f8fc"
  subtle: "#fafbfd"
  divider: "#edeef2"
  border: "#e1e3ea"
  secondary: "#edeffc"
  secondary-foreground: "#3833d2"
  dark-primary: "#9292ff"
  dark-canvas: "#171721"
  dark-surface: "#1d1d29"
  dark-foreground: "#f1f1f7"
typography:
  headline:
    fontFamily: "'PingFang SC', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif"
    fontSize: "24px"
    fontWeight: 500
    lineHeight: "32px"
    letterSpacing: "0.072px"
  body:
    fontSize: "14px"
    lineHeight: "22px"
  label:
    fontSize: "13px"
    fontWeight: 400
    lineHeight: "22px"
  help:
    fontSize: "12px"
    lineHeight: "20px"
  input-mobile:
    fontSize: "16px"
rounded:
  field: "8px"
  service-group: "10px"
  panel: "16px"
  context-switcher: "22px"
spacing:
  compact: "8px"
  panel: "16px"
  fields: "20px"
  mobile-inset: "24px"
  heading: "32px"
  submit: "40px"
components:
  button-primary:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.surface}"
    rounded: "{rounded.field}"
    height: "44px"
    padding: "0 24px"
  button-primary-hover:
    backgroundColor: "{colors.primary-hover}"
  button-outline:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.foreground}"
    height: "36px"
    padding: "0 10px"
  button-ghost:
    textColor: "{colors.foreground}"
    height: "36px"
    padding: "0 10px"
  input-auth:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.foreground}"
    rounded: "{rounded.field}"
    height: "44px"
    padding: "4px 10px"
  auth-service-notice:
    textColor: "{colors.muted-foreground}"
    typography: "{typography.label}"
  navigation-selected:
    backgroundColor: "{colors.secondary}"
    textColor: "{colors.secondary-foreground}"
  badge-secondary:
    backgroundColor: "{colors.secondary}"
    textColor: "{colors.secondary-foreground}"
    height: "20px"
    padding: "2px 8px"
  workspace-panel:
    backgroundColor: "{colors.surface}"
    rounded: "{rounded.panel}"
---

# Design System: OpenViking Studio — Workbench 局部样式

## Overview

**Creative North Star: "延续现有 OV Workbench"**

本文件记录用户指定参考的局部继承，不定义 Studio 全站的新品牌。浅色背景、紫蓝色操作、细分隔线和紧凑内容区来自已有 Workbench；保留 Studio 的组件、身份权限和连接语义。该名称用于说明继承关系，不是新创作的视觉主题。

适用范围由各文件路由的 `staticData.studioTheme` 标记决定，`src/app/layouts/app-shell.tsx` 消费标记应用主题：公共认证路径 `/login`、`/login/email`、`/login/api-key`、`/forgot-password`、`/reset-password`、`/invite`，以及兼容入口 `/onboarding`、`/filesystem`、`/settings`、`/connections/*` 启用。公共认证路径以 `src/features/auth/lib/auth-routes.ts` 为准；`/onboarding` 转到 `/login`，已有连接也能独立访问这些页面。文件系统顶栏与面板布局还受独立标记限制。其他页面继续使用既有 Studio 样式。

**Key Characteristics:**

- 现有 Workbench 的认证分栏、插画与上下文工作区样式。
- 通过背景色、边框和留白区分内容层级。
- 桌面、平板、手机布局；保留 Studio 深色模式。

规范值以本文件 frontmatter 和实际 CSS 为依据；发生漂移时应先核对 `src/shared/styles/workbench-theme.css`，再同步本文档。原始参考位于用户仓库 `vikingdb-fe/apps/ov-workbench/src/`：`layouts/auth.module.less`、`layouts/AuthLayout.tsx`、`layouts/root.module.less`、`features/navigation/navigation.module.less`。迁移证据见 `docs/all-ov-studio-ux-migration.md`。

## Colors

### Primary

紫蓝色承担主要操作、选中状态和键盘焦点；悬停使用更深的同色。不要把它推广为未迁移页面的全局颜色。

### Neutral

浅灰紫画布衬托白色内容面板，认证插画采用偏冷的浅底；正文、辅助文字、输入边框与面板分隔分别使用对应语义 token。侧栏选中项使用浅紫底与深紫文字。

原始参考没有深色模式。本次使用 `:root.dark` 下的独立语义映射保留 Studio 深色体验；frontmatter 仅列出主要深色值，完整的卡片、弹层、输入、侧栏、悬停和分隔映射以主题 CSS 为准。

## Typography

字体栈跟随 Workbench 的中文系统字体方案；正文与标签继承该字体。认证标题使用 headline，说明使用 body，表单标签使用 label，提示使用 help。文件路径和终端保留 Studio 的等宽字体，不强行替换为正文样式。

## Layout

认证页宽屏左右等分，表单最大宽度（480px），顶部品牌栏高（60px）；表单外边距随屏宽变化。宽度不超过（760px）时隐藏装饰插画，表单改单栏，提交按钮占满可用宽度。（761–900px）压缩左右留白与插画间距。

文件系统在宽度至少（1280px）时可展示目录、文档、助手/终端三栏，右栏可收起；（768–1279px）默认两栏，通过工具栏进入全屏操作面板；不超过（767px）时目录与文档上下排列，终端/助手以全屏面板打开。文件系统顶栏在手机上换行，内容两侧留出（8px）。连接管理内容区最大宽度（960px）。

目录与文档保留各自滚动区，长路径允许截断；侧栏导航及 Studio 的上下文切换保持可用。

## Elevation & Depth

工作区静态面板通过底色和细边框表达层次。限定范围内移除按钮和认证输入的非焦点阴影，但必须保留组件已有的键盘焦点环；当前覆盖使用 `:not(:focus-visible)`。弹层沿用 Studio 的交互与阴影，仅同步圆角、字体和分隔色。

**The Focus Preservation Rule.** 去除装饰阴影时，不得覆盖 `focus-visible` 的边框与焦点环。

## Shapes

认证输入、提交按钮与服务选项使用 field 圆角；服务选项容器使用 service-group 圆角；工作区外轮廓与弹层使用 panel 圆角。目录和文档拼接处只保留外侧圆角，手机上下排列时相应调整。上下文切换器使用胶囊轮廓。

## Components

### Buttons

认证提交使用 primary 及其 hover 色，禁用时回到 muted 语义色。普通按钮继续使用 Studio 的 default、outline、secondary、ghost、destructive、link 变体，尺寸由调用位置决定。所有变体保留原有禁用、错误和焦点语义；sidecar 展示认证主按钮及本次相关的输入、导航、标签与面板片段。

### Inputs / Fields

认证输入保持（44px）高度和细边框；只读字段用 subtle 底色及辅助文字色。焦点仍使用共享 Input 的 ring 与边框；密钥和密码显隐按钮有（44px）点击区域。宽度不超过（760px）时输入文字使用 input-mobile，认证链接最小高度（44px）。服务类型选项既显示选中状态，也为键盘焦点提供轮廓。

### Public Auth Pages

API Key、邮箱登录、忘记密码、重置密码和邀请页共用认证布局及表单层级。官方火山版的 `/login` 展示企业登录与 API Key 两个入口；`/login/email`、`/login/api-key` 均可返回入口选择页。官方 API Key 页隐藏服务选择与固定地址。私有化版的 `/login` 默认显示 API Key，先选中火山托管服务，再提供自部署服务选项；托管地址只读并要求协议勾选，自部署地址可编辑且不显示协议勾选。显式路径可直接访问。服务暂不可用提示使用辅助文字层级，校验错误使用共享 destructive 色并关联字段；缺少 token 时明确提示链接不完整。

本次已交付公共页面和路由，账号服务仍待联调。邮箱相关表单只做前端校验并显示服务暂不可用，不创建会话或展示演示成功；有 token 的邀请仅显示待验证与服务暂不可用状态。邀请注册、接受、拒绝等完整分支待账号 API 接入后实现。

### Navigation

文件系统保留 Studio 导航和账户/空间切换；以主题语义色表达选中项。目录条目高（36px），文字（13px）；展开与选择按钮各自保留可见焦点。手机上顶栏换行，品牌文字按空间隐藏。

### Cards / Containers

目录底色使用 subtle，文档与助手面板使用 surface，外边框使用 divider。三栏布局与圆角来自本次适配；窗口尺寸变化时优先保持操作入口可达。

### Chips

状态标签沿用 Studio Badge 的语义变体，使用紧凑胶囊外形。仅继承当前路由的颜色，不改变状态含义。

`.impeccable/design.json` 的色阶仅为面板预览合成，不是新增的产品 token；组件片段用于独立展示，交互逻辑仍以 Studio 组件为准。

### Auth Illustration

`src/assets/workbench/team.png` 复用已有认证插画，像素不变并包含来源元数据；来源说明见同目录 `README.md`。插画为装饰内容，对辅助技术隐藏，在手机上不显示。

## Do's and Don'ts

### Do:

- **Do** 从实际主题 CSS 和原始 Workbench 文件核对样式，再同步设计文档。
- **Do** 在目标路由内复用 Studio 组件，保留权限、键盘焦点、禁用和错误反馈。
- **Do** 同时检查桌面、平板、手机与 Studio 深色模式。

### Don't:

- **Don't** 将本文件视为全站改版授权，或把限定主题扩散到其他路由。
- **Don't** 为移除阴影而抹去焦点环。
- **Don't** 把邮箱、密码恢复或邀请页面视为账号服务已接通，或迁入参考项目的模拟会话、演示成功分支和 Arco 依赖。
