---
name: github-proxy
description: GitHub 国内访问加速 skill，使用 githubproxy.cc 代理加速 GitHub 仓库克隆、文件下载、Raw 文件访问等操作。使用场景：(1) 需要 git clone GitHub 仓库时加速，(2) 下载 GitHub Release 文件、Raw 文件、Archive 压缩包时加速，(3) 任何需要访问 GitHub 资源但速度慢的场景
---

# GitHub 国内代理加速 Skill

使用 githubproxy.cc 代理服务，为国内访问 GitHub 提供加速支持。

## 代理服务

当前使用的代理服务：
- **主要服务**: githubproxy.cc (测试有效，加速约 3 倍)
- **备用服务**: ghfast.top

## 使用方法

### 1. Git Clone 加速

将 GitHub 仓库链接前加上 `https://githubproxy.cc/` 前缀：

```bash
# 原始链接
git clone https://github.com/username/repo.git

# 加速链接
git clone https://githubproxy.cc/https://github.com/username/repo.git
```

### 2. 文件下载加速

支持以下类型的 GitHub 资源加速：

- **Raw 文件**: `https://raw.githubusercontent.com/...`
- **Release 文件**: 项目发布的附件
- **Archive 压缩包**: 仓库打包下载
- **Gist 文件**: `gist.github.com` 或 `gist.githubusercontent.com`

```bash
# 原始链接
wget https://raw.githubusercontent.com/username/repo/main/file.txt

# 加速链接
wget https://githubproxy.cc/https://raw.githubusercontent.com/username/repo/main/file.txt
```

### 3. 使用辅助脚本

使用 `scripts/convert_url.py` 自动转换 GitHub 链接：

```bash
python scripts/convert_url.py "https://github.com/username/repo.git"
```

## 链接转换规则

| 原始链接格式 | 转换后格式 |
|-------------|-----------|
| `https://github.com/username/repo.git` | `https://githubproxy.cc/https://github.com/username/repo.git` |
| `https://raw.githubusercontent.com/...` | `https://githubproxy.cc/https://raw.githubusercontent.com/...` |
| `https://github.com/.../releases/download/...` | `https://githubproxy.cc/https://github.com/.../releases/download/...` |
| `https://github.com/.../archive/...` | `https://githubproxy.cc/https://github.com/.../archive/...` |

## 注意事项

- 本服务仅供学习研究使用，请勿滥用
- 如果 githubproxy.cc 不可用，请尝试备用服务 ghfast.top
- 不支持 SSH Key 方式的 git clone
- Push、PR、Issue 等操作建议直接使用官方 GitHub 地址
