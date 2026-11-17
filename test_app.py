import json
import os
import unittest
from contextlib import contextmanager
from io import BytesIO
from unittest.mock import Mock, patch

import app as quiz_app
from generation import generate_quiz
from PIL import Image


class QuizRulesTests(unittest.TestCase):
    def setUp(self):
        self.questions = [{"question": "Co se stalo?", "options": ["A", "B", "C", "D"],
                           "correct": 0, "source_quote": "Přesný text.", "source_image": "2"}]

    def test_correct_zero_and_quote_after_grading(self):
        details = quiz_app.grade_answers(self.questions, {"0": 0})
        self.assertTrue(details[0]["is_correct"])
        self.assertEqual(details[0]["correct_index"], 0)
        self.assertEqual(details[0]["source_quote"], "Přesný text.")

    def test_student_quiz_does_not_expose_answers_or_quote(self):
        row = {"id": "quiz", "questions": self.questions}
        student = quiz_app.serialize_quiz(row.copy())
        self.assertEqual(student["questions"], [{"question": "Co se stalo?", "options": ["A", "B", "C", "D"]}])

    def test_all_answers_required_and_invalid_index_rejected(self):
        with self.assertRaises(ValueError):
            quiz_app.grade_answers(self.questions, {})
        with self.assertRaises(ValueError):
            quiz_app.grade_answers(self.questions, {"0": True})
        with self.assertRaises(ValueError):
            quiz_app.grade_answers(self.questions, {"0": 4})

    def test_validation_preserves_quote_and_rejects_duplicate_options(self):
        title, cleaned = quiz_app.validate_quiz({"title": "Test", "questions": self.questions})
        self.assertEqual(title, "Test")
        self.assertEqual(cleaned[0]["source_quote"], "Přesný text.")
        bad = [{**self.questions[0], "options": ["A", "A", "C", "D"]}]
        with self.assertRaises(ValueError):
            quiz_app.validate_quiz({"title": "Test", "questions": bad})

    def test_private_endpoints_require_login(self):
        client = quiz_app.app.test_client()
        for path in ("/get_quiz?id=quiz", "/get_results", "/get_result?id=1", "/admin/accounts"):
            self.assertEqual(client.get(path).status_code, 401)
        self.assertEqual(client.post("/create_quiz", json={}).status_code, 401)
        self.assertEqual(client.delete("/delete_quiz?id=quiz").status_code, 401)

    def test_only_known_frontend_origin_receives_cors_header(self):
        client = quiz_app.app.test_client()
        allowed = client.get("/", headers={"Origin": "https://unique-truffle-80425d.netlify.app"})
        unknown = client.get("/", headers={"Origin": "https://example.com"})
        self.assertEqual(allowed.headers.get("Access-Control-Allow-Origin"), "https://unique-truffle-80425d.netlify.app")
        self.assertIsNone(unknown.headers.get("Access-Control-Allow-Origin"))


class FakeCursor:
    def __init__(self, rows):
        self.rows = iter(rows)

    def execute(self, _sql, _args=None):
        pass

    def fetchone(self):
        return next(self.rows, None)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class FakeConnection:
    def __init__(self, rows):
        self.rows = rows

    def cursor(self):
        return FakeCursor(self.rows)


class PrivacyTests(unittest.TestCase):
    def test_student_cannot_read_another_result(self):
        result = {"id": 1, "account_id": 88, "answers": [], "quiz_title": "Téma"}
        rows = iter([{"id": 7, "username": "Eva", "role": "student"}, result])
        @contextmanager
        def fake_connection():
            yield FakeConnection(rows)
        with patch.dict(os.environ, {"QUIZ_SESSION_SECRET": "s" * 40}), \
             patch.object(quiz_app, "connection", fake_connection), \
             patch.object(quiz_app, "signer", return_value=Mock(loads=lambda *args, **kwargs: 7)):
            response = quiz_app.app.test_client().get("/get_result?id=1", headers={"Authorization": "Bearer signed"})
        self.assertEqual(response.status_code, 404)

    def test_student_cannot_create_quiz(self):
        rows = iter([{"id": 7, "username": "Eva", "role": "student"}])
        @contextmanager
        def fake_connection():
            yield FakeConnection(rows)
        with patch.object(quiz_app, "connection", fake_connection), \
             patch.object(quiz_app, "signer", return_value=Mock(loads=lambda *args, **kwargs: 7)):
            response = quiz_app.app.test_client().post("/create_quiz", json={}, headers={"Authorization": "Bearer signed"})
        self.assertEqual(response.status_code, 403)


class GenerationTests(unittest.TestCase):
    def test_ten_photos_can_produce_twenty_questions_without_saving(self):
        image = Image.new("RGB", (200, 300), "white")
        stream = BytesIO()
        image.save(stream, format="JPEG")
        files = [BytesIO(stream.getvalue()) for _ in range(10)]
        questions = [{"question": f"Otázka {i}?", "options": ["A", "B", "C", "D"],
                      "correct": i % 4, "source_quote": "Doslovný úryvek", "source_image": str(i % 10 + 1)}
                     for i in range(20)]
        fake_client = Mock()
        fake_client.responses.create.return_value = Mock(status="completed", output_text=json.dumps({"title": "Učivo", "questions": questions}))
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-only-placeholder"}), \
             patch("generation.OpenAI", return_value=fake_client):
            quiz = generate_quiz(files, 20, "5. třída ZŠ", "Učivo")
        self.assertEqual(len(quiz["questions"]), 20)
        sent = fake_client.responses.create.call_args.kwargs["input"][0]["content"]
        self.assertEqual(sum(item["type"] == "input_image" for item in sent), 10)


class AccountPolicyTests(unittest.TestCase):
    def test_eight_character_bootstrap_password_creates_admin(self):
        cursor = FakeCursor([])
        cursor.execute = Mock()
        connection = Mock()
        connection.cursor.return_value = cursor
        @contextmanager
        def fake_connection():
            yield connection
        with patch.dict(os.environ, {"QUIZ_BOOTSTRAP_ADMIN_PASSWORD": "abcdefgh"}), \
             patch.object(quiz_app, "connection", fake_connection), \
             patch.object(quiz_app, "generate_password_hash", return_value="hashed"):
            quiz_app.initialize_db()
        self.assertTrue(any("INSERT INTO accounts" in call.args[0] for call in cursor.execute.call_args_list))

    def test_tenth_failed_login_temporarily_locks_account(self):
        account = {"id": 3, "username": "dite", "role": "student", "password_hash": "hash",
                   "failed_attempts": 9, "is_locked": False, "lock_expired": False}
        cursor = FakeCursor([account])
        cursor.execute = Mock()
        connection = Mock()
        connection.cursor.return_value = cursor
        @contextmanager
        def fake_connection():
            yield connection
        with patch.object(quiz_app, "connection", fake_connection), \
             patch.object(quiz_app, "check_password_hash", return_value=False):
            response = quiz_app.app.test_client().post("/auth/login", json={"username": "dite", "password": "wrong"})
        self.assertEqual(response.status_code, 429)
        self.assertTrue(any("INTERVAL '15 minutes'" in call.args[0] for call in cursor.execute.call_args_list))


if __name__ == "__main__":
    unittest.main()
