"""One-call revisions over isolated SQLite, with real local content validators."""
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from db import database
from services import artifact_store as store
from services import artifact_revision as revision


INTERACTIVE = """<!doctype html><html><head><title>电流</title>
<style>body{background:white;color:black}</style></head><body>
<button onclick="step()">下一步</button><output id="value">0</output>
<script>let n=0;function step(){document.getElementById('value').textContent=++n;}</script>
</body></html>"""


def diagram(node_id="voltage", label="电压"):
    return {
        "schema_version": 1, "title": "电路关系", "summary": "电路物理量之间的关系",
        "nodes": [{"id": node_id, "label": label, "detail": "改变物理量并观察变化"}],
        "edges": [],
        "steps": [{"title": "观察电路", "caption": "观察电路变化", "active_nodes": [node_id],
                   "active_edges": [], "duration": 3.0}],
    }


class ArtifactRevisionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.db = sqlite3.connect(str(Path(temporary.name) / "revision.db"))
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.addCleanup(self.db.close)
        for module in (database, store):
            mocked = patch.object(module, "get_db", return_value=self.db)
            mocked.start()
            self.addCleanup(mocked.stop)
        database.init_db()
        self.db.executemany("INSERT INTO students(id,name,owner_id) VALUES (?,?,?)",
                            [("s1", "会话一", "u1"), ("s2", "会话二", "u1")])
        self.db.commit()
        mocked = patch.object(revision, "chat_with_diagnostics",
                              return_value=("修改后的完整正文", {"finish_reason": "stop", "model": "mock"}))
        self.llm = mocked.start()
        self.addCleanup(mocked.stop)
        budget = patch.object(revision, "input_budget", return_value=24000)
        self.budget = budget.start()
        self.addCleanup(budget.stop)

    def create(self, kind="document", content="初版正文\n  精确保留格式\n", metadata=None):
        return store.create_artifact("s1", kind, "原题目", content, metadata, user_id="u1")

    def originals(self):
        return {
            table: [tuple(row) for row in self.db.execute(f"SELECT * FROM {table} ORDER BY id")]
            for table in ("resources", "resource_versions")
        }

    def revise(self, artifact, base_version=1, expected_version=1):
        return revision.revise_artifact(artifact["id"], "s1", "u1", base_version,
                                        expected_version, "只调整第二段，保留其余内容")

    def test_success_calls_model_once_and_appends_complete_new_version(self):
        artifact = self.create(metadata={"custom": "保留"})
        old_snapshot = tuple(self.db.execute("SELECT * FROM resource_versions WHERE resource_id=? AND version=1",
                                            (artifact["id"],)).fetchone())
        raw = "新版全文\n\n  原有格式\n"
        self.llm.return_value = raw, {"finish_reason": "stop", "model": "mock"}
        result = self.revise(artifact)
        self.llm.assert_called_once()
        self.assertEqual(self.llm.call_args.kwargs["max_tokens"], revision.LLM_MAX_OUTPUT_TOKENS)
        self.assertEqual(self.llm.call_args.kwargs["thinking"], "disabled")
        self.assertEqual((result["version"], result["parent_version"]), (2, 1))
        self.assertEqual(result["content"], raw)
        self.assertEqual(result["title"], artifact["title"])
        self.assertEqual(result["metadata"]["custom"], "保留")
        self.assertEqual(tuple(self.db.execute("SELECT * FROM resource_versions WHERE resource_id=? AND version=1",
                                              (artifact["id"],)).fetchone()), old_snapshot)

    def test_revision_based_on_old_version_reads_old_source_and_records_correct_parent(self):
        first = self.create()
        store.append_revision(first["id"], "v2 不同内容", expected_version=1)
        result = self.revise(first, base_version=1, expected_version=2)
        self.assertEqual((result["version"], result["parent_version"]), (3, 1))
        sent = json.loads(self.llm.call_args.args[0][1]["content"])
        self.assertEqual(sent["original"]["version"], 1)
        self.assertEqual(sent["original"]["content"], first["content"])
        self.assertEqual(store.read_artifact(first["id"], 2)["content"], "v2 不同内容")

    def test_bad_html_length_empty_and_provider_failure_preserve_old_projection_and_snapshot(self):
        first = self.create("interactive", INTERACTIVE)
        before = self.originals()
        cases = [
            (("<html><body>正文未结束", {"finish_reason": "stop"}), None, 422),
            ((INTERACTIVE, {"finish_reason": "length"}), None, 422),
            ((" ", {"finish_reason": "stop"}), None, 422),
            (None, RuntimeError("provider failed"), 502),
        ]
        for returned, failure, status in cases:
            with self.subTest(status=status, returned=returned):
                self.llm.reset_mock()
                self.llm.side_effect = failure
                self.llm.return_value = returned
                with self.assertRaises(store.ArtifactError) as caught:
                    self.revise(first)
                self.assertEqual(caught.exception.status_code, status)
                self.llm.assert_called_once()
                self.assertEqual(self.originals(), before)

    def test_successful_html_keeps_script_and_other_unchanged_source(self):
        first = self.create("interactive", INTERACTIVE)
        revised = INTERACTIVE.replace("color:black", "color:#8d2727")
        self.llm.return_value = revised, {"finish_reason": "stop"}
        result = self.revise(first)
        self.assertEqual(result["content"], revised)
        self.assertIn("function step()", result["content"])
        self.assertTrue(result["metadata"]["structure_validation"]["passed"])
        self.assertEqual(store.read_artifact(first["id"], 1)["content"], INTERACTIVE)
        self.llm.assert_called_once()

    def test_version_race_during_model_wait_returns_conflict_without_overwrite(self):
        first = self.create()

        def concurrent_save(*args, **kwargs):
            store.append_revision(first["id"], "另一个请求已保存的作品", expected_version=1)
            return "本请求迟到的输出", {"finish_reason": "stop"}

        self.llm.side_effect = concurrent_save
        with self.assertRaises(store.ArtifactError) as caught:
            self.revise(first)
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(caught.exception.detail["current_version"], 2)
        self.assertEqual(store.read_artifact(first["id"])["content"], "另一个请求已保存的作品")
        self.assertEqual(len(store.list_versions(first["id"])), 2)
        self.llm.assert_called_once()

    def test_lecture_revision_replaces_old_bundle_and_removes_old_error_state(self):
        old_bundle = diagram()
        new_bundle = diagram("current", "电流")
        first = self.create("lecture", "旧版讲解", {"explanation_bundle": old_bundle,
                                                   "visualization_error": "旧错误", "unrelated": "保留"})
        self.llm.return_value = json.dumps({"markdown": "新版电流讲解", "bundle": new_bundle}, ensure_ascii=False), {
            "finish_reason": "stop"}
        result = self.revise(first)
        self.assertEqual(result["content"], "新版电流讲解")
        self.assertEqual(result["metadata"]["explanation_bundle"], new_bundle)
        self.assertNotIn("visualization_error", result["metadata"])
        self.assertEqual(result["metadata"]["unrelated"], "保留")
        self.assertEqual(result["metadata"]["visualization_validation"]["status"], "valid")
        self.assertEqual(store.read_artifact(first["id"], 1)["metadata"]["explanation_bundle"], old_bundle)
        self.llm.assert_called_once()

    def test_lecture_explicit_null_bundle_does_not_reuse_old_visual(self):
        first = self.create("lecture", "旧讲解", {"explanation_bundle": diagram()})
        self.llm.return_value = '{"markdown":"只需要正文的新讲解","bundle":null}', {"finish_reason": "stop"}
        result = self.revise(first)
        self.assertIsNone(result["metadata"]["explanation_bundle"])
        self.assertEqual(result["metadata"]["visualization_validation"]["status"], "not_applicable")
        self.llm.assert_called_once()

    def test_invalid_lecture_bundle_rejected_without_changing_either_version(self):
        first = self.create("lecture", "旧讲解", {"explanation_bundle": diagram()})
        before = self.originals()
        self.llm.return_value = '{"markdown":"正文","bundle":{"schema_version":1}}', {"finish_reason": "stop"}
        with self.assertRaises(store.ArtifactError) as caught:
            self.revise(first)
        self.assertEqual(caught.exception.status_code, 422)
        self.assertEqual(self.originals(), before)
        self.llm.assert_called_once()

    def test_preflight_rejects_stale_version_cross_session_unsupported_type_and_oversize(self):
        first = self.create()
        with self.assertRaises(store.ArtifactError) as caught:
            self.revise(first, expected_version=2)
        self.assertEqual(caught.exception.status_code, 409)
        with self.assertRaises(store.ArtifactError) as caught:
            revision.revise_artifact(first["id"], "s2", "u1", 1, 1, "修改")
        self.assertEqual(caught.exception.status_code, 404)
        unsupported = self.create("quiz", '{"questions":[]}')
        with self.assertRaises(store.ArtifactError) as caught:
            self.revise(unsupported)
        self.assertEqual(caught.exception.status_code, 400)
        self.budget.return_value = 10
        with self.assertRaises(store.ArtifactError) as caught:
            self.revise(first)
        self.assertEqual(caught.exception.status_code, 413)
        self.llm.assert_not_called()


if __name__ == "__main__":
    unittest.main()
