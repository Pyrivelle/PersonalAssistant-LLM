"""Model identifiers stay stable across provider grouping and stage routing."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from zhuangfy.llm import model_registry as registry_module
from zhuangfy.llm import routes as route_module
from zhuangfy.llm.model_registry import (ModelRegistry, ProviderConnection,
                                         RegisteredModel, load_registry,
                                         save_assignments, save_provider_key,
                                         save_registry)
from zhuangfy.llm.routes import ModelRoutes, load_route_key, save_route_key


class ModelRegistryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.provider = ProviderConnection("provider_1", "本地服务", "custom",
                                           "http://127.0.0.1:11434/v1")
        self.model = RegisteredModel("local_chat", "本地模型", "provider_1", "qwen-local")
        self.registry = ModelRegistry((self.provider,), (self.model,),
                                      ("local_chat",), ("local_chat",))

    def test_unique_model_identifier_and_assignment(self):
        with self.assertRaises(ValueError):
            ModelRegistry((self.provider,), (self.model, self.model),
                          ("local_chat",), ("local_chat",)).validated()
        with self.assertRaises(ValueError):
            ModelRegistry((self.provider,), (self.model,),
                          ("missing",), ("local_chat",)).validated()
        routes = self.registry.materialize(ModelRoutes())
        self.assertEqual(routes.planner.candidates[0].id, "local_chat")
        self.assertEqual(routes.reply.candidates[0].model, "qwen-local")

    def test_manual_thinking_is_preserved_in_runtime_route(self):
        provider = ProviderConnection("provider_1", "DeepSeek", "deepseek",
                                      "https://api.deepseek.com")
        model = RegisteredModel("ds_reply", "DeepSeek Flash", provider.id,
                                "deepseek-flash", thinking="disabled")
        catalog = ModelRegistry((provider,), (model,), (model.id,), (model.id,))
        with patch.object(registry_module, "load_routes", return_value=ModelRoutes()), \
             patch.object(registry_module, "save_routes") as save_routes, \
             patch.object(registry_module, "REGISTRY_FILE", self.root / "registry.json"):
            save_assignments(catalog)
        self.assertEqual(save_routes.call_args.args[0].reply.candidates[0].thinking,
                         "disabled")
        self.assertEqual(load_registry(self.root / "registry.json"), catalog)

    def test_provider_key_is_shared_but_bound_to_endpoint(self):
        env_file = self.root / ".env.local"
        catalog = self.root / "registry.json"
        with patch.object(registry_module, "ENV_FILE", env_file), \
             patch.object(route_module, "ENV_FILE", env_file), \
             patch.object(registry_module, "REGISTRY_FILE", catalog):
            save_registry(self.registry, catalog)
            save_provider_key("provider_1", "test-secret", "custom", self.provider.base_url)
            save_route_key("local_chat", "legacy-secret", "custom", self.provider.base_url)
            self.assertEqual(load_route_key("local_chat", "custom", self.provider.base_url),
                             "test-secret")
            self.assertEqual(load_route_key("local_chat", "custom",
                                            "http://127.0.0.1:11435/v1"), "")
            self.assertNotIn("test-secret", catalog.read_text(encoding="utf-8"))

    def test_memory_assignment_keeps_identity_and_consent_settings(self):
        runtime_file = self.root / "memory_runtime.json"
        runtime_file.write_text(json.dumps({"enabled": True, "model": "old",
                                            "desktop_user_id": "owner-stable",
                                            "qq_enabled": True}), encoding="utf-8")
        value = ModelRegistry(self.registry.providers, self.registry.models,
                              self.registry.planner, self.registry.reply, memory="local_chat")
        with patch.object(registry_module, "MEMORY_RUNTIME_FILE", runtime_file), \
             patch.object(registry_module, "REGISTRY_FILE", self.root / "registry.json"), \
             patch.object(registry_module, "load_routes", return_value=ModelRoutes()), \
             patch.object(registry_module, "save_routes") as save_routes:
            save_assignments(value)
        saved = json.loads(runtime_file.read_text(encoding="utf-8"))
        self.assertEqual(saved["model"], "qwen-local")
        self.assertEqual(saved["profile_id"], "local_chat")
        self.assertEqual(saved["desktop_user_id"], "owner-stable")
        self.assertTrue(saved["qq_enabled"])
        self.assertNotIn("api_key", saved)
        self.assertEqual(load_registry(self.root / "registry.json"), value)
        self.assertEqual(save_routes.call_args.args[0].reply.candidates[0].id, "local_chat")


if __name__ == "__main__":
    unittest.main()
