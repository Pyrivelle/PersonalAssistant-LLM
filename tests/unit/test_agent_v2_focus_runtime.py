"""F1 Focus runtime tests, no LLM or QQ connection."""
import asyncio
import unittest

from zhuangfy.agent_v2.focus_runtime import QqFocusRuntime


class FocusRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_and_shadow_never_block(self):
        off = QqFocusRuntime()
        async with off.turn("private:42", "m1") as d:
            self.assertFalse(d.admitted)
        shadow = QqFocusRuntime("shadow", (("circle", ("private:42", "group:9")),))
        async with shadow.turn("private:42", "m1", directed=True) as d:
            self.assertTrue(d.admitted)
            self.assertEqual(d.scope_id, "circle")
        async with shadow.turn("private:777", "m2") as d:
            self.assertFalse(d.admitted)
        self.assertEqual(len(shadow.decisions), 1)
        self.assertEqual(shadow.snapshot()["circle"]["pending_count"], 0)

    async def test_active_serializes_and_prioritizes_pending_directed(self):
        focus = QqFocusRuntime("active", (("circle", ("private:42", "group:9", "group:10")),))
        entered = []
        release_first = asyncio.Event()
        async def first():
            async with focus.turn("group:9", "first"):
                entered.append("first")
                await release_first.wait()
        async def later(session, ident, directed):
            async with focus.turn(session, ident, directed=directed):
                entered.append(ident)
                await asyncio.sleep(0)
        task1 = asyncio.create_task(first())
        await asyncio.sleep(.01)
        other = asyncio.create_task(later("group:10", "passive", False))
        direct = asyncio.create_task(later("private:42", "direct", True))
        await asyncio.sleep(.01)
        self.assertEqual(entered, ["first"])
        self.assertEqual(focus.snapshot()["circle"]["pending_count"], 2)
        release_first.set()
        await asyncio.wait_for(asyncio.gather(task1, other, direct), 2)
        self.assertEqual(entered, ["first", "direct", "passive"])
        self.assertIsNone(focus.snapshot()["circle"]["active_session"])

    async def test_cancelled_waiter_does_not_block_future_work(self):
        focus = QqFocusRuntime("active", (("circle", ("group:9", "group:10")),))
        barrier = asyncio.Event()
        async def hold():
            async with focus.turn("group:9", "busy"):
                await barrier.wait()
        task = asyncio.create_task(hold())
        await asyncio.sleep(.01)
        async def queued():
            async with focus.turn("group:10", "later"):
                return True
        future = asyncio.create_task(queued())
        await asyncio.sleep(.01)
        self.assertEqual(focus.snapshot()["circle"]["pending_count"], 1)
        future.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await future
        barrier.set()
        await task
        async with focus.turn("group:10", "fresh") as d:
            self.assertTrue(d.admitted)
        self.assertEqual(focus.snapshot()["circle"]["pending_count"], 0)


if __name__ == "__main__":
    unittest.main()
