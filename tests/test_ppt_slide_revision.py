"""Source-exact slide revisions, using existing isolated database fixtures."""
import importlib
import json
import unittest
from unittest.mock import patch

import test_artifact_revision as revision_fixtures
import test_artifact_chat_integration as chat_fixtures
from services import artifact_revision as revision, artifact_store as store, artifact_chat
from services.artifact_selection import validate_selection
from services.ppt_slides import list_slides


SLIDES = [
    '<section class="slide active" id="slide-1"><h1>第一页 😀</h1><section><p id="intro">普通嵌套 section</p></section></section>',
    '<section id="slide-2" class="slide"><h2>第二页</h2><p id="key">旧内容</p></section>',
    '<section class="slide" id="slide-3"><h2>第三页</h2><p>结论</p></section>',
]
DECK = ('<!doctype html>\r\n<html><head><style>.slide{color:black}</style></head>\r\n'
        '<body><!-- <section class="slide">注释假页面</section> -->\r\n'
        '<main id="deck">\r\n' + '\r\n'.join(SLIDES) + '\r\n</main>\r\n'
        '<script>const fake = "<section class=\'slide\'>脚本假页面</section>";</script>\r\n'
        '</body></html>')


def selection(number, content=DECK):
    text = SLIDES[number - 1]
    start = content.index(text)
    return {"start": start, "end": start + len(text), "text": text, "slide_number": number}


class PptSlideRevisionTests(unittest.TestCase):
    setUp = revision_fixtures.ArtifactRevisionTests.setUp
    create = revision_fixtures.ArtifactRevisionTests.create
    originals = revision_fixtures.ArtifactRevisionTests.originals

    def revise_slide(self, artifact, instruction="把所选页的旧内容改为新内容", chosen=None,
                     base_version=1, expected_version=1):
        return revision.revise_artifact(artifact["id"], "s1", "u1", base_version,
                                        expected_version, instruction, selection=chosen)

    def test_slide_source_ranges_ignore_nested_sections_comments_scripts_and_preserve_unicode(self):
        slides = importlib.import_module("services.ppt_slides").list_slides(DECK)
        self.assertEqual(len(slides), 3)
        for number, item in enumerate(slides, 1):
            self.assertEqual(item["number"], number)
            self.assertEqual(item["selection"], selection(number))
            self.assertIn(f"第{'一二三'[number - 1]}页", item["title"])
        legacy = DECK.replace('id="deck"', 'id="presentation"')
        self.assertEqual(len(importlib.import_module("services.ppt_slides").list_slides(legacy)), 3)

    def test_ambiguous_malformed_and_nested_slide_structures_are_rejected(self):
        invalid = [DECK.replace('</main>', ''), DECK.replace(SLIDES[1], '<section class="slide">' + SLIDES[1] + '</section>'),
                   DECK.replace(SLIDES[1], '<div>' + SLIDES[1] + '</div>')]
        for content in invalid:
            with self.subTest(content=content), self.assertRaises((store.ArtifactError, ValueError)):
                importlib.import_module("services.ppt_slides").list_slides(content)

    def test_raw_text_elements_never_create_fake_slides(self):
        fake = '<section class="slide" id="fake">假页</section>'
        content = DECK.replace('</head>', '<style>/*' + fake + '*/</style></head>').replace(
            '</body>', '<textarea>' + fake + '</textarea></body>')
        items = importlib.import_module("services.ppt_slides").list_slides(content)
        self.assertEqual(len(items), 3)
        for item, original in zip(items, SLIDES):
            self.assertEqual(item["selection"]["text"], original)
            self.assertEqual(content[item["selection"]["start"]:item["selection"]["end"]], original)

    def test_page_scope_parsing_and_ranges_even_when_a_page_is_selected(self):
        requested = importlib.import_module("services.ppt_slides").requested_slide
        for instruction, number in [("edit slide 3", 3), ("edit page 3", 3), ("修改第十二页", 12)]:
            with self.subTest(instruction=instruction):
                self.assertEqual(requested(instruction), number)
        for instruction in ["修改 slide 2 to 3", "修改第2、3页", "修改前三页"]:
            with self.subTest(instruction=instruction), self.assertRaises(ValueError):
                requested(instruction, selected_number=2)
        self.assertIsNone(requested("修改整份课件，统一字体"))

    def test_explicit_selection_splices_one_slide_with_exact_untouched_source(self):
        artifact = self.create("ppt", DECK)
        replacement = SLIDES[1].replace("旧内容", "新内容😀")
        self.llm.return_value = replacement, {"finish_reason": "stop"}
        result = self.revise_slide(artifact, chosen=selection(2))
        self.assertEqual(result["content"], DECK.replace(SLIDES[1], replacement))
        self.assertEqual(store.read_artifact(artifact["id"], 1)["content"], DECK)
        self.llm.assert_called_once()
        self.assertEqual(self.llm.call_args.kwargs["max_tokens"], 393216)

    def test_natural_language_page_uses_canonical_selection(self):
        artifact = self.create("ppt", DECK)
        replacement = SLIDES[2].replace("结论", "新结论")
        self.llm.return_value = replacement, {"finish_reason": "stop"}
        result = self.revise_slide(artifact, "只修改第三页，把结论改成新结论")
        self.assertEqual(result["content"], DECK.replace(SLIDES[2], replacement))
        sent = json.loads(self.llm.call_args.args[0][1]["content"])
        self.assertEqual(sent["original"]["selection"], selection(3))

    def test_invalid_page_requests_and_forged_ranges_never_call_model(self):
        artifact = self.create("ppt", DECK)
        cases = [("只修改第九页，把标题改短", None), ("修改第0页", None), ("修改这一页", None),
                 ("修改第二页和第三页", None),
                 ("修改第二到第三页", None), ("只修改第三页，把标题改短", selection(2)),
                 ("改短", {**selection(2), "start": selection(2)["start"] + 1}),
                 ("改短", {**selection(2), "slide_number": 3})]
        for instruction, chosen in cases:
            with self.subTest(instruction=instruction, selection=chosen):
                before = self.originals()
                with self.assertRaises(store.ArtifactError):
                    self.revise_slide(artifact, instruction, chosen)
                self.assertEqual(self.originals(), before)
        self.llm.assert_not_called()

    def test_truncated_and_provider_failure_keep_original_snapshot(self):
        artifact = self.create("ppt", DECK)
        before = self.originals()
        for response, error, status in [( (SLIDES[1], {"finish_reason": "length"}), None, 422),
                                        (None, RuntimeError("provider unavailable"), 502)]:
            with self.subTest(status=status):
                self.llm.reset_mock()
                self.llm.return_value = response
                self.llm.side_effect = error
                with self.assertRaises(store.ArtifactError) as caught:
                    self.revise_slide(artifact, chosen=selection(2))
                self.assertEqual(caught.exception.status_code, status)
                self.llm.assert_called_once()
                self.assertEqual(self.originals(), before)

    def test_slide_number_is_not_accepted_for_text_artifacts(self):
        artifact = self.create("document", "正文")
        with self.assertRaises(store.ArtifactError):
            validate_selection(artifact, {"start": 0, "end": 2, "text": "正文", "slide_number": 1})

    def test_invalid_model_output_never_writes_or_retries(self):
        artifact = self.create("ppt", DECK)
        invalid = [DECK, json.dumps({"replacement": SLIDES[1]}), SLIDES[1] + SLIDES[2],
                   SLIDES[1].replace('id="key"', ''), SLIDES[1].replace('id="slide-2"', 'id="changed"'),
                   SLIDES[1].replace('class="slide"', 'class="slide changed"'),
                   SLIDES[1].replace('</section>', '<style>body{display:none}</style></section>'),
                   SLIDES[1].replace('<p ', '<p onclick="alert(1)" '),
                   SLIDES[1].replace('</section>', '<script>alert(1)</script></section>'),
                   SLIDES[1].replace('</section>', '<p id="slide-1">冲突</p></section>')]
        before = self.originals()
        for raw in invalid:
            with self.subTest(raw=raw):
                self.llm.reset_mock()
                self.llm.return_value = raw, {"finish_reason": "stop"}
                with self.assertRaises(store.ArtifactError):
                    self.revise_slide(artifact, chosen=selection(2))
                self.llm.assert_called_once()
                self.assertEqual(self.originals(), before)

    def test_old_base_and_concurrent_update_are_version_safe(self):
        artifact = self.create("ppt", DECK)
        newer = DECK.replace("结论", "v2结论")
        store.append_revision(artifact["id"], newer, expected_version=1)
        replacement = SLIDES[1].replace("旧内容", "v3内容")
        self.llm.return_value = replacement, {"finish_reason": "stop"}
        result = self.revise_slide(artifact, chosen=selection(2), expected_version=2)
        self.assertEqual(result["parent_version"], 1)
        self.assertEqual(result["content"], DECK.replace(SLIDES[1], replacement))
        self.assertEqual(store.read_artifact(artifact["id"], 2)["content"], newer)
        def concurrent(*args, **kwargs):
            store.append_revision(artifact["id"], newer, expected_version=3)
            return replacement, {"finish_reason": "stop"}
        self.llm.side_effect = concurrent
        with self.assertRaises(store.ArtifactError) as caught:
            self.revise_slide(artifact, chosen=selection(2), expected_version=3)
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(store.read_artifact(artifact["id"])["content"], newer)

    def test_existing_slide_styles_scripts_and_events_must_remain_exact(self):
        original = SLIDES[1].replace('<p id="key">', '<p id="key" onclick="next()">').replace(
            '</section>', '<style>#key{color:red}</style><script>function next(){return 1;}</script></section>')
        content = DECK.replace(SLIDES[1], original)
        chosen = {"start": content.index(original), "end": content.index(original) + len(original),
                  "text": original, "slide_number": 2}
        artifact = self.create("ppt", content)
        for replacement in [original.replace('color:red', 'color:blue'),
                            original.replace('return 1', 'return 2'),
                            original.replace(' onclick="next()"', '')]:
            self.llm.return_value = replacement, {"finish_reason": "stop"}
            with self.subTest(replacement=replacement), self.assertRaises(store.ArtifactError):
                self.revise_slide(artifact, chosen=chosen)
            self.assertEqual(store.read_artifact(artifact["id"])["version"], 1)
        replacement = original.replace("旧内容", "新内容")
        self.llm.return_value = replacement, {"finish_reason": "stop"}
        result = self.revise_slide(artifact, chosen=chosen)
        self.assertEqual(result["content"], content.replace(original, replacement))


class PptSlideHttpTests(unittest.TestCase):
    setUp = chat_fixtures.ArtifactChatIntegrationTests.setUp
    tearDown = chat_fixtures.ArtifactChatIntegrationTests.tearDown

    def create(self):
        return store.create_artifact("session", "ppt", "课件", DECK, user_id="local_user")

    def test_page_restore_rejects_conflicting_selected_page(self):
        artifact = self.create()
        newer = DECK.replace("旧内容", "新内容").replace("结论", "新结论")
        store.append_revision(artifact["id"], newer, expected_version=1)
        ref = {"resource_id": artifact["id"], "version": 2}
        message = "把第三页恢复到第一版"
        self.planner.return_value = json.dumps({"action": "restore", "evidence": message, "target": "第一版"}, ensure_ascii=False), {"finish_reason": "stop"}
        with self.assertRaises(store.ArtifactError) as rejected:
            artifact_chat.prepare_action("session", "local_user", message,
                {**ref, "selection": list_slides(newer)[1]["selection"]})
        self.assertEqual(rejected.exception.status_code, 400)
        self.assertEqual(store.read_artifact(artifact["id"])["content"], newer)
        self.assertEqual(len(store.list_versions(artifact["id"])), 2)
        message = "恢复第一版"
        self.planner.return_value = json.dumps({"action": "restore", "evidence": message, "target": "第一版"}, ensure_ascii=False), {"finish_reason": "stop"}
        current_slide = list_slides(newer)[2]["selection"]
        plan = artifact_chat.prepare_action("session", "local_user", message, {**ref, "selection": current_slide})
        _, receipt = artifact_chat.execute_action(plan, "session", "local_user")
        self.assertEqual(receipt["resource"]["content"], DECK)
        self.assertEqual(receipt["resource"]["version"], 3)

    def test_attachment_preparation_cannot_change_original_user_page_scope(self):
        artifact = self.create()
        message = "只修改第二页，把旧内容改成新内容"
        self.planner.return_value = json.dumps({"action": "revise", "evidence": message, "target": ""}, ensure_ascii=False), {"finish_reason": "stop"}
        plan = artifact_chat.prepare_action("session", "local_user", message,
            {"resource_id": artifact["id"], "version": 1})
        prepared = message + "\n附件内容：第三页仅供参考。"
        replacement = SLIDES[1].replace("旧内容", "新内容")
        with patch.object(revision, "chat_with_diagnostics", return_value=(replacement, {"finish_reason": "stop"})) as model:
            _, receipt = artifact_chat.execute_action(plan, "session", "local_user", instruction=prepared)
        self.assertEqual(receipt["slide_number"], 2)
        self.assertEqual(receipt["resource"]["content"], DECK.replace(SLIDES[1], replacement))
        sent = json.loads(model.call_args.args[0][1]["content"])
        self.assertEqual(sent["revision_request"], prepared)
        self.assertEqual(sent["original"]["selection"], selection(2))

    def test_list_owner_version_ui_selection_revision_and_restore(self):
        artifact = self.create()
        url = f"/api/artifacts/{artifact['id']}"
        params = {"student_id": "session", "user_id": "local_user", "version": 1}
        response = self.client.get(url + "/slides", params=params)
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        slides = data if isinstance(data, list) else data["slides"]
        self.assertEqual(slides[1]["selection"], selection(2))
        denied = self.client.get(url + "/slides", params={**params, "user_id": "stranger"})
        self.assertEqual(denied.status_code, 403)
        replacement = SLIDES[1].replace("旧内容", "HTTP新内容")
        with patch.object(revision, "chat_with_diagnostics", return_value=(replacement, {"finish_reason": "stop"})) as model:
            result = self.client.post(url + "/revise", json={**params, "base_version": 1, "expected_version": 1,
                "instruction": "把所选页内容改清楚", "selection": slides[1]["selection"]})
        self.assertEqual(result.status_code, 200, result.text)
        model.assert_called_once()
        self.assertEqual(result.json()["content"], DECK.replace(SLIDES[1], replacement))
        restored = self.client.post(url + "/restore", json={**params, "expected_version": 2})
        self.assertEqual(restored.status_code, 200, restored.text)
        self.assertEqual(restored.json()["content"], DECK)

    def test_chat_natural_page_canonical_selection_receipt_and_ordinary_question(self):
        artifact = self.create()
        ref = {"resource_id": artifact["id"], "version": 1}
        message = "只修改第三页，把结论改成新结论"
        self.planner.return_value = json.dumps({"action": "revise", "evidence": message, "target": ""}, ensure_ascii=False), {"finish_reason": "stop"}
        plan = artifact_chat.prepare_action("session", "local_user", message, ref)
        self.assertEqual(plan["artifact"]["selection"], selection(3))
        with patch.object(revision, "chat_with_diagnostics", return_value=(SLIDES[2].replace("结论", "新结论"), {"finish_reason": "stop"})):
            _, receipt = artifact_chat.execute_action(plan, "session", "local_user")
        self.assertEqual(receipt["scope"], "slide")
        self.assertEqual(receipt["slide_number"], 3)
        self.planner.return_value = '{"action":"answer","evidence":"","target":""}', {"finish_reason": "stop"}
        self.assertIsNone(artifact_chat.prepare_action("session", "local_user", "第三页讲什么？", {**ref, "version": 2}))
        self.assertEqual(len(store.list_versions(artifact["id"])), 2)
        message = "恢复第一版"
        self.planner.return_value = json.dumps({"action": "restore", "evidence": message, "target": "第一版"}, ensure_ascii=False), {"finish_reason": "stop"}
        plan = artifact_chat.prepare_action("session", "local_user", message, {**ref, "version": 2})
        _, receipt = artifact_chat.execute_action(plan, "session", "local_user")
        self.assertEqual(receipt["resource"]["content"], DECK)
        self.assertEqual(receipt["resource"]["version"], 3)
