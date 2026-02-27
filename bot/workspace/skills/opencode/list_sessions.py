#!/usr/bin/env python3
"""Test listing OpenCode sessions"""

import json
import time
from opencode_ai import Opencode
from opencode_utils import (
    check_serve_status,
    execute_cmd,
    read_new_messages,
    read_status,
    write_status,
    list_project,
)
from pydantic import BaseModel

print("=" * 80)
print("Listing OpenCode Sessions")
print("=" * 80)

check_serve_status()


client = Opencode(base_url="http://127.0.0.1:4096")


class ViewSession(BaseModel):
    title: str
    session_id: str
    last_modify_time: str
    create_time: str
    directory: str
    status: str
    new_messages: str


try:
    session_notify_status = read_status()

    project_list = list_project(client)
    sessions = []
    for project in project_list:
        r = execute_cmd(f"cd {project['path']};opencode session list --format=json")
        if r is None:
            continue
        try:
            project_sessions = json.loads(r)
            sessions.extend(project_sessions)
        except Exception:
            pass

    # print(f'sessions={sessions}')
    v_sessions = []
    for session in sessions:
        # 去掉超过一天没变化的
        if time.time() - session.get("updated") / 1000 > 24 * 3600:
            continue
        v_session = ViewSession(
            title=session.get("title"),
            session_id=session.get("id"),
            last_modify_time=time.strftime(
                "%Y-%m-%d %H:%M:%S", time.localtime(session.get("updated") / 1000)
            ),
            create_time=time.strftime(
                "%Y-%m-%d %H:%M:%S", time.localtime(session.get("created") / 1000)
            ),
            directory=session.get("directory"),
            status="",
            new_messages="",
        )
        status, new_messages_list, next_ts = read_new_messages(
            client,
            session_id=v_session.session_id,
            last_ts=session_notify_status.get(v_session.session_id, 0),
        )
        v_session.status = status
        if status == "finished":
            message_texts = []
            for msg in new_messages_list:
                if msg.parts:
                    for part in msg.parts:
                        if hasattr(part, "text") and part.text:
                            message_texts.append(part.text)

            v_session.new_messages = "\n\n".join(message_texts)
            session_notify_status[v_session.session_id] = next_ts
        v_sessions.append(v_session)
    print(f"Success! Found {len(v_sessions)} session(s)")
    print()
    for i, vs in enumerate(v_sessions, 1):
        print(f"--- Session {i} ---")
        print(f"Title: {vs.title}")
        print(f"Session ID: {vs.session_id}")
        print(f"Status: {vs.status}")
        print(f"Last Modified: {vs.last_modify_time}")
        print(f"Created: {vs.create_time}")
        print(f"Directory: {vs.directory}")
        if vs.new_messages:
            print(f"New Messages To User:\n{vs.new_messages}")
        else:
            print("New Messages To User: (none)")
        print()
    write_status(session_notify_status)

except Exception as e:
    print(f"   Error: {e}")
    import traceback

    traceback.print_exc()

print("\n" + "=" * 80)
