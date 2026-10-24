"""Skill discovery and activation tests. No accounts or external services."""
from pathlib import Path
import json
import tempfile
import unittest

from zhuangfy.agent_v2.skills import (
    SkillManager, SkillPolicy, build_skill_runtime,
    load_skill_policies, inspect_skills,
)
from zhuangfy.agent_v2.runtime import AuthorizedToolRegistry, ToolBinding


def write_skill(root, name="demo", *, code=None, meta=None):
    directory = root / name
    directory.mkdir()
    data = {"id": name, "name": name, "version": "1.0.0",
            "description": "offline test", "api_version": 1, "entry": "skill.py"}
    data.update(meta or {})
    (directory / "skill.json").write_text(json.dumps(data), encoding="utf-8")
    (directory / "skill.py").write_text(code or (
        "from zhuangfy.agent_v2.runtime import ToolBinding\n"
        "async def read(args): return 'counted '+args['text']\n"
        "def setup(context):\n"
        f"    return [ToolBinding('{name}.read', read, "
        "frozenset({'private','group'}), description='Test read tool', "
        "parameters={'type':'object','properties':{'text':{'type':'string',"
        "'maxLength':12}},'required':['text'],'additionalProperties':False})]\n"
    ), encoding="utf-8")
    return directory


class DiscoveryTests(unittest.TestCase):
    def test_discovery_never_imports_untrusted_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "skills"
            root.mkdir()
            directory = write_skill(root, code=(
                "from pathlib import Path\n"
                f"Path({str(Path(tmp) / 'side_effect.txt')!r}).write_text('bad')\n"
                "def setup(context): return []\n"
            ))
            mgr = SkillManager(root, {"demo": SkillPolicy(enabled=True)})
            statuses = mgr.discover()
            self.assertEqual(len(statuses), 1)
            self.assertTrue(statuses[0].enabled)
            self.assertFalse(statuses[0].loaded)
            self.assertFalse((Path(tmp) / "side_effect.txt").exists())
            (directory / "skill.json").write_text('{"id":"../bad"}', encoding="utf-8")
            self.assertTrue(mgr.discover()[0].error)

    def test_host_owned_memory_namespace_is_reserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "skills"
            root.mkdir()
            write_skill(root, "memory")
            write_skill(root, "task_memory")
            statuses = SkillManager(root, {
                "memory": SkillPolicy(enabled=True),
                "task_memory": SkillPolicy(enabled=True),
            }).discover()
            self.assertEqual(len(statuses), 2)
            self.assertTrue(all("reserved" in s.error for s in statuses))

    def test_explicit_settings_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "skills_settings.json"
            self.assertEqual(load_skill_policies(p), {})
            p.write_text(json.dumps({"skills":{
                "demo": {"enabled":True,"scopes":["private"],"allow_mutation":False}
            }}), encoding="utf-8")
            policies = load_skill_policies(p)
            self.assertTrue(policies["demo"].enabled)
            self.assertEqual(policies["demo"].scopes, frozenset({"private"}))
            for invalid in ({"skills":{"demo":{"enabled":"yes"}}},
                            {"skills":{"demo":{"scopes":["desktop"]}}},
                            {"skills":{"demo":{"allow_mutation":1}}},
                            {"enabled":True}):
                p.write_text(json.dumps(invalid), encoding="utf-8")
                with self.assertRaises(ValueError):
                    load_skill_policies(p)

    def test_invalid_and_symlink_manifests_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "skills"
            root.mkdir()
            write_skill(root, "demo", meta={"entry":"../../escape.py"})
            mgr = SkillManager(root, {"demo":SkillPolicy(enabled=True),
                                      "unknown":SkillPolicy(enabled=True)})
            statuses = {s.id:s for s in mgr.discover()}
            self.assertFalse(statuses["demo"].loaded)
            self.assertIn("only local", statuses["demo"].error)
            self.assertIn("not found", statuses["unknown"].error)
            link = root / "alias"
            try:
                link.symlink_to(root / "demo", target_is_directory=True)
            except (OSError, NotImplementedError):
                pass
            else:
                self.assertTrue({s.id:s for s in mgr.discover()}["alias"].error)


class ActivationTests(unittest.IsolatedAsyncioTestCase):
    async def test_enable_scopes_schema_and_disable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "skills"
            root.mkdir()
            write_skill(root)
            config = Path(tmp) / "skills_settings.json"
            config.write_text(json.dumps({"skills":{
                "demo":{"enabled":False,"scopes":["private"]}
            }}), encoding="utf-8")
            base, manager = build_skill_runtime(root, config, activate=True)
            self.assertEqual(len(base.public_names(scope="private")), 2)
            self.assertFalse(manager.statuses[0].loaded)
            config.write_text(json.dumps({"skills":{
                "demo":{"enabled":True,"scopes":["private"]}
            }}), encoding="utf-8")
            registry, manager = build_skill_runtime(root, config, activate=True)
            self.assertTrue(manager.statuses[0].loaded)
            self.assertIn("demo.read", registry.public_names(scope="private"))
            self.assertNotIn("demo.read", registry.public_names(scope="group"))
            catalog = registry.prompt_catalog(scope="private")
            self.assertIn("demo.read", catalog)
            self.assertIn("maxLength", catalog)
            receipt = await registry.invoke("demo.read", {"text":"你好"}, scope="private")
            self.assertTrue(receipt.success)
            self.assertFalse((await registry.invoke("demo.read", {"text":"你好"}, scope="group")).success)
            self.assertFalse((await registry.invoke("demo.read", {"text":"a"*30}, scope="private")).success)
            self.assertFalse((await registry.invoke("demo.read", {"text":"hi","surprise":1},
                                                    scope="private")).success)

    async def test_broken_skill_cannot_break_builtin_or_other_skill(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "skills"
            root.mkdir()
            write_skill(root, "bad", code="raise RuntimeError('intentional')\n")
            write_skill(root, "good")
            config = Path(tmp) / "skills_settings.json"
            config.write_text(json.dumps({"skills":{
                "bad":{"enabled":True}, "good":{"enabled":True}
            }}), encoding="utf-8")
            reg, manager = build_skill_runtime(root, config, activate=True)
            status = {s.id:s for s in manager.statuses}
            self.assertFalse(status["bad"].loaded)
            self.assertIn("RuntimeError", status["bad"].error)
            self.assertTrue(status["good"].loaded)
            self.assertTrue((await reg.invoke("calculator.evaluate", {"expression":"1+2"},
                                               scope="private")).success)

    async def test_mutations_require_explicit_grant_and_executor_grant(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "skills"
            root.mkdir()
            code = (
                "from zhuangfy.agent_v2.runtime import ToolBinding\n"
                "async def change(args): return 'changed'\n"
                "def setup(context):\n"
                " return [ToolBinding('switch.on',change,"
                "frozenset({'private','group'}),mutating=True,"
                "description='test mutating action', parameters={'type':'object',"
                "'properties':{},'additionalProperties':False})]\n"
            )
            write_skill(root, "switch", code=code)
            cfg = Path(tmp) / "settings.json"
            def set_policy(mutate):
                cfg.write_text(json.dumps({"skills":{"switch":{
                    "enabled":True,"scopes":["private"],"allow_mutation":mutate
                }}}), encoding="utf-8")
            set_policy(False)
            reg, mgr = build_skill_runtime(root, cfg, activate=True)
            self.assertFalse(mgr.statuses[0].loaded)
            set_policy(True)
            reg, mgr = build_skill_runtime(root, cfg, activate=True)
            self.assertTrue(mgr.statuses[0].loaded)
            self.assertNotIn("switch.on", reg.public_names(scope="private"))
            self.assertIn("switch.on", reg.public_names(scope="private",
                                                        allow_mutation=True))
            self.assertFalse((await reg.invoke("switch.on", {}, scope="private")).success)
            self.assertTrue((await reg.invoke("switch.on", {}, scope="private",
                                              allow_mutation=True)).success)

    async def test_atomic_conflict_does_not_register_half_plugin(self):
        reg = AuthorizedToolRegistry()
        async def callback(args): return "ok"
        reg.register(ToolBinding("same.exists", callback))
        with self.assertRaises(ValueError):
            reg.register_many([ToolBinding("new.first", callback),
                               ToolBinding("same.exists", callback)])
        self.assertEqual(reg.public_names(scope="private"), ("same.exists",))
        with self.assertRaises(ValueError):
            reg.register_many([ToolBinding("new.same", callback),
                               ToolBinding("new.same", callback)])



class DiscoveryV3Tests(unittest.IsolatedAsyncioTestCase):
    async def test_shortlists_only_enabled_keyword_matches(self):
        from zhuangfy.agent_v2.discovery import SkillDiscovery
        from zhuangfy.agent_v2.tools import build_builtin_registry
        reg = build_builtin_registry()
        async def echo(args): return args["text"]
        for i in range(40):
            reg.register(ToolBinding(
                name=f"skill_{i}.read", handler=echo,
                scopes=frozenset({"private"}), visibility="deferred",
                description="很长的技能说明，不能全发给 Planner",
                discovery_terms=(f"技能编号_{i}",),
                parameters={"type":"object","properties":{"text":{"type":"string"}}}))
        reg.register(ToolBinding(
            name="text_stats.length", handler=echo, scopes=frozenset({"private"}),
            visibility="deferred", discovery_terms=("字数", "字符统计"),
            parameters={"type":"object","properties":{"text":{"type":"string"}},
                        "required":["text"],"additionalProperties":False}))
        reg.register(ToolBinding(
            name="device.turn_on", handler=echo, scopes=frozenset({"private"}),
            mutating=True, visibility="deferred", discovery_terms=("开灯",)))
        finder = SkillDiscovery(reg)
        social = finder.select("方宜，我今天有点疲惫", scope="private")
        self.assertEqual(social.deferred_names, ())
        chosen = finder.select("帮我统计这段文字的字数", scope="private")
        self.assertEqual(chosen.deferred_names, ("text_stats.length",))
        self.assertNotIn("device.turn_on", chosen.names)
        catalog = reg.prompt_catalog(scope="private", names=chosen.names)
        self.assertIn("text_stats.length", catalog)
        self.assertNotIn("skill_4.read", catalog)
        self.assertFalse((await reg.invoke("device.turn_on", {}, scope="private")).success)
        self.assertEqual(finder.select("开灯", scope="private").deferred_names, ())
        self.assertEqual(finder.select("开灯", scope="private",
                                      allow_mutation=True).deferred_names, ("device.turn_on",))

    async def test_enabled_manifest_is_deferred_by_default(self):
        from zhuangfy.agent_v2.discovery import SkillDiscovery
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "skills"
            root.mkdir()
            folder = write_skill(root)
            manifest = json.loads((folder / "skill.json").read_text())
            manifest["keywords"] = ["统计", "字数"]
            (folder / "skill.json").write_text(json.dumps(manifest), encoding="utf-8")
            cfg = Path(tmp) / "settings.json"
            cfg.write_text(json.dumps({"skills": {"demo": {
                "enabled": True, "scopes": ["private"]}}}), encoding="utf-8")
            registry, manager = build_skill_runtime(root, cfg, activate=True)
            self.assertTrue(manager.statuses[0].loaded)
            finder = SkillDiscovery(registry)
            self.assertNotIn("demo.read", finder.select("今天天气不错", scope="private").names)
            self.assertIn("demo.read", finder.select("统计字数", scope="private").names)
            self.assertNotIn("demo.read", finder.select("统计字数", scope="group").names)

    async def test_nested_schema_fails_closed(self):
        async def echo(args): return "ok"
        reg = AuthorizedToolRegistry()
        reg.register(ToolBinding(
            "demo.nested", echo, parameters={
                "type": "object", "required": ["query"],
                "additionalProperties": False, "properties": {
                    "query": {"type": "object",
                              "required": ["tags", "limit"],
                              "additionalProperties": False,
                              "properties": {
                                  "tags": {"type": "array", "maxItems": 3,
                                           "items": {"type": "string", "maxLength": 8}},
                                  "limit": {"type": "integer", "minimum": 1, "maximum": 5}
                              }}}}))
        good = {"query": {"tags": ["OSG"], "limit": 2}}
        self.assertTrue((await reg.invoke("demo.nested", good, scope="private")).success)
        for bad in ({"query":{"tags":["OSG"],"limit":99}},
                    {"query":{"tags":[{"unexpected":True}],"limit":1}},
                    {"query":{"tags":["OK"],"limit":2,"owner":"other"}},
                    {"query":{"tags":["veryverylongtag"],"limit":1}}):
            self.assertFalse((await reg.invoke("demo.nested", bad, scope="private")).success)

if __name__ == "__main__":
    unittest.main()
