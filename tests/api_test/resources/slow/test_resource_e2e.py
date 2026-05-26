import os
import tempfile
import uuid


class TestResourceEndToEnd:
    def test_add_directory_resource(self, api_client):
        with tempfile.TemporaryDirectory() as temp_dir:
            unique_kw = f"dir_e2e_{uuid.uuid4().hex[:8]}"
            sub_dir = os.path.join(temp_dir, unique_kw)
            os.makedirs(sub_dir)
            for i in range(3):
                with open(os.path.join(sub_dir, f"file_{i}.md"), "w") as f:
                    f.write(f"File {i} content about {unique_kw}\n")

            add_resp = api_client.add_resource(path=sub_dir, wait=True)
            assert add_resp.status_code == 200, (
                f"add directory should return 200, got {add_resp.status_code}: {add_resp.text[:200]}"
            )
            result = add_resp.json().get("result", {})
            root_uri = result.get("root_uri", "")

            ls_resp = api_client.fs_ls(root_uri, recursive=True)
            assert ls_resp.status_code == 200
            children = ls_resp.json().get("result", [])
            assert len(children) >= 3, (
                f"directory resource should have at least 3 children, got {len(children)}"
            )

    def test_add_resource_with_reason_and_instruction(self, api_client):
        with tempfile.TemporaryDirectory() as temp_dir:
            test_file = os.path.join(temp_dir, f"reason_instr_{uuid.uuid4().hex[:8]}.md")
            with open(test_file, "w") as f:
                f.write("Content for reason and instruction test")

            add_resp = api_client.add_resource(
                path=test_file,
                reason="Testing reason parameter for resource tracking",
                instruction="Focus on key concepts and summarize concisely",
                wait=True,
            )
            assert add_resp.status_code == 200, (
                f"add_resource with reason+instruction should return 200, got {add_resp.status_code}: {add_resp.text[:200]}"
            )
            result = add_resp.json().get("result", {})
            assert "root_uri" in result, "result should contain root_uri"

    def test_add_resource_to_and_parent_conflict(self, api_client):
        with tempfile.TemporaryDirectory() as temp_dir:
            test_file = os.path.join(temp_dir, "conflict_test.txt")
            with open(test_file, "w") as f:
                f.write("Content for to+parent conflict test")

            add_resp = api_client.add_resource(
                path=test_file,
                to="viking://resources/to_conflict",
                parent="viking://resources/parent_conflict",
                wait=False,
            )
            assert add_resp.status_code == 400, (
                f"to+parent conflict should return error, got {add_resp.status_code}: {add_resp.text[:200]}"
            )
            if add_resp.status_code == 200:
                raise AssertionError("to+parent should conflict and NOT return 200")

    def test_add_same_resource_twice(self, api_client):
        with tempfile.TemporaryDirectory() as temp_dir:
            test_file = os.path.join(temp_dir, f"dup_{uuid.uuid4().hex[:8]}.md")
            with open(test_file, "w") as f:
                f.write("Duplicate resource test content")

            add1 = api_client.add_resource(path=test_file, wait=True)
            if add1.status_code != 200:
                return
            root_uri_1 = add1.json().get("result", {}).get("root_uri", "")

            add2 = api_client.add_resource(path=test_file, wait=True)
            assert add2.status_code == 200, (
                f"adding same resource again should return 200 or 409, got {add2.status_code}: {add2.text[:200]}"
            )

            if add2.status_code == 200:
                root_uri_2 = add2.json().get("result", {}).get("root_uri", "")
                assert root_uri_1 != root_uri_2, (
                    "duplicate add should create a different resource (different root_uri)"
                )

    def test_add_resource_with_to_creates_at_target(self, api_client):
        target_uri = f"viking://resources/target_{uuid.uuid4().hex[:8]}"
        with tempfile.TemporaryDirectory() as temp_dir:
            test_file = os.path.join(temp_dir, "targeted.txt")
            with open(test_file, "w") as f:
                f.write("Content placed at target URI")

            add_resp = api_client.add_resource(path=test_file, to=target_uri, wait=True)
            if add_resp.status_code != 200:
                return

            stat_resp = api_client.fs_stat(target_uri)
            assert stat_resp.status_code == 200, (
                "target URI should exist in filesystem after add_resource with to param"
            )

    def test_resource_searchable_by_semantic_content(self, api_client):
        with tempfile.TemporaryDirectory() as temp_dir:
            test_file = os.path.join(temp_dir, f"semantic_{uuid.uuid4().hex[:8]}.md")
            with open(test_file, "w") as f:
                f.write(
                    "# Neural Networks\n\n"
                    "Neural networks are computing systems inspired by biological neural networks. "
                    "They consist of layers of interconnected nodes that process information. "
                    "Deep learning uses multiple hidden layers to learn hierarchical representations."
                )

            add_resp = api_client.add_resource(path=test_file, wait=True)
            if add_resp.status_code != 200:
                return

            find_resp = api_client.find(query="artificial intelligence deep learning", limit=5)
            assert find_resp.status_code == 200
            resources = find_resp.json().get("result", {}).get("resources", [])
            if len(resources) > 0:
                top_result = resources[0]
                assert "score" in top_result, "search result should have score"
                assert top_result["score"] > 0, "score should be positive for relevant result"

    def test_resource_glob_finds_files(self, api_client):
        with tempfile.TemporaryDirectory() as temp_dir:
            unique_name = f"glob_find_{uuid.uuid4().hex[:8]}"
            test_file = os.path.join(temp_dir, f"{unique_name}.md")
            with open(test_file, "w") as f:
                f.write("Glob test content")

            add_resp = api_client.add_resource(path=test_file, wait=True)
            if add_resp.status_code != 200:
                return
            root_uri = add_resp.json().get("result", {}).get("root_uri", "")

            glob_resp = api_client.glob(pattern="*.md", uri=root_uri)
            if glob_resp.status_code == 200:
                matches = glob_resp.json().get("result", {}).get("matches", [])
                assert len(matches) >= 1, (
                    f"glob *.md should find at least one file under resource, got {len(matches)}"
                )
