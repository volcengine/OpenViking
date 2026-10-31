# 钉钉凭据引用的数据空间隔离

托管层按火山账号、资源、火山用户、OV 用户和 OV 数据空间保存钉钉 OAuth 绑定。OV 数据空间在 TOP 接口为 `OpenVikingAccountID`，在内核为 `RequestContext.account_id`，在私有凭据引用为 `ov_account_id`。引用里的 `account_id` 仍表示火山账号；钉钉知识空间 `workspace_id` 用于选取来源节点。

```json
{
  "openviking_credential_ref": {
    "account_id": "volcano-account",
    "resource_id": "ov-resource",
    "user_id": "volcano-user",
    "ov_user_id": "ov-user",
    "ov_account_id": "space-b",
    "type": "oauth",
    "platform": "dingtalk",
    "binding_version": 3
  }
}
```

首次导入和每轮 watch 执行前，内核校验 `ov_account_id`、`ov_user_id` 与当前任务身份一致，再用任务的用户 API Key 调用托管层 `/api/v1/inner/connector/credential/resolve`。托管层再次核对 Key 绑定的数据空间、用户及资源，按五元组和版本取得票据，必要时刷新。票据仅用于本次 Connector 调用，watch 继续只保存引用。

旧钉钉引用省略 `ov_account_id` 时归入 `default`，仅允许默认空间使用。非默认空间必须重新获取携带目标空间的引用，不能重放默认空间的旧引用。`binding_version` 为正整数；数据空间字段若显式提供，必须为非空字符串。飞书旧引用格式保持兼容。

上线顺序为：先执行托管层增加 `ov_account_id` 和五元组唯一索引的增量迁移，再部署托管层及内核。历史默认空间 token 保留原加密派生算法，不要求重新授权。
