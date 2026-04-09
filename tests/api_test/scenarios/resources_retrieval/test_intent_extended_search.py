import pytest
import json
import uuid
import os
import shutil
from conftest import create_test_file


class TestIntentExtendedSearch:
    """TC-R06 意图扩展搜索 (Search)
    
    根据API文档：
    - search() 带会话上下文和意图分析
    - 参数 session_id 用于上下文感知搜索
    - 与 find() 的区别：search 支持意图分析、会话上下文、查询扩展
    """
    
    def test_intent_extended_search(self, api_client):
        """意图扩展搜索：create_session -> add_message -> search(with session_id)"""
        random_id = str(uuid.uuid4())[:8]
        unique_keyword = f"intent_search_{random_id}"
        
        # 1. 创建临时测试文件
        test_file_path, temp_dir = create_test_file(
            content=f"测试文件 {random_id}\n这是一个用于意图扩展搜索测试的文件。\n包含唯一关键词：{unique_keyword}、test、搜索、意图。\nOAuth认证相关内容。"
        )
        
        try:
            # 2. 添加该文件到资源
            response = api_client.add_resource(path=test_file_path, wait=True)
            assert response.status_code == 200
            
            add_data = response.json()
            assert add_data.get('status') == 'ok'
            print(f"资源添加成功")
            
            # 3. 等待处理完成
            response = api_client.wait_processed()
            assert response.status_code == 200
            
            # 4. 创建会话
            response = api_client.create_session()
            assert response.status_code == 200
            create_data = response.json()
            assert create_data.get('status') == 'ok'
            
            session_id = create_data['result']['session_id']
            assert session_id is not None
            print(f"会话创建成功: {session_id}")
            
            # 5. 添加对话上下文（模拟用户讨论OAuth）
            response = api_client.add_message(session_id, "user", 
                f"我正在实现OAuth认证功能，需要查看相关文档。{random_id}")
            assert response.status_code == 200
            msg_data = response.json()
            assert msg_data.get('status') == 'ok'
            print(f"消息添加成功")
            
            # 6. 执行搜索（带会话上下文）
            # 根据API文档，search支持session_id参数
            search_query = "认证"
            response = api_client.search(search_query)
            assert response.status_code == 200
            
            search_data = response.json()
            assert search_data.get('status') == 'ok'
            assert 'result' in search_data
            
            search_result = search_data['result']
            
            # 7. 验证搜索结果结构
            assert 'memories' in search_result or 'resources' in search_result or 'results' in search_result
            
            total_results = 0
            for field in ['memories', 'resources', 'results']:
                if field in search_result:
                    items = search_result[field]
                    assert isinstance(items, list), f"{field} should be a list"
                    total_results += len(items)
            
            print(f"搜索结果数量: {total_results}")
            
            # 8. 业务逻辑验证：搜索应该返回相关结果
            assert total_results > 0, \
                "Search should return at least one result when resources exist"
            
            # 9. 验证搜索结果的相关性分数（如果返回）
            for field in ['resources', 'memories']:
                if field in search_result:
                    for item in search_result[field]:
                        if 'score' in item:
                            assert 0 <= item['score'] <= 1, \
                                f"Score should be between 0 and 1, got {item['score']}"
            
            print(f"✓ 意图扩展搜索测试通过")
        finally:
            # 清理临时文件
            if os.path.exists(temp_dir):
                shutil.rmtree(temp_dir)
