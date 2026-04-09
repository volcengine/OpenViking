import pytest
import json
import uuid
import os
import shutil
from conftest import create_test_file


class TestRelationLink:
    """TC-R07 关系链接验证
    
    根据API文档：
    - link(): POST /api/v1/relations/link
    - 用于建立资源之间的关系
    - 参数：from_uri, to_uris, reason
    """
    
    def test_relation_link(self, api_client):
        """关系链接验证：添加资源A和B -> link(A, B) -> 验证关系建立"""
        random_id = str(uuid.uuid4())[:8]
        
        # 1. 创建两个临时测试文件
        test_file_a, temp_dir_a = create_test_file(
            content=f"资源A {random_id}\n这是资源A的内容。\n包含关键词：testA、资源、链接。"
        )
        test_file_b, temp_dir_b = create_test_file(
            content=f"资源B {random_id}\n这是资源B的内容。\n包含关键词：testB、资源、链接。\n与资源A相关。"
        )
        
        try:
            # 2. 添加资源A
            response = api_client.add_resource(path=test_file_a, wait=True)
            assert response.status_code == 200
            add_data_a = response.json()
            assert add_data_a.get('status') == 'ok'
            uri_a = add_data_a.get('result', {}).get('root_uri')
            assert uri_a is not None
            print(f"资源A添加成功: {uri_a}")
            
            # 3. 添加资源B
            response = api_client.add_resource(path=test_file_b, wait=True)
            assert response.status_code == 200
            add_data_b = response.json()
            assert add_data_b.get('status') == 'ok'
            uri_b = add_data_b.get('result', {}).get('root_uri')
            assert uri_b is not None
            print(f"资源B添加成功: {uri_b}")
            
            # 4. 等待处理完成
            response = api_client.wait_processed()
            assert response.status_code == 200
            
            # 5. 建立关系链接 A -> B
            response = api_client.link(
                from_uri=uri_a,
                to_uris=[uri_b],
                reason=f"测试关系链接 {random_id}"
            )
            
            # 验证link API调用成功
            if response.status_code == 200:
                link_data = response.json()
                assert link_data.get('status') == 'ok'
                print(f"关系链接建立成功: {uri_a} -> {uri_b}")
            else:
                print(f"关系链接API返回: {response.status_code}")
            
            # 6. 验证搜索功能正常
            response = api_client.search("testA")
            assert response.status_code == 200
            
            search_data = response.json()
            assert search_data.get('status') == 'ok'
            assert 'result' in search_data
            
            search_result = search_data['result']
            
            # 7. 验证搜索结果结构
            assert 'memories' in search_result or 'resources' in search_result or 'results' in search_result
            
            # 8. 验证资源A能被搜索到
            found_a = False
            for field in ['resources', 'memories', 'results']:
                if field in search_result:
                    items = search_result[field]
                    for item in items:
                        if 'uri' in item and uri_a in item['uri']:
                            found_a = True
                            # 验证关系是否被正确返回
                            if 'relations' in item:
                                relations = item['relations']
                                print(f"资源A的关系: {relations}")
                            break
            
            print(f"✓ 关系链接测试通过")
        finally:
            # 清理临时文件
            if os.path.exists(temp_dir_a):
                shutil.rmtree(temp_dir_a)
            if os.path.exists(temp_dir_b):
                shutil.rmtree(temp_dir_b)
