---
"name": "个人研究助理前端原型"
"description": "继承飞书紫色浅底草图的研究差异审阅与记忆纠正工作区"
colors:
  "accent": "#6554c0"
  "accent-light": "#efecfb"
  "accent-hover": "#5543ae"
  "ink": "#252638"
  "muted": "#696b7b"
  "canvas": "#f7f7fa"
  "surface": "#fff"
  "sidebar": "#f2f1f7"
  "line": "#e5e5ee"
  "focus": "#a598e4"
  "selection": "#dcd5ff"
  "success": "#237459"
  "danger": "#b3434c"
  "nav-active-bg": "#e7e1f8"
  "nav-active-text": "#5946b0"
  "change-active-bg": "#f0ecfb"
  "change-active-line": "#dcd3f0"
  "diff-added-bg": "#eef6f0"
  "diff-added-text": "#37694e"
  "warning-bg": "#fff5e4"
  "warning-text": "#856026"
  "warning-line": "#eeddbb"
  "badge-purple-bg": "#eee9fb"
  "badge-purple-text": "#6553ac"
  "badge-green-bg": "#e8f2ec"
  "badge-green-text": "#267557"
  "badge-amber-bg": "#faf0db"
  "badge-amber-text": "#8e6520"
  "badge-blue-bg": "#eaf0fa"
  "badge-blue-text": "#466994"
  "publish-bg": "#f1edfa"
  "field-line": "#ddd8e7"
  "field-text": "#353041"
  "toast": "#343042"
typography:
  headline:
    "fontFamily": "-apple-system, BlinkMacSystemFont, \"PingFang SC\", \"Microsoft YaHei\", sans-serif"
    "fontSize": "29px"
    "fontWeight": 650
    "lineHeight": 1.4
    "letterSpacing": "-0.7px"
  title:
    "fontFamily": "-apple-system, BlinkMacSystemFont, \"PingFang SC\", \"Microsoft YaHei\", sans-serif"
    "fontSize": "18px"
    "fontWeight": 600
  subtitle:
    "fontFamily": "-apple-system, BlinkMacSystemFont, \"PingFang SC\", \"Microsoft YaHei\", sans-serif"
    "fontSize": "15px"
    "fontWeight": 600
  body:
    "fontFamily": "-apple-system, BlinkMacSystemFont, \"PingFang SC\", \"Microsoft YaHei\", sans-serif"
    "fontSize": "14px"
    "lineHeight": 1.75
  diff:
    "fontFamily": "-apple-system, BlinkMacSystemFont, \"PingFang SC\", \"Microsoft YaHei\", sans-serif"
    "fontSize": "13px"
    "lineHeight": 2
  memory-quote:
    "fontFamily": "-apple-system, BlinkMacSystemFont, \"PingFang SC\", \"Microsoft YaHei\", sans-serif"
    "fontSize": "21px"
    "fontWeight": 500
    "lineHeight": 1.8
  button:
    "fontFamily": "-apple-system, BlinkMacSystemFont, \"PingFang SC\", \"Microsoft YaHei\", sans-serif"
    "fontSize": "12px"
    "fontWeight": 500
    "lineHeight": 1.5
  badge:
    "fontFamily": "-apple-system, BlinkMacSystemFont, \"PingFang SC\", \"Microsoft YaHei\", sans-serif"
    "fontSize": "10px"
    "lineHeight": 1
rounded:
  "badge": "4px"
  "filter": "6px"
  "control": "7px"
  "item": "8px"
  "bar": "9px"
  "mobile-panel": "10px"
  "brand": "11px"
  "panel": "12px"
  "modal": "14px"
  "circle": "50%"
spacing:
  "4": "4px"
  "6": "6px"
  "8": "8px"
  "10": "10px"
  "12": "12px"
  "14": "14px"
  "16": "16px"
  "18": "18px"
  "20": "20px"
  "22": "22px"
  "24": "24px"
  "26": "26px"
  "28": "28px"
  "30": "30px"
  "32": "32px"
  "36": "36px"
  "38": "38px"
  "44": "44px"
  "48": "48px"
components:
  button-primary:
    "backgroundColor": "{colors.accent}"
    "textColor": "{colors.surface}"
    "rounded": "{rounded.control}"
    "padding": "9px 14px"
    "typography": "{typography.button}"
  button-secondary:
    "backgroundColor": "{colors.surface}"
    "textColor": "{colors.ink}"
    "rounded": "{rounded.control}"
    "padding": "9px 14px"
  button-soft:
    "backgroundColor": "{colors.accent-light}"
    "textColor": "#5946ad"
    "rounded": "{rounded.control}"
    "padding": "9px 14px"
  button-danger:
    "backgroundColor": "{colors.danger}"
    "textColor": "{colors.surface}"
    "rounded": "{rounded.control}"
    "padding": "9px 14px"
  field:
    "backgroundColor": "{colors.surface}"
    "textColor": "{colors.field-text}"
    "rounded": "{rounded.control}"
    "padding": "10px 12px"
  navigation-active:
    "backgroundColor": "{colors.nav-active-bg}"
    "textColor": "{colors.nav-active-text}"
    "rounded": "{rounded.item}"
    "padding": "13px 14px"
  badge-purple:
    "backgroundColor": "{colors.badge-purple-bg}"
    "textColor": "{colors.badge-purple-text}"
    "rounded": "{rounded.badge}"
    "padding": "4px 7px"
  panel:
    "backgroundColor": "{colors.surface}"
    "rounded": "{rounded.panel}"
  diff-added:
    "backgroundColor": "{colors.diff-added-bg}"
    "textColor": "{colors.diff-added-text}"
    "rounded": "{rounded.badge}"
    "padding": "10px"
  drawer:
    "backgroundColor": "{colors.surface}"
    "width": "530px"
    "height": "100dvh"
    "padding": "28px"
---

# Design System: 个人研究助理前端原型

## Overview

**Creative North Star: "飞书紫色浅底工作区"**

沿用用户确认的飞书页面草图：浅灰画布、白色正文区、紫色操作与选中状态。Operate 场景以研究差异审阅和记忆纠正抽屉为核心，桌面并排阅读，手机纵向阅读。

**Key Characteristics:**
- 细线分隔的白色阅读面板。
- 紫色主操作、选中导航与淡紫提示。
- 文字差异对照、出处侧栏与右侧纠错抽屉。

## Colors

前置 tokens 对应 `src/styles.css` 的实际颜色。accent、accent-light、line、muted、success、danger 分别绑定 CSS 变量 `--accent`、`--accent-light`、`--line`、`--muted`、`--green`、`--red`；其余为实现中的字面量。紫色用于交互；绿色用于已同步与新增正文；琥珀色用于待重算或阻塞提示；红色用于删除操作。面板采用 surface，侧栏采用 sidebar，页面采用 canvas。

## Typography

系统字体不加载外部字体。页面标题使用 headline；研究页标题为 25px，手机为 23px。正文基础字号 14px；说明文字 12px，时间和来源元数据 9–11px。差异正文默认 13px / 2，大屏 15px，手机 14px / 1.9。记忆原文桌面 21px / 1.8，800px 以下 18px，600px 以下 19px。表单桌面 13px，手机 16px。图标由 Lucide React 输出 SVG；按钮图标 16px，关闭图标 20px。

## Layout

桌面固定侧栏 224px，顶部栏高 70px；内容最大宽 1660px，默认内边距 38px 36px 20px。研究审阅网格为 `210px minmax(280px, 1fr) 205px`，依次为变化列表、正文对照、证据；最小高度 423px。正文为两个等宽列。记忆网格为 `280px minmax(0, 1fr)`。设置页和表单为两个等宽列。

| 媒体条件 | 实际布局 |
| --- | --- |
| min-width: 1500px | 内容内边距 44px 48px；审阅列 240px / minmax(360px, 1fr) / 250px |
| max-width: 1180px | 侧栏 190px；内容 28px 24px；审阅列 195px / 1fr，证据跨整行；记忆列表 230px |
| max-width: 800px | 侧栏变为 210px 抽出菜单；主区无左偏移；顶栏 60px；内容 26px 20px；记忆列表 210px |
| max-width: 600px | 内容 23px 16px；审阅与记忆网格转 block，前后差异转单列；设置单列；抽屉满宽；字段 16px；发布条 sticky，bottom 10px |

手机表单仍为双列，列间距 12px。抽屉占满 100dvh，内容独立滚动；表单操作区 sticky，bottom -22px。发布确认框宽 480px、最大 `calc(100vw - 32px)`。

运行：在此目录执行 `npm install`、`npm run dev`，访问 `http://127.0.0.1:4178`。`npm run build` 构建，`npm run preview` 预览构建，`npm test` 运行模型测试。React + Vite 原型使用模拟数据与 localStorage，不连接真实文档或模型。

## Elevation & Depth

普通列表和阅读面板只使用 1px 边框与底色分层。抽屉及确认框阴影为 `-8px 0 45px #28213c20`，背景遮罩 `#27223655`；手机导航遮罩 `#28203466`。Toast 阴影为 `0 8px 30px #22212b22`，手机发布条为 `0 6px 25px #3e315f14`。侧栏 z-index 20、导航遮罩 19、手机发布条 5、Toast 100；原生 dialog 通过 showModal 进入顶层。

## Shapes

tokens 中的圆角来自现有实现：小标签 badge，输入与按钮 control，列表项 item，文档栏和发布条 bar，阅读容器 panel。手机拆开的审阅面板使用 mobile-panel；确认框使用 modal。头像、状态圆点为 circle。分隔线大多使用 line；选中变化项额外使用 change-active-line。

## Components

- **按钮**：默认白底描边，最小高 40px，12px / 500，9px 14px 内边距。主按钮紫底白字，hover 使用 accent-hover；次级 hover 为 `#f1eef9` 底和 `#c8bee5` 边框。另有淡紫、红色描边与红色实底变体。disabled 透明度 0.46。手机发布、记忆控制和表单操作最小高 44px；不是所有按钮均为 44px。
- **输入与搜索**：字段最小高 42px，textarea 最小高 100px、可纵向调整、行高 1.8。搜索容器高至少 38px、输入至少 36px；focus-within 为 2px `#c8bee7`。通用 focus-visible 为 3px focus、偏移 3px；光标使用 accent。
- **导航与页签**：导航最小高 46px、13px 14px 内边距、8px 圆角；hover `#eae7f4`，选中使用 nav-active tokens。页签以 2px 紫色下边线表示选中；手机侧栏由菜单按钮打开。
- **状态标签**：10px 字号、行高 1、4px 7px 内边距，紫/绿/琥珀/蓝四种语义色；同时保留状态文字。
- **研究审阅**：变化项选中为淡紫底加细边框；原正文和建议正文有独立标签，建议正文为绿色底、10px 内边距。证据引用用上下细线区分。手机保持变化列表 → 前后正文 → 证据顺序，发布操作粘在底部。
- **记忆详情与纠正**：列表项选中 `#f1ecfb` 底和 `#d6cbed` 边框，hover `#f4f1fa`；原文以 memory-quote 展示，出处为 `#f8f7fb` 底、8px 圆角。纠正表单使用右侧 530px 抽屉；标题 21px，内容内边距 28px（手机 22px 20px）。
- **弹层**：原生 dialog，Escape、关闭按钮和遮罩点击关闭，卸载后恢复前焦点。发布确认使用居中紧凑 modal，编辑使用 drawer。
- **反馈与动效**：Toast 自动显示 4000ms。按钮背景/边框过渡 0.18s；侧栏 transform 过渡 0.2s；抽屉从右侧 30px 平移进入，0.24s `cubic-bezier(0.16, 1, 0.3, 1)`；加载图标 1s linear infinite。prefers-reduced-motion 禁用所有动画和过渡。

## Do's and Don'ts

### Do:
- Do 沿用紫色交互、浅底正文与系统中文无衬线字体。
- Do 保留状态文字、键盘焦点与 reduced-motion 分支。
- Do 在手机把审阅列表、前后正文与证据按顺序纵向排列。

### Don't:
- Don't 将现有正文审阅区域替换为指标卡墙。
- Don't 用颜色替代状态文字。
- Don't 把桌面多栏和固定宽抽屉直接压缩到手机。
