# 官网部署内容上下文

更新日期：2026-09-22。范围：官网内容、双语导航与阅读入口；不修改部署工具或环境。

## 内容目标

让读者完成：选择服务方式 → 准备环境 → 配置与预览 → 部署与验收 → 维护与排障。

托管服务、开源自建、Viking 私有交付分别说明。官网保留决策条件、操作主线、失败分支和验收标准；完整 CLI 参数、依赖初始化 SQL、授权操作细节继续指向对应随包手册。无需复制所有附件或增加与部署无关的产品页。

## 来源与证据范围

- 输入包：`viking-deploy-v0.3.0-20260922-87c30111.zip`，27,641,644 bytes。
- SHA-256：`11720a376d17d2a6d305854b6dc68b6e4e3d92851a94255298041fb3d1a80075`。这是本地下载记录，不是发布方签名证明。
- ZIP CRC 检查通过。静态读取包内手册；未运行 ovadmin、未接入集群、未执行部署与 smoke。
- 下载链接带签名，只用于取得输入，不写入站点、示例、日志附件或 llms.txt。
- `NOTICE.md` 将该包限定为授权测试材料，无生产、商用、对外服务或再分发授权。手册里的生产建议不覆盖此限制。本站内容以本地待审稿交付，公开发布前由内容负责人按实际授权范围确定可公开的内容；不分发原始手册、二进制或物料清单。

| 官网主题 | 随包依据 | 应保留的限制 |
| --- | --- | --- |
| 环境、资源、依赖 | `Viking部署手册.md` §1–5；`版本兼容性说明.md`；`基础组件配置要求.md` | 私有组合的规划参考不等于开源最低配置，不承诺容量或 HA |
| 安装主线 | `install.md`；`Viking部署手册.md` §6；`ovadmin使用手册.md` | VikingDB Ready 后部署 OpenViking；Operator 与 workspace 分开创建 |
| 授权首次启动 | `install.md`；部署手册 §6.4–6.5 | CRD、CR 与首次状态同步在导入前；不提供激活材料 |
| 模型与模板 | `Viking模型要求.md` §3、6 | CR 覆盖向量库维度，不覆盖 embedding 维度；修改源模板 |
| 验收 | 部署手册 §7–8；升级说明 §2.5 | 当前 generation Ready、License Active（如启用）、doctor、各产品 P0 分别验证 |
| 升级和排障 | `Viking升级说明.md`；ovadmin 手册 §3.9–4 | 配置备份不等于数据备份；回滚不可仅降镜像；失败先观测 |
| 开源 Helm 入口 | 仓库 `deploy/helm/README.md` | 使用 `deploy/helm/openviking`、`config.*` 字段与 root key；区别于私有 Operator |

版本冲突处理：部署手册把 Kubernetes 1.24+ 写为建议，版本兼容矩阵写为最低值。官网标明“兼容矩阵声明”，未补造最大版本。包版本 v0.3.0 不代表 runtime 版本。命令示例使用交付清单占位值，不沿用手册中的旧 runtime tag。

## 信息组织参考

- [MongoDB Ops Manager installation checklist](https://www.mongodb.com/docs/ops-manager/current/core/installation-checklist/)：部署前先确定拓扑、安全和备份，区分测试安装与正式运行。
- [GitLab installation](https://docs.gitlab.com/install/)：按 requirements、installation methods、offline、post-install、upgrade 组织任务入口。
- [GitLab requirements](https://docs.gitlab.com/install/requirements/)：说明资源要求与工作负载的关系。

只借鉴任务路径，不引入 MongoDB / GitLab 的组件要求或容量数字作为 Viking 的结论。

## 站点适配位置

- `docs/{zh,en}/guides/19-deployment-checklist.md`：方式选择、条件与环境清单。
- `docs/{zh,en}/guides/20-private-deployment.md`：版本范围、物料、配置、安装、客户端与验收。
- `docs/{zh,en}/guides/21-private-operations.md`：配置生命周期、升级、回滚准备、故障表。
- `docs/{zh,en}/guides/03-deployment.md`：保留既有开源安装内容，增加新入口并修正 Helm 路径。
- `docs/.vitepress/docs-navigation.ts`：在“配置与部署”下、服务端部署之后增加“企业私有化部署”子栏目，依次放置部署前检查、安装与验收、升级与排障。
- `docs/.vitepress/theme/components/DocsHome.vue`：在既有“部署与运维”条目中增加“企业私有化部署”链接。
- 目录与搜索继续使用既有 Markdown 扫描机制；不再维护第二套页面清单。本文件置于 `.vitepress`，不进入网站正文和 llms.txt。

## 验证记录

改动前 `check:docs` 已报告 `zh/qa/harness-qa-plan.md` 缺少英文对应页。该文件是工作区已有内容，不纳入本次修复；验收应比较新增错误，不能删减检查来获得全绿。

本次验证结果：

- `npm run docs:build` 通过；构建仍有既有 promql / caddyfile 高亮回退提示。
- 文档一致性检查覆盖 100 篇英文、101 篇中文、1,104 个本地链接；仅保留改动前已有的 QA 页面英文缺失，无新增错误。
- 六个新页面进入生成 HTML、`llms.txt`、`docs-search-index.json`；本 context 与本次签名下载地址未进入公开文本产物。
- ego-browser 实测英文首页 → checklist → private deployment → operations；中文切换、首页入口、清单与安装页跳转通过。
- 首页“私有交付”筛选返回两个对应页面；全站关键词查询在远端不可用后回退本地，返回安装、运维、清单与服务端部署页。未验证远端检索；发布流程需同步并验收远端索引。
- 桌面页面与 390px 手机首页无整页横向溢出，截图检查通过。
- 未执行部署命令、集群验收、提交或线上发布。页面中的命令为随包手册静态核对结果。


## 阅读与导航调整（2026-09-22）

按用户要求，正文移除测试包适用范围、NOTICE 和测试用途声明；来源版本及其使用限制只作为本 context 的资料记录保留。正文保留影响实际操作的版本兼容、License 激活、容量与数据恢复约束，不由删除提示推导新的生产能力承诺。

企业私有化部署页标题后提供“联系我们获取部署物料”按钮。中文跳转 README_CN.md 的商业版本锚点，英文跳转仓库首页的 commercial-editions 锚点；复用 VitePress VPButton，在正文中保留段落间距并去掉链接下划线；不发送联系消息。

调整验证：构建通过；浏览器确认“企业私有化部署”为配置与部署下的可折叠子栏目，包含三篇页面。中英文 CTA 的实际 href 与用户指定地址一致，适用范围提示已移除。桌面与手机视口检查通过。文档一致性检查仍仅报告原有 QA 页面缺少英文版本。

栏目命名：统一使用 `Enterprise Deployment / 企业私有化部署`，覆盖侧栏、首页入口、页面标题与交叉引用；保留现有 URL。

恢复记录（2026-09-24）：本次内容曾于 2026-09-22 在工作区完成但未提交，后被 `git clean` 清除；现依据当时的编辑记录在 main 上重放，并把侧栏改动从 `config.ts` 迁到已合入的 `docs-navigation.ts`。
