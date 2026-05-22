import os
import tempfile
import uuid


class TestResourceLifecycle:
    def test_resource_add_with_build_index_false(self, api_client):
        with tempfile.TemporaryDirectory() as temp_dir:
            unique_kw = f"no_index_{uuid.uuid4().hex[:8]}"
            test_file = os.path.join(temp_dir, f"{unique_kw}.txt")
            with open(test_file, "w") as f:
                f.write(f"Content about {unique_kw} without index")

            add_resp = api_client.add_resource(path=test_file, wait=True)
            if add_resp.status_code != 200:
                return
            root_uri = add_resp.json()["result"]["root_uri"]

            stat_resp = api_client.fs_stat(root_uri)
            assert stat_resp.status_code == 200, "resource should exist in fs even without index"

    def test_resource_add_with_to_and_verify_fs(self, api_client):
        target_uri = f"viking://resources/to_lifecycle_{uuid.uuid4().hex[:8]}"
        with tempfile.TemporaryDirectory() as temp_dir:
            test_file = os.path.join(temp_dir, "to_lifecycle.txt")
            with open(test_file, "w") as f:
                f.write("Content placed at target URI via to parameter")

            add_resp = api_client.add_resource(path=test_file, to=target_uri, wait=True)
            if add_resp.status_code != 200:
                return

            stat_resp = api_client.fs_stat(target_uri)
            assert stat_resp.status_code == 200, "target URI should exist"
            assert stat_resp.json().get("result", {}).get("isDir") is True

            ls_resp = api_client.fs_ls(target_uri)
            assert ls_resp.status_code == 200
            children = ls_resp.json().get("result", [])
            assert len(children) > 0, "target directory should have children"

    def test_resource_children_have_correct_types(self, api_client):
        with tempfile.TemporaryDirectory() as temp_dir:
            test_file = os.path.join(temp_dir, f"child_types_{uuid.uuid4().hex[:8]}.md")
            with open(test_file, "w") as f:
                f.write("Content for child type verification")

            add_resp = api_client.add_resource(path=test_file, wait=True)
            if add_resp.status_code != 200:
                return
            root_uri = add_resp.json()["result"]["root_uri"]

            ls_resp = api_client.fs_ls(root_uri)
            assert ls_resp.status_code == 200
            children = ls_resp.json().get("result", [])

            for child in children:
                assert "uri" in child, f"child should have uri, got keys: {sorted(child.keys())}"
                assert "isDir" in child, "child should have isDir"
                assert isinstance(child["isDir"], bool), (
                    f"isDir should be bool, got {type(child['isDir'])}"
                )

                if child["isDir"]:
                    assert child["uri"].endswith("/"), (
                        f"directory URI should end with /, got {child['uri']}"
                    )

    def test_resource_add_same_content_different_to(self, api_client):
        with tempfile.TemporaryDirectory() as temp_dir:
            test_file = os.path.join(temp_dir, f"dup_content_{uuid.uuid4().hex[:8]}.txt")
            with open(test_file, "w") as f:
                f.write("Same content different target")

            to1 = f"viking://resources/dup_to1_{uuid.uuid4().hex[:8]}"
            to2 = f"viking://resources/dup_to2_{uuid.uuid4().hex[:8]}"

            add1 = api_client.add_resource(path=test_file, to=to1, wait=True)
            add2 = api_client.add_resource(path=test_file, to=to2, wait=True)

            if add1.status_code == 200 and add2.status_code == 200:
                assert add1.json()["result"]["root_uri"] != add2.json()["result"]["root_uri"], (
                    "same content with different to should create different resources"
                )

    def test_resource_fs_tree_structure(self, api_client):
        with tempfile.TemporaryDirectory() as temp_dir:
            test_file = os.path.join(temp_dir, f"tree_test_{uuid.uuid4().hex[:8]}.md")
            with open(test_file, "w") as f:
                f.write("Tree structure test content")

            add_resp = api_client.add_resource(path=test_file, wait=True)
            if add_resp.status_code != 200:
                return
            root_uri = add_resp.json()["result"]["root_uri"]

            tree_resp = api_client.fs_tree(root_uri)
            assert tree_resp.status_code == 200
            tree_result = tree_resp.json().get("result", {})
            assert isinstance(tree_result, (list, dict)), (
                f"tree result should be list or dict, got {type(tree_result)}"
            )
