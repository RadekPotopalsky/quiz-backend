from flask import Flask, request, jsonify
from flask_cors import CORS
import os
import json
from datetime import datetime

app = Flask(__name__)
CORS(app)  # povolení CORS pro frontend i asistenta

DATA_DIR = "quizzes"
os.makedirs(DATA_DIR, exist_ok=True)


@app.route("/")
def index():
    return "Quiz API is running!"


@app.route("/create_quiz", methods=["POST"])
def create_quiz():
    data = request.json
    if not data or "title" not in data or "questions" not in data:
        return jsonify({"error": "Invalid format"}), 400

    # Přidáme timestamp do názvu kvízu
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    data["title"] = f"{timestamp} - {data['title']}"

    # Název souboru = timestamp (unikátní pro každý kvíz)
    filename = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    filepath = os.path.join(DATA_DIR, filename)

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    return jsonify({"message": "Quiz saved successfully", "id": filename[:-5]}), 200


@app.route("/get_quiz")
def get_quiz():
    quiz_id = request.args.get("id")
    if not quiz_id:
        return jsonify({"error": "Missing id parameter"}), 400

    filepath = os.path.join(DATA_DIR, f"{quiz_id}.json")
    if not os.path.exists(filepath):
        return jsonify({"error": "Quiz not found"}), 404

    with open(filepath, "r", encoding="utf-8") as f:
        quiz = json.load(f)

    return jsonify(quiz), 200


@app.route("/get_all_quizzes")
def get_all_quizzes():
    quizzes = []
    for filename in os.listdir(DATA_DIR):
        if filename.endswith(".json"):
            filepath = os.path.join(DATA_DIR, filename)
            with open(filepath, "r", encoding="utf-8") as f:
                quiz = json.load(f)
                quiz_id = filename[:-5]
                quizzes.append({
                    "id": quiz_id,
                    "title": quiz.get("title", f"Untitled ({quiz_id})")
                })
    return jsonify(quizzes), 200


@app.route("/submit_answers", methods=["POST"])
def submit_answers():
    data = request.json
    quiz_id = data.get("quiz_id")
    answers = data.get("answers")

    if not quiz_id or not answers:
        return jsonify({"error": "Missing quiz_id or answers"}), 400

    # Najdi kvíz podle ID
    filepath = os.path.join(DATA_DIR, f"{quiz_id}.json")
    if not os.path.exists(filepath):
        return jsonify({"error": "Quiz not found"}), 404

    with open(filepath, "r", encoding="utf-8") as f:
        quiz = json.load(f)

    # Porovnej odpovědi
    total = len(quiz["questions"])
    score = 0
    results = []

    for i, question in enumerate(quiz["questions"]):
        correct = question.get("answer") or question.get("correct")
        user_answer = answers.get(str(i))  # odpověď uživatele
        is_correct = (user_answer == correct)
        if is_correct:
            score += 1
        results.append({
            "question": question["question"],
            "correct_answer": correct,
            "user_answer": user_answer,
            "is_correct": is_correct
        })

    # Ulož výsledek do JSON (zatím – DB přijde potom)
    result_data = {
        "quiz_id": quiz_id,
        "score": score,
        "total": total,
        "answers": results,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }

    results_file = os.path.join(DATA_DIR, f"result_{quiz_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
    with open(results_file, "w", encoding="utf-8") as f:
        json.dump(result_data, f, ensure_ascii=False, indent=2)

    return jsonify(result_data), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)