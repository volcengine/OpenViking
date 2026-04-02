#!/usr/bin/env python3
"""Simple test for opencode-ai SDK"""

import json
import os
import subprocess
import sys
import traceback
import time

from opencode_ai import Opencode


def execute_cmd(cmd):
    try:
        # 同时捕获stdout和stderr，排查输出位置
        result = subprocess.run(
            cmd,
            shell=True,
            text=True,
            encoding="utf-8",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,  # 捕获错误输出
            check=True,  # 等价于check_output的行为，命令失败会抛异常
        )
        stdout = result.stdout.strip()  # 去除空白符（换行/空格）
        stderr = result.stderr.strip()
        return stdout
    except subprocess.CalledProcessError as e:
        # 捕获命令执行失败的异常（返回码非0）
        print(f"命令执行失败：{e}")
        return None
    except Exception as e:
        # 捕获其他异常（如命令不存在、超时等）
        print(f"执行异常：{str(e)}")
        return None


def start_opencode():
    """子进程函数：启动opencode serve并完全脱离子进程控制"""
    pid = None
    try:
        if sys.platform == "win32":
            # Windows：使用CREATE_NEW_PROCESS_GROUP创建独立进程组，detach脱离父进程
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
            proc = subprocess.Popen(
                ["opencode", "serve"],
                shell=False,
                creationflags=creationflags,
                stdout=subprocess.DEVNULL,  # 重定向输出避免控制台关联
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
            )
            pid = proc.pid
        else:
            # Linux/macOS：使用os.setsid创建新会话，完全脱离控制终端
            # 先fork一次，再启动进程，确保脱离所有父进程关联
            cmd = ["opencode", "serve"]
            # 创建新会话 + 重定向所有输出
            proc = subprocess.Popen(
                cmd,
                preexec_fn=os.setsid,  # 关键：创建新的会话ID
                stdout=open("opencode.log", "a"),
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
            )
            pid = proc.pid

        print(f"opencode serve已启动，PID: {pid}")
        # 短暂等待确保进程启动成功
        time.sleep(2)
        return pid
    except Exception as e:
        print(f"启动opencode失败: {e}")
        traceback.print_exc()
        return None


def check_serve_status():
    """测试opencode连接，失败则启动服务并重试"""
    try:
        client = Opencode(base_url="http://127.0.0.1:4096")
        modes = client.app.modes()
    except Exception as e:
        print(f"连接opencode失败，错误: {e}")
        # 启动服务
        pid = start_opencode()
        if pid:
            # 启动后重试连接
            time.sleep(3)


def read_new_messages(client, session_id, last_ts):
    """
    读取上一次之后的消息， 通过client.session.messages实现，注意
    """
    messages = client.session.messages(id=session_id, extra_query={"limit": 10})
    next_ts = last_ts
    new_messages = []
    has_finished = False

    for message in messages:
        created_time = 0
        if hasattr(message, "info") and message.info:
            if hasattr(message.info, "time") and message.info.time:
                if hasattr(message.info.time, "created"):
                    created_time = message.info.time.created

        if created_time > last_ts:
            new_messages.append(message)
            if created_time > next_ts:
                next_ts = created_time

    if messages:
        last_message = messages[-1]
        if last_message.parts:
            for part in last_message.parts:
                if hasattr(part, "type") and part.type == "step-finish":
                    has_finished = True

    status = "finished" if has_finished else "running"
    return status, new_messages, next_ts


file_path = "status.json"


def read_status():
    # 检查文件是否存在
    if not os.path.exists(file_path):
        return {}
    # 读取并解析JSON文件
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data
    except Exception as e:
        # 捕获其他未知异常
        print(f"读取 {file_path} 时发生错误：{e}")
        return {}


def write_status(status):
    try:
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(json.dumps(status))
    except Exception as e:
        # 捕获其他未知异常
        print(f"写入 {file_path} 时发生错误：{e}")


def list_project(client):
    import httpx

    http_client = httpx.Client(base_url="http://127.0.0.1:4096")
    response = http_client.get("/project")
    projects = response.json()
    project_list = []
    for p in projects:
        project_list.append({"id": p.get("id"), "path": p.get("worktree")})
    return project_list
