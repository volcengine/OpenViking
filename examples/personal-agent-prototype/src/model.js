export const STORAGE_KEY = "personal-agent-prototype-v1";
export const oldPreference = "只纳入最近半年发布的新产品。";
export const suggestedPreference =
  "优先最近两个月活跃的产品；老产品有近期更新仍可纳入。";
const source = (name, excerpt) => ({
  name,
  excerpt,
  date: "2026-11-03 09:00",
  label: "模拟来源快照",
});
function patches(goal, preference = oldPreference) {
  const flexible = preference !== oldPreference;
  return [
    {
      id: "scope",
      title: "补充持续委托的授权边界",
      kind: "补充",
      section: "执行与授权",
      before: goal.document.scope || "Agent 在授权范围内持续跟踪研究主题。",
      after:
        "持续研究仅更新用户指定的文档章节。每次更新先展示差异，由用户审阅后应用；授权到期或撤销后停止写入。",
      reason: "让委托的范围、期限和退出条件清晰可见。",
      source: source(
        "授权机制 · 示例摘录",
        "持续委托需要明确目标资源、允许动作及有效期。",
      ),
      status: "pending",
      selected: true,
    },
    {
      id: "memory",
      title: "增加记忆纠错与可追溯性",
      kind: "新增",
      section: "长期记忆",
      before: goal.document.memory || "个人助理应能在不同会话中记住用户偏好。",
      after:
        "每条偏好保留来源及适用范围。用户可纠正或停用记忆；更改后重算受影响的草稿，不直接修改已发布正文。",
      reason: "把“记得住”进一步落实为“看得见、改得动”。",
      source: source(
        "OpenViking · 会话与记忆示例",
        "会话归档与长期记忆处理分阶段完成，可检查记忆变更。",
      ),
      status: "pending",
      selected: true,
    },
    {
      id: "range",
      title: flexible
        ? "重新纳入近期仍活跃的产品"
        : "筛选口径可能遗漏持续活跃的产品",
      kind: flexible ? "修订" : "待判断",
      section: "产品筛选",
      before: goal.document.range || "按产品首次发布日期筛选研究样本。",
      after: flexible
        ? `本次筛选采用：${preference} 同时记录首次发布时间与最近一次实质更新，分别比较。`
        : "仅纳入最近半年首次发布的产品；更早发布的产品暂不进入主表。",
      reason: flexible
        ? "已按纠正后的偏好重新整理筛选口径。"
        : "这条建议采用了“只看新产品”的旧偏好，可能误排除仍活跃的项目。",
      source: source(
        "研究范围 · 示例记录",
        "产品首次发布和近期维护活跃是两种不同的筛选维度。",
      ),
      status: "pending",
      selected: false,
      memoryId: "range",
    },
  ];
}
const baseGoal = (id, title, section) => ({
  id,
  title,
  section,
  docTitle: "Personal Agent 产品研究",
  sources: "产品官网、GitHub 官方仓库、指定飞书文档",
  frequency: "每周一 09:00",
  expiry: "2026-12-03",
  budget: "20",
  paused: false,
  grant: true,
  version: 12,
  baseVersion: 12,
  document: {
    scope: "Agent 在授权范围内持续跟踪研究主题。",
    memory: "个人助理应能在不同会话中记住用户偏好。",
    range: "按产品首次发布日期筛选研究样本。",
  },
  patches: [],
  stale: false,
  running: false,
  activities: [
    {
      id: "initial",
      text: "已完成本轮研究，生成 3 条待审阅建议",
      time: "09:12",
    },
  ],
});
export function initialState() {
  const g = baseGoal(
    "personal",
    "Personal Agent 产品研究",
    "产品机制与技术内核",
  );
  g.patches = patches(g);
  const g2 = baseGoal("context", "上下文工程观察", "记忆与检索");
  g2.docTitle = "上下文工程周报";
  g2.patches = [];
  return {
    schema: 1,
    goals: [g, g2],
    memories: [
      {
        id: "range",
        title: "产品时间范围",
        text: oldPreference,
        scope: "personal",
        status: "active",
        type: "用户确认",
        origin: "研究委托 · 10 月 27 日",
        quote: "本周先只看最近半年发布的产品。",
        sync: "done",
        revision: 1,
      },
      {
        id: "evidence",
        title: "证据与引用",
        text: "优先使用官方资料，关键结论保留引用。",
        scope: "all",
        status: "active",
        type: "用户确认",
        origin: "首次设置 · 10 月 26 日",
        quote: "给我可以追溯到原文的结论。",
        sync: "done",
        revision: 1,
      },
      {
        id: "concise",
        title: "输出长度",
        text: "少写说明，多用对比表和图。",
        scope: "all",
        status: "active",
        type: "自动提取",
        origin: "文档反馈 · 11 月 3 日",
        quote: "文字太多了，加一些图表。",
        sync: "done",
        revision: 1,
      },
    ],
    tombstones: [],
    revision: 0,
  };
}
export function effectivePreference(state, goalId) {
  return (
    state.memories.find(
      (m) =>
        m.id === "range" &&
        m.status === "active" &&
        (m.scope === "all" || m.scope === goalId),
    )?.text || "依据近期活跃情况筛选，遇到不明确的范围先向用户确认。"
  );
}
const log = (g, text) =>
  g.activities.unshift({
    id: globalThis.crypto?.randomUUID?.() || String(Date.now() + Math.random()),
    text,
    time: new Date().toLocaleTimeString("zh-CN", {
      hour: "2-digit",
      minute: "2-digit",
    }),
  });
export function reduceState(state, action) {
  const s = structuredClone(state),
    g = s.goals.find((x) => x.id === action.goalId);
  switch (action.type) {
    case "SELECT":
      if (g) g.patches.find((p) => p.id === action.id).selected = action.value;
      break;
    case "EDIT_PATCH":
      if (g) {
        let p = g.patches.find((p) => p.id === action.id);
        p.after = action.text;
        log(g, "编辑了建议内容，待重新审阅");
      }
      break;
    case "DEFER":
      if (g) {
        const p = g.patches.find((p) => p.id === action.id);
        p.selected = false;
        p.status = action.reject ? "rejected" : "deferred";
        log(g, action.reject ? "本次不采用一条建议" : "暂缓一条建议");
      }
      break;
    case "RESTORE":
      if (g) {
        let p = g.patches.find((p) => p.id === action.id);
        p.status = "pending";
        p.selected = true;
      }
      break;
    case "PUBLISH": {
      if (
        !g ||
        !g.grant ||
        g.expiry < new Date().toISOString().slice(0, 10) ||
        g.stale ||
        g.running ||
        g.baseVersion !== g.version ||
        action.revision !== state.revision
      )
        return state;
      const chosen = g.patches.filter(
        (p) => p.selected && p.status === "pending",
      );
      if (!chosen.length) return state;
      chosen.forEach((p) => {
        g.document[p.id] = p.after;
        p.status = "published";
        p.selected = false;
      });
      g.version++;
      g.baseVersion = g.version;
      log(
        g,
        `已更新本地文档 v${g.version} · ${chosen.length} 条，读回核验完成`,
      );
      break;
    }
    case "CONFLICT":
      if (g) {
        g.version++;
        g.document.scope += " 文档所有者补充了现有说明。";
        log(g, "文档被其他协作者编辑，需刷新差异");
      }
      break;
    case "RUN":
      if (g && !g.running) {
        g.running = true;
        g.finishAt = Date.now() + 1600;
        log(g, action.reason || "开始核查来源并整理草稿");
      }
      break;
    case "CANCEL":
      if (g) {
        g.running = false;
        delete g.finishAt;
        log(g, "已取消本次研究，后续计划保持不变");
      }
      break;
    case "PAUSE":
      if (g) {
        g.paused = !g.paused;
        log(g, g.paused ? "已暂停后续计划" : "已恢复后续计划");
      }
      break;
    case "TICK": {
      let changed = false;
      for (const x of s.goals)
        if (x.running && x.finishAt <= action.now) {
          const made = patches(x, effectivePreference(s, x.id));
          x.patches = made.map((p) => ({
            ...p,
            before: x.document[p.id] || "",
            status: x.document[p.id] === p.after ? "unchanged" : "pending",
            selected: x.document[p.id] !== p.after && p.selected,
          }));
          x.running = false;
          delete x.finishAt;
          x.stale = false;
          x.baseVersion = x.version;
          log(
            x,
            x.patches.some((p) => p.status === "pending")
              ? "草稿已重算，请重新审阅差异"
              : "核查完成，本轮没有新的变化",
          );
          changed = true;
        }
      for (const m of s.memories)
        if (m.sync === "pending" && m.syncAt <= action.now) {
          m.sync = "done";
          delete m.syncAt;
          changed = true;
        }
      if (!changed) return state;
      break;
    }
    case "SAVE_MEMORY": {
      const m = s.memories.find((x) => x.id === action.id);
      if (!m || !action.text.trim()) return state;
      const previousScope = m.scope;
      m.text = action.text.trim();
      m.scope = action.scope;
      m.type = "用户确认";
      m.status = "active";
      m.revision++;
      m.sync = "pending";
      m.syncAt = Date.now() + 2600;
      s.goals.forEach((x) => {
        if (
          [previousScope, m.scope].some((sc) => sc === "all" || sc === x.id)
        ) {
          x.stale = true;
          log(x, `偏好“${m.title}”已纠正，草稿待重算`);
        }
      });
      break;
    }
    case "MEMORY_STATUS": {
      const m = s.memories.find((x) => x.id === action.id);
      if (!m) return state;
      s.goals.forEach((x) => {
        if (m.scope === "all" || m.scope === x.id) {
          x.stale = true;
          log(
            x,
            `偏好“${m.title}”已${action.status === "active" ? "启用" : action.status === "deleted" ? "删除" : "停用"}，草稿待重算`,
          );
        }
      });
      if (action.status === "deleted") {
        s.tombstones.push({
          id: m.id,
          scope: m.scope,
          date: new Date().toISOString(),
        });
        s.memories = s.memories.filter((x) => x.id !== m.id);
      } else m.status = action.status;
      break;
    }
    case "IGNORE_ONCE":
      if (g) {
        let p = g.patches.find((p) => p.id === "range");
        if (p && p.status !== "published") {
          p.after =
            "本次不使用时间偏好，保留全部候选并标记活跃时间，等待用户选择。";
          p.reason = "只对本次建议生效，长期记忆未改变。";
          p.status = "pending";
          p.selected = true;
        }
        log(g, "本次不使用产品时间偏好");
      }
      break;
    case "GRANT":
      if (g) {
        g.grant = action.value;
        log(
          g,
          action.value ? "已授予指定文档的逐次审批权限" : "已撤销文档写入权限",
        );
      }
      break;
    case "SETTINGS":
      if (g) {
        Object.assign(g, action.values);
        log(g, "已保存委托设置");
      }
      break;
    case "CREATE": {
      const id = globalThis.crypto.randomUUID();
      const n = baseGoal(id, action.values.title, action.values.section);
      Object.assign(n, action.values);
      n.docTitle = action.values.docTitle;
      n.document = {};
      n.version = 1;
      n.baseVersion = 1;
      n.activities = [];
      n.running = true;
      n.finishAt = Date.now() + 1500;
      s.goals.unshift(n);
      log(n, "委托已创建，正在生成首版草稿");
      break;
    }
    default:
      return state;
  }
  s.revision++;
  return s;
}
