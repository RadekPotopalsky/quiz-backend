from flask import Flask, request, jsonify
from flask_cors import CORS
import os
import json
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime

app = Flask(__name__)
CORS(app)

# ====== Připojení k PostgreSQL (Render -> Environment -> DATABASE_URL) ======
DATABASE_URL = os.getenv("DATABASE_URL")

def get_conn():
    # Render External URL vyžaduje SSL; Internal většinou nevadí mít require
    return psycopg2.connect(DATABASE_URL, sslmode="require")

# ====== Inicializace DB (vytvoří tabulky, pokud chybí) ======
def init_db():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS quizzes (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    questions JSONB NOT NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT NOW()
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS results (
                    id SERIAL PRIMARY KEY,
                    quiz_id TEXT NOT NULL REFERENCES quizzes(id) ON DELETE CASCADE,
                    user_name TEXT,
                    score INTEGER NOT NULL,
                    total INTEGER NOT NULL,
                    answers JSONB NOT NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT NOW()
                );
            """)
        conn.commit()

# Pomocné funkce pro robustní vyhodnocení
def normalize_correct(q):
    """Vrátí text správné odpovědi. Podporuje:
       - q['answer'] jako text
       - q['correct'] jako index do q['options']"""
    if isinstance(q.get("answer"), str):
        return q["answer"]
    if "correct" in q and isinstance(q["correct"], int):
        idx = q["correct"]
        opts = q.get("options") or []
        if 0 <= idx < len(opts):
            return opts[idx]
    return None

def user_answer_text(q, user_val):
    """Převede uživatelskou odpověď na text (index -> options[index], text -> text)."""
    opts = q.get("options") or []
    if isinstance(user_val, int):
        if 0 <= user_val < len(opts):
            return opts[user_val]
        return None
    if isinstance(user_val, str):
        return user_val
    return None

@app.route("/")
def index():
    return "Quiz API is running with PostgreSQL!"

# ==================== Kvízy ====================

@app.route("/create_quiz", methods=["POST"])
def create_quiz():
    data = request.json
    if not data or "title" not in data or "questions" not in data or not isinstance(data["questions"], list):
        return jsonify({"error": "Invalid format"}), 400

    quiz_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    title = f"{ts} - {data['title']}"
    questions = data["questions"]

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO quizzes (id, title, questions) VALUES (%s, %s, %s)",
                (quiz_id, title, json.dumps(questions, ensure_ascii=False))
            )
        conn.commit()

    return jsonify({"message": "Quiz saved successfully", "id": quiz_id}), 200

@app.route("/get_all_quizzes")
def get_all_quizzes():
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id, title FROM quizzes ORDER BY created_at DESC;")
            rows = cur.fetchall()
    # Zachováme jednoduchý tvar, který očekává frontend: [{id, title}]
    return jsonify(rows), 200

@app.route("/get_quiz")
def get_quiz():
    quiz_id = request.args.get("id")
    if not quiz_id:
        return jsonify({"error": "Missing id parameter"}), 400

    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id, title, questions FROM quizzes WHERE id = %s;", (quiz_id,))
            row = cur.fetchone()

    if not row:
        return jsonify({"error": "Quiz not found"}), 404

    # psycopg2 vrací JSONB už jako Python objekt; ale pro jistotu:
    questions = row["questions"]
    if isinstance(questions, str):
        questions = json.loads(questions)

    return jsonify({"id": row["id"], "title": row["title"], "questions": questions}), 200

# ==================== Vyhodnocení & výsledky ====================

@app.route("/submit_answers", methods=["POST"])
def submit_answers():
    """
    Přijímá:
    {
      "quiz_id": "20250919_063617",
      "answers": { "0": 1, "1": "Kupec Sámo", ... }  # indexy nebo texty
      "user_name": "Radek"  # volitelné
    }
    """
    payload = request.json or {}
    quiz_id = payload.get("quiz_id")
    answers_in = payload.get("answers")
    user_name = payload.get("user_name") or "Anonym"

    if not quiz_id or answers_in is None:
        return jsonify({"error": "Missing quiz_id or answers"}), 400

    # Načti kvíz
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT questions FROM quizzes WHERE id = %s;", (quiz_id,))
            row = cur.fetchone()
    if not row:
        return jsonify({"error": "Quiz not found"}), 404

    questions = row["questions"]
    if isinstance(questions, str):
        questions = json.loads(questions)

    # Helper pro čtení odpovědí (dict se string klíči nebo list)
    def get_user_val(i):
        if isinstance(answers_in, dict):
            return answers_in.get(str(i), answers_in.get(i))
        if isinstance(answers_in, list) and 0 <= i < len(answers_in):
            return answers_in[i]
        return None

    # Vyhodnocení
    total = len(questions)
    score = 0
    details = []
    for i, q in enumerate(questions):
        correct_text = normalize_correct(q)
        user_val = get_user_val(i)
        user_text = user_answer_text(q, user_val)
        is_correct = (user_text is not None and correct_text is not None and user_text == correct_text)
        if is_correct:
            score += 1
        details.append({
            "index": i,
            "question": q.get("question"),
            "options": q.get("options"),
            "correct_text": correct_text,
            "user_text": user_text,
            "is_correct": is_correct
        })

    # Ulož výsledek
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                INSERT INTO results (quiz_id, user_name, score, total, answers)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id, created_at;
                """,
                (quiz_id, user_name, score, total, json.dumps(details, ensure_ascii=False))
            )
            r = cur.fetchone()
        conn.commit()

    return jsonify({
        "result_id": r["id"],
        "quiz_id": quiz_id,
        "user_name": user_name,
        "score": score,
        "total": total,
        "percentage": round(score / total * 100, 2) if total else 0.0,
        "created_at": r["created_at"].strftime("%Y-%m-%d %H:%M:%S"),
        "details": details
    }), 200

@app.route("/results")
def list_results():
    quiz_id = request.args.get("quiz_id")
    if not quiz_id:
        return jsonify({"error": "Missing quiz_id"}), 400

    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT id, user_name, score, total, created_at FROM results WHERE quiz_id = %s ORDER BY created_at DESC;",
                (quiz_id,)
            )
            rows = cur.fetchall()
    out = [
        {
            "id": r["id"],
            "user_name": r["user_name"],
            "score": r["score"],
            "total": r["total"],
            "created_at": r["created_at"].strftime("%Y-%m-%d %H:%M:%S")
        }
        for r in rows
    ]
    return jsonify(out), 200

@app.route("/results/<rid>")
def result_detail(rid):
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT id, quiz_id, user_name, score, total, answers, created_at FROM results WHERE id = %s;",
                (rid,)
            )
            r = cur.fetchone()
    if not r:
        return jsonify({"error": "Result not found"}), 404

    answers = r["answers"]
    if isinstance(answers, str):
        answers = json.loads(answers)

    return jsonify({
        "id": r["id"],
        "quiz_id": r["quiz_id"],
        "user_name": r["user_name"],
        "score": r["score"],
        "total": r["total"],
        "answers": answers,
        "created_at": r["created_at"].strftime("%Y-%m-%d %H:%M:%S")
    }), 200

# ===== start =====
if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)