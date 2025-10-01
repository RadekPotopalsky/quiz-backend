from flask import Flask, request, jsonify
from flask_cors import CORS
import os
import json
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime
from zoneinfo import ZoneInfo  # ← používáme místo pytz

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

    quiz_id = datetime.now().strftime("%Y%m%d%H%M%S")

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO quizzes (id, title, questions) VALUES (%s, %s, %s)",
        (quiz_id, data["title"], json.dumps(data["questions"]))
    )
    conn.commit()
    cur.close()
    conn.close()

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

        user_ans = answers.get(str(i)) or answers.get(i)
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
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)