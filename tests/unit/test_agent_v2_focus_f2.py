"""F2 checkpoint recovery and cross-channel identity isolation."""
import tempfile
from pathlib import Path
import unittest

from zhuangfy.agent_v2.focus_checkpoint import FocusCheckpointStore, session_ref
from zhuangfy.agent_v2.focus_runtime import QqFocusRuntime


class FocusF2Tests(unittest.IsolatedAsyncioTestCase):
    async def test_restart_restores_fairness_not_messages_or_active_leases(self):
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / "focus.db"
            groups = (("circle", ("private:42", "group:9")),)
            first = QqFocusRuntime("active", groups, checkpoint_store=FocusCheckpointStore(file),
                                   instance_id="qq:bot1")
            async with first.turn("group:9", "m1"):
                self.assertEqual(first.snapshot()["circle"]["active_session"], "group:9")
            second = QqFocusRuntime("active", groups, checkpoint_store=FocusCheckpointStore(file),
                                    instance_id="qq:bot1")
            snapshot = second.snapshot()["circle"]
            self.assertIsNone(snapshot["active_session"])
            self.assertEqual(snapshot["pending_count"], 0)
            self.assertEqual(second.coordinator._groups["circle"].last_session, "group:9")
            self.assertEqual(second.coordinator._groups["circle"].streak, 1)
            third = QqFocusRuntime("active", groups, checkpoint_store=FocusCheckpointStore(file),
                                   instance_id="qq:bot2")
            self.assertEqual(third.coordinator._groups["circle"].last_session, "")
            # Changes to membership invalidate previous fairness state.
            new = QqFocusRuntime("active", (("circle", ("private:42",)),),
                                 checkpoint_store=FocusCheckpointStore(file),
                                 instance_id="qq:bot1")
            self.assertEqual(new.coordinator._groups["circle"].last_session, "")

    async def test_off_and_shadow_do_not_write_checkpoints(self):
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory)/"focus.db"
            store = FocusCheckpointStore(file)
            shadow = QqFocusRuntime("shadow", (("circle", ("private:42",)),),
                                    checkpoint_store=store, instance_id="qq:bot1")
            async with shadow.turn("private:42", "test"):
                pass
            self.assertIsNone(store.read("qq:bot1", "circle", frozenset({"private:42"})))

    async def test_channel_account_ids_never_collide(self):
        a = session_ref(channel="qq", account="bot1", kind="private", peer="42")
        b = session_ref(channel="weixin", account="bot1", kind="private", peer="42")
        c = session_ref(channel="qq", account="bot2", kind="private", peer="42")
        self.assertEqual(len({a,b,c}), 3)
        with self.assertRaises(ValueError):
            session_ref(channel="qq", account="user:admin", kind="private", peer="42")


if __name__ == "__main__":
    unittest.main()
