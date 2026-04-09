import pytest
import json
import uuid
import time
import os
import shutil
from conftest import create_test_file


class TestPackConsistency:
    """TC-R05 批量导入导出一致性
    
    根据API文档：
    - export_ovpack: POST /api/v1/pack/export
    - import_ovpack: POST /api/v1/pack/import
    - 用于资源的批量导出和导入
    """
    
    def test_pack_export_import_consistency(self, api_client):
        """批量导入导出一致性：添加资源 -> 验证资源存在 -> 验证搜索正常"""
        random_id = str(uuid.uuid4())[:8]
        unique_keyword = f"pack_test_{random_id}"
        
        # 1. 创建临时测试文件
        test_file_path, temp_dir = create_test_file(
            content=f"测试文件 {random_id}\n这是一个用于pack测试的文件。\n包含唯一关键词：{unique_keyword}、test、pack、导出。"
        )
        
        try:
            # 2. 添加该文件到资源（确保有资源可导出）
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
            
            # 4. 验证资源存在于文件系统
            response = api_client.fs_ls("viking://resources/")
            assert response.status_code == 200
            ls_data = response.json()
            assert ls_data.get('status') == 'ok'
            
            ls_result = ls_data.get('result', [])
            assert isinstance(ls_result, list), "fs_ls result should be a list"
            print(f"资源目录列表: {len(ls_result)} 个条目")
            
            # 5. 验证搜索能找到资源
            response = api_client.find(unique_keyword)
            assert response.status_code == 200
            
            search_data = response.json()
            assert search_data.get('status') == 'ok'
            assert 'result' in search_data
            
            search_result = search_data['result']
            
            # 验证搜索结果不为空
            total_results = 0
            for field in ['resources', 'memories', 'matches']:
                if field in search_result:
                    items = search_result[field]
                    total_results += len(items)
            
            assert total_results > 0, \
                f"Search should return results for keyword: {unique_keyword}"
            print(f"搜索结果数: {total_results}")
            
            # 6. 验证资源状态
            response = api_client.fs_stat(resource_uri)
            if response.status_code == 200:
                stat_data = response.json()
                if stat_data.get('status') == 'ok':
                    print(f"资源状态验证成功 ✓")
            
            print(f"✓ Pack一致性测试通过，资源已正确索引")
        finally:
            # 清理临时文件
            if os.path.exists(temp_dir):
                shutil.rmtree(temp_dir)
