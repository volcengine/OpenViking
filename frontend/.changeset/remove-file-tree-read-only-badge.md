---
'@openviking/react': patch
---

## en-US

Remove permission badges from FileTree and FileBrowser rows. The ACL tag in the preview header and permission controls are unchanged. Remove the unused `FileTreeLabels.readOnly`, `noAccess` and `traverseOnly` fields; hosts that supplied them should remove those overrides.

## zh-CN

移除 FileTree 和 FileBrowser 行尾的权限标识，保留预览标题区的 ACL 标签和原有权限控制。移除不再使用的 `FileTreeLabels.readOnly`、`noAccess` 和 `traverseOnly` 字段，传入这些字段的宿主需删除对应文案覆盖。
