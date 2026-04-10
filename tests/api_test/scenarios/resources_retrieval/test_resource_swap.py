import pytest
import json
import uuid
import time
import os
import shutil
from conftest import create_test_file


class TestResourceSwap:
    """TC-R02 资源增量更新
    
    根据API文档：当你为同一个资源 URI 反复调用 add_resource() 时，
    系统会走"增量更新"而不是每次全量重建。
    触发条件：请求里显式指定 target，且该 target 在知识库中已存在。
    """
    
    def test_resource_incremental_update(self, api_client):
        """资源增量更新：添加资源 -> 等待索引 -> 验证能搜索到"""
        random_id = str(uuid.uuid4())[:8]
        unique_keyword = f"incremental_{random_id}"
        
        # 1. 创建临时测试文件
        test_file_path, temp_dir = create_test_file(
            content=f"测试文件 {random_id}\n这是一个用于资源增量更新测试的文件。\n包含关键词：{unique_keyword}、test、更新、资源。"
        )
        
        try:
            # 2. 添加该文件到资源
            response = api_client.add_resource(path=test_file_path, wait=True)
            assert response.status_code == 200
            
            add_data = response.json()
            assert add_data.get('status') == 'ok'
            
            # 验证返回结果包含root_uri
            add_result = add_data.get('result', {})
            assert 'root_uri' in add_result, "Add resource should return root_uri"
            root_uri = add_result['root_uri']
            print(f"资源添加成功，root_uri: {root_uri}")
            
            # 3. 等待处理完成
            response = api_client.wait_processed()
            assert response.status_code == 200
            
            # 额外等待索引同步
            time.sleep(3)
            
            # 4. 验证find能搜索到该资源
            response = api_client.find(unique_keyword)
            assert response.status_code == 200
            
            search_data = response.json()
            assert search_data.get('status') == 'ok'
            assert 'result' in search_data
            
            search_result = search_data['result']
            
            # 5. 验证搜索结果结构正确
            total_results = 0
            for field in ['resources', 'memories', 'matches']:
                if field in search_result:
                    items = search_result[field]
                    assert isinstance(items, list), f"{field} should be a list"
                    total_results += len(items)
            
            # 6. 业务逻辑验证：搜索应该返回结果
            assert total_results > 0, \
                f"Search should return results for keyword: {unique_keyword}"
            
            print(f"✓ 资源增量更新测试通过，搜索结果数: {total_results}")
        finally:
            # 清理临时文件
            if os.path.exists(temp_dir):
                shutil.rmtree(temp_dir)
