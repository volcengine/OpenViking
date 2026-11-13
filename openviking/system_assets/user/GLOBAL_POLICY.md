---
type: global-access-policy
schema_version: 1
default: deny
rules:
  - effect: allow
    path: memories/experiences
    recursive: true
  - effect: allow
    path: peers
    recursive: true
---

# 给 Global Agent 的留言

## 读取范围

只读取以下目录，其他目录一律不读取；privacy、sessions 目录在任何情况下都不读取。

- memories/experiences/
- peers/{peer_id}/memories/preferences/：每个 peer 下的 preferences 目录

## 使用要求

以下要求由平台服务角色遵守。

### 用途

- 可以：整理为团队经验，在团队问答中作为依据
- 不可以：用于绩效评估或人事决策；分享到团队空间以外；用于训练模型

### 不要提取的内容

- 闲聊、情绪表达等个人交流内容
- 个人隐私：健康、家庭、请假原因与个人安排
- 人事信息：薪资、绩效、晋升，以及对同事的评价
- 密钥、账号与密码
- 尚未形成结论的讨论

### 时间范围

- 不限
