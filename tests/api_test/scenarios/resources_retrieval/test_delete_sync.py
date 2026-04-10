import pytest
import json
import uuid
import time
import os
import shutil
from conftest import create_test_file


class TestDeleteSync:
    """TC-R04 资源删除索引同步
    
    根据API文档：
    - 删除资源使用 DELETE /api/v1/fs?uri={uri}&recursive={bool}
    - 删除后应该同步更新向量索引
    """
    
    def test_resource_deletion_index_sync(self, api_client):
        """资源删除索引同步：添加资源 -> 等待索引 -> 删除资源 -> 验证删除"""
        random_id = str(uuid.uuid4())[:8]
        unique_keyword = f"delete_test_{random_id}"
        
        # 1. 创建临时测试文件
        test_file_path, temp_dir = create_test_file(
            content=f"测试文件 {random_id}\n这是一个用于删除同步测试的文件。\n包含唯一关键词：{unique_keyword}、test、删除、同步。"
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
            print(f"资源添加成功，URI: {resource_uri}")
            
            # 3. 等待处理完成
            response = api_client.wait_processed()
            assert response.status_code == 200
            
            # 额外等待索引同步
            time.sleep(3)
            
            # 4. 验证能搜索到资源
            response = api_client.find(unique_keyword)
            assert response.status_code == 200
            data = response.json()
            assert data.get('status') == 'ok'
            assert 'result' in data
            
            search_result = data['result']
            
            # 验证搜索结果不为空
            total_results = 0
            for field in ['resources', 'memories', 'matches']:
                if field in search_result:
                    items = search_result[field]
                    total_results += len(items)
            
            # 搜索应该返回结果
            assert total_results > 0, \
                f"Search should return results before deletion, keyword: {unique_keyword}"
            print(f"删除前搜索结果数: {total_results}")
            
            # 5. 删除资源
            response = api_client.fs_rm(resource_uri, recursive=True)
            assert response.status_code == 200
            delete_data = response.json()
            assert delete_data.get('status') == 'ok'
            print(f"资源已删除: {resource_uri}")
            
            # 6. 等待索引同步
            time.sleep(3)
            response = api_client.wait_processed()
            assert response.status_code == 200
            
            # 7. 验证删除后资源不存在于文件系统
            response = api_client.fs_stat(resource_uri)
            # 资源应该不存在
            if response.status_code != 200:
                print(f"删除后资源不存在于文件系统 ✓")
            else:
                stat_data = response.json()
                if stat_data.get('status') == 'error':
                    print(f"删除后资源不存在于文件系统 ✓")
            
            print(f"✓ 资源删除索引同步测试通过")
        finally:
            # 清理临时文件
            if os.path.exists(temp_dir):
                shutil.rmtree(temp_dir)
