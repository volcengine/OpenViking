# Web Studio 双版本接口核对与补充

核对日期：2026-10-02。开源服务基准：main `1394d4769a34058dcd64bd9afcf917d3cae7ab54`；该基准保留为历史 API 快照；当前 Studio 实现说明更新于 2026-10-14。云端依据仅为火山官方文档，未采用其他前端项目的内部 Action/IDL。

## 核对结论

原接口调研需要补充，不能直接认定为全量契约和全量兼容。

- 原开源清单包含 179 个路由声明，展开为 181 个方法/路径组合；默认 FastAPI schema 为 127 个路径、168 个操作。源码与该基准一致，未发现新增服务端路由漏入清单。声明、schema、普通用户可访问接口的数量是三个不同口径。
- 重新完整读取原来的 52 个官方文档快照（去重后 48 个 URL），正文均未变化。另确认 5 个漏入原对照的公开接口，见下表。官方 API 概览未列出所有技能接口，仅看概览会漏项。
- 技能列表与详情此前已在 Studio 补充报告确认，但未同步到最早的覆盖附录。因此原主覆盖表需要修正 7 项：5 个新发现漏项，加 2 个已有补充未同步项。
- “公开契约存在”“目标实例曾实测成功”“当前前端已经适配”应分别标注。本次不使用真实凭证执行写入、校验或删除；先前实例证据来自 admin Key，不能外推普通 User、其他版本或其他实例。

## 应修正的完整覆盖表

以下修正取代原表中对应接口的“未确认”描述。公开存在不表示已经运行验证。

| 开源接口 / 云管理能力 | 火山官方契约 | 当前 Studio 使用情况 |
|---|---|---|
| GET /api/v1/skills | [get_skills](https://docs.volcengine.com/docs/84313/2600958)，User Key、同路径 | 已用于技能页列表及开源 Compile 技能选择；此前已补充，原附录未同步 |
| GET /api/v1/skills/{skill_name} | [get_skill](https://docs.volcengine.com/docs/84313/2600962)，User Key、同路径 | 已用于技能详情；此前已补充，原附录未同步 |
| POST /api/v1/skills/find | [find_skills](https://docs.volcengine.com/docs/84313/2600959)，User Key、同路径 | 未发现现有页面调用；现有通用检索使用 /search/find，不等同于此接口 |
| POST /api/v1/skills/validate | [validate_skill](https://docs.volcengine.com/docs/84313/2600960)，User Key、同路径 | 未发现现有页面调用 |
| PUT /api/v1/skills/{skill_name} | [update_skill](https://docs.volcengine.com/docs/84313/2600963)，User Key、同路径 | 未发现现有页面调用；文件正文编辑不是完整技能替换 |
| DELETE /api/v1/skills/{skill_name} | [delete_skill](https://docs.volcengine.com/docs/84313/2600964)，User Key、同路径 | 未发现现有页面调用；通用文件删除不能代替带隐私清理的技能删除 |
| DELETE /api/v1/admin/accounts/{account_id} | [DeleteOpenVikingAccount](https://docs.volcengine.com/docs/84313/2693669)，企业版、AK/SK TOP 管理面 | 开源管理页已有删除调用；云端管理页当前隐藏，不应沿用 User Key |

## 新增接口的参数与返回值对照

### 技能搜索

| 参数 | 开源 | 火山公开契约 |
|---|---|---|
| query | Body string，必填 | 同名、必填 |
| limit | integer，默认 10 | 同名、默认 10；每个作用域的上限 |
| score_threshold | float/null，默认 null | 同名、默认 null |
| level | integer[]/null，默认 null | array/null，默认 null |
| telemetry | bool/object，默认 false | bool/object，默认 false |
| target_uri | string/null，默认 null | 同名、默认 null；指定单一根 |

返回封装都是 status/result，可选 telemetry。result 包含 skills[]、total；指定根时返回 root_uri，默认两范围时返回 root_uris。命中条目可含 name、uri、description、score、match_reason、level、abstract、tags、allowed_tools 等。

**明确差异：**开源 `skills.py` 将两范围结果排序后裁剪至 limit；火山文章明确默认两范围各执行 limit，合并后不再次裁剪，total 最多可达 2×limit。前端不能假设返回数不超过 limit，应显式传 target_uri 或按产品要求裁剪。列表/命中总数都不是跨页全量总数。

### 技能校验

| 参数 | 开源 | 火山公开契约 |
|---|---|---|
| data | Any，必填 | string/object，必填；SKILL.md 或结构化技能 |
| strict | bool，默认 false | 同名、默认 false |
| source_path | string/null，默认 null | 同名、仅回显定位来源 |
| skill_dir_name | string/null，默认 null | 同名、校验目录名与 name |
| target_uri | string/null，默认 null | 接收但校验逻辑不使用，不改变规则 |

返回 result.valid、strict、name、description、tags、allowed_tools、body_lines、source_path、skill_dir_name、errors[]、warnings[]；错误/警告条目有 rule、message、可选 field。源端通过技能验证服务返回该对象，字段按结果可选。

**不能只检查 HTTP 200 或 status=ok：**结构合法但技能业务校验不通过时仍可成功响应，必须检查 result.valid。CLI 的 skills validate 是本地校验，与这个 HTTP 接口不能混为一谈。

### 技能更新

| 参数 | 开源 | 火山公开契约 |
|---|---|---|
| skill_name | Path string，必填 | 同名、必填，须与新内容的 name 一致 |
| data | Any，可选 | string/object，和 temp_file_id 条件必选 |
| temp_file_id | string/null，默认 null | 同名；同时传 data 时优先使用临时文件 |
| from_source | bool，默认 false；与 data/temp_file_id 互斥 | 参数表未描述，不能推定支持从原来源更新 |
| wait | bool，默认 false | 同名；是否等待语义和索引处理 |
| timeout | float/null，默认 null | 同名；wait=true 生效，单位秒 |
| source_metadata | object/null，默认 null | object，可自动生成 |
| telemetry | bool/object，默认 false | 同名、默认 false |
| target_uri | string/null，默认 null | 同名；不传先查私有根，再查共享根 |

返回对象可包含 status、action=update、root_uri、uri、name、auxiliary_files；异步路径返回 task_id，等待路径可返回 queue_status。开源也可能带其他处理或清理信息，不能把生成 schema 的 result:any 当成字段完全一致的证明。

两端都是**整体替换**而非局部 patch；新内容没有的旧辅助文件不会保留。恢复/回滚与异步任务语义需要独立处理，不能把 200 视为索引已经完成。

### 技能删除

| 参数 | 开源 | 火山公开契约 |
|---|---|---|
| skill_name | Path string，必填 | 同名、必填 |
| target_uri | Query string/null，默认 null | 同名、默认 null；同名跨作用域时应显式传入 |

两端结果都可有 name、uri、root_uri、privacy_deleted，底层能估算时有 estimated_deleted_count。删除包含技能目录及对应隐私配置，不等同于单独 DELETE /fs。无 target_uri 时优先匹配私有范围，再匹配共享范围。

### 企业版数据空间删除

| 比较项 | 开源管理 API | 火山公开 TOP API |
|---|---|---|
| 调用 | DELETE /api/v1/admin/accounts/{account_id} | POST TOP、Version=2025-06-09；删除 Action 需见下方文档冲突 |
| 鉴权 | ROOT | AK/SK、企业版及删除权限；User Key 不可替代 |
| 目标参数 | Path account_id | Body ResourceID、OpenVikingAccountID，均为 string、必填；没有同名自动映射 |
| 成功响应 | HTTP 202，status/result；当前删除服务返回 status=deleting、task_id 等，需跟踪后台清理 | ResponseMetadata/Result，Result.Success 为 bool；未定义开源同样的任务字段 |
| 对象边界 | 服务内部 Account | 指定云库 ResourceID 下的数据空间；不是整个云库 |

**官方文档存在冲突：**标题、文字、请求示例和响应示例均为 DeleteOpenVikingAccount，但“请求接口”表把 Action 写成 CreateOpenVikingAccount。本表记录原文冲突，不能把创建 Action 当删除接口；实际管理集成前应确认服务端正确 Action。默认空间 default 禁止删除，删除是级联操作。此次没有发出删除请求。

## 已有补充还需明确的差异

| 能力 | 已确认的边界 | 对 Studio 的要求 |
|---|---|---|
| 技能列表 | 火山文档说 node_limit 虽被接收，底层仍每范围固定最多 1000；源端将 node_limit 传给底层。两端都可能有私有/共享同名技能 | 用 URI 而非 name 区分对象，不将 node_limit 当可靠分页 |
| 目录分页 | 源端有 offset/has_more；官方云文档未保证这两个字段。先前实例接受 offset/limit，但未提供可靠 has_more | 页大小不代表读完全部；不能遇到缺少 has_more 就默认为全量 |
| 任务列表 | 源端普通模式与云文档都为 Task[]；只有源端 pagination=cursor 模式才是 {items,has_more,next_cursor} | 当前普通任务页请求普通模式，能兼容数组；不能据此认为 Compile 游标模式兼容 |
| 高级检索 | 源端 HTTP 字段是 session_id；云 search 主表写 session，其他说明使用 session_id | 不把文档冲突说成已证实的线上差异；须有带真实会话的联调样本 |
| 配置 | Session 配置、User 默认记忆策略、Account 模板、实例配置分别属于不同对象和授权面 | Session 配置公开不意味着 User Key 能编辑管理配置；现阶段云管理仍隐藏 |
| 身份与服务识别 | Key 确认身份与判断部署类型是两件事；默认健康字段和版本号不是稳定 provider 标识 | 官方域名可自动识别；自定义域名保留选择。不开启任意跨用户 Header 切换 |

## 当前实现逐功能核对

| 功能 | 当前完成状态 | 未完成或证据边界 |
|---|---|---|
| 鉴权 | 火山按官方契约使用 Authorization: Bearer；开源保留 X-API-Key；健康检查、基础请求、权限及扩展探测保持一致 | 官方域名自动识别，自定义火山代理需明确选择服务类型；真实普通 User 仍待联调 |
| 连接与个人目录身份 | 已在数据 health 返回后同步用户；管理验证不会阻塞数据身份；失败可重新连接 | 自动检测先匹配火山官方域名，其余 HTTP(S) 地址默认开源；自定义火山代理需手动选择。普通 User 的真实实例权限仍未补测 |
| 我的目录与记忆 | 两种服务复用原 user/resources/agent 命名空间目录树与正文预览；仅接口不兼容的操作按版本限制 | 树状浏览已提供；单次列表最多 500 项，没有可靠的全量游标，不能声称目录完整 |
| 文件详情、摘要、原文和下载 | 已复用正文预览；两种服务共用文本编辑入口；云端导入和权限管理仍受门禁，保存失败保留编辑内容并显示错误 | 非文本样本、不同权限及版本待测；当前实例扩展读取成功不是稳定公开契约 |
| 技能列表与详情 | 现有页面使用上述公开读接口，详情显式带 target_uri；两种服务的技能与检索详情均可跳转原文件系统 | 搜索、校验、完整替换、专用删除未接入；这不是当前功能范围的必需项 |
| 检索 | 复用 find/grep/glob/search；结果有归一化 | 高级参数仍直接透传，未逐参数按 provider 隔离；带 session 的 search 待确认 |
| 会话基础记录 | 保留列表/详情/context；云端已隐藏 Bot Composer | 火山不请求未确认的 archives；界面明确只展示当前上下文消息，尚未展示历史归档或其摘要 |
| 会话记忆影响 | 开源保留入口，火山隐藏 | 依赖会话 user.user_id、归档编号和 memory_diff.json；火山不宣称支持 |
| 任务列表与状态 | 普通数组接口；Task 详情请求 include_events | 当前列表最多取 200，分页是本地切片；include_events 不在云公开参数表中，需兼容缺失 |
| 任务重试 | 当前仍显示已有重试动作 | URL 资源重试已改为 {path,reason}，并检查业务响应后提示成功；真实写入及不同任务类型重试仍需实例验证 |
| Watches | 云端按列表响应结构探测后展示；修改、触发、删除按钮隐藏 | 历史使用公开任务读取；有真实历史样本前不保证完整语义 |
| 监控与日志 | 云端按无副作用 GET 的结构与错误分类启用 | 当前实例监控曾通过、audit 曾 ApiBlocked；不能按全局角色或单次 200 开放全部面板 |
| 首页统计、Bot、Compile、经验分析 | 云端日常导航与深链接已阻止使用开源扩展页面 | 当前实例存在路径/响应或网关不兼容证据；其他实例是否提供这些扩展未知 |
| 管理及跨设备授权 | 云端隐藏原生管理与 OAuth；开源独立管理凭证控制入口 | 未开发 TOP 管理适配器；不能在浏览器里沿用 User Key 调这些 Action |

## 后续开发顺序

1. 使用真实普通 User Key 验证 Bearer 鉴权、私有目录、共享权限和会话隔离；不以管理员 Key 的结果代替。
2. 现有目录 500 条和任务 200 条提示已存在；后续增加目录全量浏览，需要全量任务时分 provider 处理游标。
3. 用普通 User、非空目录、已提交会话和真实异步任务补只读联调；高级检索按参数和模式核对。
4. 需要技能写操作时再接入上表中的专用接口；TOP 管理作为独立后续能力。

本次完成的是源码、文档和调用契约核对，不是对所有公开接口重新执行端到端测试。不存在“全部云 API 已实测兼容”的结论。仅从公开检索没有定位到某扩展契约，仍保留“未确认”，不改成“不存在”。

## 原文档与代码依据

- [原 API 对照主文档](https://www.feishu.cn/docx/QSGidE7Z7oPciyxlZMYcUyoRnye)
- [原 Studio 兼容性与实例验证报告](https://www.feishu.cn/docx/XY1Cdkd27oxZy0x0ZY9cgLHonfe)
- [开源技能路由与请求模型](https://github.com/volcengine/OpenViking/blob/1394d4769a34058dcd64bd9afcf917d3cae7ab54/openviking/server/routers/skills.py)
- [开源资源请求模型](https://github.com/volcengine/OpenViking/blob/1394d4769a34058dcd64bd9afcf917d3cae7ab54/openviking/server/routers/resources.py)
- [开源管理删除路由](https://github.com/volcengine/OpenViking/blob/1394d4769a34058dcd64bd9afcf917d3cae7ab54/openviking/server/routers/admin.py)、[删除任务结果](https://github.com/volcengine/OpenViking/blob/1394d4769a34058dcd64bd9afcf917d3cae7ab54/openviking/service/deletion.py)
- [开源任务列表两种返回模式](https://github.com/volcengine/OpenViking/blob/1394d4769a34058dcd64bd9afcf917d3cae7ab54/openviking/server/routers/tasks.py)


## 2026-10-13 UI 范围收敛（覆盖此前导航方案）

当前目标为同一套 Web Studio 兼容两种 API Key，整体 UI 保持最新 main。取消此前的全局功能域切换和独立“我的目录”页；在原页面内部按接口支持范围控制 Tab和动作。文件系统复用原目录树与预览，开源保留 Agent／终端，火山使用相同命名空间目录树和文本预览／编辑，不增加个人／共享 Tab。原侧栏分组、页面布局和开源首页恢复。管理、Gateway和启动配置保留 Provider与权限门禁。

接口鉴权、实例识别、数据／管理凭证隔离和既有契约差异继续有效；本节仅收敛 UI方案，不扩大云端接口兼容声明。详情见 [当前兼容与 UI范围](./main-update-compatibility-2026-10-13.md)。

## 2026-10-14 审查修复

- Gateway 接入最新 main `b8ec9d8210ab5eba3f235c5cf42a3a247223934c` 的 `/gateway/admin/*`、`/access` 和独立管理密钥。开源普通用户与开发模式可进入；对象修改和代他人签发权限由 `/access` 决定。火山连接继续拦截；开源 trusted 可查询访问状态并展示服务端不兼容说明，运行时仍拒绝 trusted。独立 Gateway /studio 宿主识别与专用连接页面保持主干行为。
- 配置切换、身份切换和组件卸载后取消旧聊天流；每次持久化前再次检查连接，禁止旧消息写入新配置。
- 火山定时同步保留只读历史查询，写操作继续关闭。
- 火山 [content/write](https://docs.volcengine.com/docs/84313/2487451) 已有公开契约，恢复原文本编辑入口和同一保存流程；资源 ACL 由服务端校验。读取仍受 10000 行上限约束，未完整读取的正文不能直接覆盖保存。真实云端临时资源写入验证与普通用户权限验证应单独记录，不能由公开契约或 Mock 通过推断已完成。
