from flask import Flask, request, jsonify
from flask_cors import CORS
import os
import json
from datetime import datetime
import psycopg2
from psycopg2.extras import Json

app = Flask(__name__)
CORS(app)  # CORS pro frontend i asistenta

# ----- DB pomocné funkce -----
DATABASE_URL = os.environ.get("DATABASE_URL")  # nastavili jsme v Renderu

def get_conn():
    # Render PG vyžaduje sslmode=require (je už v URL)
    return psycopg2.connect(DATABASE_URL)

def init_db():
    """Vytvoří tabulku, pokud neexistuje."""
    ddl = """
    CREATE TABLE IF NOT EXISTS quizzes (
        id SERIAL PRIMARY KEY,
        title TEXT NOT NULL,
        questions JSONB NOT NULL,
        created_at TIMESTAMP NOT NULL
    );
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(ddl)
        conn.commit()

init_db()

# ----- API -----
@app.route("/")
def index():
    return "Quiz API is running!"

@app.route("/create_quiz", methods=["POST"])
def create_quiz():
    data = request.json
    if not data or "title" not in data or "questions" not in data:
        return jsonify({"error": "Invalid format"}), 400

    # Doplníme timestamp do názvu
    timestamp_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    data["title"] = f"{timestamp_str} - {data['title']}"

    # Uložíme do DB
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO quizzes (title, questions, created_at) VALUES (%s, %s, %s) RETURNING id",
                (data["title"], Json(data["questions"], dumps=lambda o: json.dumps(o, ensure_ascii=False)), datetime.now())
            )
            quiz_id = cur.fetchone()[0]
        conn.commit()

    return jsonify({"message": "Quiz saved successfully", "id": str(quiz_id)}), 200

@app.route("/get_quiz")
def get_quiz():
    quiz_id = request.args.get("id")
    if not quiz_id:
        return jsonify({"error": "Missing id parameter"}), 400

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, title, questions FROM quizzes WHERE id = %s", (quiz_id,))
            row = cur.fetchone()

    if not row:
        return jsonify({"error": "Quiz not found"}), 404

    # psycopg2 pro JSONB může vracet dict nebo string; ošetříme obě varianty
    questions = row[2]
    if isinstance(questions, str):
        questions = json.loads(questions)

    return jsonify({"id": str(row[0]), "title": row[1], "questions": questions}), 200

@app.route("/get_all_quizzes")
def get_all_quizzes():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, title FROM quizzes ORDER BY created_at DESC")
            rows = cur.fetchall()

    quizzes = [{"id": str(r[0]), "title": r[1]} for r in rows]
    return jsonify(quizzes), 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)