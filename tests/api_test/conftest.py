import os

import psutil
import pytest
from api.client import OpenVikingAPIClient
from config import Config

TEST_CASE_DESCRIPTIONS = {
    "test_add_resource.py::TestAddResource::test_add_resource_simple": "向知识库添加资源",
    "test_pack.py::TestPack::test_export_ovpack": "导出资源包",
    "test_wait_processed.py::TestWaitProcessed::test_wait_processed": "等待资源处理完成",
    "test_fs_ls.py::TestFsLs::test_fs_ls_root": "列出文件系统根目录",
    "test_fs_mkdir.py::TestFsMkdir::test_fs_mkdir": "创建目录",
    "test_fs_mv.py::TestFsMv::test_fs_mv": "移动文件/目录",
    "test_fs_read_write.py::TestFsReadWrite::test_fs_read": "读取文件内容",
    "test_fs_rm.py::TestFsRm::test_fs_rm": "删除文件/目录",
    "test_fs_stat.py::TestFsStat::test_fs_stat": "获取文件状态",
    "test_fs_tree.py::TestFsTree::test_fs_tree": "获取目录树结构",
    "test_get_abstract.py::TestGetAbstract::test_get_abstract": "获取内容摘要",
    "test_get_overview.py::TestGetOverview::test_get_overview": "获取内容概览",
    "test_link_relations.py::TestLinkRelations::test_link_relations_unlink": "管理内容关联关系",
    "test_add_message.py::TestAddMessage::test_add_message": "向会话添加消息",
    "test_create_session.py::TestCreateSession::test_create_session": "创建会话",
    "test_delete_session.py::TestDeleteSession::test_delete_session": "删除会话",
    "test_get_session.py::TestGetSession::test_get_session": "获取会话信息",
    "test_list_sessions.py::TestListSessions::test_list_sessions": "列出所有会话",
    "test_session_used_commit.py::TestSessionUsedCommit::test_session_used_commit": "会话使用和提交",
    "test_find.py::TestFind::test_find_basic": "基础查找搜索",
    "test_find.py::TestFind::test_find_with_different_query": "不同查询的查找搜索",
    "test_search.py::TestSearch::test_basic_search": "基础语义搜索",
    "test_search.py::TestSearch::test_search_with_different_query": "不同查询的语义搜索",
    "test_grep.py::TestGrep::test_grep_basic": "基础文本搜索",
    "test_glob.py::TestGlob::test_glob_basic": "基础模式匹配",
    "test_is_healthy.py::TestIsHealthy::test_is_healthy": "检查系统健康状态",
    "test_observer.py::TestObserver::test_observer_queue": "观察任务队列状态",
    "test_observer.py::TestObserver::test_observer_vikingdb": "观察向量数据库状态",
    "test_observer.py::TestObserver::test_observer_system": "观察系统整体状态",
    "test_system_status.py::TestSystemStatus::test_system_status": "获取系统状态",
    "test_system_wait.py::TestSystemWait::test_system_wait": "等待系统处理完成",
    "test_admin_accounts.py::TestAdminAccounts::test_admin_list_accounts": "列出所有账户",
    "test_admin_accounts.py::TestAdminAccounts::test_admin_create_delete_account": "创建和删除账户",
    "test_admin_regenerate_key.py::TestAdminRegenerateKey::test_admin_regenerate_key": "重新生成API密钥",
    "test_admin_role.py::TestAdminRole::test_admin_set_role": "设置用户角色",
    "test_admin_users.py::TestAdminUsers::test_admin_list_users": "列出账户下的用户",
    "test_admin_users.py::TestAdminUsers::test_admin_register_remove_user": "注册和删除用户",
    "test_server_health_check.py::TestServerHealthCheck::test_server_health_check": "服务器健康检查",
    "test_semantic_retrieval.py::TestSemanticRetrieval::test_semantic_retrieval_end_to_end": "语义检索全链路验证",
    "test_resource_swap.py::TestResourceSwap::test_resource_incremental_update": "资源增量更新",
    "test_grep_validation.py::TestGrepValidation::test_grep_pattern_match": "正则检索验证",
    "test_delete_sync.py::TestDeleteSync::test_resource_deletion_index_sync": "资源删除索引同步",
    "test_pack_consistency.py::TestPackConsistency::test_pack_export_import_consistency": "批量导入导出一致性",
    "test_intent_extended_search.py::TestIntentExtendedSearch::test_intent_extended_search": "意图扩展搜索",
    "test_relation_link.py::TestRelationLink::test_relation_link": "关系链接验证",
    "test_watch_update.py::TestWatchUpdate::test_watch_update": "定时监听更新",
    "test_session_commit.py::TestSessionCommit::test_session_persistence_and_commit": "对话持久化与Commit",
    "test_long_context_recall.py::TestLongContextRecall::test_long_context_recall": "长程上下文召回",
    "test_session_delete_cleanup.py::TestSessionDeleteCleanup::test_session_delete_cleanup": "会话删除与清理",
    "test_concurrent_write.py::TestConcurrentWrite::test_concurrent_write_conflict": "并发写入冲突验证",
    "test_account_isolation.py::TestAccountIsolation::test_processed_not_zero_after_resource_ops": "账户隔离完整性验证",
    "test_account_isolation.py::TestAccountIsolation::test_consecutive_health_checks": "账户隔离连续健康检查",
}


TEST_CASE_APIS = {
    "test_add_resource.py::TestAddResource::test_add_resource_simple": "/api/v1/resources",
    "test_pack.py::TestPack::test_export_ovpack": "/api/v1/resources/pack",
    "test_wait_processed.py::TestWaitProcessed::test_wait_processed": "/api/v1/resources/wait",
    "test_fs_ls.py::TestFsLs::test_fs_ls_root": "/api/v1/fs/ls",
    "test_fs_mkdir.py::TestFsMkdir::test_fs_mkdir": "/api/v1/fs/mkdir",
    "test_fs_mv.py::TestFsMv::test_fs_mv": "/api/v1/fs/mv",
    "test_fs_read_write.py::TestFsReadWrite::test_fs_read": "/api/v1/fs/read",
    "test_fs_rm.py::TestFsRm::test_fs_rm": "/api/v1/fs/rm",
    "test_fs_stat.py::TestFsStat::test_fs_stat": "/api/v1/fs/stat",
    "test_fs_tree.py::TestFsTree::test_fs_tree": "/api/v1/fs/tree",
    "test_get_abstract.py::TestGetAbstract::test_get_abstract": "/api/v1/fs/abstract",
    "test_get_overview.py::TestGetOverview::test_get_overview": "/api/v1/fs/overview",
    "test_link_relations.py::TestLinkRelations::test_link_relations_unlink": "/api/v1/fs/relations",
    "test_add_message.py::TestAddMessage::test_add_message": "/api/v1/sessions/messages",
    "test_create_session.py::TestCreateSession::test_create_session": "/api/v1/sessions",
    "test_delete_session.py::TestDeleteSession::test_delete_session": "/api/v1/sessions",
    "test_get_session.py::TestGetSession::test_get_session": "/api/v1/sessions",
    "test_list_sessions.py::TestListSessions::test_list_sessions": "/api/v1/sessions",
    "test_session_used_commit.py::TestSessionUsedCommit::test_session_used_commit": "/api/v1/sessions/commit",
    "test_find.py::TestFind::test_find_basic": "/api/v1/search/find",
    "test_find.py::TestFind::test_find_with_different_query": "/api/v1/search/find",
    "test_search.py::TestSearch::test_basic_search": "/api/v1/search",
    "test_search.py::TestSearch::test_search_with_different_query": "/api/v1/search",
    "test_grep.py::TestGrep::test_grep_basic": "/api/v1/search/grep",
    "test_glob.py::TestGlob::test_glob_basic": "/api/v1/search/glob",
    "test_is_healthy.py::TestIsHealthy::test_is_healthy": "/api/v1/system/healthy",
    "test_observer.py::TestObserver::test_observer_queue": "/api/v1/system/observer",
    "test_observer.py::TestObserver::test_observer_vikingdb": "/api/v1/system/observer",
    "test_observer.py::TestObserver::test_observer_system": "/api/v1/system/observer",
    "test_system_status.py::TestSystemStatus::test_system_status": "/api/v1/system/status",
    "test_system_wait.py::TestSystemWait::test_system_wait": "/api/v1/system/wait",
    "test_admin_accounts.py::TestAdminAccounts::test_admin_list_accounts": "/api/v1/admin/accounts",
    "test_admin_accounts.py::TestAdminAccounts::test_admin_create_delete_account": "/api/v1/admin/accounts",
    "test_admin_regenerate_key.py::TestAdminRegenerateKey::test_admin_regenerate_key": "/api/v1/admin/keys",
    "test_admin_role.py::TestAdminRole::test_admin_set_role": "/api/v1/admin/roles",
    "test_admin_users.py::TestAdminUsers::test_admin_list_users": "/api/v1/admin/users",
    "test_admin_users.py::TestAdminUsers::test_admin_register_remove_user": "/api/v1/admin/users",
    "test_server_health_check.py::TestServerHealthCheck::test_server_health_check": "/health",
    "test_semantic_retrieval.py::TestSemanticRetrieval::test_semantic_retrieval_end_to_end": "/api/v1/resources,/api/v1/search/find",
    "test_resource_swap.py::TestResourceSwap::test_resource_incremental_update": "/api/v1/resources,/api/v1/search/find",
    "test_grep_validation.py::TestGrepValidation::test_grep_pattern_match": "/api/v1/resources,/api/v1/search/grep",
    "test_delete_sync.py::TestDeleteSync::test_resource_deletion_index_sync": "/api/v1/resources,/api/v1/fs/rm,/api/v1/search/find",
    "test_pack_consistency.py::TestPackConsistency::test_pack_export_import_consistency": "/api/v1/resources/pack/export,/api/v1/resources/pack/import",
    "test_intent_extended_search.py::TestIntentExtendedSearch::test_intent_extended_search": "/api/v1/sessions,/api/v1/search",
    "test_relation_link.py::TestRelationLink::test_relation_link": "/api/v1/fs/relations/link,/api/v1/search/find",
    "test_watch_update.py::TestWatchUpdate::test_watch_update": "/api/v1/resources,/api/v1/system/wait,/api/v1/search",
    "test_session_commit.py::TestSessionCommit::test_session_persistence_and_commit": "/api/v1/sessions,/api/v1/sessions/messages,/api/v1/sessions/commit",
    "test_long_context_recall.py::TestLongContextRecall::test_long_context_recall": "/api/v1/sessions/messages,/api/v1/sessions/commit,/api/v1/search",
    "test_session_delete_cleanup.py::TestSessionDeleteCleanup::test_session_delete_cleanup": "/api/v1/sessions (创建/获取/删除)",
    "test_concurrent_write.py::TestConcurrentWrite::test_concurrent_write_conflict": "/api/v1/resources (并发写入)",
    "test_account_isolation.py::TestAccountIsolation::test_processed_not_zero_after_resource_ops": "/api/v1/resources,/api/v1/search,/api/v1/system/observer",
    "test_account_isolation.py::TestAccountIsolation::test_consecutive_health_checks": "/api/v1/system/healthy,/api/v1/system/observer",
}


CATEGORY_NAMES = {
    "admin": "管理API",
    "filesystem": "文件系统API",
    "health_check": "健康检查",
    "resources": "资源管理API",
    "retrieval": "检索API",
    "sessions": "会话管理API",
    "system": "系统管理API",
    "resources_retrieval": "P1 知识中枢场景",
    "scenarios": "场景级测试",
    "stability_error": "P3 运维与异常边界",
}


def get_test_description(nodeid):
    for key, desc in TEST_CASE_DESCRIPTIONS.items():
        if key in nodeid:
            return desc
    return nodeid.split("::")[-1]


def get_test_api(nodeid):
    for key, api in TEST_CASE_APIS.items():
        if key in nodeid:
            return api
    return ""


def format_memory(bytes_value):
    if bytes_value is None:
        return ""

    if bytes_value < 1024:
        value = bytes_value
        unit = "B"
    elif bytes_value < 1024 * 1024:
        value = bytes_value / 1024
        unit = "KB"
    elif bytes_value < 1024 * 1024 * 1024:
        value = bytes_value / (1024 * 1024)
        unit = "MB"
    else:
        value = bytes_value / (1024 * 1024 * 1024)
        unit = "GB"

    return f"{value:.1f} {unit}"


def format_memory_delta(delta_bytes):
    if delta_bytes is None:
        return ""

    abs_bytes = abs(delta_bytes)
    if abs_bytes < 1024:
        value = abs_bytes
        unit = "B"
    elif abs_bytes < 1024 * 1024:
        value = abs_bytes / 1024
        unit = "KB"
    elif abs_bytes < 1024 * 1024 * 1024:
        value = abs_bytes / (1024 * 1024)
        unit = "MB"
    else:
        value = abs_bytes / (1024 * 1024 * 1024)
        unit = "GB"

    sign = "+" if delta_bytes > 0 else "" if delta_bytes == 0 else "-"
    return f"{sign}{value:.1f} {unit}"


def get_test_category(nodeid):
    parts = nodeid.split(os.sep)
    
    # 优先匹配更具体的路径（倒序匹配）
    priority_categories = ["stability_error", "resources_retrieval", "filesystem", "sessions"]
    
    for part in parts:
        if part in priority_categories:
            # 特殊处理：将子目录映射到正确的分类
            if part == "stability_error":
                return "P3 运维与异常边界"
            elif part == "resources_retrieval":
                return "P1 知识中枢场景"
            elif part == "filesystem":
                return "文件系统API"
            elif part == "sessions":
                return "会话管理API"
    
    # 如果没有匹配到优先分类，则按原逻辑匹配
    for part in parts:
        if part in CATEGORY_NAMES:
            return CATEGORY_NAMES[part]
    
    return "其他"


@pytest.fixture(scope="session")
def api_client():
    client = OpenVikingAPIClient(server_url=Config.SERVER_URL, api_key=Config.OPENVIKING_API_KEY)
    return client


def pytest_collection_modifyitems(config, items):
    cache = config.cache
    lastfailed = cache.get("cache/lastfailed", {})

    def item_sort_key(item):
        is_failed = item.nodeid in lastfailed
        category = get_test_category(item.nodeid)
        return (0 if is_failed else 1, category, item.name)

    items.sort(key=item_sort_key)


def pytest_runtest_setup(item):
    process = psutil.Process()
    mem_info = process.memory_info()
    item._start_memory = mem_info.rss


@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()

    if report.when == "call":
        category = get_test_category(item.nodeid)
        description = get_test_description(item.nodeid)
        report.category = category
        report.description = description
        report.nodeid = item.nodeid
        report.is_failed = report.failed

        if hasattr(item, "_start_memory"):
            process = psutil.Process()
            mem_info = process.memory_info()
            delta = mem_info.rss - item._start_memory
            report.memory_current = mem_info.rss
            report.memory_delta = delta

        # 为所有测试添加 cURL 和 Response 信息
        for _fixture_name, fixture_value in item.funcargs.items():
            if hasattr(fixture_value, "to_curl"):
                curl = fixture_value.to_curl()
                if curl:
                    report.sections.append(("cURL Command", curl))
            
            # 添加 Response Body 显示
            if hasattr(fixture_value, "last_response") and fixture_value.last_response:
                response = fixture_value.last_response
                if hasattr(response, "text"):
                    response_text = response.text
                    if response_text:
                        try:
                            import json
                            response_json = json.loads(response_text)
                            formatted_response = json.dumps(response_json, indent=2, ensure_ascii=False)
                            report.sections.append(("Response Body", f"<pre>{formatted_response}</pre>"))
                        except Exception:
                            report.sections.append(("Response Body", f"<pre>{response_text}</pre>"))


def pytest_report_teststatus(report, config):
    if report.when == "call":
        category = getattr(report, "category", "其他")
        description = getattr(report, "description", "")

        return (report.outcome, f"{category} - {description}", "")


@pytest.hookimpl(optionalhook=True)
def pytest_html_results_table_header(cells):
    cells.insert(2, "<th>分类</th>")
    cells.insert(3, "<th>描述</th>")
    cells.insert(4, "<th>API</th>")
    cells.insert(6, "<th>内存用量</th>")

    result = cells[0]
    test = cells[1]
    category = cells[2]
    description = cells[3]
    api = cells[4]
    duration = cells[5]
    memory = cells[6]

    cells.clear()
    cells.append(result)
    cells.append(category)
    cells.append(description)
    cells.append(api)
    cells.append(duration)
    cells.append(memory)
    cells.append(test)


@pytest.hookimpl(optionalhook=True)
def pytest_html_results_table_row(report, cells):
    if hasattr(report, "nodeid"):
        category = get_test_category(report.nodeid)
        description = get_test_description(report.nodeid)
        api = get_test_api(report.nodeid)
        memory_current = getattr(report, "memory_current", None)
        memory_delta = getattr(report, "memory_delta", None)

        memory_current_str = format_memory(memory_current)
        memory_delta_str = format_memory_delta(memory_delta)

        if memory_current_str and memory_delta_str:
            memory_str = f"{memory_current_str} ({memory_delta_str})"
        elif memory_current_str:
            memory_str = memory_current_str
        else:
            memory_str = ""

        cells.insert(2, f"<td>{category}</td>")
        cells.insert(3, f"<td>{description}</td>")
        cells.insert(4, f"<td>{api}</td>")
        cells.insert(6, f"<td>{memory_str}</td>")

    result = cells[0]
    test = cells[1]
    category = cells[2]
    description = cells[3]
    api = cells[4]
    duration = cells[5]
    memory = cells[6]

    cells.clear()
    cells.append(result)
    cells.append(category)
    cells.append(description)
    cells.append(api)
    cells.append(duration)
    cells.append(memory)
    cells.append(test)


@pytest.hookimpl(optionalhook=True)
def pytest_html_report_title(report):
    report.title = "OpenViking API测试报告"


@pytest.hookimpl(optionalhook=True)
def pytest_html_results_summary(prefix, summary, postfix):
    prefix.extend(
        [
            """
    <p><strong>OpenViking Version:</strong> 0.2.9</p>
    """
        ]
    )
    prefix.extend(
        [
            """
    <style>
        /* 隐藏时长描述 */
        .run-count {
            display: none !important;
        }

        /* 设置列宽度 */
        #results-table th:nth-child(1),
        #results-table td:nth-child(1) {
            width: 60px !important;
        }

        #results-table th:nth-child(2),
        #results-table td:nth-child(2) {
            width: 120px !important;
        }

        #results-table th:nth-child(3),
        #results-table td:nth-child(3) {
            width: 250px !important;
        }

        #results-table th:nth-child(4),
        #results-table td:nth-child(4) {
            width: 200px !important;
            font-family: monospace !important;
            font-size: 12px !important;
        }

        #results-table th:nth-child(5),
        #results-table td:nth-child(5) {
            width: 80px !important;
        }

        #results-table th:nth-child(6),
        #results-table td:nth-child(6) {
            width: 100px !important;
            text-align: right !important;
        }

        #results-table th:nth-child(7),
        #results-table td:nth-child(7) {
            width: 180px !important;
            max-width: 180px !important;
            overflow: hidden !important;
            text-overflow: ellipsis !important;
            white-space: nowrap !important;
        }
    </style>
    <script>
        function sortFailedFirst() {
            var table = document.getElementById('results-table');
            if (table) {
                var tbodies = table.querySelectorAll('tbody');
                tbodies.forEach(function(tbody) {
                    var rows = Array.from(tbody.querySelectorAll('tr.collapsible'));
                    if (rows.length > 0) {
                        var sortedRows = rows.sort(function(a, b) {
                            var aFailed = a.querySelector('.failed, .error') !== null ||
                                          (a.querySelector('.col-result') &&
                                           (a.querySelector('.col-result').textContent.includes('Failed') ||
                                            a.querySelector('.col-result').textContent.includes('Error')));
                            var bFailed = b.querySelector('.failed, .error') !== null ||
                                          (b.querySelector('.col-result') &&
                                           (b.querySelector('.col-result').textContent.includes('Failed') ||
                                            b.querySelector('.col-result').textContent.includes('Error')));
                            if (aFailed && !bFailed) return -1;
                            if (!aFailed && bFailed) return 1;
                            return 0;
                        });
                        sortedRows.forEach(function(row) {
                            tbody.insertBefore(row, tbody.firstChild);
                            if (row.nextElementSibling && row.nextElementSibling.classList.contains('extras-row')) {
                                tbody.insertBefore(row.nextElementSibling, tbody.firstChild.nextSibling);
                            }
                        });
                    }
                });
            }
        }

        document.addEventListener('DOMContentLoaded', function() {
            setTimeout(sortFailedFirst, 100);
            setTimeout(sortFailedFirst, 500);
            setTimeout(sortFailedFirst, 1000);
        });
    </script>
    """
        ]
    )
