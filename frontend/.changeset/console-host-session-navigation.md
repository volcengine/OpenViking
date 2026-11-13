---
'@openviking/react': patch
---

## en-US

Keep Console's internally managed conversation active when the host reflects a newly created session in its URL, and support host-provided session navigation and metadata. Prevent cancelled requests from restoring an old draft after navigation. Notify the host once for each explicit history selection, including the current session, and for each explicit New action even before a remote session exists.

## zh-CN

宿主把新建会话 ID 同步到 URL 时，保持 Console 内部管理的对话继续运行，并支持宿主提供会话导航和元数据。取消请求后切换会话，不再恢复旧草稿。每次显式选择历史会话（包括当前会话）或新建会话时均通知宿主一次，新建会话尚未在远端创建时也会通知。
