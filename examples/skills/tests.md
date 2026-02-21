# Memory Skill Test Cases

## T1: Profile — Basic Identity
**Conversation:**
1. `我叫张明，在字节跳动做后端开发，base北京`
2. `ovm`

**Eval search:** "张明是谁"
**Expected memory:**
- `profile`: name=张明, role=后端开发, company=字节跳动, location=北京

---

## T2: Profile — Incremental Update (Merge)
**Conversation:**
1. `我叫张明，做后端开发`
2. `ovm`
3. `最近转岗了，现在做 infra`
4. `ovm`

**Eval search:** "张明做什么工作"
**Expected memory:**
- `profile`: role updated to infra (merged, not duplicated)

---

## T3: Preferences
**Conversation:**
1. `写代码的时候我习惯用 vim + tmux，不喜欢 IDE`
2. `回复我的时候用中文就好，技术术语保持英文`
3. `ovm`

**Eval search:** "用户开发工具偏好"
**Expected memories:**
- `preferences`: editor=vim+tmux, anti-preference=IDE
- `preferences`: language=中文为主, technical terms in English

---

## T4: Entities — People & Projects
**Conversation:**
1. `我们组的 tech lead 是 Kevin，他主推用 Go 重写网关`
2. `目前在做 Project Atlas，是一个内部 API 网关平台`
3. `ovm`

**Eval search:** "Kevin" / "Project Atlas"
**Expected memories:**
- `entities`: person Kevin, role=tech lead, advocates Go rewrite
- `entities`: project Atlas, type=内部API网关平台

---

## T5: Events — Decision Point
**Conversation:**
1. `今天和老板聊了，决定放弃 Python 方案，全面转 Go`
2. `主要原因是性能瓶颈和团队技术栈统一`
3. `ovm`

**Eval search:** "为什么选 Go"
**Expected memory:**
- `events`: decision=弃Python转Go, reason=性能+技术栈统一, date=today

---

## T6: Cases — Problem → Solution
**Conversation:**
1. `我们的 gRPC 服务偶尔出现 deadline exceeded，大概每天几十次`
2. `查了 trace 发现是下游 Redis 偶尔 latency spike`
3. `试了连接池调大没用，最后发现是 Redis cluster 有个慢节点`
4. `把那个节点摘掉换了新实例就好了`
5. `ovm`

**Eval search:** "gRPC deadline exceeded 怎么解决"
**Expected memory:**
- `cases`: problem=gRPC deadline exceeded, root_cause=Redis慢节点, solution=替换节点, investigation_path=[trace→Redis latency→连接池排除→慢节点定位]

---

## T7: Patterns — Reusable Practice
**Conversation:**
1. `我发现做 code review 有个好办法`
2. `先看测试理解意图，再看 diff，最后跑一遍确认`
3. `这样比直接看 diff 效率高很多，漏的也少`
4. `ovm`

**Eval search:** "code review 方法"
**Expected memory:**
- `patterns`: code review流程=测试→diff→运行验证, benefit=高效+低遗漏

---

## T8: Patterns — Merge Existing
**Conversation (session A):**
1. `部署前一定要先跑 smoke test`
2. `ovm`

**Conversation (session B):**
1. `部署前除了 smoke test，还要检查 config diff`
2. `ovm`

**Eval search:** "部署前检查"
**Expected memory:**
- `patterns`: pre-deploy checklist=[smoke test, config diff] (merged)

---

## T9: Complex Multi-Round — Mixed Categories
**Conversation:**
1. `我在做一个 RAG 系统的 chunk 策略优化`
2. `现在用的是固定 512 token 切分，效果不好`
3. `我试了 semantic chunking，用 embedding similarity 找分割点`
4. `同事 Lisa 建议试 late chunking，她在另一个项目上效果不错`
5. `最后我们决定用 semantic chunking + overlap 50 token 的方案`
6. `关键 insight 是：chunk boundary 要对齐语义边界，不能硬切`
7. `以后做 RAG 都应该先评估 chunk 质量再调 retrieval`
8. `ovm`

**Eval search:** "RAG chunking 怎么做" / "Lisa" / "chunk 优化经验"
**Expected memories:**
- `events`: decision=semantic chunking + 50 token overlap, rejected=固定512切分
- `entities`: person Lisa, context=推荐late chunking
- `cases`: problem=固定chunk效果差, solution=semantic chunking+overlap
- `patterns`: RAG best practice=先评估chunk质量再调retrieval, chunk应对齐语义边界

---

## T10: Noise Resistance — Should NOT Store
**Conversation:**
1. `今天天气不错`
2. `帮我写个 hello world`
3. `谢谢，挺好的`
4. `ovm`

**Eval search:** "天气" / "hello world"
**Expected memory:** **None** (no meaningful memory to extract)

---

## Coverage Matrix

| Test | profile | preferences | entities | events | cases | patterns | merge |
|------|---------|-------------|----------|--------|-------|----------|-------|
| T1   | x       |             |          |        |       |          |       |
| T2   | x       |             |          |        |       |          | x     |
| T3   |         | x           |          |        |       |          |       |
| T4   |         |             | x        |        |       |          |       |
| T5   |         |             |          | x      |       |          |       |
| T6   |         |             |          |        | x     |          |       |
| T7   |         |             |          |        |       | x        |       |
| T8   |         |             |          |        |       | x        | x     |
| T9   |         |             | x        | x      | x     | x        |       |
| T10  |         |             |          |        |       |          | noise |

**Key properties tested:**
- Single-category extraction (T1-T7)
- Merge behavior on mergeable categories (T2, T8)
- Multi-category extraction from one conversation (T9)
- Noise resistance — no spurious memories (T10)
- Non-mergeable uniqueness — events/cases create new entries, not merge (T5, T6)

