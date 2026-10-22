"""A1/A2 offline regressions."""
import unittest
from types import SimpleNamespace
from zhuangfy.agent_v2 import MessageDebouncer,from_qq_incoming

class TestMessage(unittest.TestCase):
    def incoming(self,kind="private",**extras):
        return SimpleNamespace(kind=kind,user_id=7,group_id=extras.get("group_id"),text=extras.get("text",""),message_id="m1",image_count=extras.get("image_count",0),image=extras.get("image"))
    def test_group_and_private_scope(self):
        p=from_qq_incoming(self.incoming(text="hi"),bot_id=88,event={"message":[{"type":"text","data":{"text":"hi"}}]},received_at=1)
        g=from_qq_incoming(self.incoming("group",group_id=5),bot_id=88,event={"message":[{"type":"at","data":{"qq":"88"}},{"type":"text","data":{"text":"test"}}]},received_at=1)
        self.assertEqual(p.session_id,"qq:88:private:7")
        self.assertEqual(g.session_id,"qq:88:group:5")
        self.assertEqual(g.text,"test")
    def test_image_emoji_audio_quote(self):
        raw={"message":[{"type":"image","data":{"file":"opaque"}}]}
        i=from_qq_incoming(self.incoming(image_count=1),bot_id=88,event=raw,received_at=1)
        self.assertTrue(i.is_bare_image)
        e=from_qq_incoming(self.incoming(),bot_id=88,event={"message":[{"type":"face","data":{"id":"2"}}]},received_at=1)
        self.assertTrue(e.is_pure_emoji)
        a=from_qq_incoming(self.incoming(),bot_id=88,event={"message":[{"type":"reply","data":{"id":"old"}},{"type":"record","data":{"file":"voice"}}]},received_at=1)
        self.assertEqual(a.reply_to_id,"old")
        self.assertEqual(a.parts[1].kind,"audio")

class TestDebounce(unittest.TestCase):
    def test_initial_active_and_quiet(self):
        d=MessageDebouncer("natural")
        self.assertEqual(d.observe(author="a",now=0),2)
        d.clear_batch()
        self.assertEqual(d.observe(author="a",now=5),10)
        d.clear_batch()
        self.assertEqual(d.observe(author="a",now=40),42)
    def test_continuation_and_other_speaker(self):
        d=MessageDebouncer()
        d.observe(author="a",now=0)
        self.assertEqual(d.observe(author="a",now=1),3)
        self.assertEqual(d.observe(author="b",now=1.5),2)
        self.assertAlmostEqual(d.remaining(1.7),.3)
    def test_image_emoji_rules(self):
        d=MessageDebouncer("fast")
        self.assertEqual(d.observe(author="a",now=0,is_bare_image=True),4)
        self.assertEqual(d.observe(author="a",now=2,is_pure_emoji=True),3)
    def test_mention_cap(self):
        d=MessageDebouncer("steady")
        self.assertEqual(d.observe(author="a",now=0),4)
        for i in range(1,8): d.observe(author="a",now=i)
        self.assertLessEqual(d.deadline,8)
        d.clear_batch()
        self.assertEqual(d.observe(author="a",now=20,mentioned=True),21)
        self.assertEqual(d.observe(author="a",now=20.5),21)
    def test_expired_and_modes(self):
        d=MessageDebouncer()
        d.observe(author="a",now=0)
        self.assertEqual(d.observe(author="a",now=4),2)
        d.clear_batch()
        self.assertEqual(d.observe(author="a",now=4),9)
        self.assertEqual(MessageDebouncer("fast").observe(author="a",now=0),1)
        with self.assertRaises(ValueError): MessageDebouncer("invalid")

if __name__=="__main__": unittest.main()
