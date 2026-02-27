"""ov 命令的极简 Python 包装器

设计原则：
1. 职责单一：仅负责查找二进制并 execv
2. 无网络依赖：不实现下载功能
3. 极简代码：尽可能减少启动开销
4. 快速失败：找不到立即提示用户

性能说明：
- Python 虚拟机启动 + 导入基础模块：约 30-50ms
- 一旦 execv 执行，后续为纯 Rust 二进制，零开销

Rust CLI 独立发布能力完全保留，用户可通过以下方式获取：
- 官方安装脚本（零开销）
- GitHub Releases 手动下载（零开销）
- cargo install（零开销）
- 包管理器（未来）
"""

import os
import sys
from pathlib import Path
from shutil import which


def main():
    """
    极简入口点：查找 ov 二进制并执行

    按优先级查找：
    1. Wheel 自带：{package_dir}/openviking/bin/ov
    2. PATH 查找：系统全局安装的 ov
    """
    # 1. 检查 Wheel 自带（不导入 openviking，避免额外开销）
    try:
        # __file__ is openviking_cli/rust_cli.py, so parent is openviking_cli directory
        package_dir = Path(__file__).parent.parent / "openviking"
        package_bin = package_dir / "bin"
        for binary_name in ["ov", "ov.exe"]:
            binary = package_bin / binary_name
            if binary.exists() and os.access(binary, os.X_OK):
                # 找到后立即 execv，不返回
                args = [str(binary)] + sys.argv[1:]
                os.execv(str(binary), args)
    except Exception:
        pass

    # 2. 检查 PATH，但跳过当前 Python 脚本
    path_binary = which("ov")
    if path_binary:
        # 检查文件是否是 Python 脚本（避免无限循环）
        try:
            candidate_path = Path(path_binary).resolve()
            with open(candidate_path, "rb") as f:
                first_bytes = f.read(2)
            # Skip if it starts with #! (shebang, likely Python script)
            if first_bytes != b"#!":
                args = [path_binary] + sys.argv[1:]
                os.execv(path_binary, args)
        except Exception:
            pass

    # 都找不到，提示用户
    print(
        """错误: 未找到 ov 二进制文件。

        请选择以下方式之一安装：

        1. 使用预构建 wheel（推荐）：
   pip install openviking

        2. 使用官方安装脚本（零 Python 开销）：
   curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/crates/ov_cli/install.sh | bash

        3. 从 GitHub Releases 下载（零 Python 开销）：
   https://github.com/volcengine/OpenViking/releases

        4. 从源码构建（零 Python 开销）：
   cargo install --git https://github.com/volcengine/OpenViking ov_cli""",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
