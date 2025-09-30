from flask import Flask, request, jsonify
from flask_cors import CORS
import os
import json
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime

app = Flask(__name__)
CORS(app)  # Povolení CORS pro frontend

# Připojení k databázi (Render → Environment → DATABASE_URL)
DATABASE_URL = os.environ.get("DATABASE_URL")

def get_db_connection():
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    return conn

# Inicializace tabulek
def init_db():
    conn = get_db_connection()
    cur = conn.cursor()

    # Users
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            username VARCHAR(100) DEFAULT 'Anonym',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    # Quizzes
    cur.execute("""
        CREATE TABLE IF NOT EXISTS quizzes (
            id VARCHAR(50) PRIMARY KEY,
            title VARCHAR(255) NOT NULL,
            questions JSONB NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    # Results
    cur.execute("""
        CREATE TABLE IF NOT EXISTS results (
            id SERIAL PRIMARY KEY,
            quiz_id VARCHAR(50) REFERENCES quizzes(id) ON DELETE CASCADE,
            user_id INT REFERENCES users(id) ON DELETE SET NULL,
            user_name VARCHAR(100),
            score INT NOT NULL,
            total INT NOT NULL,
            answers JSONB NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    conn.commit()
    cur.close()
    conn.close()

# Migrace databáze – přidání chybějících sloupců
def migrate_db():
    conn = get_db_connection()
    cur = conn.cursor()
    # Přidáme sloupec percentage, pokud ještě neexistuje
    cur.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 
                FROM information_schema.columns 
                WHERE table_name='results' AND column_name='percentage'
            ) THEN
                ALTER TABLE results ADD COLUMN percentage DECIMAL(5,2);
            END IF;
        END
        $$;
    """)
    conn.commit()
    cur.close()
    conn.close()

# Spustíme inicializaci a migraci
init_db()
migrate_db()

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
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    data["title"] = f"{timestamp} - {data['title']}"

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
    cur.execute("SELECT id, title, created_at FROM quizzes ORDER BY created_at DESC")
    quizzes = cur.fetchall()
    cur.close()
    conn.close()
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

    # Načti kvíz
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT questions FROM quizzes WHERE id = %s", (quiz_id,))
    quiz = cur.fetchone()
    if not quiz:
        cur.close()
        conn.close()
        return jsonify({"error": "Quiz not found"}), 404

    questions = quiz["questions"]

    # Pokud uživatel neexistuje, vlož ho
    cur.execute("SELECT id FROM users WHERE username = %s", (user_name,))
    user = cur.fetchone()
    if user:
        user_id = user["id"]
    else:
        cur.execute("INSERT INTO users (username) VALUES (%s) RETURNING id", (user_name,))
        user_id = cur.fetchone()["id"]

    # Vyhodnocení
    score = 0
    total = len(questions)
    details = []

    for i, q in enumerate(questions):
        correct = q.get("correct")
        opts = q.get("options", [])
        correct_text = None

        if isinstance(correct, int) and 0 <= correct < len(opts):
            correct_text = opts[correct]
        elif isinstance(correct, str):
            correct_text = correct

        user_ans = answers.get(str(i)) or answers.get(i)
        user_text = None
        if isinstance(user_ans, int) and 0 <= user_ans < len(opts):
            user_text = opts[user_ans]
        elif isinstance(user_ans, str):
            user_text = user_ans

        is_correct = (user_text == correct_text)
        if is_correct:
            score += 1

        details.append({
            "index": i,
            "question": q.get("question"),
            "options": opts,
            "correct": correct_text,
            "user_answer": user_text,
            "is_correct": is_correct
        })

    percentage = round((score / total) * 100, 2) if total > 0 else 0.0

    # Ulož do results
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
        "user_name": user_name,
        "score": score,
        "total": total,
        "percentage": percentage,
        "created_at": result["created_at"],
        "details": details
    }), 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)