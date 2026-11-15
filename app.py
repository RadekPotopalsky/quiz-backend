from flask import Flask, request, jsonify
from flask_cors import CORS
from contextlib import closing
from hmac import compare_digest
import os
import json
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime
from uuid import uuid4
from zoneinfo import ZoneInfo  # používáme vestavěné

app = Flask(__name__)
CORS(app)

DATABASE_URL = os.environ.get("DATABASE_URL")

def get_db_connection():
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    return conn

# ===== Inicializace tabulek =====
def init_db():
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            username VARCHAR(100) DEFAULT 'Anonym',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS quizzes (
            id VARCHAR(50) PRIMARY KEY,
            title VARCHAR(255) NOT NULL,
            questions JSONB NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS results (
            id SERIAL PRIMARY KEY,
            quiz_id VARCHAR(50) REFERENCES quizzes(id) ON DELETE CASCADE,
            user_id INT REFERENCES users(id) ON DELETE SET NULL,
            user_name VARCHAR(100),
            score INT NOT NULL,
            total INT NOT NULL,
            percentage DECIMAL(5,2) NOT NULL,
            answers JSONB NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    conn.commit()
    cur.close()
    conn.close()

init_db()

@app.route("/")
def index():
    return jsonify({"message": "Quiz API is running with PostgreSQL!"})

# ===== Kvízy =====
@app.route("/create_quiz", methods=["POST"])
def create_quiz():
    data = request.json
    if not data or "title" not in data or "questions" not in data:
        return jsonify({"error": "Invalid format"}), 400

    cleaned_questions = []
    for q in data["questions"]:
        question = q.get("question")
        options = q.get("options", [])
        correct = q.get("correct")
        answer = q.get("answer")  # fallback pokud přijde špatný klíč

        # --- validace options ---
        if not isinstance(options, list) or len(options) != 4:
            return jsonify({"error": f"Question '{question}' nemá přesně 4 možnosti."}), 400

        # --- pokud přišel klíč answer místo correct ---
        if correct is None and answer is not None:
            if isinstance(answer, (int, float)) and 0 <= int(answer) < 4:
                correct = int(answer)
            elif isinstance(answer, str) and answer in options:
                correct = options.index(answer)

        # --- fallback validace správnosti ---
        if not isinstance(correct, int) or not (0 <= correct < 4):
            return jsonify({"error": f"Otázka '{question}' nemá validní správnou odpověď."}), 400

        # --- čistá verze otázky ---
        cleaned_questions.append({
            "question": question,
            "options": options,
            "correct": correct
        })

    quiz_id = datetime.now().strftime("%Y%m%d%H%M%S")
    return save_quiz(quiz_id, data["title"], cleaned_questions)


@app.route("/create_plugin_quiz", methods=["POST"])
def create_plugin_quiz():
    token = os.environ.get("QUIZ_PLUGIN_WRITE_TOKEN")
    if not token:
        return jsonify({"error": "Plugin writes are not configured"}), 503

    authorization = request.headers.get("Authorization", "")
    if not compare_digest(authorization.encode("utf-8"), f"Bearer {token}".encode("utf-8")):
        return jsonify({"error": "Unauthorized"}), 401

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Expected a JSON object"}), 400

    title = data.get("title")
    questions = data.get("questions")
    if not isinstance(title, str) or not title.strip() or len(title) > 255:
        return jsonify({"error": "Expected a nonempty title of at most 255 characters"}), 400
    if not isinstance(questions, list) or not questions:
        return jsonify({"error": "Expected a nonempty questions array"}), 400

    cleaned_questions = []
    for index, item in enumerate(questions, start=1):
        if not isinstance(item, dict):
            return jsonify({"error": f"Invalid question at position {index}"}), 400
        question = item.get("question")
        options = item.get("options")
        correct = item.get("correct")
        if (
            not isinstance(question, str) or not question.strip()
            or not isinstance(options, list) or len(options) != 4
            or any(not isinstance(option, str) or not option.strip() for option in options)
            or type(correct) is not int or not 0 <= correct <= 3
        ):
            return jsonify({"error": f"Invalid question at position {index}"}), 400
        cleaned_questions.append({"question": question, "options": options, "correct": correct})

    return save_quiz(uuid4().hex, title, cleaned_questions)


def save_quiz(quiz_id, title, questions):
    with closing(get_db_connection()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO quizzes (id, title, questions) VALUES (%s, %s, %s)",
                (quiz_id, title, json.dumps(questions))
            )
        conn.commit()

    return jsonify({"message": "Quiz saved successfully", "id": quiz_id}), 200

@app.route("/get_all_quizzes")
def get_all_quizzes():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT q.id, q.title, q.created_at,
               COALESCE(ROUND(AVG(r.percentage)::numeric, 1), 0) AS avg_success,
               COUNT(r.id) AS attempts
        FROM quizzes q
        LEFT JOIN results r ON q.id = r.quiz_id
        GROUP BY q.id, q.title, q.created_at
        ORDER BY q.created_at DESC
    """)
    quizzes = cur.fetchall()
    cur.close()
    conn.close()

    # Převod času na CET a formátování
    for q in quizzes:
        if q["created_at"]:
            utc_time = q["created_at"].replace(tzinfo=ZoneInfo("UTC"))
            local_time = utc_time.astimezone(ZoneInfo("Europe/Prague"))
            q["created_at"] = local_time.strftime("%Y-%m-%d %H:%M:%S")

    return jsonify(quizzes), 200

@app.route("/get_quiz")
def get_quiz():
    quiz_id = request.args.get("id")
    if not quiz_id:
        return jsonify({"error": "Missing id parameter"}), 400

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM quizzes WHERE id = %s", (quiz_id,))
    quiz = cur.fetchone()
    cur.close()
    conn.close()

    if not quiz:
        return jsonify({"error": "Quiz not found"}), 404

    return jsonify(quiz), 200

# ===== Delete kvíz =====
@app.route("/delete_quiz", methods=["DELETE"])
def delete_quiz():
    quiz_id = request.args.get("id")
    if not quiz_id:
        return jsonify({"error": "Missing id parameter"}), 400

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM quizzes WHERE id = %s RETURNING id", (quiz_id,))
    deleted = cur.fetchone()
    conn.commit()
    cur.close()
    conn.close()

    if not deleted:
        return jsonify({"error": "Quiz not found"}), 404

    return jsonify({"message": "Quiz deleted successfully", "id": quiz_id}), 200

# ===== Vyhodnocení =====
@app.route("/submit_answers", methods=["POST"])
def submit_answers():
    data = request.json
    quiz_id = data.get("quiz_id")
    answers = data.get("answers")
    user_name = data.get("user_name", "Anonym")

    if not quiz_id or not answers:
        return jsonify({"error": "Missing quiz_id or answers"}), 400

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT questions, title FROM quizzes WHERE id = %s", (quiz_id,))
    quiz = cur.fetchone()
    if not quiz:
        cur.close()
        conn.close()
        return jsonify({"error": "Quiz not found"}), 404

    questions = quiz["questions"]
    quiz_title = quiz["title"]

    cur.execute("SELECT id FROM users WHERE username = %s", (user_name,))
    user = cur.fetchone()
    if user:
        user_id = user["id"]
    else:
        cur.execute("INSERT INTO users (username) VALUES (%s) RETURNING id", (user_name,))
        user_id = cur.fetchone()["id"]

    score = 0
    total = len(questions)
    details = []

    for i, q in enumerate(questions):
        correct = q.get("correct")
        opts = q.get("options", [])

        correct_index = None
        correct_text = None
        if isinstance(correct, int) and 0 <= correct < len(opts):
            correct_index = correct
            correct_text = opts[correct]
        elif isinstance(correct, str) and correct in opts:
            correct_index = opts.index(correct)
            correct_text = correct

        # --- FIX: explicitní kontrola kvůli hodnotě 0 ---
        user_ans = answers.get(str(i))
        if user_ans is None:
            user_ans = answers.get(i)

        user_index = None
        user_text = None
        if isinstance(user_ans, int) and 0 <= user_ans < len(opts):
            user_index = user_ans
            user_text = opts[user_ans]
        elif isinstance(user_ans, str) and user_ans in opts:
            user_index = opts.index(user_ans)
            user_text = user_ans

        is_correct = (user_index == correct_index)
        if is_correct:
            score += 1

        details.append({
            "index": i,
            "question": q.get("question"),
            "options": opts,
            "correct": correct_text,
            "correct_index": correct_index,
            "user_answer": user_text,
            "user_index": user_index,
            "is_correct": is_correct
        })

    percentage = round((score / total) * 100, 2) if total > 0 else 0.0

    cur.execute("""
        INSERT INTO results (quiz_id, user_id, user_name, score, total, percentage, answers)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        RETURNING id, created_at
    """, (quiz_id, user_id, user_name, score, total, percentage, json.dumps(details)))
    result = cur.fetchone()
    conn.commit()
    cur.close()
    conn.close()

    return jsonify({
        "result_id": result["id"],
        "quiz_id": quiz_id,
        "quiz_title": quiz_title,
        "user_name": user_name,
        "score": score,
        "total": total,
        "percentage": percentage,
        "created_at": result["created_at"],
        "details": details
    }), 200

@app.route("/get_results")
def get_results():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT r.*, q.title AS quiz_title
        FROM results r
        LEFT JOIN quizzes q ON r.quiz_id = q.id
        ORDER BY r.created_at DESC
    """)
    results = cur.fetchall()
    cur.close()
    conn.close()
    return jsonify(results), 200

@app.route("/get_results_by_quiz")
def get_results_by_quiz():
    quiz_id = request.args.get("quiz_id")
    if not quiz_id:
        return jsonify({"error": "Missing quiz_id"}), 400

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT r.*, q.title AS quiz_title
        FROM results r
        LEFT JOIN quizzes q ON r.quiz_id = q.id
        WHERE r.quiz_id = %s
        ORDER BY r.created_at DESC
    """, (quiz_id,))
    results = cur.fetchall()
    cur.close()
    conn.close()
    return jsonify(results), 200

@app.route("/get_result")
def get_result():
    result_id = request.args.get("id")
    if not result_id:
        return jsonify({"error": "Missing id"}), 400

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT r.*, q.title AS quiz_title
        FROM results r
        LEFT JOIN quizzes q ON r.quiz_id = q.id
        WHERE r.id = %s
    """, (result_id,))
    result = cur.fetchone()
    cur.close()
    conn.close()

    if not result:
        return jsonify({"error": "Result not found"}), 404

    return jsonify(result), 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)