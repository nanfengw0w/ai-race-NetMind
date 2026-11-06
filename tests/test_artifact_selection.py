"""Exact Unicode selections, deterministic partial edits, and persisted references."""
import json
import unittest
from unittest.mock import patch

import test_artifact_revision as revision_fixtures
import test_artifact_chat_integration as chat_fixtures

store = revision_fixtures.store
revision = revision_fixtures.revision


def selected(content, text, start=None):
    start = content.index(text) if start is None else start
    return {"start": start, "end": start + len(text), "text": text}


class ArtifactSelectionTests(unittest.TestCase):
    setUp = revision_fixtures.ArtifactRevisionTests.setUp
    create = revision_fixtures.ArtifactRevisionTests.create
    originals = revision_fixtures.ArtifactRevisionTests.originals

    def edit(self, artifact, selection, base=1, expected=1):
        return revision.revise_artifact(artifact["id"], "s1", "u1", base, expected,
                                        "只修改引用部分", selection=selection)

    def output(self, replacement, **extra):
        self.llm.return_value = (json.dumps({"replacement": replacement, **extra}, ensure_ascii=False),
                                 {"finish_reason": "stop"})

    def test_second_repeated_paragraph_after_emoji_uses_codepoints_and_preserves_crlf(self):
        content = "😀开头\r\n\r\n重复段落\r\n\r\n重复段落\r\n尾部  \r\n"
        artifact = self.create(content=content)
        start = content.rindex("重复段落")
        choice = selected(content, "重复段落", start)
        self.output("新的段落")
        result = self.edit(artifact, choice)
        expected = content[:start] + "新的段落" + content[choice["end"]:]
        self.assertEqual(result["content"].encode(), expected.encode())
        self.assertEqual(store.read_artifact(artifact["id"], 1)["content"], content)
        self.llm.assert_called_once()
        self.assertIn(choice["text"], str(self.llm.call_args))
        self.assertEqual(self.llm.call_args.kwargs["max_tokens"], 393216)

    def test_supported_markdown_types_and_empty_replacement_deletes_only_selection(self):
        for kind in ("document", "lab", "assessment"):
            with self.subTest(kind=kind):
                artifact = self.create(kind, "保留\r\n删除\r\n结尾")
                self.llm.reset_mock()
                self.output("")
                result = self.edit(artifact, selected(artifact["content"], "删除"))
                self.assertEqual(result["content"], "保留\r\n\r\n结尾")
                self.llm.assert_called_once()

    def test_tampered_out_of_range_empty_boolean_and_noninteger_offsets_never_call_model(self):
        artifact = self.create(content="😀原文段落")
        choices = [
            {"start": 1, "end": 3, "text": "伪造"},
            {"start": -1, "end": 2, "text": "😀原"},
            {"start": 0, "end": 99, "text": artifact["content"]},
            {"start": 1, "end": 1, "text": ""},
            {"start": True, "end": 2, "text": "原"},
            {"start": 1.0, "end": 2, "text": "原"},
            {"start": "1", "end": 2, "text": "原"},
        ]
        before = self.originals()
        for choice in choices:
            with self.subTest(choice=choice), self.assertRaises(store.ArtifactError):
                self.edit(artifact, choice)
            self.assertEqual(self.originals(), before)
        self.llm.assert_not_called()

    def test_invalid_output_truncation_failure_and_erasing_whole_body_do_not_save_or_retry(self):
        artifact = self.create(content="唯一正文")
        choice = selected(artifact["content"], artifact["content"])
        before = self.originals()
        for raw, diagnostics, failure in [
            ('{"replacement":', {"finish_reason": "stop"}, None),
            ('{"replacement":"新正文","markdown":"未授权全文"}', {"finish_reason": "stop"}, None),
            ('{"replacement":12}', {"finish_reason": "stop"}, None),
            ('{"markdown":"伪完整正文"}', {"finish_reason": "stop"}, None),
            ('{"replacement":""}', {"finish_reason": "stop"}, None),
            ('{"replacement":"新正文"}', {"finish_reason": "length"}, None),
            (None, {}, RuntimeError("provider failed")),
        ]:
            with self.subTest(raw=raw):
                self.llm.reset_mock()
                self.llm.side_effect = failure
                self.llm.return_value = raw, diagnostics
                with self.assertRaises(store.ArtifactError):
                    self.edit(artifact, choice)
                self.llm.assert_called_once()
                self.assertEqual(self.originals(), before)

    def test_lecture_requires_valid_bundle_and_synchronizes_it(self):
        artifact = self.create("lecture", "开头\n选中\n结尾", {"explanation_bundle": revision_fixtures.diagram()})
        choice = selected(artifact["content"], "选中")
        before = self.originals()
        for extra in ({}, {"bundle": {"schema_version": 1}}):
            self.output("修订", **extra)
            with self.assertRaises(store.ArtifactError):
                self.edit(artifact, choice)
            self.assertEqual(self.originals(), before)
        bundle = revision_fixtures.diagram("current", "电流")
        self.output("修订", bundle=bundle)
        result = self.edit(artifact, choice)
        self.assertEqual(result["content"], "开头\n修订\n结尾")
        self.assertEqual(result["metadata"]["explanation_bundle"], bundle)
        self.output("纯文字", bundle=None)
        result = self.edit(artifact, choice, expected=2)
        self.assertIsNone(result["metadata"]["explanation_bundle"])

    def test_old_version_is_source_and_concurrent_save_is_not_overwritten(self):
        artifact = self.create(content="原始选段，保留")
        choice = selected(artifact["content"], "选段")
        store.append_revision(artifact["id"], "第二版不同正文", expected_version=1)
        self.output("修订")
        result = self.edit(artifact, choice, expected=2)
        self.assertEqual((result["version"], result["parent_version"]), (3, 1))
        self.assertEqual(result["content"], "原始修订，保留")
        self.llm.reset_mock()

        def racing_save(*args, **kwargs):
            store.append_revision(artifact["id"], "并发胜出", expected_version=3)
            return '{"replacement":"迟到"}', {"finish_reason": "stop"}

        self.llm.side_effect = racing_save
        with self.assertRaises(store.ArtifactError) as caught:
            self.edit(artifact, choice, expected=3)
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(store.read_artifact(artifact["id"])["content"], "并发胜出")
        self.assertEqual(len(store.list_versions(artifact["id"])), 4)
        self.llm.assert_called_once()

    def test_binding_rejects_wrong_resource_or_version(self):
        from services.artifact_selection import bind_selection
        artifact = self.create(content="原始正文")
        choice = selected(artifact["content"], "正文")
        for ref in ({"resource_id": artifact["id"] + 1, "version": 1},
                    {"resource_id": artifact["id"], "version": 2}):
            with self.subTest(ref=ref), self.assertRaises(store.ArtifactError) as caught:
                bind_selection(artifact, {**ref, "selection": choice})
            self.assertEqual(caught.exception.status_code, 409)

    def test_full_revision_without_selection_keeps_existing_plain_text_protocol(self):
        artifact = self.create()
        self.llm.return_value = "完整新版正文\r\n", {"finish_reason": "stop"}
        result = revision.revise_artifact(artifact["id"], "s1", "u1", 1, 1, "修改全文")
        self.assertEqual(result["content"], "完整新版正文\r\n")
        self.llm.assert_called_once()


class ArtifactSelectionChatTests(unittest.TestCase):
    setUp = chat_fixtures.ArtifactChatIntegrationTests.setUp
    tearDown = chat_fixtures.ArtifactChatIntegrationTests.tearDown
    save_message = chat_fixtures.ArtifactChatIntegrationTests.save_message

    def test_normal_question_sends_and_persists_selection_without_creating_version(self):
        artifact = self.save_message()
        choice = selected(artifact["content"], "潮汐编号 7319")
        reference = {"resource_id": artifact["id"], "version": 1, "selection": choice}
        captured = []

        def answer(messages, **kwargs):
            captured.extend(messages)
            return "这里说的是潮汐。"

        with patch.object(chat_fixtures.chat_service, "llm_chat", side_effect=answer):
            result = self.client.post("/api/chat/talk", json={
                "student_id": "session", "user_id": "local_user", "message": "解释引用这段话",
                "resource_ref": reference,
            })
        self.assertEqual(result.status_code, 200, result.text)
        source = next(item["content"] for item in captured if "准确回读的作品原文" in item["content"])
        self.assertEqual(json.loads(source.split("\n", 1)[1])["selection"], choice)
        self.assertEqual(len(store.list_versions(artifact["id"])), 1)
        history = self.client.get("/api/conversations/session").json()
        message = next(item for item in history if item["role"] == "user" and item["content"] == "解释引用这段话")
        self.assertEqual(message["resource_reference"]["selection"], choice)
        self.assertEqual(message["resource_reference"]["version"], 1)

    def test_cross_session_selection_is_rejected_without_chat_or_revision(self):
        artifact = self.save_message()
        choice = selected(artifact["content"], "灯塔")
        with patch.object(chat_fixtures.chat_service, "llm_chat") as model:
            result = self.client.post("/api/chat/talk", json={
                "student_id": "other", "user_id": "local_user", "message": "解释引用段落",
                "resource_ref": {"resource_id": artifact["id"], "version": 1, "selection": choice},
            })
        self.assertEqual(result.status_code, 404)
        model.assert_not_called()
        self.assertEqual(len(store.list_versions(artifact["id"])), 1)

    def test_http_revision_passes_selection_to_deterministic_splice(self):
        artifact = self.save_message()
        choice = selected(artifact["content"], "7319")
        with patch.object(chat_fixtures.artifact_revision, "chat_with_diagnostics", return_value=(
                '{"replacement":"9000"}', {"finish_reason": "stop"})) as model:
            response = self.client.post(f"/api/artifacts/{artifact['id']}/revise", json={
                "student_id": "session", "user_id": "local_user", "base_version": 1,
                "expected_version": 1, "instruction": "修改引用编号", "selection": choice,
            })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["content"], artifact["content"].replace("7319", "9000"))
        model.assert_called_once()

    def test_tampered_selection_in_chat_is_rejected_before_model_or_history_write(self):
        artifact = self.save_message()
        before = self.db.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
        choice = selected(artifact["content"], "7319")
        choice["text"] = "篡改"
        with patch.object(chat_fixtures.chat_service, "llm_chat") as model:
            response = self.client.post("/api/chat/talk", json={
                "student_id": "session", "user_id": "local_user", "message": "解释这段",
                "resource_ref": {"resource_id": artifact["id"], "version": 1, "selection": choice},
            })
        self.assertGreaterEqual(response.status_code, 400)
        model.assert_not_called()
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM conversations").fetchone()[0], before)

    def test_chat_partial_revision_records_reference_and_commits_once(self):
        artifact = self.save_message()
        choice = selected(artifact["content"], "7319")
        message = "把引用编号改为9000"
        self.planner.return_value = (json.dumps({"action": "revise", "evidence": message, "target": ""}),
                                    {"finish_reason": "stop"})
        with patch.object(chat_fixtures.artifact_revision, "chat_with_diagnostics", return_value=(
                '{"replacement":"9000"}', {"finish_reason": "stop"})) as model:
            response = self.client.post("/api/chat/talk", json={
                "student_id": "session", "user_id": "local_user", "message": message,
                "resource_ref": {"resource_id": artifact["id"], "version": 1, "current_version": 1,
                                 "selection": choice},
            })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(store.read_artifact(artifact["id"])["content"],
                         artifact["content"].replace("7319", "9000"))
        self.assertEqual(len(store.list_versions(artifact["id"])), 2)
        model.assert_called_once()
        history = self.client.get("/api/conversations/session").json()
        saved = next(row for row in history if row["role"] == "user" and row["content"] == message)
        self.assertEqual(saved["resource_reference"]["selection"], choice)

    def test_restore_with_selection_restores_entire_historical_artifact(self):
        artifact = self.save_message()
        current = store.append_revision(artifact["id"], "新版开头\n新版选段\n新版结尾", expected_version=1,
                                         student_id="session")
        choice = selected(current["content"], "新版选段")
        message = "恢复第一版"
        self.planner.return_value = (json.dumps({"action": "restore", "evidence": message, "target": "第一版"}),
                                    {"finish_reason": "stop"})
        with patch.object(chat_fixtures.artifact_revision, "chat_with_diagnostics") as model:
            response = self.client.post("/api/chat/talk", json={
                "student_id": "session", "user_id": "local_user", "message": message,
                "resource_ref": {"resource_id": artifact["id"], "version": 2, "current_version": 2,
                                 "selection": choice},
            })
        self.assertEqual(response.status_code, 200, response.text)
        model.assert_not_called()
        restored = store.read_artifact(artifact["id"])
        self.assertEqual(restored["content"], artifact["content"])
        self.assertEqual(restored["version"], 3)
