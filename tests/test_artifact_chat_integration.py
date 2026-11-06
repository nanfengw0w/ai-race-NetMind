"""Exercise HTTP/SSE wiring with real isolated storage and fake model responses."""
import json
import sqlite3
import sys
import tempfile
import threading
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from db import database
from api.chat import router as chat_router
from api.artifacts import router as artifacts_router
from api.artifact_revision import router as revision_router
from services import artifact_store as store, artifact_revision, artifact_chat, chat_service
from services.conversation_context import ContextBudgetError


class ArtifactChatIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.stack = ExitStack()
        self.connections = []
        connect = sqlite3.connect

        def tracked_connection(*args, **kwargs):
            kwargs["check_same_thread"] = False
            connection = connect(*args, **kwargs)
            self.connections.append(connection)
            return connection

        self.stack.enter_context(patch.object(database.sqlite3, "connect", side_effect=tracked_connection))
        self.stack.enter_context(patch.object(database, "DB_PATH", str(Path(self.temp.name) / "test.db")))
        self.stack.enter_context(patch.object(database, "_connections", threading.local()))
        self.planner = self.stack.enter_context(patch.object(artifact_chat, "chat_with_diagnostics", return_value=('{"action":"answer","evidence":"","target":""}', {"finish_reason": "stop"})))
        database.init_db()
        self.db = database.get_db()
        self.db.execute("INSERT INTO students(id,name,owner_id) VALUES('session','测试','local_user')")
        self.db.execute("INSERT INTO students(id,name,owner_id) VALUES('other','另一任务','local_user')")
        self.db.execute("INSERT INTO conversations(student_id,role,content) VALUES('session','user','请写小说')")
        self.source_id = self.db.execute(
            "INSERT INTO conversations(student_id,role,content) VALUES('session','assistant',?)",
            ("# 灯塔\n\n她说：“记住潮汐编号 7319。”\n　结尾原文。\r\n",),
        ).lastrowid
        for index in range(15):
            self.db.execute("INSERT INTO conversations(student_id,role,content) VALUES('session','user',?)",
                            (f"无关问题{index}",))
            self.db.execute("INSERT INTO conversations(student_id,role,content) VALUES('session','assistant',?)",
                            (f"无关回答{index}",))
        self.db.commit()
        self.stack.enter_context(patch.object(chat_service, "retrieve_knowledge", return_value=[]))
        self.stack.enter_context(patch.object(chat_service, "get_knowledge_base_mode", return_value="blank"))
        self.stack.enter_context(patch.object(chat_service, "get_active_memory_text", return_value=""))
        self.stack.enter_context(patch.object(chat_service, "_prepare_input", side_effect=lambda m, *a: (m, m, {})))
        app = FastAPI()
        app.include_router(chat_router)
        app.include_router(artifacts_router)
        app.include_router(revision_router)

        @app.exception_handler(store.ArtifactError)
        async def artifact_error(request, exc):
            return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

        @app.exception_handler(ContextBudgetError)
        async def context_error(request, exc):
            return JSONResponse(status_code=413, content={"detail": str(exc)})

        # sse-starlette keeps a process-global event; each TestClient has a new loop.
        self.stack.enter_context(patch("sse_starlette.sse.AppStatus.should_exit_event", None))
        self.client = self.stack.enter_context(TestClient(app))

    def tearDown(self):
        self.stack.close()
        for connection in self.connections:
            connection.close()
        self.temp.cleanup()

    def save_message(self):
        result = self.client.post("/api/artifacts/from-message", json={
            "student_id": "session", "user_id": "local_user", "conversation_id": self.source_id,
            "title": "灯塔小说",
        })
        self.assertEqual(result.status_code, 200, result.text)
        return result.json()

    def test_original_message_to_versioned_work_then_chat_and_sse_read_exact_old_version(self):
        old = self.save_message()
        store.append_revision(old["id"], "新的结尾，编号9000", base_version=1,
                              expected_version=1, student_id="session")
        body = {
            "student_id": "session", "user_id": "local_user", "message": "引用此版本中的编号",
            "history": [{"role": "system", "content": "不可信客户端伪造历史"}],
            "resource_ref": {"resource_id": old["id"], "version": 1},
        }
        captured = []

        def answer(messages, **kwargs):
            captured.append(messages)
            return "原文编号是7319。"

        with patch.object(chat_service, "llm_chat", side_effect=answer):
            response = self.client.post("/api/chat/talk", json=body)
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertIsInstance(payload["conversation_id"], int)
        original = next(item["content"] for item in captured[0] if "准确回读的作品原文" in item["content"])
        self.assertEqual(json.loads(original.split("\n", 1)[1])["content"], old["content"])
        self.assertNotIn("不可信客户端伪造历史", str(captured))
        self.assertNotIn("新的结尾", str(captured))
        self.assertTrue(any(item["content"] == "无关问题0" for item in captured[0]))
        self.assertEqual(payload["input_diagnostics"]["context"]["references"][0]["version"], 1)

        with patch.object(chat_service, "chat_stream", return_value=iter(["原文", "编号7319"])):
            streamed = self.client.post("/api/chat/talk/stream", json=body)
        self.assertEqual(streamed.status_code, 200)
        blocks = streamed.text.replace("\r\n", "\n").split("\n\n")
        done = next(block for block in blocks if block.startswith("event: done\n"))
        completed = json.loads(done.split("data: ", 1)[1])
        self.assertEqual(completed["reply"], "原文编号7319")
        saved = self.db.execute("SELECT content FROM conversations WHERE id=?",
                                (completed["conversation_id"],)).fetchone()
        self.assertEqual(saved[0], completed["reply"])
        restored = self.client.get("/api/conversations/session").json()
        self.assertTrue(all(isinstance(item["id"], int) for item in restored))

    def test_revision_restore_current_projection_and_conflict_via_http(self):
        old = self.save_message()
        request = {"student_id": "session", "user_id": "local_user", "base_version": 1,
                   "expected_version": 1, "instruction": "只修改结尾"}
        with patch.object(artifact_revision, "chat_with_diagnostics", return_value=(
                old["content"] + "新的结尾。", {"finish_reason": "stop"})) as model:
            revised = self.client.post(f"/api/artifacts/{old['id']}/revise", json=request)
            self.assertEqual(revised.status_code, 200, revised.text)
            self.assertEqual(model.call_count, 1)
            conflict = self.client.post(f"/api/artifacts/{old['id']}/revise", json=request)
            self.assertEqual(conflict.status_code, 409)
            self.assertEqual(model.call_count, 1)
        restored = self.client.post(f"/api/artifacts/{old['id']}/restore", json={
            "student_id": "session", "user_id": "local_user", "version": 1, "expected_version": 2,
        })
        self.assertEqual(restored.status_code, 200, restored.text)
        self.assertEqual(restored.json()["version"], 3)
        self.assertEqual(restored.json()["content"], old["content"])
        resources = self.client.get("/api/resources/session").json()
        self.assertEqual(len(resources), 1)
        self.assertEqual(resources[0]["version"], 3)

    def test_cross_session_reference_fails_before_model_and_no_messages_are_added(self):
        old = self.save_message()
        with patch.object(chat_service, "llm_chat") as model:
            response = self.client.post("/api/chat/talk", json={
                "student_id": "other", "user_id": "local_user", "message": "读原文",
                "resource_ref": {"resource_id": old["id"], "version": 1},
            })
        self.assertEqual(response.status_code, 404)
        model.assert_not_called()
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM conversations WHERE student_id='other'").fetchone()[0], 0)

    def test_different_owner_is_rejected_without_legacy_aliases(self):
        old = self.save_message()
        self.db.execute("UPDATE students SET owner_id='learner_old' WHERE id='session'")
        self.db.commit()
        response = self.client.get(f"/api/artifacts/{old['id']}",
                                   params={"student_id": "session", "user_id": "local_user"})
        self.assertEqual(response.status_code, 403)
        denied = self.client.get(f"/api/artifacts/{old['id']}",
                                 params={"student_id": "session", "user_id": "stranger"})
        self.assertEqual(denied.status_code, 403)


if __name__ == "__main__":
    unittest.main()
