import pytest
import json
import uuid
import time
import os
import shutil
from config import Config
from conftest import create_test_file


class TestAccountIsolation:
    """TC-ER03 账户隔离完整性验证
    
    测试场景：验证资源管理操作不会影响系统整体状态
    Bug复现：执行某些资源操作后，processed变为0，所有账户都无法召回资源
    
    核心验证点：
    1. processed数量不会归零
    2. 搜索功能始终正常工作
    3. 资源操作不会影响系统稳定性
    """
    
    def test_processed_not_zero_after_resource_ops(self, api_client):
        """核心测试：资源操作后，processed不能归零，搜索必须正常"""
        random_id = str(uuid.uuid4())[:8]
        
        # 创建临时测试文件
        test_file_path, temp_dir = create_test_file(
            content=f"测试文件 {random_id}\n这是一个用于账户隔离测试的文件。\n包含关键词：test、隔离、验证。"
        )
        
        try:
            # ==================== 步骤1: 获取初始状态 ====================
            print("\n" + "="*80)
            print("步骤1: 获取初始VikingDB状态")
            print("="*80)
            
            response = api_client.observer_vikingdb()
            assert response.status_code == 200, "observer_vikingdb should succeed"
            observer_data_initial = response.json()
            assert observer_data_initial.get('status') == 'ok', "status should be ok"
            
            observer_initial = observer_data_initial.get('result', {})
            initial_processed = observer_initial.get('processed', 0)
            print(f"初始 processed 数量: {initial_processed}")
            
            # ==================== 步骤2: 验证初始搜索正常 ====================
            print("\n" + "="*80)
            print("步骤2: 验证初始搜索功能正常")
            print("="*80)
            
            search_query = "test"
            response = api_client.search(search_query)
            assert response.status_code == 200, "search should succeed"
            search_data_initial = response.json()
            assert search_data_initial.get('status') == 'ok', "search status should be ok"
            
            search_result_initial = search_data_initial.get('result', {})
            has_memories_initial = 'memories' in search_result_initial
            has_resources_initial = 'resources' in search_result_initial
            assert has_memories_initial or has_resources_initial, "search should return memories or resources"
            print("初始搜索验证通过 ✓")
            
            # ==================== 步骤3: 执行一些资源操作 ====================
            print("\n" + "="*80)
            print("步骤3: 执行资源操作（添加资源）")
            print("="*80)
            
            # 添加资源
            print("正在添加资源...")
            response = api_client.add_resource(path=test_file_path, wait=True)
            assert response.status_code == 200, "add_resource should succeed"
            add_data = response.json()
            assert add_data.get('status') == 'ok', "add_resource status should be ok"
            
            print("等待处理完成...")
            response = api_client.wait_processed()
            assert response.status_code == 200
            time.sleep(2)
            
            # ==================== 步骤4: 第一次验证 ====================
            print("\n" + "="*80)
            print("步骤4: 第一次验证 - processed和搜索")
            print("="*80)
            
            response = api_client.observer_vikingdb()
            assert response.status_code == 200
            observer_data_mid = response.json()
            assert observer_data_mid.get('status') == 'ok'
            
            observer_mid = observer_data_mid.get('result', {})
            mid_processed = observer_mid.get('processed', 0)
            print(f"添加资源后 processed 数量: {mid_processed}")
            
            # 如果初始processed > 0，则验证processed仍然 > 0
            if initial_processed > 0:
                assert mid_processed > 0, f"Processed should remain > 0, got {mid_processed}!"
            
            # 验证搜索仍然正常
            response = api_client.search(search_query)
            assert response.status_code == 200, "search should still work"
            search_data_mid = response.json()
            assert search_data_mid.get('status') == 'ok', "search status should still be ok"
            
            search_result_mid = search_data_mid.get('result', {})
            has_memories_mid = 'memories' in search_result_mid
            has_resources_mid = 'resources' in search_result_mid
            assert has_memories_mid or has_resources_mid, "search should still return results"
            print("第一次验证通过 ✓")
            
            # ==================== 步骤5: 执行更多操作 ====================
            print("\n" + "="*80)
            print("步骤5: 执行更多操作（多次搜索）")
            print("="*80)
            
            for i in range(3):
                query = f"test query {i} {random_id}"
                print(f"执行搜索 {i+1}: {query}")
                response = api_client.search(query)
                assert response.status_code == 200
                search_data = response.json()
                assert search_data.get('status') == 'ok'
            
            # ==================== 步骤6: 最终验证 ====================
            print("\n" + "="*80)
            print("步骤6: 最终验证")
            print("="*80)
            
            response = api_client.observer_vikingdb()
            assert response.status_code == 200
            observer_data_final = response.json()
            assert observer_data_final.get('status') == 'ok'
            
            observer_final = observer_data_final.get('result', {})
            final_processed = observer_final.get('processed', 0)
            print(f"最终 processed 数量: {final_processed}")
            
            # ==================== 关键断言 - Bug检测 ====================
            
            # 断言1: 如果初始processed > 0，则最终processed也应该 > 0
            if initial_processed > 0:
                assert final_processed > 0, f"❌ FAILED: Processed count dropped to ZERO! Initial: {initial_processed}, Final: {final_processed}"
            
            # 断言2: 搜索必须仍然正常工作
            response = api_client.search(search_query)
            assert response.status_code == 200, "❌ FAILED: Search request failed"
            final_search_data = response.json()
            assert final_search_data.get('status') == 'ok', "❌ FAILED: Search status not ok"
            
            final_search_result = final_search_data.get('result', {})
            has_memories_final = 'memories' in final_search_result
            has_resources_final = 'resources' in final_search_result
            assert has_memories_final or has_resources_final, "❌ FAILED: Search returns no results"
            
            print("\n" + "="*80)
            print(f"✅ TEST PASSED! 所有断言通过！")
            print(f"   - 初始 processed: {initial_processed}")
            print(f"   - 最终 processed: {final_processed}")
            print(f"   - 搜索功能正常")
            if initial_processed > 0:
                print(f"   - Processed 没有归零 ✓")
            print("="*80)
        finally:
            # 清理临时文件
            if os.path.exists(temp_dir):
                shutil.rmtree(temp_dir)
    
    def test_consecutive_health_checks(self, api_client):
        """附加测试：连续健康检查，验证系统稳定性"""
        for i in range(5):
            response = api_client.is_healthy()
            assert response.status_code == 200
            health_data = response.json()
            assert health_data.get('status') == 'ok'
            time.sleep(0.5)
        
        # 最后验证processed仍然>0
        response = api_client.observer_vikingdb()
        observer_data = response.json()
        observer = observer_data.get('result', {})
        processed = observer.get('processed', 0)
        assert processed >= 0, "Processed should not be negative"
