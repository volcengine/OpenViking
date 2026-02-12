import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

# Ensure the project root is in sys.path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../"))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from openviking.models.embedder.base import EmbedResult  # noqa: E402


class TestQuickStartLite(unittest.TestCase):
    def setUp(self):
        # Clean up data directory if exists to ensure fresh start
        self.data_dir = os.path.join(PROJECT_ROOT, "examples", "data")
        if os.path.exists(self.data_dir):
            shutil.rmtree(self.data_dir)

        # Create a temporary config file
        self.config_dir = tempfile.mkdtemp()
        self.config_file = os.path.join(self.config_dir, "ov_test.json")

        # Create a dummy config structure (minimal valid config for Volcengine provider)
        config_data = {
            "storage": {"agfs": {"port": 1833}},
            "embedding": {
                "dense": {
                    "provider": "volcengine",
                    "model": "dummy_embedding_model",
                    "api_key": "dummy_embedding_key",
                    "api_base": "https://dummy.api.com",
                    "dimension": 2048,
                }
            },
            "vlm": {
                "provider": "volcengine",
                "model": "dummy_vlm_model",
                "api_key": "dummy_vlm_key",
                "api_base": "https://dummy.api.com",
            },
        }

        with open(self.config_file, "w") as f:
            json.dump(config_data, f)

    def tearDown(self):
        # Cleanup
        if os.path.exists(self.data_dir):
            try:
                shutil.rmtree(self.data_dir)
            except:
                pass

        # Cleanup temp config
        shutil.rmtree(self.config_dir)

        # Reset OpenVikingConfig singleton to avoid side effects
        from openviking_cli.utils.config.open_viking_config import OpenVikingConfigSingleton

        OpenVikingConfigSingleton.reset_instance()

    def test_quick_start_script_execution(self):
        """
        Run examples/quick_start.py with real C++ engine and AGFS,
        but Mocked Remote Models (VLM & Embedding).
        Configuration is provided via OPENVIKING_CONFIG_FILE pointing to a real file.
        """
        script_path = os.path.join(PROJECT_ROOT, "examples/quick_start.py")
        if not os.path.exists(script_path):
            self.fail(f"Script not found: {script_path}")

        # --- 1. Mock VLM ---
        mock_vlm = MagicMock()

        async def async_return(text):
            return text

        def generate_pseudo_completion(prompt: str, images=None, **kwargs):
            """
            Smart Mock for VLM.
            Generates context-aware responses based on prompt keywords and content.
            """
            prompt_lower = prompt.lower()

            # 1. Summarization Task (File Summary)
            if "generate a summary for the following file" in prompt_lower:
                # Extract filename if possible
                import re

                match = re.search(r"【File Name】\s+(.+)", prompt)
                filename = match.group(1).strip() if match else "unknown_file"

                return (
                    f"Summary of {filename}: This file contains documentation or code "
                    f"relevant to {filename}. Key topics include configuration, usage, and API details."
                )

            # 2. Directory Overview Task
            elif (
                "generate an overview document based on the following directory content"
                in prompt_lower
            ):
                import re

                match = re.search(r"\[Directory Name\]\s+(.+)", prompt)
                dirname = match.group(1).strip() if match else "unknown_dir"

                return (
                    f"# {dirname}\n\n"
                    f"This directory serves as a container for {dirname} related resources. "
                    f"It includes several files and subdirectories.\n\n"
                    f"## Quick Navigation\n"
                    f"- To learn basics → [1]\n"
                    f"- To see configuration → [2]\n"
                )

            # 3. Image Understanding Task (if any)
            elif images:
                return "This image appears to contain a screenshot or diagram related to software architecture."

            # 4. Default Fallback
            else:
                return f"Processed request. Prompt length: {len(prompt)} chars."

        mock_vlm.get_completion.side_effect = generate_pseudo_completion
        mock_vlm.get_completion_async.side_effect = lambda prompt, *args, **kwargs: async_return(
            generate_pseudo_completion(prompt)
        )

        mock_vlm.get_vision_completion.side_effect = lambda prompt, images, **kwargs: (
            generate_pseudo_completion(prompt, images)
        )
        mock_vlm.get_vision_completion_async.side_effect = lambda prompt, images, **kwargs: (
            async_return(generate_pseudo_completion(prompt, images))
        )

        # --- 2. Mock Embedder ---
        mock_embedder = MagicMock()
        # Default config usually uses 2048 dimension unless overridden
        DIMENSION = 2048
        mock_embedder.get_dimension.return_value = DIMENSION
        mock_embedder.is_sparse = False
        mock_embedder.is_hybrid = False

        def generate_pseudo_embedding(text: str):
            """
            Generate a deterministic pseudo-embedding based on text content.
            Features:
            1. Deterministic: Same text -> Same vector (using hash seed)
            2. Semantic Simulation: If text contains 'openviking', boost dimension 0.
               This allows "what is openviking" query to match "OpenViking" docs better.
            3. Length Feature: Encode length in dimension 1 (as requested by user).
            """
            import hashlib
            import math
            import random

            # 1. Deterministic Randomness based on text content
            text_lower = text.lower()
            hash_object = hashlib.md5(text_lower.encode("utf-8"))
            seed = int(hash_object.hexdigest(), 16)
            rng = random.Random(seed)

            # Initialize random vector [-0.1, 0.1]
            vector = [rng.uniform(-0.1, 0.1) for _ in range(DIMENSION)]

            # 2. Semantic Simulation (Keyword Boosting)
            # If text is relevant to "openviking", boost the first dimension significantly
            if "openviking" in text_lower:
                vector[0] = 1.0  # Strong signal

            # 3. Length Feature (as requested)
            # Map length to [0, 1] range roughly
            length_feature = min(len(text) / 10000.0, 1.0)
            vector[1] = length_feature

            # 4. L2 Normalization (Crucial for Cosine Similarity)
            norm = math.sqrt(sum(x**2 for x in vector))
            if norm > 0:
                vector = [x / norm for x in vector]
            else:
                vector = [0.0] * DIMENSION

            return vector

        # Mock embed_batch
        def side_effect_embed_batch(texts):
            return [EmbedResult(dense_vector=generate_pseudo_embedding(t)) for t in texts]

        # Mock single embed
        def side_effect_embed(text):
            return EmbedResult(dense_vector=generate_pseudo_embedding(text))

        mock_embedder.embed_batch.side_effect = side_effect_embed_batch
        mock_embedder.embed.side_effect = side_effect_embed

        # --- 3. Patch Factories ---
        # We STILL need to patch get_embedder/get_vlm_instance because we don't want to use the REAL factories
        # (which would try to instantiate Volcengine clients and fail without real network/auth).
        # BUT, we are now providing a valid CONFIG FILE so that the config loading phase passes validation naturally.

        # NOTE: We do NOT use patch.dict(os.environ, env_vars) here anymore.
        # Instead, we rely on OPENVIKING_CONFIG_FILE pointing to our file.

        env_override = {"OPENVIKING_CONFIG_FILE": self.config_file}

        # IMPORTANT: We need to ensure that when `initialize_openviking_config` is called,
        # it reads our file. We can set the env var for the subprocess/exec context.

        with (
            patch.dict(os.environ, env_override),
            patch(
                "openviking_cli.utils.config.EmbeddingConfig.get_embedder",
                return_value=mock_embedder,
            ),
            patch("openviking_cli.utils.config.VLMConfig.get_vlm_instance", return_value=mock_vlm),
        ):
            # Reset the singleton again inside the patched environment just in case
            from openviking_cli.utils.config.open_viking_config import OpenVikingConfigSingleton

            OpenVikingConfigSingleton.reset_instance()

            # Read script code
            with open(script_path, "r", encoding="utf-8") as f:
                code = f.read()

            # Execute in a sandbox namespace
            # Set CWD to examples/ so path="./data" works relative to it
            original_cwd = os.getcwd()
            try:
                os.chdir(os.path.dirname(script_path))
                global_ns = {"__name__": "__main__", "__file__": script_path}
                exec(code, global_ns)
            except Exception as e:
                self.fail(f"Quick start script execution failed: {e}")
            finally:
                os.chdir(original_cwd)


if __name__ == "__main__":
    unittest.main()
