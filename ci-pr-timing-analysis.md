# OpenViking 最近已完成 PR CI 耗时分析

采样日期：2026-10-31。数据来自 `volcengine/OpenViking` GitHub Actions。

分支状态更新：PR #6806 的代码曾直接退回 `dbed5ef34`；之后仅补充本报告及配套数据。本报告中的 `fade53aaa` 两次构建属于历史对照。直接恢复提交后自动触发的 [PR Checks](https://github.com/volcengine/OpenViking/actions/runs/41167270251) 和 [API/CLI](https://github.com/volcengine/OpenViking/actions/runs/41167270236) 均已完成并通过，已计入完成样本。用户已决定暂缓合并。

拉取并汇总了 **24 组已完成工作流记录：11 组 PR Checks attempt、11 组 API/CLI 工作流、2 组 main 安全扫描工作流**，其中包含任务跳过及提前失败的记录。成功执行的同类任务用于耗时比较，失败或跳过任务不参与性能范围计算。另引用此前已取得的 Intel 单任务重跑结果。

下载的逐工作流、逐 job、逐 step 数据见 [ci-pr-timing-data.csv](ci-pr-timing-data.csv)，共 2,544 条数据记录，另有一行表头。

## 主要结论

1. **最近其他 PR 的约 22–24 分钟 PR Checks，主要是 Linux x86_64 wheel 的执行时间。它们跳过了 macOS、Windows 和 Linux ARM，无法直接与我们的完整五平台 CI 比较。**
2. 四个其他 PR 的 hosted Linux wheel 执行时间为 **20:24–21:41**，中位数约 **21:04**。本 PR 回滚后的自建 runner Linux wheel 为 **14:07**，比该样本中位数短约 **33%**。这是跨 PR、不同 runner 的观察值，不是同一机器的受控加速实验。
3. **API/CLI 工作流中的测试范围会变化。** 基础 API 路径对应的测试 job 约 **4:17–4:26**；包含 VLM/Embedding 的较完整测试路径对应其他后端 PR 的 job 为 **22:11–22:56**。本 PR 回滚后为 **23:09**，相差 13–58 秒。
4. 回滚恢复了 API native-runtime 的原有缓存行为，耗时从拆分版本的 **15:33** 回到 **7:23**，接近其他成功样本的 **5:39–7:12**。
5. **最近这些其他 PR 没有实际执行 Intel wheel，无法据此判断 Intel 的 28:40 → 41:33 是否是全仓库普遍现象。** Intel 的具体变慢原因仍未定位，不能把 runner 性能差异当作已经证明的根因。

## 计时口径

- **job 执行时间**：`completed_at - started_at`。
- **单 job 排队时间**：`started_at - created_at`，只对实际执行任务统计，排除 skipped 的异常时间戳。
- **工作流历时**：`run_started_at` 到该 attempt 最后一个 job 完成；包含工作流调度、依赖等待和 job 排队。
- 各 job 并行执行，所以不能把所有 job 耗时相加当作工作流历时。
- 固定读取明确的已完成 attempt，避免把后续重跑的更新时间误算成首次构建耗时。用户重跑 attempt 2 已完成，作为单独记录纳入。

成功样本的最长单 job 排队通常为 **3–14 秒**；约 20–40 分钟的大头来自执行过程。工作流启动到 job 创建之间的调度时间，以及等待前置 job 的时间，不等于上述单 job 排队时间。

## macOS/Windows 如何被跳过

这是已有的 PR 平台选择规则，本次自建 runner 分流没有修改它：

1. `pr.yml` 的 `check-deps` 调用 `.github/scripts/ci_plan.py`，按 PR 相对 base 的 merge-base diff 获取改动文件。
2. 只涉及 `web-studio/` 或 `crates/ov_cli/` 的 wheel 相关改动时，输出 `wheel_os=["ubuntu-24.04"]`。涉及 `src/`、`third_party/`、其他 Rust crates、打包文件或匹配到的 CI 配置时，输出五个平台。
3. `pr.yml` 将 `wheel_os` 传给 `_build.yml` 的 `os_json`。
4. `_build.yml` 的 `build-other` 仅在平台列表包含 `macos` 或 `windows` 时执行，否则 Actions 将该 job 标为 `skipped`。

源码位置：[选择规则](https://github.com/volcengine/OpenViking/blob/dbed5ef34/.github/scripts/ci_plan.py#L15-L45)、[传递平台参数](https://github.com/volcengine/OpenViking/blob/dbed5ef34/.github/workflows/pr.yml#L159-L182)、[非 Linux job 条件](https://github.com/volcengine/OpenViking/blob/dbed5ef34/.github/workflows/_build.yml#L435-L439)。

## 最近其他 PR 的构建时间

以下四个 PR 的 PR Checks 均只执行 Linux x86_64 wheel；macOS/Windows 和 Linux ARM 没有执行。

| PR 内容 / Actions 来源 | 工作流历时 | Linux x86_64 job | Rust CLI step | Wheel step | Linux smoke step |
| --- | ---: | ---: | ---: | ---: | ---: |
| [Studio 官方标识](https://github.com/volcengine/OpenViking/actions/runs/41160255259/attempts/1) | 24:22 | 21:39 | 3:33 | 10:50 | 3:22 |
| [Studio 连接恢复](https://github.com/volcengine/OpenViking/actions/runs/41157653509/attempts/1) | 23:21 | 20:24 | 3:29 | 10:15 | 2:51 |
| [#6829 memory compile](https://github.com/volcengine/OpenViking/actions/runs/41102319053/attempts/1) | 23:42 | 21:41 | 3:31 | 11:20 | 3:17 |
| [Studio admin/workbench 分离](https://github.com/volcengine/OpenViking/actions/runs/41048919795/attempts/1) | 22:23 | 20:30 | 3:22 | 10:24 | 3:07 |

最新 Linux 样本的完整日志确认 Cargo 和 C++ 缓存均未命中。其他三个样本这里使用 job/step 时间，未逐一下载日志核验缓存，因此不能将四个样本整体称为经过验证的同条件冷构建。

较短的 PR Checks 也不能当作完整 CI：

- [服务错误信息 PR #6816](https://github.com/volcengine/OpenViking/actions/runs/41043318291/attempts/1)：PR Checks **0:20**，只有 `check-deps` 实际执行，构建被跳过；其独立 API/CLI 工作流实际运行了 **28:14**。
- [onboarding 文档 PR](https://github.com/volcengine/OpenViking/actions/runs/41038877574/attempts/1)：PR Checks **0:23**，构建/测试被跳过；独立 API 工作流 **0:17**，仅执行 prepare。

## 本 PR 的完整平台构建对照

| 实际执行的 job | 原基线 dbed5ef34 | 缓存拆分 4f61b378 attempt 1 | 回滚 fade53aaa attempt 1 |
| --- | ---: | ---: | ---: |
| Linux x86_64 | 14:29 | 14:19 | 14:07 |
| Linux ARM | 15:35 | 15:59 | 15:12 |
| macOS ARM | 16:06 | 17:11 | 13:30 |
| Windows | 32:16 | 31:01 | 26:04 |
| macOS Intel | 28:40 | 44:20 | 41:33 |
| PR Checks 工作流历时 | 33:49 | 46:15 | 43:58 |

来源：[原基线](https://github.com/volcengine/OpenViking/actions/runs/41044252248/attempts/1)、[缓存拆分首次](https://github.com/volcengine/OpenViking/actions/runs/41053335011/attempts/1)、[回滚首次](https://github.com/volcengine/OpenViking/actions/runs/41157166403/attempts/1)。

原基线的工作流最长任务是 Windows；缓存拆分和回滚后的最长任务是 Intel。只有 Intel 持续高于原基线；回滚后的其他四个平台均低于原基线。

### Intel 的已完成阶段数据

| 阶段 | 原基线 | 拆分首次 | 拆分同提交重跑 | 回滚首次 |
| --- | ---: | ---: | ---: | ---: |
| CLI step | 6:22 | 12:42 | 13:50 | 11:58 |
| Wheel step | 16:22 | 23:27 | 21:01 | 20:52 |
| Smoke step | 4:40 | 6:28 | 5:43 | 6:32 |
| 整个 Intel job | 28:40 | 44:20 | 42:36 | 41:33 |

回滚首次相对原基线仍增加 **12:53（44.9%）**，相对拆分首次缩短 **2:47（6.3%）**，相对拆分重跑缩短 **1:03（2.5%）**。

原基线与回滚的 Rust/C++ 源码、Cargo manifests/lockfile、setup.py、pyproject.toml、workflows 和 actions 的 Git tree hashes 一致。完整日志还确认：

- 同一 Intel runner 镜像 `20260824.0482.1`、Rust `1.99.1` 和原有缓存指纹。
- 两次 Cargo/C++ 缓存均未命中。
- 两次 CLI 都编译相同的 329 个 crate/version，RAGFS 都是相同的 330 个；拆分版本额外编译的 15 个 RAGFS 依赖已消失。
- C++ 的并行度均为 `-j4`。

这些证据说明，回滚消除了编译输出拆分带来的额外依赖工作，却没有消除大部分 Intel 耗时差异。软件镜像相同不代表实际 CPU、磁盘或宿主机负载相同，但现有日志没有这些性能指标，**环境因素仍然只是待验证方向**。

## API/CLI 测试时间

测试 job 与 native-runtime 是前后依赖关系；两个阶段都影响 API 工作流历时。

| Actions 来源 | native-runtime job | API/CLI 测试 job | API step | CLI 兼容性 step | CLI 集成 step | 工作流历时 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| [Studio 官方标识](https://github.com/volcengine/OpenViking/actions/runs/41160255146/attempts/1) | 6:07 | 4:26 | 0:16 | 1:17 | 1:25 | 10:54 |
| [Studio 存储提示](https://github.com/volcengine/OpenViking/actions/runs/41162688204/attempts/1) | 6:18 | 4:17 | 0:14 | 1:17 | 1:26 | 11:01 |
| [服务错误信息 #6816](https://github.com/volcengine/OpenViking/actions/runs/41043318277/attempts/1) | 5:39 | 22:11 | 15:46 | 1:17 | 3:59 | 28:14 |
| [memory compile #6829](https://github.com/volcengine/OpenViking/actions/runs/41102319413/attempts/1) | 7:12 | 22:56 | 15:58 | 1:17 | 4:09 | 30:41 |
| [本 PR 原基线](https://github.com/volcengine/OpenViking/actions/runs/41044252276/attempts/1) | 7:51 | 24:43 | 17:41 | 1:17 | 4:27 | 32:56 |
| [本 PR 缓存拆分](https://github.com/volcengine/OpenViking/actions/runs/41053334450/attempts/1) | 15:33 | 25:46 | 18:22 | 1:17 | 4:48 | 41:45 |
| [本 PR 回滚](https://github.com/volcengine/OpenViking/actions/runs/41157166390/attempts/1) | 7:23 | 23:09 | 17:09 | 1:17 | 3:38 | 31:09 |

两份完整测试日志证明，同名 API step 并不一定执行同一测试范围：

- Studio 官方标识 PR 选择 `Running basic tests only`，API 两段分别 **14 passed in 7.45s**、**55 passed in 5.45s**，忽略资源操作、检索等部分用例。
- memory compile PR 选择 `Running full test suite with VLM/Embedding`。串行 filesystem/resource 段 **34 passed in 12:18**；后续并行段 **89 passed in 3:36**。完整 API step 共 **15:58**，串行资源段占主要部分。
- 两份日志中的 CLI 兼容性测试都是 16 个，CLI 集成测试都是 45 个，但集成测试执行时间不同；测试范围并不是所有时间差异的唯一解释。

[Studio 连接恢复的失败 API 工作流](https://github.com/volcengine/OpenViking/actions/runs/41157653563/attempts/1) 历时 **10:17**，测试 job 仅 **1:18**：API step 8 秒后失败，后续 CLI 测试被跳过。它不代表快速完成了整套测试。

## 后续排查重点

- 比较工作流历时之前，先匹配实际执行的平台、测试分支和测试数量，避免把跳过或基础路径当作完整验证。
- Intel 需要在后续受控运行记录硬件型号、CPU 数量/使用率、内存和磁盘指标；当前这些其他 PR 的样本不能证明或否定其环境层面的原因。
- 后端完整 API 路径的大头是串行资源操作段；优化测试耗时应先分析这部分，而非从约 4 分钟的基础路径推算整体测试速度。
- 用户发起的 `fade53aaa` attempt 2 已完成并通过，结果如下。

## 用户同提交重跑结果

[回滚版本 attempt 2](https://github.com/volcengine/OpenViking/actions/runs/41157166403/attempts/2) 全部 17 个 job 通过，同一 head `fade53aaa`、同一 merge checkout `29eb5db33`。完整 Intel 日志确认镜像仍为 `20260824.0482.1`，原有 Cargo/C++ 缓存均未命中。

| 项目 | 回滚首次 | 回滚重跑 |
| --- | ---: | ---: |
| Intel CLI step | 11:58 | 10:26 |
| Intel wheel step | 20:52 | 21:37 |
| Intel smoke step | 6:32 | 6:16 |
| 整个 Intel job | 41:33 | 40:36 |
| Windows job | 26:04 | 31:41 |
| macOS ARM job | 13:30 | 14:22 |
| Linux x86_64 job | 14:07 | 14:09 |
| Linux ARM job | 15:12 | 15:08 |
| PR Checks 工作流历时 | 43:58 | 42:56 |

Intel 重跑比回滚首次缩短 57 秒，但仍比原基线 28:40 增加 **11:56（41.6%）**。回滚后两次均约 40–41 分钟，持续偏慢的现象再次复现，具体原因仍未确定。

## Linux 8 核、32 GB runner 的扩核收益

用户确认自建 runner 为 8 核、32 GB。对照最新 hosted Linux 样本与回滚首次的完整日志，C++ 实际命令分别为 `-j4` 和 `-j8`；新增 CPU 已进入构建并行度，没有被固定限制为 4 个 C++ job。

| 阶段 | Hosted 样本 | 自建 8 核样本 | 时间比：hosted / 自建 |
| --- | ---: | ---: | ---: |
| CPython step | 1:35 | 0:50 | 1.90 倍 |
| Rust CLI 实际编译 | 3:31 | 2:17 | 1.54 倍 |
| RAGFS 实际编译 | 7:54 | 4:51 | 1.63 倍 |
| C++ 实际 build，不含 configure | 2:35 | 0:57 | 2.72 倍 |
| Wheel step，含 RAGFS/C++ 等 | 10:50 | 6:06 | 1.78 倍 |
| Linux smoke step | 3:22 | 2:24 | 1.40 倍 |
| 整个 Linux job | 21:39 | 14:07 | 1.53 倍 |

两个完整日志中 Cargo/C++ 缓存均未命中，编译指纹相同。源码通过 `os.cpu_count()` 选择 C++ job 数量；CPython 使用 `make -j"$(nproc)"`。

因此，C++ 阶段已超过两倍速度，但占比更大的 Rust 阶段只达到约 1.5–1.6 倍；安装、下载、测试和工作流其他开销也不会随 CPU 核数同比例减少。更多内存有助于避免内存不足，并不保证正常情况下的任务耗时减半。

Rust 的 release 配置开启 `lto = true`，RAGFS 设置 `codegen-units = 1`。这些配置以及 crate 依赖顺序、最终链接都可能限制扩核收益。现有日志没有 CPU 利用率和内存峰值，尚不能量化每项因素的贡献；下一步应重点测量 Rust 编译/链接的 CPU 利用率和各阶段时间。

这仍是不同 PR/runner 的对照，不能仅凭 4 核到 8 核就假设单核性能、磁盘和网络相同。要把 14:07 压到 hosted 样本的一半约 10:50，需要再减少约 3:17，重点在 Rust 和非编译开销。

## 直接恢复 dbed5ef34 后的完成结果

PR Checks 的 17 个 job 和 API/CLI 的 4 个 job 全部通过。最长单 job 排队分别为 6 秒和 3 秒，排队不是耗时大头。

| 项目 | 原 dbed5ef34 运行 | fade53aaa 回滚首次 | fade53aaa 重跑 | 直接恢复 dbed5ef34 新运行 |
| --- | ---: | ---: | ---: | ---: |
| Linux x86_64 job | 14:29 | 14:07 | 14:09 | 14:05 |
| Linux ARM job | 15:35 | 15:12 | 15:08 | 16:27 |
| macOS ARM job | 16:06 | 13:30 | 14:22 | 11:42 |
| Windows job | 32:16 | 26:04 | 31:41 | 25:24 |
| macOS Intel job | 28:40 | 41:33 | 40:36 | 39:10 |
| Intel CLI step | 6:22 | 11:58 | 10:26 | 11:17 |
| Intel wheel step | 16:22 | 20:52 | 21:37 | 19:59 |
| Intel smoke step | 4:40 | 6:32 | 6:16 | 5:41 |
| API native-runtime job | 7:51 | 7:23 | 未重跑 | 6:56 |
| API/CLI 测试 job | 24:43 | 23:09 | 未重跑 | 24:11 |
| PR Checks 工作流历时 | 33:49 | 43:58 | 42:56 | 41:03 |
| API 工作流历时 | 32:56 | 31:09 | 未重跑 | 31:28 |

新 Intel 任务比原提交的历史运行仍慢 **10:30（36.6%）**，比 `fade53aaa` 两次分别快 **2:23** 和 **1:26**。CLI 增加 4:55、wheel 增加 3:37、smoke 增加 1:01，主要差额并非排队。

实际 checkout 是 `afe5479ec`，即 `dbed5ef34` 与更新后的 main 合并；旧基线的 checkout 为 `a9f158dd3`。已通过 Git tree hashes 确认两者的 Rust/C++ 源码、第三方原生依赖、Cargo manifests/lockfile、setup.py 和 pyproject.toml 相同。新运行 `.github` 树与此前回滚测试相同，已核验 workflow/action 内容与旧基线一致；差异在 CI 脚本目录中的其他文件。

完整新 Intel 日志确认同一镜像 `20260824.0482.1`、Rust `1.99.1`、原有缓存指纹和缓存未命中；CLI 与 RAGFS 编译的 crate/version 集合仍分别为相同的 329、330 个。新 native-runtime 日志确认 Cargo/C++ 缓存恢复，符合其 6:56 的耗时。

结论：**直接将提交指针退回 dbed5ef34 也未恢复 Intel 的历史速度。** Linux x86_64 三次稳定在 14:05–14:09；API 工作流新结果 31:28 与原基线 32:56 接近。Intel 根因仍未确定，现有数据不能把全部差异归因于此前缓存改动。

## 最近 main 对照与合并建议

最近已完成的 main push [3dedecc0 的 Main Branch Checks](https://github.com/volcengine/OpenViking/actions/runs/41173934696) 仅包含 CodeQL C++/Python 安全扫描。已读取 `.github/workflows/ci.yml`，确认它不调用 wheel 构建。最近 main 直接 `_build.yml` 及 `release.yml` 历史运行距离当前已有数月，不适合作为近期 wheel 基线。

因此 main 的安全扫描总时间不能与本 PR 的五平台 wheel 总时间比较；**未找到近期 main 上同范围的 Intel wheel 对照**。

更贴近的对照是 [main 7aef6cbb 的 API 定时运行](https://github.com/volcengine/OpenViking/actions/runs/41167038823)，该提交正是本 PR 新测试 merge checkout 的 main 基线。

| 实际成功执行的相同任务 | main 7aef6cbb | 本 PR dbed5ef34 合并测试 | 差异 |
| --- | ---: | ---: | ---: |
| Ubuntu native-runtime job | 7:25 | 6:56 | 减少 0:29 |
| Ubuntu API/CLI 测试 job | 25:06 | 24:11 | 减少 0:55 |
| API step | 18:02 | 17:25 | 减少 0:37 |
| CLI 兼容性 step | 1:18 | 1:18 | 相同 |
| CLI 集成 step | 4:28 | 4:10 | 减少 0:18 |
| cuVS job | 0:49 | 0:55 | 增加 0:06 |

main 的完整 Ubuntu 日志确认测试数量为 34 + 89 个 API 用例、16 个 CLI 兼容性用例、45 个 CLI 集成用例，执行包含 VLM/Embedding 的路径。其整体工作流虽然失败，Ubuntu/API 和 macOS 任务成功，失败发生在 Windows 本地编译 RAGFS 的输出编码：`UnicodeEncodeError: 'charmap' codec can't encode character '\U0001f517'`。本 PR 的 Windows wheel job 通过，并不等于验证了这个不同的 Windows API 构建路径。

更新的 main API 运行 `41179045017` 查询时仍未完整结束，已有 Ubuntu/Windows 失败；未将其失败提前结束的耗时当作性能改善。

**最终决定：暂缓合并，保留 PR 及数据，后续再评估收益。** 本 PR 不能解决 Intel 变慢，也不能让整体五平台 CI 减半。

- 它将官方仓库的 Linux x86_64 PR/分支 wheel 放到自建 8 核 runner。实际三次为 14:05–14:09，其他近期 hosted PR 的相同 job 为 20:24–21:41，节省约 6–7 分钟，约 33% 时间。跨 PR 对照不能精确分离所有 runner/源码因素，但该方向的收益已稳定复现。
- main Ubuntu API 测试与本 PR 接近；本 PR 未修改该任务的 runner 或测试逻辑，没有证据说明它改善 API 测试性能。
- fork PR、发布/tag、手动和定时任务仍使用 hosted，因此并非所有贡献者及所有事件都能获得这项收益。
- 完整五平台 PR 仍由 Intel 的约 39 分钟决定，Linux 缩短不会同比例缩短其总等待时间；Studio/CLI 等 Linux-only 官方 PR 更直接受益。
- 自建 runner 需要运维。执行超时为 60 分钟，但没有自动排队回退；runner 离线时需要设置 `CI_DISABLE_SELF_HOSTED=true` 并重新触发任务。

用户确认 hosted runner 为 4 核、16 GB，自建 runner 为 8 核、32 GB。资源翻倍，但任务时间仅减少约 33%，吞吐约提升 1.5 倍。若每分钟机器成本也翻倍、每次只执行一个任务且按执行时间付费，单次构建成本约增加 33%；这是条件估算，不是实际账单结论。对于整机持续付费，还需考虑闲置时间与利用率。

因此，不应仅凭当前提速专门增加机器或急于合并。后续可以同提交对照测量 Rust 编译/链接，或评估并发任务吞吐和实际费用。现阶段保持 PR 开放，不执行合并。
