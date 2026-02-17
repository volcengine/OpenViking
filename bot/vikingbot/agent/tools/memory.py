"""Memory management tools using OpenViking APIs."""

from typing import Any, Optional
from pathlib import Path
from enum import Enum

from vikingbot.agent.tools.base import Tool


class MemoryCategory(str, Enum):
    """Memory categories from OpenViking."""
    PROFILE = "profile"
    PREFERENCES = "preferences"
    ENTITIES = "entities"
    EVENTS = "events"
    CASES = "cases"
    PATTERNS = "patterns"


class VikingMemoryAddTool(Tool):
    """Tool to add user memory or knowledge to OpenViking."""

    def __init__(
        self,
        workspace: Path,
    ):
        self._workspace = workspace

    @property
    def name(self) -> str:
        return "viking_memory_add"
    
    @property
    def description(self) -> str:
        return "Add or update a memory or knowledge item in OpenViking's persistent storage."
    
    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "The memory content to save. Can be in Markdown format."
                },
                "category": {
                    "type": "string",
                    "description": "Memory category: profile, preferences, entities, events, cases, or patterns",
                    "enum": ["profile", "preferences", "entities", "events", "cases", "patterns"]
                },
                "title": {
                    "type": "string",
                    "description": "Short title/name for this memory (used as filename)"
                },
                "abstract": {
                    "type": "string",
                    "description": "One-sentence summary of the memory (for indexing and search)"
                }
            },
            "required": ["content", "category", "title"]
        }
    
    async def execute(
        self,
        content: str,
        category: str,
        title: str,
        abstract: Optional[str] = None,
        **kwargs: Any
    ) -> str:
        try:
            from openviking import OpenViking
            
            # Initialize OpenViking client
            client = OpenViking()
            
            # Sanitize title for filename
            safe_title = "".join(c for c in title if c.isalnum() or c in (' ', '-', '_')).rstrip()
            safe_title = safe_title.replace(' ', '_').lower()
            
            # Build URI
            if category in ["cases", "patterns"]:
                uri = f"viking://agent/memories/{category}/{safe_title}.md"
            else:
                uri = f"viking://user/memories/{category}/{safe_title}.md"
            
            # Write L2 content (full)
            await client._async_client.viking_fs.write_file(uri, content)
            
            # Write L0 abstract if provided
            if abstract:
                abstract_uri = f"{uri}.abstract.md"
                await client._async_client.viking_fs.write_file(abstract_uri, abstract)
            
            # Also write L1 overview (structured version)
            overview_content = f"# {title}\n\n"
            if abstract:
                overview_content += f"{abstract}\n\n"
            overview_content += "## Details\n\n" + content
            overview_uri = f"{uri}.overview.md"
            await client._async_client.viking_fs.write_file(overview_uri, overview_content)
            
            # Create context for vectorization
            from openviking.core.context import Context, ContextType, Vectorize
            
            memory_context = Context(
                uri=uri,
                parent_uri=f"viking://{'agent' if category in ['cases', 'patterns'] else 'user'}/memories/{category}",
                is_leaf=True,
                abstract=abstract or title,
                context_type=ContextType.MEMORY.value,
                category=category
            )
            memory_context.set_vectorize(Vectorize(text=content))
            
            # TODO: Vectorize the memory (will happen automatically via queue)
            
            return f"Successfully saved memory to {uri}"
        except ImportError as e:
            return f"Error: OpenViking not available. {str(e)}"
        except Exception as e:
            return f"Error saving memory: {str(e)}"


class VikingMemorySearchTool(Tool):
    """Tool to search memories in OpenViking."""

    def __init__(
        self,
        workspace: Path,
    ):
        self._workspace = workspace

    @property
    def name(self) -> str:
        return "viking_memory_search"
    
    @property
    def description(self) -> str:
        return "Search for memories or knowledge in OpenViking using semantic search."
    
    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query to find relevant memories"
                },
                "category": {
                    "type": "string",
                    "description": "Filter by category: profile, preferences, entities, events, cases, patterns (optional)",
                    "enum": ["profile", "preferences", "entities", "events", "cases", "patterns"]
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of results to return (default: 10)",
                    "minimum": 1,
                    "maximum": 50
                }
            },
            "required": ["query"]
        }
    
    async def execute(
        self,
        query: str,
        category: Optional[str] = None,
        limit: int = 10,
        **kwargs: Any
    ) -> str:
        try:
            from openviking import OpenViking
            
            client = OpenViking()
            
            # Build target URI
            target_uri = ""
            if category:
                if category in ["cases", "patterns"]:
                    target_uri = f"viking://agent/memories/{category}"
                else:
                    target_uri = f"viking://user/memories/{category}"
            
            # Perform search
            results = client.find(query, target_uri=target_uri, limit=limit)
            
            if not results:
                return "No memories found matching your query."
            
            # Format results
            output = f"Found {len(results)} relevant memories:\n\n"
            for i, result in enumerate(results, 1):
                uri = result.get("uri", "")
                score = result.get("score", 0)
                output += f"{i}. {uri} (score: {score:.2f})\n"
                
                # Try to get abstract or content
                try:
                    abstract = client.abstract(uri)
                    if abstract:
                        output += f"   Summary: {abstract}\n"
                except:
                    pass
                output += "\n"
            
            return output
        except ImportError as e:
            return f"Error: OpenViking not available. {str(e)}"
        except Exception as e:
            return f"Error searching memories: {str(e)}"


class VikingMemoryReadTool(Tool):
    """Tool to read a specific memory from OpenViking."""

    def __init__(
        self,
        workspace: Path,
    ):
        self._workspace = workspace

    @property
    def name(self) -> str:
        return "viking_memory_read"
    
    @property
    def description(self) -> str:
        return "Read a specific memory or knowledge item from OpenViking by URI."
    
    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "uri": {
                    "type": "string",
                    "description": "The Viking URI of the memory to read (e.g., viking://user/memories/entities/project_x.md)"
                },
                "level": {
                    "type": "string",
                    "description": "Detail level: abstract (L0), overview (L1), or full (L2, default)",
                    "enum": ["abstract", "overview", "full"]
                }
            },
            "required": ["uri"]
        }
    
    async def execute(
        self,
        uri: str,
        level: str = "full",
        **kwargs: Any
    ) -> str:
        try:
            from openviking import OpenViking
            
            client = OpenViking()
            
            if level == "abstract":
                try:
                    content = client.abstract(uri)
                    if content:
                        return f"Abstract (L0) of {uri}:\n\n{content}"
                except:
                    pass
                return f"Abstract not available for {uri}"
            
            elif level == "overview":
                try:
                    content = client.overview(uri)
                    if content:
                        return f"Overview (L1) of {uri}:\n\n{content}"
                except:
                    pass
                # Fall back to full if overview not available
            
            # Full content (L2)
            content = client.read(uri)
            return f"Full content (L2) of {uri}:\n\n{content}"
        except ImportError as e:
            return f"Error: OpenViking not available. {str(e)}"
        except Exception as e:
            return f"Error reading memory: {str(e)}"


class VikingMemoryListTool(Tool):
    """Tool to list memories in OpenViking."""

    def __init__(
        self,
        workspace: Path,
    ):
        self._workspace = workspace

    @property
    def name(self) -> str:
        return "viking_memory_list"
    
    @property
    def description(self) -> str:
        return "List all memories or knowledge items in a specific category or all categories."
    
    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "description": "Category to list: profile, preferences, entities, events, cases, patterns (optional, list all if not specified)",
                    "enum": ["profile", "preferences", "entities", "events", "cases", "patterns"]
                }
            }
        }
    
    async def execute(
        self,
        category: Optional[str] = None,
        **kwargs: Any
    ) -> str:
        try:
            from openviking import OpenViking
            
            client = OpenViking()
            
            categories = [category] if category else list(MemoryCategory)
            output = ""
            
            for cat in categories:
                cat_str = str(cat) if isinstance(cat, MemoryCategory) else cat
                if cat_str in ["cases", "patterns"]:
                    uri = f"viking://agent/memories/{cat_str}"
                else:
                    uri = f"viking://user/memories/{cat_str}"
                
                try:
                    entries = client.ls(uri)
                    if entries:
                        output += f"## {cat_str.upper()}\n"
                        for entry in entries:
                            if not entry.endswith(('.abstract.md', '.overview.md')) and entry.endswith('.md'):
                                output += f"- {entry}\n"
                        output += "\n"
                except Exception as e:
                    # Category might not exist yet
                    pass
            
            if not output:
                return "No memories found in any category."
            
            return f"Memories organized by category:\n\n{output}"
        except ImportError as e:
            return f"Error: OpenViking not available. {str(e)}"
        except Exception as e:
            return f"Error listing memories: {str(e)}"
