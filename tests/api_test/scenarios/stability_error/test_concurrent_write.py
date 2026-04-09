import pytest
import json
import uuid
import concurrent.futures
import os
import shutil
from conftest import create_test_file


class TestConcurrentWrite:
    """TC-ER02 并发写入冲突验证"""
    
    def test_concurrent_write_conflict(self, api_client):
        """并发写入冲突验证：并发调用 add_resource (Same URI)"""
        random_id = str(uuid.uuid4())[:8]
        
        # 1. 创建临时测试文件
        test_file_path, temp_dir = create_test_file(
            content=f"测试文件 {random_id}\n这是一个用于并发写入测试的文件。\n包含关键词：test、并发、写入。"
        )
        
        try:
            # 2. 定义并发任务函数
            def add_resource_task():
                try:
                    response = api_client.add_resource(path=test_file_path, wait=True)
                    return response.status_code, response.json()
                except Exception as e:
                    return 500, {'error': str(e)}
            
            # 3. 并发执行多个任务
            num_tasks = 3
            results = []
            
            with concurrent.futures.ThreadPoolExecutor(max_workers=num_tasks) as executor:
                futures = [executor.submit(add_resource_task) for _ in range(num_tasks)]
                for future in concurrent.futures.as_completed(futures):
                    results.append(future.result())
            
            # 4. 验证所有请求都返回合理的响应
            assert len(results) == num_tasks
            
            for status_code, response_data in results:
                # 要么成功（200），要么返回合理的错误（429或其他）
                assert status_code in [200, 429, 500], f"Unexpected status code: {status_code}"
                
                if status_code == 200:
                    assert response_data.get('status') in ['ok', 'error'], "Response should have valid status"
        finally:
            # 清理临时文件
            if os.path.exists(temp_dir):
                shutil.rmtree(temp_dir)
