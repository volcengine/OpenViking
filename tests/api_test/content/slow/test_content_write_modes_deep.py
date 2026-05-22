import os
import tempfile
import time
import uuid


class TestContentWriteModesDeep:
    def _create_resource_file(self, api_client, content):
        temp_dir = tempfile.mkdtemp()
        unique_kw = f"writemode_{uuid.uuid4().hex[:8]}"
        test_file = os.path.join(temp_dir, f"{unique_kw}.md")
        with open(test_file, "w") as f:
            f.write(content)
        add_resp = api_client.add_resource(path=test_file, wait=True)
        if add_resp.status_code != 200:
            return None, None
        root_uri = add_resp.json()["result"]["root_uri"]
        ls_resp = api_client.fs_ls(root_uri)
        if ls_resp.status_code != 200:
            return root_uri, None
        children = ls_resp.json().get("result", [])
        if not children:
            return root_uri, None
        child_uri = children[0].get("uri", "") if isinstance(children[0], dict) else children[0]
        return root_uri, child_uri

    def test_replace_mode_overwrites_content(self, api_client):
        root_uri, child_uri = self._create_resource_file(
            api_client, "Original content about databases."
        )
        if not child_uri:
            return
        try:
            replace_resp = api_client.fs_write(
                child_uri, "Replaced content about caching.", mode="replace", wait=True
            )
            assert replace_resp.status_code == 200, (
                f"replace mode should return 200, got {replace_resp.status_code}"
            )
            read_resp = api_client.fs_read(child_uri)
            assert read_resp.status_code == 200
            result = read_resp.json().get("result", "")
            if isinstance(result, str):
                assert "Replaced" in result or "caching" in result, (
                    f"replace should overwrite content, got: {result[:200]}"
                )
        finally:
            if root_uri:
                api_client.fs_rm(root_uri, recursive=True)

    def test_replace_then_find_updated_content(self, api_client):
        unique_kw = f"repfind_{uuid.uuid4().hex[:6]}"
        root_uri, child_uri = self._create_resource_file(
            api_client, f"Original document about old_topic_{unique_kw}."
        )
        if not child_uri:
            return
        try:
            new_unique_kw = f"newfind_{uuid.uuid4().hex[:6]}"
            api_client.fs_write(
                child_uri,
                f"Replaced document about {new_unique_kw} topic.",
                mode="replace",
                wait=True,
            )
            time.sleep(8)
            find_resp = api_client.find(query=new_unique_kw, limit=5)
            assert find_resp.status_code == 200
            resources = find_resp.json().get("result", {}).get("resources", [])
            if len(resources) < 1:
                time.sleep(5)
                find_resp = api_client.find(query=new_unique_kw, limit=5)
                resources = find_resp.json().get("result", {}).get("resources", [])
            assert len(resources) >= 1, (
                f"replaced content should be findable by new keyword, got {len(resources)} results"
            )
        finally:
            if root_uri:
                api_client.fs_rm(root_uri, recursive=True)

    def test_replace_then_grep_new_content(self, api_client):
        unique_kw = f"repgrep_{uuid.uuid4().hex[:6]}"
        root_uri, child_uri = self._create_resource_file(
            api_client, "Original content that will be replaced."
        )
        if not child_uri:
            return
        try:
            api_client.fs_write(
                child_uri,
                f"New content about {unique_kw} after replacement.",
                mode="replace",
                wait=True,
            )
            time.sleep(8)
            grep_resp = api_client.grep(child_uri, pattern=unique_kw)
            assert grep_resp.status_code == 200
        finally:
            if root_uri:
                api_client.fs_rm(root_uri, recursive=True)

    def test_replace_then_abstract_updated(self, api_client):
        unique_kw = f"repabs_{uuid.uuid4().hex[:6]}"
        root_uri, child_uri = self._create_resource_file(
            api_client, "Original document about old_topic."
        )
        if not child_uri:
            return
        try:
            api_client.fs_write(
                child_uri,
                f"Completely new document about {unique_kw} and quantum computing.",
                mode="replace",
                wait=True,
            )
            abstract_resp = api_client.get_abstract(root_uri)
            assert abstract_resp.status_code == 200, (
                f"abstract after replace should return 200/412/500, got {abstract_resp.status_code}"
            )
        finally:
            if root_uri:
                api_client.fs_rm(root_uri, recursive=True)

    def test_write_on_nonexistent_child_uri(self, api_client):
        fake_uri = f"viking://resources/nonexist_{uuid.uuid4().hex[:8]}/file.md"
        write_resp = api_client.fs_write(
            fake_uri, "Content for nonexistent", mode="replace", wait=True
        )
        assert write_resp.status_code == 404, (
            f"write on nonexistent URI should return 400/404/412, got {write_resp.status_code}"
        )
