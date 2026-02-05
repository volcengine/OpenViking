#!/usr/bin/env python3
# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Constants for CodeRepositoryParser.

This file contains all constant definitions used by CodeRepositoryParser
to keep the main code file clean and focused on logic.
"""

# Directories to ignore in code repositories
IGNORE_DIRS = {
    ".git",
    ".svn",
    ".hg",
    ".idea",
    ".vscode",
    "__pycache__",
    "node_modules",
    "venv",
    ".venv",
    "env",
    ".env",
    "dist",
    "build",
    "target",
    "bin",
    "obj",
    ".DS_Store",
}

# Extensions to ignore (binary, huge files, and non-text content)
IGNORE_EXTENSIONS = {
    # Binary/compiled files
    ".pyc",
    ".pyo",
    ".pyd",
    ".so",
    ".dll",
    ".dylib",
    ".exe",
    ".bin",
    ".iso",
    ".img",
    ".db",
    ".sqlitive",
    # Archive formats
    ".zip",
    ".tar",
    ".gz",
    ".rar",
    ".7z",
    # Image formats (explicitly mentioned as exception in README.md)
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".bmp",
    ".ico",
    # Document formats
    ".pdf",
    ".doc",
    ".docx",
    ".ppt",
    ".pptx",
    ".xls",
    ".xlsx",
    # Java compiled files
    ".class",
    ".jar",
    ".war",
    ".ear",
    # Video formats (non-text content per README.md requirements)
    ".mp4",
    ".mov",
    ".avi",
    ".webm",
    ".mkv",
    ".flv",
    ".wmv",
    ".mpg",
    ".mpeg",
    # Audio formats (non-text content per README.md requirements)
    ".mp3",
    ".wav",
    ".m4a",
    ".flac",
    ".aac",
    ".ogg",
    ".wma",
    ".mid",
    ".midi",
}

# Code file extensions for file type detection
CODE_EXTENSIONS = {
    ".py",
    ".java",
    ".cpp",
    ".c",
    ".h",
    ".hpp",
    ".cs",
    ".js",
    ".ts",
    ".jsx",
    ".tsx",
    ".go",
    ".rs",
    ".rb",
    ".php",
    ".swift",
    ".kt",
    ".scala",
    ".m",
    ".hs",
    ".lua",
    ".pl",
    ".r",
    ".sql",
    ".sh",
    ".bash",
    ".zsh",
    ".fish",
    ".ps1",
    ".bat",
    ".cmd",
    ".yml",
    ".yaml",
    ".toml",
    ".json",
    ".xml",
    ".html",
    ".htm",
    ".css",
    ".scss",
    ".less",
    ".sass",
    ".vue",
    ".svelte",
    ".elm",
    ".clj",
    ".cljs",
    ".edn",
    ".ex",
    ".exs",
    ".erl",
    ".hrl",
    ".fs",
    ".fsx",
    ".fsi",
    ".dart",
    ".groovy",
    ".gradle",
    ".julia",
    ".nim",
    ".odin",
    ".zig",
    ".v",
    ".sv",
    ".vhd",
    ".vhdl",
    ".tex",
    ".bib",
    ".asm",
    ".s",
    ".inc",
    ".make",
    ".mk",
    ".cmake",
    ".proto",
    ".thrift",
    ".avdl",
    ".graphql",
    ".gql",
    ".prisma",
}

# Documentation file extensions for file type detection
DOCUMENTATION_EXTENSIONS = {
    ".md",
    ".markdown",
    ".mdown",
    ".mkd",
    ".txt",
    ".text",
    ".rst",
    ".adoc",
    ".asciidoc",
    ".org",
    ".texi",
    ".texinfo",
    ".wiki",
}

# File type constants for consistent return values
FILE_TYPE_CODE = "code"
FILE_TYPE_DOCUMENTATION = "documentation"
FILE_TYPE_OTHER = "other"
FILE_TYPE_BINARY = "binary"

# Text file extensions for encoding detection and conversion
# These are additional text file extensions not already in CODE_EXTENSIONS or DOCUMENTATION_EXTENSIONS
ADDITIONAL_TEXT_EXTENSIONS = {
    ".ini",
    ".cfg",
    ".conf",
    ".properties",
    ".toml",
    ".json",
    ".yaml",
    ".yml",
    ".xml",
    ".csv",
    ".tsv",
    ".log",
    ".gitignore",
    ".dockerignore",
    ".editorconfig",
    ".eslintrc",
    ".prettierrc",
    ".babelrc",
    ".npmrc",
    ".yarnrc",
    ".env",
    ".env.example",
}

# Common text encodings to try for encoding detection (in order of likelihood)
TEXT_ENCODINGS = [
    "utf-8",  # Most common modern encoding
    "utf-8-sig",  # UTF-8 with BOM
    "gbk",  # Chinese GBK (simplified Chinese)
    "gb2312",  # Chinese GB2312 (simplified Chinese)
    "big5",  # Traditional Chinese
    "shift_jis",  # Japanese
    "euc-kr",  # Korean
    "iso-8859-1",  # Latin-1 (Western European)
    "cp1252",  # Windows Latin-1
    "latin-1",  # Latin-1 alias
]

# UTF-8 variants that don't need conversion
UTF8_VARIANTS = {"utf-8", "utf-8-sig"}
