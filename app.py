"""Quiz API. Existing quizzes and historical results are kept in place."""

import json
import os
from contextlib import contextmanager
from functools import wraps
from hmac import compare_digest
from uuid import uuid4

import psycopg2
from flask import Flask, g, jsonify, request
from flask_cors import CORS
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from psycopg2.extras import RealDictCursor
from werkzeug.security import check_password_hash, generate_password_hash

from generation import GenerationError, generate_quiz


app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 80 * 1024 * 1024
origins = [item.strip() for item in os.getenv(
    "FRONTEND_ORIGINS", "http://localhost:3000,https://unique-truffle-80425d.netlify.app"
).split(",") if item.strip()]
CORS(app, origins=origins, allow_headers=["Authorization", "Content-Type"])


def db():
    return psycopg2.connect(os.environ["DATABASE_URL"], cursor_factory=RealDictCursor)


@contextmanager
def connection():
    conn = db()
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def initialize_db():
    """Additive migration; original quiz and result rows are preserved."""
    with connection() as conn, conn.cursor() as cur:
        cur.execute("""CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY, username VARCHAR(100) DEFAULT 'Anonym',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
        cur.execute("""CREATE TABLE IF NOT EXISTS quizzes (
            id VARCHAR(50) PRIMARY KEY, title VARCHAR(255) NOT NULL,
            questions JSONB NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
        cur.execute("""CREATE TABLE IF NOT EXISTS results (
            id SERIAL PRIMARY KEY, quiz_id VARCHAR(50) REFERENCES quizzes(id) ON DELETE CASCADE,
            user_id INT REFERENCES users(id) ON DELETE SET NULL, user_name VARCHAR(100),
            score INT NOT NULL, total INT NOT NULL, percentage DECIMAL(5,2) NOT NULL,
            answers JSONB NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
        cur.execute("""CREATE TABLE IF NOT EXISTS accounts (
            id SERIAL PRIMARY KEY, username VARCHAR(100) UNIQUE NOT NULL,
            password_hash TEXT NOT NULL, role VARCHAR(10) NOT NULL
                CHECK (role IN ('admin', 'student')),
            active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
        cur.execute("""ALTER TABLE results ADD COLUMN IF NOT EXISTS account_id
            INT REFERENCES accounts(id) ON DELETE SET NULL""")
        cur.execute("CREATE INDEX IF NOT EXISTS results_account_id_idx ON results(account_id)")
        # The bootstrap password is only used when no admin exists. Remove it from
        # Render after the first successful login; it is never stored in plaintext.
        cur.execute("SELECT id FROM accounts WHERE role = 'admin' LIMIT 1")
        if not cur.fetchone():
            username = os.getenv("QUIZ_BOOTSTRAP_ADMIN_USERNAME", "admin").strip()
            password = os.getenv("QUIZ_BOOTSTRAP_ADMIN_PASSWORD", "")
            if username and len(password) >= 12:
                cur.execute("""INSERT INTO accounts(username, password_hash, role)
                    VALUES (%s, %s, 'admin') ON CONFLICT (username) DO NOTHING""",
                    (username, generate_password_hash(password)))


def signer():
    secret = os.getenv("QUIZ_SESSION_SECRET", "")
    if len(secret) < 32:
        raise RuntimeError("QUIZ_SESSION_SECRET must contain at least 32 characters")
    return URLSafeTimedSerializer(secret, salt="quiz-session-v2")


def authenticated(role=None):
    def decorate(func):
        @wraps(func)
        def wrapped(*args, **kwargs):
            authorization = request.headers.get("Authorization", "")
            if not authorization.startswith("Bearer "):
                return jsonify(error="Přihlaš se prosím."), 401
            try:
                account_id = signer().loads(authorization[7:], max_age=7 * 24 * 3600)
            except (BadSignature, SignatureExpired, RuntimeError):
                return jsonify(error="Přihlášení vypršelo. Přihlaš se znovu."), 401
            with connection() as conn, conn.cursor() as cur:
                cur.execute("SELECT id, username, role FROM accounts WHERE id = %s AND active = TRUE", (account_id,))
                g.account = cur.fetchone()
            if not g.account:
                return jsonify(error="Účet není dostupný."), 401
            if role and g.account["role"] != role:
                return jsonify(error="Nemáš oprávnění k této akci."), 403
            return func(*args, **kwargs)
        return wrapped
    return decorate


def validate_quiz(data):
    if not isinstance(data, dict):
        raise ValueError("Očekávám kvíz ve formátu JSON.")
    title = data.get("title")
    questions = data.get("questions")
    if not isinstance(title, str) or not title.strip() or len(title.strip()) > 255:
        raise ValueError("Název kvízu musí mít 1 až 255 znaků.")
    if not isinstance(questions, list) or not 1 <= len(questions) <= 50:
        raise ValueError("Kvíz musí obsahovat 1 až 50 otázek.")
    cleaned = []
    seen = set()
    for index, q in enumerate(questions, 1):
        if not isinstance(q, dict):
            raise ValueError(f"Otázka {index} není platná.")
        question, options, correct = q.get("question"), q.get("options"), q.get("correct")
        if (not isinstance(question, str) or not question.strip() or len(question) > 1000
                or not isinstance(options, list) or len(options) != 4
                or any(not isinstance(x, str) or not x.strip() or len(x) > 500 for x in options)
                or len({x.strip().casefold() for x in options}) != 4
                or type(correct) is not int or correct not in range(4)):
            raise ValueError(f"Otázka {index} musí mít text, čtyři různé možnosti a správný index 0–3.")
        if question.strip().casefold() in seen:
            raise ValueError(f"Otázka {index} se opakuje.")
        seen.add(question.strip().casefold())
        source_quote = q.get("source_quote", "")
        source_image = q.get("source_image", "")
        if not isinstance(source_quote, str) or len(source_quote) > 1000:
            raise ValueError(f"Citace u otázky {index} je příliš dlouhá.")
        if not isinstance(source_image, str) or len(source_image) > 100:
            raise ValueError(f"Označení stránky u otázky {index} je neplatné.")
        cleaned.append({"question": question.strip(), "options": [x.strip() for x in options],
                        "correct": correct, "source_quote": source_quote.strip(),
                        "source_image": source_image.strip()})
    return title.strip(), cleaned


def serialize_quiz(row, admin=False):
    if not admin:
        row["questions"] = [{"question": q.get("question"), "options": q.get("options")}
                            for q in row["questions"]]
    return row


def save_quiz(data, require_quotes=False):
    title, questions = validate_quiz(data)
    if require_quotes and any(not q["source_quote"] or not q["source_image"] for q in questions):
        raise ValueError("Každá otázka musí mít citaci a číslo fotografie.")
    quiz_id = uuid4().hex
    with connection() as conn, conn.cursor() as cur:
        cur.execute("INSERT INTO quizzes(id, title, questions) VALUES (%s, %s, %s)",
                    (quiz_id, title, json.dumps(questions, ensure_ascii=False)))
    return jsonify(id=quiz_id, message="Kvíz byl uložen."), 201


@app.errorhandler(413)
def file_too_large(_error):
    return jsonify(error="Fotky jsou dohromady příliš velké (maximum 80 MB)."), 413


@app.get("/")
def health():
    return jsonify(status="ok")


@app.post("/auth/login")
def login():
    data = request.get_json(silent=True) or {}
    username, password = data.get("username"), data.get("password")
    if not isinstance(username, str) or not isinstance(password, str):
        return jsonify(error="Zadej jméno a heslo."), 400
    with connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT id, username, role, password_hash FROM accounts WHERE username = %s AND active = TRUE",
                    (username.strip(),))
        account = cur.fetchone()
    if not account or not check_password_hash(account["password_hash"], password):
        return jsonify(error="Nesprávné jméno nebo heslo."), 401
    return jsonify(token=signer().dumps(account["id"]),
                   account={key: account[key] for key in ("id", "username", "role")})


@app.get("/auth/me")
@authenticated()
def me():
    return jsonify(g.account)


@app.get("/admin/accounts")
@authenticated("admin")
def accounts():
    with connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT id, username, role, active, created_at FROM accounts ORDER BY id")
        return jsonify(cur.fetchall())


@app.post("/admin/accounts")
@authenticated("admin")
def create_account():
    data = request.get_json(silent=True) or {}
    username, password = data.get("username"), data.get("password")
    if (not isinstance(username, str) or not 2 <= len(username.strip()) <= 100
            or not isinstance(password, str) or len(password) < 12):
        return jsonify(error="Jméno musí mít aspoň 2 a heslo aspoň 12 znaků."), 400
    try:
        with connection() as conn, conn.cursor() as cur:
            cur.execute("""INSERT INTO accounts(username, password_hash, role)
                VALUES (%s, %s, 'student') RETURNING id""",
                (username.strip(), generate_password_hash(password)))
            return jsonify(id=cur.fetchone()["id"]), 201
    except psycopg2.errors.UniqueViolation:
        return jsonify(error="Toto jméno už existuje."), 409


@app.post("/admin/accounts/<int:account_id>/password")
@authenticated("admin")
def change_password(account_id):
    data = request.get_json(silent=True) or {}
    password = data.get("password")
    if not isinstance(password, str) or len(password) < 12:
        return jsonify(error="Heslo musí mít aspoň 12 znaků."), 400
    with connection() as conn, conn.cursor() as cur:
        cur.execute("UPDATE accounts SET password_hash = %s WHERE id = %s RETURNING id",
                    (generate_password_hash(password), account_id))
        if not cur.fetchone():
            return jsonify(error="Účet nebyl nalezen."), 404
    return jsonify(message="Heslo bylo změněno.")


@app.post("/admin/generate-quiz")
@authenticated("admin")
def generate():
    files = request.files.getlist("images")
    try:
        count = int(request.form.get("question_count", ""))
    except ValueError:
        count = 0
    audience = request.form.get("audience", "").strip()
    topic = request.form.get("topic", "").strip()
    if not 1 <= count <= 30 or not 2 <= len(audience) <= 100 or len(topic) > 150:
        return jsonify(error="Zadej cílovou skupinu a počet 1 až 30 otázek."), 400
    try:
        draft = generate_quiz(files, count, audience, topic)
        title, questions = validate_quiz(draft)
    except ValueError as error:
        return jsonify(error=str(error)), 400
    except GenerationError as error:
        return jsonify(error=str(error)), 502
    return jsonify(title=title, questions=questions)


@app.post("/create_quiz")
@authenticated("admin")
def create_quiz():
    try:
        return save_quiz(request.get_json(silent=True), require_quotes=True)
    except ValueError as error:
        return jsonify(error=str(error)), 400


@app.post("/create_plugin_quiz")
def create_plugin_quiz():
    token = os.getenv("QUIZ_PLUGIN_WRITE_TOKEN", "")
    header = request.headers.get("Authorization", "")
    if not token or not compare_digest(header, f"Bearer {token}"):
        return jsonify(error="Nástroj nemá přístup."), 401
    try:
        return save_quiz(request.get_json(silent=True))
    except ValueError as error:
        return jsonify(error=str(error)), 400


@app.get("/get_all_quizzes")
@authenticated()
def get_all_quizzes():
    with connection() as conn, conn.cursor() as cur:
        if g.account["role"] == "admin":
            cur.execute("""SELECT q.id, q.title, q.created_at, jsonb_array_length(q.questions) question_count,
                COUNT(r.id) attempts, ROUND(AVG(r.percentage)::numeric, 1) avg_success
                FROM quizzes q LEFT JOIN results r ON r.quiz_id = q.id
                GROUP BY q.id ORDER BY q.created_at DESC""")
        else:
            cur.execute("""SELECT q.id, q.title, q.created_at, jsonb_array_length(q.questions) question_count,
                COUNT(r.id) attempts, ROUND(AVG(r.percentage)::numeric, 1) avg_success
                FROM quizzes q LEFT JOIN results r ON r.quiz_id = q.id AND r.account_id = %s
                GROUP BY q.id ORDER BY q.created_at DESC""", (g.account["id"],))
        return jsonify(cur.fetchall())


@app.get("/get_quiz")
@authenticated()
def get_quiz():
    quiz_id = request.args.get("id", "")
    with connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT id, title, questions, created_at FROM quizzes WHERE id = %s", (quiz_id,))
        quiz = cur.fetchone()
    if not quiz:
        return jsonify(error="Kvíz nebyl nalezen."), 404
    return jsonify(serialize_quiz(quiz, admin=g.account["role"] == "admin"))


@app.delete("/delete_quiz")
@authenticated("admin")
def delete_quiz():
    with connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM quizzes WHERE id = %s RETURNING id", (request.args.get("id", ""),))
        deleted = cur.fetchone()
    if not deleted:
        return jsonify(error="Kvíz nebyl nalezen."), 404
    return jsonify(id=deleted["id"])


def grade_answers(questions, answers):
    if not isinstance(answers, dict) or set(answers) != {str(i) for i in range(len(questions))}:
        raise ValueError("Odpověz prosím na všechny otázky.")
    details = []
    for index, q in enumerate(questions):
        choice = answers[str(index)]
        options = q["options"]
        if type(choice) is not int or choice not in range(len(options)):
            raise ValueError("Jedna z odpovědí není platná.")
        correct = q["correct"]
        details.append({"index": index, "question": q["question"], "options": options,
                        "user_index": choice, "user_answer": options[choice],
                        "correct_index": correct, "correct": options[correct],
                        "is_correct": choice == correct,
                        "source_quote": q.get("source_quote", ""),
                        "source_image": q.get("source_image", "")})
    return details


@app.post("/submit_answers")
@authenticated()
def submit_answers():
    data = request.get_json(silent=True) or {}
    quiz_id = data.get("quiz_id")
    with connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT title, questions FROM quizzes WHERE id = %s", (quiz_id,))
        quiz = cur.fetchone()
        if not quiz:
            return jsonify(error="Kvíz nebyl nalezen."), 404
        try:
            details = grade_answers(quiz["questions"], data.get("answers"))
        except ValueError as error:
            return jsonify(error=str(error)), 400
        score, total = sum(item["is_correct"] for item in details), len(details)
        percentage = round(score / total * 100, 2)
        cur.execute("""INSERT INTO results(quiz_id, account_id, user_name, score, total, percentage, answers)
            VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id, created_at""",
            (quiz_id, g.account["id"], g.account["username"], score, total, percentage,
             json.dumps(details, ensure_ascii=False)))
        result = cur.fetchone()
    return jsonify(result_id=result["id"], quiz_id=quiz_id, quiz_title=quiz["title"],
                   user_name=g.account["username"], score=score, total=total,
                   percentage=percentage, created_at=result["created_at"], details=details)


def result_summary(row):
    return {key: row[key] for key in ("id", "quiz_id", "quiz_title", "user_name", "score", "total", "percentage", "created_at")}


@app.get("/get_results")
@authenticated()
def get_results():
    with connection() as conn, conn.cursor() as cur:
        if g.account["role"] == "admin":
            cur.execute("""SELECT r.id, r.quiz_id, q.title quiz_title, r.user_name,
                r.score, r.total, r.percentage, r.created_at FROM results r
                LEFT JOIN quizzes q ON q.id = r.quiz_id ORDER BY r.created_at DESC""")
        else:
            cur.execute("""SELECT r.id, r.quiz_id, q.title quiz_title, r.user_name,
                r.score, r.total, r.percentage, r.created_at FROM results r
                LEFT JOIN quizzes q ON q.id = r.quiz_id WHERE r.account_id = %s
                ORDER BY r.created_at DESC""", (g.account["id"],))
        return jsonify(cur.fetchall())


@app.get("/get_results_by_quiz")
@authenticated()
def get_results_by_quiz():
    quiz_id = request.args.get("quiz_id", "")
    with connection() as conn, conn.cursor() as cur:
        if g.account["role"] == "admin":
            cur.execute("""SELECT r.id, r.quiz_id, q.title quiz_title, r.user_name,
                r.score, r.total, r.percentage, r.created_at FROM results r
                JOIN quizzes q ON q.id = r.quiz_id WHERE r.quiz_id = %s
                ORDER BY r.created_at DESC""", (quiz_id,))
        else:
            cur.execute("""SELECT r.id, r.quiz_id, q.title quiz_title, r.user_name,
                r.score, r.total, r.percentage, r.created_at FROM results r
                JOIN quizzes q ON q.id = r.quiz_id WHERE r.quiz_id = %s AND r.account_id = %s
                ORDER BY r.created_at DESC""", (quiz_id, g.account["id"]))
        return jsonify(cur.fetchall())


@app.get("/get_result")
@authenticated()
def get_result():
    try:
        result_id = int(request.args.get("id", ""))
    except ValueError:
        return jsonify(error="ID výsledku není platné."), 400
    with connection() as conn, conn.cursor() as cur:
        cur.execute("""SELECT r.id, r.quiz_id, q.title quiz_title, r.user_name, r.account_id,
            r.score, r.total, r.percentage, r.answers, r.created_at FROM results r
            LEFT JOIN quizzes q ON q.id = r.quiz_id WHERE r.id = %s""", (result_id,))
        result = cur.fetchone()
    if not result or (g.account["role"] != "admin" and result["account_id"] != g.account["id"]):
        return jsonify(error="Výsledek nebyl nalezen."), 404
    result.pop("account_id")
    return jsonify(result)


if __name__ == "__main__":
    initialize_db()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))
