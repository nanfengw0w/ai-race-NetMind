"""Artifact persistence/API contract using isolated SQLite and no model calls."""
import hashlib
import sqlite3
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from db import database
from services import artifact_store as store


class ArtifactStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.previous_connections = database._connections
        self.path_patch = patch.object(database, "DB_PATH", str(Path(self.temp.name) / "test.db"))
        self.path_patch.start()
        database._connections = threading.local()
        database.init_db()
        self.db = database.get_db()
        self.db.executemany("INSERT INTO students (id,name,owner_id) VALUES (?,?,?)",
                            [("s1", "一", "u1"), ("s2", "二", "u1"), ("s3", "三", "u2")])
        self.db.commit()

    def tearDown(self):
        self.db.close()
        database._connections = self.previous_connections
        self.path_patch.stop()
        self.temp.cleanup()

    def artifact(self):
        return store.create_artifact("s1", "document", "标题", "初始全文", {"a": 1}, user_id="u1")

    def assert_error(self, status, function, *args, **kwargs):
        with self.assertRaises(store.ArtifactError) as caught:
            function(*args, **kwargs)
        self.assertEqual(caught.exception.status_code, status)
        return caught.exception

    def test_new_artifact_preserves_exact_content_and_metadata(self):
        content = "  <html>\n<script>let x=1;</script></html>\n"
        metadata = {"a": 1, "中文": "完整原文"}
        first = store.create_artifact("s1", "interactive", "原始", content, metadata, user_id="u1")
        second = store.read_artifact(first["id"], student_id="s1", user_id="u1")
        self.assertEqual(first, second)
        self.assertEqual(first["content"], content)
        self.assertEqual(first["metadata"], metadata)
        self.assertEqual(first["version"], 1)
        self.assertEqual(first["content_hash"], hashlib.sha256(content.encode()).hexdigest())
        projection = self.db.execute("SELECT * FROM resources WHERE id=?", (first["id"],)).fetchone()
        snapshot = self.db.execute("SELECT * FROM resource_versions WHERE resource_id=?", (first["id"],)).fetchone()
        self.assertEqual(projection["content"], content)
        self.assertEqual(snapshot["content"], content)
        self.assertEqual(projection["metadata_json"], first["metadata_json"])
        self.assertEqual(snapshot["metadata_json"], first["metadata_json"])

    def test_unversioned_old_data_is_not_readable_or_searchable_and_never_backfilled(self):
        valid = self.artifact()
        old_ids = []
        for current_version in (0, 1):
            cursor = self.db.execute(
                "INSERT INTO resources (student_id,type,title,content,metadata_json,current_version) "
                "VALUES ('s1','document','废弃旧数据','旧数据全文','{}',?)",
                (current_version,),
            )
            old_ids.append(cursor.lastrowid)
        self.db.commit()
        before = [tuple(row) for row in self.db.execute("SELECT * FROM resources ORDER BY id")]
        snapshots_before = [tuple(row) for row in self.db.execute("SELECT * FROM resource_versions ORDER BY id")]
        for resource_id in old_ids:
            with self.subTest(resource_id=resource_id):
                self.assert_error(404, store.read_artifact, resource_id, student_id="s1", user_id="u1")
                self.assert_error(404, store.read_artifact, resource_id, version=1, student_id="s1", user_id="u1")
        self.assertEqual([row["id"] for row in store.search_artifacts("s1", "u1")], [valid["id"]])
        self.assertEqual(store.search_artifacts("s1", "u1", "废弃旧数据"), [])
        self.assertEqual([tuple(row) for row in self.db.execute("SELECT * FROM resources ORDER BY id")], before)
        self.assertEqual([tuple(row) for row in self.db.execute("SELECT * FROM resource_versions ORDER BY id")],
                         snapshots_before)

    def test_revisions_restore_and_metadata_are_immutable_snapshots(self):
        first = self.artifact()
        second = store.append_revision(first["id"], "修改全文", {"a": 2}, "新标题", 1, 1,
                                       "仅改颜色", "s1", "u1")
        self.assertEqual((second["version"], second["current_version"], second["parent_version"]), (2, 2, 1))
        old = store.read_artifact(first["id"], 1, "s1", "u1")
        self.assertEqual(old["content"], "初始全文")
        self.assertEqual(old["metadata"], {"a": 1})
        self.assertEqual(old["current_version"], 2)
        restored = store.restore_version(first["id"], 1, 2, "s1", "u1")
        self.assertEqual(restored["version"], 3)
        self.assertEqual(restored["content"], first["content"])
        self.assertEqual(restored["metadata_json"], first["metadata_json"])
        self.assertEqual(restored["title"], first["title"])
        self.assertEqual(store.read_artifact(first["id"], 2)["content"], "修改全文")
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("UPDATE resource_versions SET content='oops' WHERE resource_id=?", (first["id"],))
        self.db.rollback()

    def test_conflicts_invalid_input_and_missing_versions_roll_back_atomically(self):
        first = self.artifact()
        store.append_revision(first["id"], "第二版", expected_version=1)
        error = self.assert_error(409, store.append_revision, first["id"], "过期写入", expected_version=1)
        self.assertEqual(error.detail["current_version"], 2)
        self.assert_error(400, store.append_revision, first["id"], "无版本保护")
        self.assert_error(400, store.append_revision, first["id"], "不合法元数据", metadata=[], expected_version=2)
        self.assert_error(404, store.append_revision, first["id"], "丢失基线", base_version=9, expected_version=2)
        self.assert_error(404, store.restore_version, first["id"], 9, 2)
        self.assertEqual(store.read_artifact(first["id"])["content"], "第二版")
        self.assertEqual(len(store.list_versions(first["id"])), 2)

    def test_all_external_operations_check_owner_and_exact_session(self):
        first = self.artifact()
        operations = [
            (store.read_artifact, (first["id"],), {}),
            (store.list_versions, (first["id"],), {}),
            (store.append_revision, (first["id"], "修改"), {"expected_version": 1}),
            (store.restore_version, (first["id"], 1, 1), {}),
        ]
        for function, args, kwargs in operations:
            with self.subTest(function.__name__):
                self.assert_error(403, function, *args, student_id="s1", user_id="u2", **kwargs)
                self.assert_error(404, function, *args, student_id="s2", user_id="u1", **kwargs)
        self.assert_error(403, store.search_artifacts, "s1", "u2")
        self.assert_error(403, store.create_artifact, "s1", "document", "", "", user_id="u2")
        self.assert_error(400, store.read_artifact, first["id"], user_id="u1")

    def test_source_message_is_persisted_assistant_only_and_idempotent(self):
        cursor = self.db.execute("INSERT INTO conversations (student_id,role,content) VALUES ('s1','assistant',?)",
                                 ("完整原文\n```html\n<p>x</p>\n```",))
        message_id = cursor.lastrowid
        user_message = self.db.execute("INSERT INTO conversations (student_id,role,content) VALUES ('s1','user','提问')").lastrowid
        self.db.commit()
        first = store.create_artifact_from_message(message_id, "s1", "u1", "保存消息")
        repeated = store.create_artifact_from_message(message_id, "s1", "u1", "另一个标题")
        self.assertEqual(first["id"], repeated["id"])
        self.assertEqual(first["type"], "document")
        self.assertEqual(first["source_conversation_id"], message_id)
        self.assertEqual(first["metadata"]["source_message"], message_id)
        self.assertEqual(first["content"], "完整原文\n```html\n<p>x</p>\n```")
        self.assert_error(400, store.create_artifact_from_message, user_message, "s1", "u1")
        self.assert_error(404, store.create_artifact_from_message, message_id, "s2", "u1")
        self.assert_error(403, store.create_artifact_from_message, message_id, "s1", "u2")
        self.assert_error(404, store.create_artifact_from_message, 999, "s1", "u1")

    def test_search_and_version_list_do_not_expose_full_content_or_large_metadata(self):
        first = store.create_artifact("s1", "interactive", "UniqueTitle", "全文" * 1000,
                                      {"html": "全文" * 1000}, user_id="u1")
        result = store.search_artifacts("s1", "u1", "uniquetitle")
        self.assertEqual(len(result), 1)
        self.assertNotIn("content", result[0])
        self.assertNotIn("metadata_json", result[0])
        self.assertNotIn("metadata", result[0])
        self.assertLessEqual(len(result[0]["summary"]), 180)
        self.assertNotIn("content", store.list_versions(first["id"], "s1", "u1")[0])
        self.assertEqual(store.search_artifacts("s2", "u1"), [])
        self.assertEqual(store.search_artifacts("s1", "u1", "missing"), [])

    def test_resource_deletion_cascades_versions_and_context_references(self):
        message_id = self.db.execute("INSERT INTO conversations (student_id,role,content) VALUES ('s1','assistant','原文')").lastrowid
        self.db.commit()
        first = store.create_artifact_from_message(message_id, "s1", "u1")
        store.append_revision(first["id"], "新版", expected_version=1)
        self.db.execute("INSERT INTO conversation_resource_refs (conversation_id,resource_id,version) VALUES (?,?,?)", (message_id, first["id"], 2))
        self.db.execute("DELETE FROM resources WHERE id=?", (first["id"],))
        self.db.commit()
        for table in ("resource_versions", "resource_source_messages", "conversation_resource_refs"):
            self.assertEqual(self.db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0)

    def test_two_connections_cannot_append_same_expected_version(self):
        first = self.artifact()
        outcomes = []
        barrier = threading.Barrier(2)

        def worker(content):
            try:
                barrier.wait(timeout=5)
                result = store.append_revision(first["id"], content, expected_version=1)
                outcomes.append(result["version"])
            except store.ArtifactError as exc:
                outcomes.append(exc.status_code)
            finally:
                database.get_db().close()

        threads = [threading.Thread(target=worker, args=(f"修改{i}",)) for i in (1, 2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
            self.assertFalse(thread.is_alive())
        self.assertCountEqual(outcomes, [2, 409])
        self.assertEqual(len(store.list_versions(first["id"])), 2)

    def test_api_contract(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from api.artifacts import router

        app = FastAPI()
        app.include_router(router)
        # Use this test's temporary connection in TestClient's worker thread.
        api_db = sqlite3.connect(str(Path(self.temp.name) / "test.db"), check_same_thread=False)
        api_db.row_factory = sqlite3.Row
        api_db.execute("PRAGMA foreign_keys=ON")
        try:
            with patch.object(store, "get_db", return_value=api_db), TestClient(app) as client:
                response = client.post("/api/artifacts", json={"student_id": "s1", "user_id": "u1",
                                                             "type": "document", "title": "标题", "content": "原文"})
                self.assertEqual(response.status_code, 200, response.text)
                resource_id = response.json()["id"]
                path = f"/api/artifacts/{resource_id}"
                identity = {"student_id": "s1", "user_id": "u1"}
                self.assertEqual(client.get(path, params=identity).json()["content"], "原文")
                self.assertEqual(client.get(path).status_code, 422)
                self.assertEqual(client.get(path, params={**identity, "user_id": "u2"}).status_code, 403)
                self.assertEqual(client.get(path + "/versions", params=identity).json()[0]["version"], 1)
                self.assertEqual(len(client.get("/api/artifacts", params={**identity, "q": "原文"}).json()), 1)
                restore = {**identity, "version": 1, "expected_version": 1}
                self.assertEqual(client.post(path + "/restore", json=restore).json()["version"], 2)
                self.assertEqual(client.post(path + "/restore", json=restore).status_code, 409)
        finally:
            api_db.close()


if __name__ == "__main__":
    unittest.main()
