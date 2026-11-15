"""Prepare safe Markdown writes from the selected completed stage."""

from openviking.utils.path_safety import safe_join_viking_uri
from vikingbot.compile import file_ops
from vikingbot.compile.hashing import content_hash
from vikingbot.compile.renderer import RenderedBundle, validate_relative_file_path


async def run(runtime, documents):
    """Reject duplicate paths and unseen replacements before preparing publication operations.

    Only selected, completed worker outputs are eligible. Existing target files require a
    prior read by a worker. This preflight does not provide atomic compare-and-swap writes.
    """
    bundle = RenderedBundle()
    contents, outputs = {}, {}
    for document in documents:
        path = validate_relative_file_path(document.path)
        if path in contents:
            raise ValueError(f"Multiple output writers claim {path}")
        content = (await runtime.files.sandbox.read_file_bytes(document.workspace_path)).decode(
            "utf-8"
        )
        if not path.lower().endswith(".md") or not content.strip():
            raise ValueError(f"Output must be nonempty UTF-8 Markdown: {path}")
        contents[path] = content
        outputs[path] = {"source_refs": document.source_refs, "sha256": content_hash(content)}
        uri = safe_join_viking_uri(runtime.target, path)
        read_before = path in runtime.old
        old = await file_ops.load_old(runtime, path)
        if old is not None and not read_before:
            # Create mode lets the server preserve an existing target as a reported conflict.
            mode = "create"
        elif old is not None:
            current = await runtime.client.download_bytes(uri)
            if content_hash(current) != content_hash(old):
                raise ValueError(f"Target changed after reading: {uri}")
            if current == content.encode("utf-8"):
                bundle.unchanged.append(uri)
                continue
            mode = "replace"
        else:
            mode = "create"
        bundle.operations.append({"uri": uri, "content": content, "mode": mode})
        (bundle.updated if mode == "replace" else bundle.created).append(uri)
        if runtime.output_format == "wiki":
            bundle.wiki_uris.append(uri)
        outputs[path] = {"source_refs": document.source_refs, "sha256": content_hash(content)}
    if runtime.skill_target and contents:
        file_ops.validate_skill_output(contents)
    await runtime.files.put("finalize", {"outputs": outputs})
    return bundle
