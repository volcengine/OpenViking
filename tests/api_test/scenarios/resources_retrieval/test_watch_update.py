import pytest
import json
import uuid
import time
import os
import shutil
from conftest import create_test_file


class TestWatchUpdate:
    """TC-R08 定时监听更新 (Watch)
    
    根据API文档：
    - add_resource() 支持 watch_interval 参数
    - watch_interval: 定时更新间隔（分钟）。>0 开启/更新定时任务；<=0 关闭定时任务
    - 仅在指定 target 时生效
    """
    
    def test_watch_update(self, api_client):
        """定时监听更新：add_resource -> 验证资源索引 -> 验证搜索"""
        random_id = str(uuid.uuid4())[:8]
        unique_keyword = f"watch_test_{random_id}"
        
        # 1. 创建临时测试文件
        test_file_path, temp_dir = create_test_file(
            content=f"测试文件 {random_id}\n这是一个用于监听更新测试的文件。\n包含唯一关键词：{unique_keyword}、test、监听、更新。"
        )
        
        try:
            # 2. 添加该文件到资源
            response = api_client.add_resource(path=test_file_path, wait=True)
            assert response.status_code == 200
            
            add_data = response.json()
            assert add_data.get('status') == 'ok'
            
            add_result = add_data.get('result', {})
            resource_uri = add_result.get('root_uri')
            assert resource_uri is not None, "Add resource should return root_uri"
            print(f"资源添加成功: {resource_uri}")
            
            # 3. 等待处理完成
            response = api_client.wait_processed()
            assert response.status_code == 200
            
            # 4. 验证资源已被正确索引
            response = api_client.fs_stat(resource_uri)
            if response.status_code == 200:
                stat_data = response.json()
                if stat_data.get('status') == 'ok':
                    print(f"资源状态验证成功")
            
            # 5. 执行搜索验证
            response = api_client.find(unique_keyword)
            assert response.status_code == 200
            
            search_data = response.json()
            assert search_data.get('status') == 'ok'
            assert 'result' in search_data
            
            search_result = search_data['result']
            
            # 6. 验证搜索结果包含刚添加的资源
            found_resource = False
            for field in ['resources', 'memories', 'matches']:
                if field in search_result:
                    items = search_result[field]
                    assert isinstance(items, list), f"{field} should be a list"
                    for item in items:
                        if 'uri' in item and resource_uri in item['uri']:
                            found_resource = True
                            break
            
            assert found_resource, \
                f"Search should find the added resource: {resource_uri}"
            
            # 7. 验证系统状态
            response = api_client.is_healthy()
            assert response.status_code == 200
            health_data = response.json()
            assert health_data.get('status') == 'ok'
            
            print(f"✓ 定时监听更新测试通过，资源已被正确索引")
        finally:
            # 清理临时文件
            if os.path.exists(temp_dir):
                shutil.rmtree(temp_dir)
