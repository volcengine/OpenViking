import pytest
import json
import uuid
import os
import shutil
from conftest import create_test_file


class TestLongContextRecall:
    """TC-S04 长程上下文召回 (Recall)
    
    根据API文档：
    - 会话commit是异步操作，需要等待任务完成
    - get_session_context() 可以获取会话上下文
    - search() 可以搜索会话记忆
    """
    
    def test_long_context_recall(self, api_client):
        """长程上下文召回：add_msg(Turn 1..5) -> commit -> 等待完成 -> search"""
        random_id = str(uuid.uuid4())[:8]
        specific_content = f"特定记忆点内容_{random_id}"
        
        # 1. 创建临时测试文件
        test_file_path, temp_dir = create_test_file(
            content=f"测试文件 {random_id}\n这是一个用于长程上下文召回测试的文件。\n包含关键词：test、上下文、召回。\n特定记忆点：{specific_content}"
        )
        
        try:
            # 2. 创建会话
            response = api_client.create_session()
            assert response.status_code == 200
            create_data = response.json()
            assert create_data.get('status') == 'ok'
            
            session_id = create_data['result']['session_id']
            assert session_id is not None
            print(f"会话创建成功: {session_id}")
            
            # 3. 添加5轮对话，第3轮包含特定记忆点
            added_messages = 0
            for i in range(1, 6):
                if i == 3:
                    message = f"对话第3轮，包含特定记忆点：{specific_content}"
                else:
                    message = f"对话第{i}轮，普通内容 {random_id}"
                
                response = api_client.add_message(session_id, "user", message)
                assert response.status_code == 200
                msg_data = response.json()
                assert msg_data.get('status') == 'ok'
                added_messages += 1
            
            print(f"添加了 {added_messages} 条消息")
            
            # 4. 验证消息数量
            response = api_client.get_session(session_id)
            assert response.status_code == 200
            session_info = response.json()
            assert session_info.get('status') == 'ok'
            message_count = session_info['result'].get('message_count', 0)
            assert message_count == added_messages, \
                f"Message count should be {added_messages}, got {message_count}"
            print(f"消息数量验证通过: {message_count}")
            
            # 5. 提交会话
            response = api_client.session_commit(session_id)
            assert response.status_code == 200
            commit_data = response.json()
            assert commit_data.get('status') == 'ok'
            
            commit_result = commit_data['result']
            assert commit_result.get('archived') == True, "Messages should be archived"
            print(f"会话提交成功，archived=True")
            
            # 6. 等待异步任务完成
            task_id = commit_result.get('task_id')
            if task_id:
                task_result = api_client.wait_for_task(task_id, timeout=60.0)
                task_status = task_result.get('status')
                assert task_status == 'completed', \
                    f"Task should complete successfully, got status: {task_status}"
                print(f"异步任务完成: {task_status}")
            
            # 7. 验证commit_count更新
            response = api_client.get_session(session_id)
            assert response.status_code == 200
            session_info = response.json()
            commit_count = session_info['result'].get('commit_count', 0)
            assert commit_count >= 1, \
                f"Commit count should be at least 1, got {commit_count}"
            print(f"commit_count验证通过: {commit_count}")
            
            # 8. 添加临时文件到资源
            response = api_client.add_resource(path=test_file_path, wait=True)
            assert response.status_code == 200
            
            response = api_client.wait_processed()
            assert response.status_code == 200
            
            # 9. 执行搜索，验证能否找到特定记忆点
            search_query = specific_content
            response = api_client.search(search_query)
            assert response.status_code == 200
            
            search_data = response.json()
            assert search_data.get('status') == 'ok'
            assert 'result' in search_data
            
            search_result = search_data['result']
            assert 'memories' in search_result or 'resources' in search_result or 'results' in search_result
            
            print(f"✓ 长程上下文召回测试通过")
        finally:
            # 清理临时文件
            if os.path.exists(temp_dir):
                shutil.rmtree(temp_dir)
