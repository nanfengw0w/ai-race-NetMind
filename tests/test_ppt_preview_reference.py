"""Preview references are validated defaults; current-message scope wins."""
import json
import unittest
import test_artifact_chat_integration as fixtures
from test_ppt_slide_revision import DECK
from services import artifact_chat, artifact_store as store
from services.artifact_selection import bind_selection
from services.conversation_context import resolve_reference
from services.ppt_slides import list_slides


class PreviewReferenceTests(unittest.TestCase):
    setUp = fixtures.ArtifactChatIntegrationTests.setUp
    tearDown = fixtures.ArtifactChatIntegrationTests.tearDown

    def create(self, content=DECK):
        return store.create_artifact("session", "ppt", "课件", content, user_id="local_user")

    def ref(self, artifact, number=1, **extra):
        return {"resource_id": artifact["id"], "version": artifact["version"],
                "current_version": artifact["current_version"], "selection_source": "preview",
                "selection": list_slides(artifact["content"])[number - 1]["selection"], **extra}

    def plan(self, message, ref, action="revise", target=""):
        self.planner.return_value = (json.dumps({"action": action, "evidence": message, "target": target}, ensure_ascii=False), {"finish_reason": "stop"})
        return artifact_chat.prepare_action("session", "local_user", message, ref)

    def test_current_message_page_and_whole_override_but_this_page_defaults(self):
        artifact = self.create()
        ref = self.ref(artifact)
        for message, expected in [("只修改第三页", 3), ("修改这页", 1), ("修改整份", None), ("修改整篇课件", None)]:
            with self.subTest(message=message):
                plan = self.plan(message, ref)
                self.assertEqual(plan["artifact"].get("selection", {}).get("slide_number"), expected)
        manual = dict(ref)
        manual.pop("selection_source")
        with self.assertRaises(store.ArtifactError):
            self.plan("只修改第三页", manual)

    def test_ordinary_question_has_canonical_page_without_write(self):
        artifact = self.create()
        ref = self.ref(artifact)
        message = "解释这页"
        self.assertIsNone(self.plan(message, ref, action="answer"))
        resolved = bind_selection(resolve_reference("session", "local_user", message, ref), ref, message)
        self.assertEqual(resolved["selection"], list_slides(DECK)[0]["selection"])
        self.assertEqual(len(store.list_versions(artifact["id"])), 1)


    def test_explicit_version_rereads_page_and_other_work_drops_default(self):
        artifact = self.create()
        newer = store.append_revision(artifact["id"], DECK.replace("第一页", "新版第一页"), expected_version=1)
        ref = self.ref(newer)
        message = "解释v1这页"
        resolved = bind_selection(resolve_reference("session", "local_user", message, ref), ref, message)
        self.assertEqual(resolved["version"], 1)
        self.assertEqual(resolved["selection"], list_slides(DECK)[0]["selection"])
        revised = self.plan("基于v1只修改这页", ref)
        self.assertEqual(revised["artifact"]["version"], 1)
        self.assertEqual(revised["artifact"]["selection"], list_slides(DECK)[0]["selection"])
        other = self.create(DECK.replace("第一页", "别的第一页"))
        message = f"修改作品#{other['id']}，把内容精简"
        self.assertNotIn("selection", self.plan(message, ref)["artifact"])

    def test_tampered_original_and_stale_current_reject(self):
        artifact = self.create()
        ref = self.ref(artifact)
        forged = {**ref, "selection": {**ref["selection"], "text": "伪造原文"}}
        with self.assertRaises(store.ArtifactError):
            self.plan("只修改第三页", forged)
        store.append_revision(artifact["id"], DECK.replace("结论", "新版结论"), expected_version=1)
        with self.assertRaises(store.ArtifactError) as rejected:
            self.plan("修改这页", ref)
        self.assertEqual(rejected.exception.status_code, 409)
        self.assertEqual(len(store.list_versions(artifact["id"])), 2)

    def test_restore_explicit_page_wins_and_unspecified_remains_whole(self):
        artifact = self.create()
        newer = store.append_revision(artifact["id"], DECK.replace("结论", "新版结论"), expected_version=1)
        ref = self.ref(newer)
        whole = self.plan("恢复第一版", ref, "restore", "第一版")
        self.assertNotIn("slide_number", whole)
        page = self.plan("只恢复第三页到第一版", ref, "restore", "第一版")
        self.assertEqual(page["slide_number"], 3)
        forged = {**ref, "selection": {**ref["selection"], "text": "伪造"}}
        with self.assertRaises(store.ArtifactError):
            self.plan("恢复第一版", forged, "restore", "第一版")


    def test_read_only_multi_page_background_keeps_canonical_validation(self):
        artifact = self.create()
        newer = store.append_revision(artifact["id"], DECK.replace("第一页", "新版第一页"), expected_version=1)
        ref = self.ref(newer)
        message = "比较v1第二页和第三页"
        source = resolve_reference("session", "local_user", message, ref)
        result = bind_selection(source, ref, message, read_only=True)
        self.assertEqual(result["version"], 1)
        self.assertEqual(result["content"], DECK)
        self.assertNotIn("selection", result)
        with self.assertRaises(store.ArtifactError):
            bind_selection(source, ref, message)
        forged = {**ref, "selection": {**ref["selection"], "text": "伪造原文"}}
        with self.assertRaises(store.ArtifactError):
            bind_selection(source, forged, message, read_only=True)
        with self.assertRaises(store.ArtifactError):
            self.plan("修改第二页和第三页", ref)
        with self.assertRaises(store.ArtifactError):
            self.plan("恢复第二页和第三页到第一版", ref, "restore", "第一版")
        self.assertEqual(len(store.list_versions(artifact["id"])), 2)
