## 步骤1：安装连接器

1. 打开豆包工作，点击左侧导航栏的 **技能·连接器·伙伴**，搜索“OpenViking Context”，点击右侧的 <strong>+</strong>。
![添加 OpenViking Context 连接器](https://docs.openviking.net/agents/image/doubao-work/01-add-connector.png)

2. 在“授权配置”窗口中填写 OpenViking USER API Key：

   ```text
   {{OPENVIKING_API_KEY}}
   ```

3. 点击 **保存并连接**。页面顶部出现“连接器已安装”提示，且“OpenViking Context”右侧由 <strong>+</strong> 变为已添加状态，即表示接入完成。
![保存并连接 OpenViking Context](https://docs.openviking.net/agents/image/doubao-work/02-save-and-connect.png)

## 步骤2：验证

1. 返回豆包主对话，点击对话框下方的 **连接器**，确认能够找到“OpenViking Context”。
![验证 OpenViking Context 连接器](https://docs.openviking.net/agents/image/doubao-work/03-verify-connector.png)

2. 点击对话框下方的 **更多技能**，确认能够找到“OpenViking 上下文数据库”，并让豆包调用 OpenViking 返回相关内容。
![验证 OpenViking 上下文数据库技能](https://docs.openviking.net/agents/image/doubao-work/04-verify-skill.png)

## 故障排查

| 问题 | 处理 |
|---|---|
| 搜索不到“OpenViking Context” | 确认使用的是豆包工作；清除搜索条件后重新搜索；若仍未出现，请联系企业管理员确认连接器是否已对当前组织开放 |
| 提示连接失败 | 检查 OpenViking USER API Key 是否正确 |
