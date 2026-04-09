import pytest
import json
import uuid
import os
from conftest import create_test_file


class TestSemanticRetrieval:
    """TC-R01 语义检索全链路验证"""
    
    def test_semantic_retrieval_end_to_end(self, api_client):
        """语义检索全链路验证：添加资源 -> 等待处理 -> 搜索验证"""
        random_id = str(uuid.uuid4())[:8]
        unique_keyword = f"unique_keyword_{random_id}"
        
        # 1. 创建临时测试文件，包含唯一关键词
        test_file_path, temp_dir = create_test_file(
            content=f"测试文件 {random_id}\n这是一个用于语义检索测试的文件。\n包含唯一关键词：{unique_keyword}、test、测试、检索。"
        )
        
        try:
            # 2. 添加该文件到资源
            response = api_client.add_resource(path=test_file_path, wait=True)
            assert response.status_code == 200
            
            add_data = response.json()
            assert add_data.get('status') == 'ok'
            
            # 验证添加资源的返回结果
            add_result = add_data.get('result', {})
            assert 'root_uri' in add_result or 'resource_id' in add_result, \
                "Add resource should return root_uri or resource_id"
            
            # 保存添加的资源URI，用于后续验证
            added_resource_uri = add_result.get('root_uri') or add_result.get('resource_id')
        
            # 3. 等待处理完成
            response = api_client.wait_processed()
            assert response.status_code == 200
            
            # 4. 执行语义搜索，使用唯一关键词
            search_query = unique_keyword
            response = api_client.find(search_query)
            assert response.status_code == 200
            
            search_data = response.json()
            assert search_data.get('status') == 'ok'
            assert 'result' in search_data
            
            search_result = search_data['result']
            
            # 5. 验证搜索结果结构正确
            found_added_resource = False
            total_results = 0
            
            for field in ['resources', 'memories', 'matches']:
                if field in search_result:
                    items = search_result[field]
                    assert isinstance(items, list), f"{field} should be a list"
                    total_results += len(items)
                    
                    # 如果有结果，验证每个结果的结构
                    for item in items:
                        assert 'score' in item or 'uri' in item, \
                            "Each search result should have score or uri"
                        
                        # 验证是否找到添加的资源
                        if 'uri' in item and added_resource_uri:
                            if added_resource_uri in item['uri']:
                                found_added_resource = True
            
            # 记录搜索结果数量
            print(f"Total search results: {total_results}")
            
            # 6. 验证业务逻辑：搜索结果应该包含刚添加的资源
            # 这是一个重要的业务逻辑验证，应该保持失败状态以发现问题
            if added_resource_uri:
                assert found_added_resource, \
                    f"Search result should contain the added resource: {added_resource_uri}. " \
                    f"This indicates that the resource was not correctly indexed or the search algorithm has issues."
            
            # 7. 验证搜索结果的相关性（如果返回了score）
            for field in ['resources', 'memories', 'matches']:
                if field in search_result:
                    items = search_result[field]
                    for item in items:
                        if 'score' in item:
                            # 验证score是合理的范围（0-1）
                            assert 0 <= item['score'] <= 1, \
                                f"Score should be between 0 and 1, got {item['score']}"
            
            # 8. 业务逻辑验证：验证资源是否被正确索引
            # 使用更通用的关键词进行搜索
            response = api_client.find("test")
            assert response.status_code == 200
            general_search_data = response.json()
            assert general_search_data.get('status') == 'ok'
            
            # 记录通用搜索的结果数量
            general_search_result = general_search_data.get('result', {})
            general_total = 0
            for field in ['resources', 'memories', 'matches']:
                if field in general_search_result:
                    general_total += len(general_search_result[field])
            
            print(f"General search results: {general_total}")
            
            # 9. 业务逻辑验证：验证资源列表
            response = api_client.fs_ls("viking://")
            assert response.status_code == 200
            ls_data = response.json()
            assert ls_data.get('status') == 'ok'
            
            ls_result = ls_data.get('result', [])
            print(f"Total resources in root: {len(ls_result)}")
            
            # 10. 业务逻辑验证：验证系统状态
            response = api_client.is_healthy()
            assert response.status_code == 200
            health_data = response.json()
            assert health_data.get('status') == 'ok'
            print("✓ System is healthy")
            
            print("✓ Semantic retrieval test passed")
        finally:
            # 清理临时文件
            import shutil
            if os.path.exists(temp_dir):
                shutil.rmtree(temp_dir)
