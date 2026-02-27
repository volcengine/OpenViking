import json
import sys
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import gradio as gr

from vikingbot.config.loader import load_config, save_config
from vikingbot.config.schema import Config, ChannelType, SandboxBackend, SandboxMode


def resolve_schema_ref(
    schema: Dict[str, Any], ref: str, root_schema: Dict[str, Any]
) -> Dict[str, Any]:
    if ref.startswith("#/$defs/"):
        def_name = ref[len("#/$defs/") :]
        return root_schema["$defs"].get(def_name, {})
    return schema


def get_effective_schema(field_info: Dict[str, Any], root_schema: Dict[str, Any]) -> Dict[str, Any]:
    if "$ref" in field_info:
        return get_effective_schema(
            resolve_schema_ref(field_info, field_info["$ref"], root_schema), root_schema
        )
    return field_info


def create_dashboard_tab():
    with gr.Tab("Dashboard"):
        from vikingbot import __version__

        config = load_config()
        gr.Markdown("# ⚓ Vikingbot Console")
        gr.Markdown(f"""
        | Status | Value |
        |--------|-------|
        | 🟢 Status | Running |
        | 📦 Version | {__version__} |
        | 📁 Config Path | {str(config.workspace_path.parent / "config.json")} |
        | 🖥️ Workspace Path | {str(config.workspace_path)} |
        """)


def create_field_group(
    field_name: str,
    field_info: Dict[str, Any],
    current_value: Any,
    parent_path: str = "",
    root_schema: Optional[Dict[str, Any]] = None,
) -> Tuple[List, Dict[str, Any]]:
    if root_schema is None:
        root_schema = Config.model_json_schema()

    field_path = f"{parent_path}.{field_name}" if parent_path else field_name

    tab_paths = {"providers", "sandbox.backends"}

    effective_field_info = get_effective_schema(field_info, root_schema)
    description = effective_field_info.get("description", "")
    title = effective_field_info.get("title", field_name.replace("_", " ").title())
    components = []
    field_metadata = {}

    field_type = effective_field_info.get("type", "string")
    enum_values = effective_field_info.get("enum")

    if enum_values:
        dropdown = gr.Dropdown(
            choices=enum_values,
            value=current_value,
            label=title,
            elem_id=f"field_{field_path.replace('.', '_')}",
        )
        components.append(dropdown)
        field_metadata[field_path] = {"type": "enum"}
    elif field_type == "object" and "properties" in effective_field_info:
        properties = list(effective_field_info["properties"].items())
        if field_path in tab_paths and len(properties) > 1:
            with gr.Tab(title):
                if description:
                    gr.Markdown(f"*{description}*")
                with gr.Tabs():
                    for nested_field_name, nested_field_info in properties:
                        with gr.Tab(
                            nested_field_info.get(
                                "title", nested_field_name.replace("_", " ").title()
                            )
                        ):
                            nested_value = (
                                current_value.get(nested_field_name, None)
                                if current_value
                                else None
                            )
                            nested_components, nested_metadata = create_field_group(
                                nested_field_name,
                                nested_field_info,
                                nested_value,
                                field_path,
                                root_schema,
                            )
                            components.extend(nested_components)
                            field_metadata.update(nested_metadata)
        else:
            with gr.Group():
                gr.Markdown(f"### {title}")
                if description:
                    gr.Markdown(f"*{description}*")
                for nested_field_name, nested_field_info in properties:
                    nested_value = (
                        current_value.get(nested_field_name, None) if current_value else None
                    )
                    nested_components, nested_metadata = create_field_group(
                        nested_field_name, nested_field_info, nested_value, field_path, root_schema
                    )
                    components.extend(nested_components)
                    field_metadata.update(nested_metadata)
    elif field_type == "array":
        items_info = effective_field_info.get("items", {})
        effective_items_info = get_effective_schema(items_info, root_schema)
        items_type = effective_items_info.get("type", "string")
        use_textbox = False
        if items_type == "string" and not (
            "properties" in effective_items_info or items_type == "object"
        ):
            current_list = current_value or []
            if all(isinstance(item, str) for item in current_list):
                use_textbox = True

        if use_textbox:
            current_list = current_value or []
            value = "\n".join(current_list) if current_list else ""
            textbox = gr.Textbox(
                value=value,
                label=f"{title} (one per line)",
                lines=3,
                elem_id=f"field_{field_path.replace('.', '_')}",
            )
            components.append(textbox)
            field_metadata[field_path] = {"type": "array", "items_type": "string"}
        else:
            value = json.dumps(current_value, indent=2) if current_value else ""
            code = gr.Code(
                value=value,
                label=title,
                language="json",
                elem_id=f"field_{field_path.replace('.', '_')}",
            )
            components.append(code)
            field_metadata[field_path] = {"type": "array", "items_type": "json"}
    elif field_type == "integer":
        number = gr.Number(
            value=current_value, label=title, elem_id=f"field_{field_path.replace('.', '_')}"
        )
        components.append(number)
        field_metadata[field_path] = {"type": "integer"}
    elif field_type == "number":
        number = gr.Number(
            value=current_value, label=title, elem_id=f"field_{field_path.replace('.', '_')}"
        )
        components.append(number)
        field_metadata[field_path] = {"type": "number"}
    elif field_type == "boolean":
        checkbox = gr.Checkbox(
            value=current_value or False,
            label=title,
            elem_id=f"field_{field_path.replace('.', '_')}",
        )
        components.append(checkbox)
        field_metadata[field_path] = {"type": "boolean"}
    else:
        textbox = gr.Textbox(
            value=current_value or "", label=title, elem_id=f"field_{field_path.replace('.', '_')}"
        )
        components.append(textbox)
        field_metadata[field_path] = {"type": "string"}

    return components, field_metadata


def collect_values_from_components(
    components, schema, parent_path: str = "", root_schema: Optional[Dict[str, Any]] = None
) -> Tuple[Dict[str, Any], int]:
    if root_schema is None:
        root_schema = Config.model_json_schema()

    result = {}
    comp_idx = 0

    for field_name, field_info in schema.get("properties", {}).items():
        field_path = f"{parent_path}.{field_name}" if parent_path else field_name
        effective_field_info = get_effective_schema(field_info, root_schema)
        field_type = effective_field_info.get("type", "string")

        if field_type == "object" and "properties" in effective_field_info:
            nested_result, num_consumed = collect_values_from_components(
                components[comp_idx:], effective_field_info, field_path, root_schema
            )
            result[field_name] = nested_result
            comp_idx += num_consumed
        else:
            component = components[comp_idx]
            comp_idx += 1

            if hasattr(component, "value"):
                value = component.value

                if field_type == "array":
                    items_info = effective_field_info.get("items", {})
                    effective_items_info = get_effective_schema(items_info, root_schema)
                    items_type = effective_items_info.get("type", "string")
                    if items_type == "string" and isinstance(value, str):
                        value = [line.strip() for line in value.split("\n") if line.strip()]
                    elif isinstance(value, str):
                        try:
                            value = json.loads(value)
                        except:
                            value = []

                result[field_name] = value

    return result, comp_idx


def create_config_tabs():
    config = load_config()
    config_dict = config.model_dump()
    schema = Config.model_json_schema()

    all_components = []
    component_metadata = {}

    top_level_fields = list(schema["properties"].keys())

    with gr.Tabs():
        for field_name in top_level_fields:
            if field_name in schema["properties"]:
                with gr.Tab(
                    schema["properties"][field_name].get(
                        "title", field_name.replace("_", " ").title()
                    )
                ):
                    gr.Markdown(
                        f"## {schema['properties'][field_name].get('title', field_name.replace('_', ' ').title())}"
                    )
                    field_info = schema["properties"][field_name]
                    current_value = config_dict.get(field_name, None)
                    components, metadata = create_field_group(
                        field_name, field_info, current_value, root_schema=schema
                    )
                    all_components.extend(components)
                    component_metadata.update(metadata)

    save_btn = gr.Button("Save Config", variant="primary")
    status_msg = gr.Markdown("")

    def save_config_fn(*args):
        try:
            config_dict = load_config().model_dump()

            remaining_args = list(args)
            comp_idx = 0

            for field_name in top_level_fields:
                if field_name in schema["properties"]:
                    field_info = schema["properties"][field_name]
                    field_result, num_consumed = collect_values_from_components(
                        remaining_args[comp_idx:], field_info, root_schema=schema
                    )
                    config_dict[field_name] = field_result
                    comp_idx += num_consumed

            config = Config(**config_dict)
            save_config(config)
            return "✓ Config saved successfully! Please restart the gateway service for changes to take effect."
        except Exception as e:
            return f"✗ Error: {str(e)}"

    save_btn.click(fn=save_config_fn, inputs=all_components, outputs=status_msg)


def create_sessions_tab():
    with gr.Tab("Sessions"):
        gr.Markdown("## Sessions")

        with gr.Row():
            with gr.Column(scale=1):
                session_list = gr.Dropdown(
                    choices=[],
                    label="Select Session",
                    info="Click Refresh to load sessions",
                    allow_custom_value=True,
                )
                refresh_btn = gr.Button("Refresh Sessions")

            with gr.Column(scale=2):
                session_content = gr.HTML(value="", label="Session Content")
                status_msg = gr.Markdown("")

        def refresh_sessions():
            sessions_dir = Path.home() / ".vikingbot" / "sessions"
            if not sessions_dir.exists():
                return gr.Dropdown(choices=[], value=None), ""
            session_files = list(sessions_dir.glob("*.jsonl")) + list(sessions_dir.glob("*.json"))
            session_names = [f.stem for f in session_files]
            return gr.Dropdown(choices=session_names, value=None), ""

        def load_session(session_name):
            if not session_name:
                return "", "Please select a session"
            sessions_dir = Path.home() / ".vikingbot" / "sessions"
            session_file_jsonl = sessions_dir / f"{session_name}.jsonl"
            session_file_json = sessions_dir / f"{session_name}.json"

            lines = []
            if session_file_jsonl.exists():
                with open(session_file_jsonl, "r") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            data = json.loads(line)
                            role = data.get("role", "")
                            content = data.get("content", "")
                            if role == "user":
                                lines.append(
                                    f'<div style="color: green;"><b>User:</b> {content}</div>'
                                )
                            elif role == "assistant":
                                lines.append(
                                    f'<div style="color: red;"><b>Assistant:</b> {content}</div>'
                                )
                            else:
                                lines.append(
                                    f'<div style="color: black;"><b>{role}:</b> {content}</div>'
                                )
                        except:
                            lines.append(f'<div style="color: black;">{line}</div>')
            elif session_file_json.exists():
                with open(session_file_json, "r") as f:
                    return f.read(), ""
            else:
                return "Session not found", ""
            return "<br>".join(lines), ""

        refresh_btn.click(fn=refresh_sessions, outputs=[session_list, status_msg])

        session_list.change(
            fn=load_session, inputs=session_list, outputs=[session_content, status_msg]
        )


def create_workspace_tab():
    with gr.Tab("Workspace"):
        gr.Markdown("## Workspace")
        config = load_config()
        workspace_path = config.workspace_path
        # Create workspace directory if it doesn't exist
        workspace_path.mkdir(parents=True, exist_ok=True)
        workspace_path_str = str(workspace_path)

        with gr.Row():
            with gr.Column(scale=1):
                file_explorer = gr.FileExplorer(
                    root_dir=workspace_path_str,
                    label="Workspace File Explorer",
                    file_count="single",
                )

            with gr.Column(scale=2):
                file_content = gr.Code(
                    value="", label="File Content", language="python", interactive=False
                )
                status_msg = gr.Markdown("")

        def load_file_content(selected_file):
            if not selected_file:
                return "", "Please select a file to view"

            if Path(selected_file).is_file():
                try:
                    with open(selected_file, "r") as f:
                        return f.read(), f"Loaded {Path(selected_file).name}"
                except:
                    return "Cannot read file (binary or encoding error)", ""
            elif Path(selected_file).is_dir():
                return "", f"{Path(selected_file).name} is a directory"
            return "", "File not found"

        file_explorer.change(
            fn=load_file_content, inputs=file_explorer, outputs=[file_content, status_msg]
        )


with gr.Blocks(title="Vikingbot Console") as demo:
    with gr.Tabs():
        create_dashboard_tab()
        with gr.Tab("Config"):
            create_config_tabs()
        create_sessions_tab()
        create_workspace_tab()


if __name__ == "__main__":
    import uvicorn
    from fastapi import FastAPI

    port = 18791
    if len(sys.argv) > 1:
        try:
            port = int(sys.argv[1])
        except ValueError:
            pass

    # Create FastAPI app for health endpoint
    app = FastAPI()

    # Add /health endpoint
    @app.get("/health")
    async def health_endpoint():
        from vikingbot import __version__

        return {"status": "healthy", "version": __version__}

    # Mount Gradio app
    demo.queue()
    app = gr.mount_gradio_app(app, demo, path="/")

    # Launch with uvicorn
    uvicorn.run(app, host="0.0.0.0", port=port)
