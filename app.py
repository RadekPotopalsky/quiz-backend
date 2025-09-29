from flask import Flask, request, jsonify
from flask_cors import CORS
import os, json
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime

app = Flask(__name__)
CORS(app)

DATABASE_URL = os.environ.get("DATABASE_URL")

def get_db_connection():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL env var is missing.")
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

def init_db():
    conn = get_db_connection()
    cur = conn.cursor()

    # --- Users
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            username VARCHAR(100) UNIQUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    # --- Quizzes
    cur.execute("""
        CREATE TABLE IF NOT EXISTS quizzes (
            id VARCHAR(50) PRIMARY KEY,
            title VARCHAR(255) NOT NULL,
            questions JSONB NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    # --- Results (základ)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS results (
            id SERIAL PRIMARY KEY,
            quiz_id VARCHAR(50) REFERENCES quizzes(id) ON DELETE CASCADE,
            score INT NOT NULL,
            total INT NOT NULL,
            percentage NUMERIC(5,2) NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    # --- MIGRACE: dovytvoř chybějící sloupce
    cur.execute("""ALTER TABLE results ADD COLUMN IF NOT EXISTS user_id INT;""")
    cur.execute("""ALTER TABLE results ADD COLUMN IF NOT EXISTS answers JSONB;""")

    # --- MIGRACE: doplň FK na users (jen pokud chybí)
    cur.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.table_constraints
                WHERE constraint_name = 'results_user_id_fkey'
                  AND table_name = 'results'
            ) THEN
                ALTER TABLE results
                ADD CONSTRAINT results_user_id_fkey
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL;
            END IF;
        END$$;
    """)

    conn.commit()
    cur.close()
    conn.close()

init_db()

@app.route("/")
def index():
    return jsonify({"message": "Quiz API is running with PostgreSQL!"})

# ------------------- KVÍZY -------------------
@app.route("/create_quiz", methods=["POST"])
def create_quiz():
    data = request.get_json(silent=True)
    if not data or "title" not in data or "questions" not in data:
        return jsonify({"error": "Invalid format"}), 400

    quiz_id = datetime.now().strftime("%Y%m%d%H%M%S")
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    title = f"{timestamp} - {data['title']}"
    questions = data["questions"]

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO quizzes (id, title, questions) VALUES (%s, %s, %s)",
        (quiz_id, title, json.dumps(questions))
    )
    conn.commit()
    cur.close()
    conn.close()

    return jsonify({"message": "Quiz saved successfully", "id": quiz_id}), 200

@app.route("/get_all_quizzes")
def get_all_quizzes():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT id, title, created_at FROM quizzes ORDER BY created_at DESC;")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return jsonify(rows), 200

@app.route("/get_quiz")
def get_quiz():
    quiz_id = request.args.get("id")
    if not quiz_id:
        return jsonify({"error": "Missing id parameter"}), 400

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT id, title, questions, created_at FROM quizzes WHERE id=%s;", (quiz_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()

    if not row:
        return jsonify({"error": "Quiz not found"}), 404
    return jsonify(row), 200

# ------------------- VYHODNOCENÍ -------------------
@app.route("/submit_answers", methods=["POST"])
def submit_answers():
    """
    JSON:
    {
      "quiz_id": "20250929XXXXXX",
      "answers": {"0": 2, "1": "Slované", ...},
      "user_name": "Radek"   # volitelné
    }
    """
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Invalid JSON"}), 400

    quiz_id = data.get("quiz_id")
    answers = data.get("answers")
    user_name = (data.get("user_name") or "Anonym").strip() or "Anonym"

    if not quiz_id or not isinstance(answers, dict):
        return jsonify({"error": "Missing quiz_id or answers (dict)"}), 400

    conn = get_db_connection()
    cur = conn.cursor()

    # načti kvíz
    cur.execute("SELECT questions FROM quizzes WHERE id=%s;", (quiz_id,))
    row = cur.fetchone()
    if not row:
        cur.close(); conn.close()
        return jsonify({"error": "Quiz not found"}), 404

    questions = row["questions"]
    total = len(questions)
    score = 0
    details = []

    # vyhodnocení
    for i, q in enumerate(questions):
        opts = q.get("options", [])
        correct = q.get("correct")
        # Převod správné odpovědi na text
        if isinstance(correct, int) and 0 <= correct < len(opts):
            correct_text = opts[correct]
        elif isinstance(correct, str):
            correct_text = correct
        else:
            correct_text = None

        # Uživatelská odpověď
        raw_user = answers.get(str(i), answers.get(i))
        if isinstance(raw_user, int) and 0 <= raw_user < len(opts):
            user_text = opts[raw_user]
        elif isinstance(raw_user, str):
            user_text = raw_user
        else:
            user_text = None

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

    percentage = round((score / total) * 100, 2) if total else 0.0

    # uživatel (není povinný – uložíme jen když je jméno jiné než prázdné)
    user_id = None
    if user_name:
        cur.execute("SELECT id FROM users WHERE username=%s;", (user_name,))
        u = cur.fetchone()
        if u:
            user_id = u["id"]
        else:
            cur.execute("INSERT INTO users (username) VALUES (%s) RETURNING id;", (user_name,))
            user_id = cur.fetchone()["id"]

    # ulož výsledek
    cur.execute("""
        INSERT INTO results (quiz_id, user_id, score, total, percentage, answers)
        VALUES (%s, %s, %s, %s, %s, %s)
        RETURNING id, created_at;
    """, (quiz_id, user_id, score, total, percentage, json.dumps(details)))
    res = cur.fetchone()
    conn.commit()
    cur.close(); conn.close()

    return jsonify({
        "result_id": res["id"],
        "created_at": res["created_at"],
        "quiz_id": quiz_id,
        "user_name": user_name,
        "score": score,
        "total": total,
        "percentage": percentage,
        "details": details
    }), 200

# ------------------- DIAGNOSTIKA -------------------
@app.route("/db_status")
def db_status():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
      SELECT table_name
      FROM information_schema.tables
      WHERE table_schema='public'
      ORDER BY table_name;
    """)
    tables = [r["table_name"] for r in cur.fetchall()]
    cur.close(); conn.close()
    return jsonify({"ok": True, "tables": tables})

@app.route("/debug_db")
def debug_db():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
      SELECT table_name, column_name, data_type
      FROM information_schema.columns
      WHERE table_schema='public'
      ORDER BY table_name, ordinal_position;
    """)
    cols = cur.fetchall()
    cur.close(); conn.close()
    return jsonify(cols), 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)