---
'@openviking/react': patch
---

## en-US

Fix `openResource(uri)` to automatically expand directory targets and refresh their loaded children. Support root navigation without a synthetic tree row, preserve loaded pages and expanded branches during refresh, and retain cached children with the existing retry action when refresh fails. `{ reveal: false }` continues to open previews without changing the tree.

## zh-CN

修复 `openResource(uri)` 定位目录时未展开目标的问题，定位时自动展开目标目录并刷新已加载的子项。支持根目录定位，无需添加虚拟树行；刷新期间保留已加载分页和子分支展开状态，刷新失败时保留缓存并使用现有重试操作。`{ reveal: false }` 继续只打开预览，不改变目录树。
