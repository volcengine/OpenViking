import os
import shutil
import tempfile
import uuid


def create_test_file(content=None, suffix=".txt", filename=None):
    if content is None:
        content = (
            f"测试文件内容 - {uuid.uuid4()}\n这是一个用于构建测试的临时文件。\n包含一些测试数据。"
        )

    temp_dir = tempfile.mkdtemp()
    fname = filename or f"test_file_{str(uuid.uuid4())[:8]}{suffix}"
    test_file_path = os.path.join(temp_dir, fname)

    with open(test_file_path, "w", encoding="utf-8") as f:
        f.write(content)

    return test_file_path, temp_dir


def create_test_directory(file_count=3, nested=True):
    temp_dir = tempfile.mkdtemp()

    for i in range(file_count):
        file_path = os.path.join(temp_dir, f"file_{i}.txt")
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(f"测试文件 {i} 的内容\n一些测试数据 {uuid.uuid4()}")

    if nested:
        subdir = os.path.join(temp_dir, "subdir")
        os.makedirs(subdir)
        with open(os.path.join(subdir, "nested_file.txt"), "w", encoding="utf-8") as f:
            f.write("嵌套文件的内容")

    return temp_dir


def create_test_zip(file_count=3):
    import zipfile

    temp_dir = tempfile.mkdtemp()
    zip_path = os.path.join(temp_dir, f"test_archive_{str(uuid.uuid4())[:8]}.zip")

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for i in range(file_count):
            content = f"压缩包内文件 {i} 的内容\n测试数据 {uuid.uuid4()}"
            zf.writestr(f"file_{i}.txt", content)
        zf.writestr("subdir/nested_file.txt", "压缩包内嵌套文件的内容")

    return zip_path, temp_dir


def cleanup_temp_dir(temp_dir):
    if temp_dir and os.path.exists(temp_dir):
        shutil.rmtree(temp_dir, ignore_errors=True)


def assert_root_uri_valid(root_uri):
    assert root_uri, "add_resource 必须返回 root_uri"
    assert root_uri.startswith("viking://resources/"), (
        f"root_uri 应以 viking://resources/ 开头, 实际: {root_uri}"
    )


def assert_resource_findable(api_client, root_uri, keyword, timeout=90, poll_interval=3):
    import time

    api_client.wait_processed()

    start = time.time()
    poll_count = 0
    while time.time() - start < timeout:
        poll_count += 1

        if poll_count % 3 == 0:
            api_client.wait_processed()

        find_resp = api_client.find(keyword, target_uri=root_uri)
        if find_resp.status_code == 200:
            find_data = find_resp.json()
            if find_data.get("status") == "ok":
                search_result = find_data.get("result", {})
                for field in ["resources", "memories", "matches"]:
                    items = search_result.get(field, [])
                    if not isinstance(items, list):
                        continue
                    for item in items:
                        if isinstance(item, dict) and "uri" in item and root_uri in item["uri"]:
                            return True
                        elif isinstance(item, str) and root_uri in item:
                            return True

        if time.time() - start >= timeout:
            break
        time.sleep(poll_interval)

    return False


def assert_resource_indexed(api_client, root_uri, keyword, timeout=120):
    found = assert_resource_findable(api_client, root_uri, keyword, timeout=timeout)
    assert found, f"搜索应能检索到资源 {root_uri}, 关键词: {keyword}"
    return found


def _extract_error_message(data):
    if not isinstance(data, dict):
        return str(data)
    error = data.get("error")
    if isinstance(error, dict):
        return error.get("message", "") + " " + error.get("code", "")
    errors = data.get("errors", [])
    if isinstance(errors, list):
        return " ".join(str(e) for e in errors)
    return str(data.get("message", data.get("msg", "")))


def assert_source_format(api_client, root_uri, expected_format):
    if isinstance(expected_format, str):
        expected_format = [expected_format]

    stat_resp = api_client.fs_stat(root_uri)
    assert stat_resp.status_code == 200, f"fs_stat 应返回200, root_uri: {root_uri}"
    stat_data = stat_resp.json()
    stat_result = stat_data.get("result")

    if isinstance(stat_result, dict):
        actual_format = stat_result.get("source_format")
        if actual_format:
            assert actual_format in expected_format, (
                f"source_format 应为 {expected_format}, 实际: {actual_format}"
            )
            return

    tree_resp = api_client.fs_tree(root_uri)
    if tree_resp.status_code == 200:
        tree_data = tree_resp.json()
        tree_result = tree_data.get("result")
        if isinstance(tree_result, dict):
            actual_format = tree_result.get("source_format")
            if actual_format:
                assert actual_format in expected_format, (
                    f"source_format 应为 {expected_format}, 实际: {actual_format}"
                )
                return
            children = tree_result.get("children", [])
        elif isinstance(tree_result, list):
            children = tree_result
        else:
            children = []

        for child in children:
            if isinstance(child, dict):
                actual_format = child.get("source_format")
                if actual_format:
                    assert actual_format in expected_format, (
                        f"source_format 应为 {expected_format}, 实际: {actual_format}"
                    )
                    return

    ls_resp = api_client.fs_ls(root_uri, recursive=True)
    if ls_resp.status_code == 200:
        ls_data = ls_resp.json()
        ls_result = ls_data.get("result")
        items = ls_result if isinstance(ls_result, list) else []
        for item in items:
            if isinstance(item, dict):
                actual_format = item.get("source_format")
                if actual_format:
                    assert actual_format in expected_format, (
                        f"source_format 应为 {expected_format}, 实际: {actual_format}"
                    )
                    return

    ext_format_map = {
        ".txt": ["text", "markdown"],
        ".text": ["text"],
        ".md": ["markdown"],
        ".markdown": ["markdown"],
        ".pdf": ["pdf"],
        ".html": ["html"],
        ".htm": ["html"],
        ".docx": ["docx"],
        ".doc": ["doc"],
        ".pptx": ["pptx"],
        ".xlsx": ["xlsx"],
        ".epub": ["epub"],
        ".zip": ["zip"],
        ".png": ["image"],
        ".jpg": ["image"],
        ".jpeg": ["image"],
        ".mp3": ["audio"],
        ".wav": ["audio"],
        ".mp4": ["video"],
    }
    if ls_resp.status_code == 200:
        ls_data = ls_resp.json()
        ls_result = ls_data.get("result")
        items = ls_result if isinstance(ls_result, list) else []
        for item in items:
            if isinstance(item, dict):
                rel_path = item.get("rel_path", "")
                uri = item.get("uri", "")
                for ext, fmt_list in ext_format_map.items():
                    if rel_path.endswith(ext) or uri.endswith(ext):
                        assert any(f in expected_format for f in fmt_list), (
                            f"根据扩展名 {ext} 推断 source_format 应为 {fmt_list}, 期望: {expected_format}"
                        )
                        return


def assert_tree_has_child_nodes(api_client, root_uri, min_nodes=1):
    tree_resp = api_client.fs_tree(root_uri)
    assert tree_resp.status_code == 200, f"fs_tree 应返回200, root_uri: {root_uri}"
    tree_data = tree_resp.json()
    tree_result = tree_data.get("result")

    if isinstance(tree_result, list):
        children = tree_result
    elif isinstance(tree_result, dict):
        children = tree_result.get("children", [])
    else:
        children = []

    assert len(children) >= min_nodes, (
        f"tree 应至少包含 {min_nodes} 个子节点, 实际: {len(children)}"
    )


def assert_content_no_html_tags(api_client, root_uri):
    grep_resp = api_client.grep(root_uri, pattern="<[a-z][a-z0-9]*[^>]*>")
    if grep_resp.status_code != 200:
        return
    grep_data = grep_resp.json()
    grep_result = grep_data.get("result", {})
    matches = grep_result.get("matches", [])
    html_matches = [m for m in matches if isinstance(m, dict) and "<" in str(m.get("line", ""))]
    assert len(html_matches) == 0, (
        f"HTML内容应被剥离, 但仍包含HTML标签: {[m.get('line', '')[:80] for m in html_matches[:3]]}"
    )


def assert_overview_or_abstract_contains(
    api_client, root_uri, keyword, timeout=60, poll_interval=3
):
    import time

    start = time.time()
    while time.time() - start < timeout:
        overview_resp = api_client.get_overview(root_uri)
        if overview_resp.status_code == 200:
            overview_data = overview_resp.json()
            overview_text = str(overview_data.get("result", ""))
            if keyword.lower() in overview_text.lower():
                return True

        abstract_resp = api_client.get_abstract(root_uri)
        if abstract_resp.status_code == 200:
            abstract_data = abstract_resp.json()
            abstract_text = str(abstract_data.get("result", ""))
            if keyword.lower() in abstract_text.lower():
                return True

        if time.time() - start >= timeout:
            break
        time.sleep(poll_interval)

    return False
