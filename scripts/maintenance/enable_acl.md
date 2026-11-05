# 为已有 OpenViking 开启 ACL

脚本：[enable_acl.py](enable_acl.py)。从仓库根目录、使用安装了本项目依赖的 Python 环境运行。

适用范围：火山引擎 VikingDB（`storage.vectordb.backend = "volcengine"`），迁移一个
context collection 及 `ov.conf` 指定的 index，再为已有 account 开启 ACL。
本地 `local` backend 在新版服务启动时自动补字段及标量索引，不需要此 schema 步骤；
私有化 `vikingdb` backend 不在本脚本支持范围内。

## 迁移内容

| Collection 字段 | 类型 | 默认值 |
| --- | --- | --- |
| `acl_mode` | `string` | `"none"` |
| `acl_direct_grants` | `list<string>` | `["none"]` |
| `acl_inherited_grants` | `list<string>` | `["none"]` |

三个字段也必须加入 index 的 `ScalarIndex`。脚本只新增缺少的字段，保留已有标量索引，
等待 index `Status=READY` 后才修改 account。已有同名字段的类型或默认值不符时会报错。

已有记录无需回填，也无需重新 embedding。缺失 ACL 的共享记录继续按根目录
`user:* = manage` 默认继承计算；开启开关本身不会使旧共享内容变私有。
不要把旧记录批量写成 `acl_mode=inherit` 且 grants 为空，这会改变原有访问权限。

## 凭据与目标

- `--config` 指向目标环境的 JSON `ov.conf`。读取 `storage.vectordb` 下的 `project`
  （兼容 `project_name`）、`name`、`index_name`；默认分别为 `default`、`context`、`default`。
- 控制面使用 `volcengine.ak/sk/region/session_token`。也可分别用环境变量
  `VIKINGDB_AK`、`VIKINGDB_SK`、`VIKINGDB_REGION`、`VIKINGDB_SESSION_TOKEN` 覆盖。
  只有数据面 API key 不足以修改 schema，仍需提供有权限的 AK/SK。
- 开启账号时需设置 `OV_ROOT_API_KEY`（或用 `--root-key-env` 指定另一个环境变量名），
  并用 `--url` 指向与该配置对应的 OpenViking 服务。
- `volcengine.host` 是数据面地址，脚本不会将它用于控制面。特殊部署可通过
  `--console-host` 单独指定控制面 hostname，不含 `https://`。

不要在脚本运行期间同时修改 index schema 或 account 的向量库配置。

## 从旧版本升级：分两步执行

首先使用新版本仓库中的脚本补远端 schema。此时不需要启动新版 OpenViking 服务。
不带 `--execute` 只读取并打印计划：

```bash
python -m scripts.maintenance.enable_acl \
  --config /path/to/ov.conf --phase schema
```

确认目标、字段计划正确后执行：

```bash
python -m scripts.maintenance.enable_acl \
  --config /path/to/ov.conf --phase schema --execute
```

随后部署并启动支持 ACL 的新版 OpenViking 服务。账号阶段需要 Admin API 的
`/accounts/{id}/configuration` 和 `/accounts/{id}/settings` 接口。设置目标服务的
`OV_ROOT_API_KEY` 环境变量后，先预览：

```bash
python -m scripts.maintenance.enable_acl \
  --config /path/to/ov.conf --phase accounts \
  --url http://127.0.0.1:1933 --all-accounts
```

开启所有现有 active account：

```bash
python -m scripts.maintenance.enable_acl \
  --config /path/to/ov.conf --phase accounts \
  --url http://127.0.0.1:1933 --all-accounts --execute
```

可以将 `--all-accounts` 换为 `--account acme`，或重复 `--account acme --account beta`。
`accounts` 阶段仍检查 collection/index 的 ACL schema 和 READY 状态，但不会修改它们。

若新版服务已经运行，可以用 `--phase all`（默认）一次完成两步：

```bash
python -m scripts.maintenance.enable_acl \
  --config /path/to/ov.conf --url http://127.0.0.1:1933 \
  --all-accounts --execute
```

## 账号处理与失败重跑

- 全量模式先读取一次 account 列表，跳过非 active account。运行后新增的 account
  不在此次快照内，需另行开启。
- 在任何写入前，预检所有选中 account 的配置和 ACL 开关。若 account 配置了独立
  `vectordb`，但 collection/index、region 或连接凭据与传入配置不一致，会停止。
  对这些 account，按其实际配置准备 `ov.conf`，用 `--account` 分别迁移；脚本不会
  因为公共 collection 已就绪就开启独立 collection 的账号。
- 账号更新调用兼容 Admin API
  `PATCH /api/v1/admin/accounts/{account_id}/settings`，仅提交
  `{"acl":{"enabled":true}}`，保留其他配置。此接口当前已 deprecated，但仍由服务支持，
  且返回生效的 ACL 开关供脚本校验。脚本不打印完整配置或凭据。
- schema 检查、更新或等待失败时，不会开启任何 account。账号更新中途失败会返回非零
  退出码，已成功的账号保持启用；再次运行会跳过已完成部分。多个步骤不构成原子事务。
- 默认每 5 秒检查一次，就绪等待每阶段最多 600 秒。较大的索引可用
  `--wait-timeout 1800` 延长。超时后检查 VikingDB 状态，再重跑。
- PATCH 所在服务进程立即更新配置；其他服务副本按配置刷新周期生效，当前默认约 30 秒。
  批量操作完成后，待各副本刷新，再使用普通用户验证受限资源的访问与检索。

## 依据与验证

字段定义见 `openviking/storage/collection_schemas.py`，权限语义和接口依据如下：

- [OpenViking ACL 文档](../../docs/zh/concepts/15-acl.md)
- [VikingDB 新增 collection 字段](https://docs.volcengine.com/docs/vector-database-vikingdb/update-dataset-update-vikingdb-collection?lang=en)
- [VikingDB 更新 index](https://docs.volcengine.com/docs/vector-database-vikingdb/update-index-update-vikingdb-index?lang=en)
- [VikingDB index 详情及状态](https://docs.volcengine.com/docs/vector_database_vikingdb/Viewindexdetails-GetVikingdbIndex?lang=en)

离线测试（不连接实际服务）：

```bash
python -m pytest --noconftest -o addopts='' tests/misc/test_enable_acl.py
```
