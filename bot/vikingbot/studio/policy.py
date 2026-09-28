"""Tools permitted for externally reachable Studio-managed group conversations."""

GROUP_TOOLS = frozenset(
    {
        "openviking_list",
        "openviking_search",
        "openviking_grep",
        "openviking_glob",
        "openviking_multi_read",
        "openviking_memory_commit",
    }
)


def disabled_group_tools(tool_names):
    # Deny by default, including installed MCP tools and local filesystem/Shell.
    return [name for name in tool_names if name not in GROUP_TOOLS]
