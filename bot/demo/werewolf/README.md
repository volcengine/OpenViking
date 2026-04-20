# 狼人杀 Demo（中文版）

本目录提供一个狼人杀演示服务，包含：
- OpenViking + bot 通道初始化
- Web UI（默认端口 `1995`）
- 对局记录、排行榜、回放查看

## 1. 启动前准备

请先确认以下命令可用：
- `python`（建议 3.10+）
- `openviking-server`

并准备好配置文件（默认）：
- `~/.openviking/ov.conf`

## 2. 推荐启动方式（一键启动）

在本目录执行：

```bash
python start_werewolf_demo.py --config ~/.openviking/ov.conf
```

默认行为：
- 自动补齐狼人杀所需 channel（`god`、`player_1`...`player_6`）
- 自动准备工作目录与 SOUL 文件
- 启动 OpenViking 服务
- 等待 bot 健康检查通过后启动 UI 服务

默认参数：
- UI 端口：`1995`
- OpenViking host：`127.0.0.1`
- OpenViking port：`1933`
- Vikingbot URL：`http://localhost:18790`
- game mode：`all_agents`

常见可选参数：

```bash
python start_werewolf_demo.py \
  --config ~/.openviking/ov.conf \
  --ui-port 1995 \
  --game-mode all_agents \
  --smart-buttons
```

说明：
- `--game-mode` 可选：`all_agents` / `human_player`
- `--smart-buttons`：启用前端“智能按钮显示”逻辑（根据游戏状态动态隐藏/显示按钮）

## 3. 手动启动方式（调试用）

如果你要分开调试服务，可以手动启动：

### 3.1 启动 OpenViking

```bash
openviking-server \
  --config ~/.openviking/ov.conf \
  --host 127.0.0.1 \
  --port 1933 \
  --with-bot \
  --bot-url http://localhost:18790
```

### 3.2 启动狼人杀 UI 服务

```bash
python werewolf_server.py \
  --config ~/.openviking/ov.conf \
  --port 1995 \
  --game-mode all_agents
```

## 4. 访问地址

启动后打开：

- 主页面：`http://localhost:1995/`
- 测试页：`http://localhost:1995/test`
- 调试页：`http://localhost:1995/debug`

## 5. 页面按钮如何控制

## 5.1 顶部导航

- **游戏**：主对局页
- **记忆**：查看 OpenViking memory 目录
- **排行榜**：查看累计战绩与胜率曲线
- **回放**：按历史会话回放对局

## 5.2 顶部控制按钮（游戏页）

这些按钮由前端调用后端 API 控制：

- **开始游戏**
  - 调用：`POST /api/start`
  - 作用：发送“开始”指令，进入当前局流程

- **继续**
  - 调用：`POST /api/continue`
  - 作用：在暂停态下催促 god 继续本局

- **自动N局**（旁边输入框填局数）
  - 调用：`POST /api/auto-run`
  - 作用：开启/关闭连续自动跑局
  - 例如输入 `3` 后点击，可自动连续完成 3 局

- **停止游戏**
  - 调用：`POST /api/stop`
  - 作用：停止当前路由流程并关闭自动连跑

- **初始化游戏 / 重新开始**
  - 调用：`POST /api/restart`
  - 作用：强制新建 session 并重新初始化新局

## 5.3 模式选择（全AI / 真人参与）

顶部“模式”下拉框会影响 `start/restart` 请求中的 `game_mode`：
- `all_agents`：全 AI 玩家
- `human_player`：保留一个真人席位（human）

## 5.4 真人参与模式下的按钮

当模式为 `human_player` 时，会显示“真实玩家”区域：

- **只发给 god**
  - 调用：`POST /api/human/send`，`target=god`
- **发给全员**
  - 调用：`POST /api/human/send`，`target=all`
- **查看 GAME.md**
  - 调用：`GET /api/human/game-md`

按钮是否可点，取决于后端状态 `waiting_for_human`。

## 5.5 智能按钮显示（smart buttons）

当以 `--smart-buttons` 启动时，前端会根据 `GET /api/status` 返回的状态动态调整按钮可见性，例如：
- 游戏进行中隐藏“开始/继续”
- 游戏结束后显示“重新开始”

## 6. 常见问题

- **点击开始/继续没反应**
  - 先检查后端是否在线：`/api/status`
  - 再检查 `vikingbot_url` 是否可访问 `/bot/v1/health`

- **真人模式看不到输入区**
  - 确认模式选择为 `human_player`，并用该模式执行了开始或重启

- **回放内容不完整**
  - 回放依赖会话记录与归档状态文件，建议让一局正常结束后再查看
