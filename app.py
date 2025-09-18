from flask import Flask, request, jsonify
from flask_cors import CORS
import os
import json
import uuid

app = Flask(__name__)
CORS(app)  # Povolení CORS

DATA_DIR = "quizzes"
os.makedirs(DATA_DIR, exist_ok=True)

@app.route("/")
def index():
    return jsonify({"message": "Quiz API is running!"})

@app.route("/create_quiz", methods=["POST"])
def create_quiz():
    data = request.json
    if not data or "title" not in data or "questions" not in data:
        return jsonify({"error": "Invalid format"}), 400

    # použijeme náhodné ID místo timestampu
    quiz_id = uuid.uuid4().hex
    filepath = os.path.join(DATA_DIR, f"{quiz_id}.json")

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    return jsonify({"message": "Quiz saved successfully", "id": quiz_id}), 200

@app.route("/quizzes", methods=["GET"])
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

@app.route("/quizzes/<quiz_id>", methods=["GET"])
def get_quiz(quiz_id):
    filepath = os.path.join(DATA_DIR, f"{quiz_id}.json")
    if not os.path.exists(filepath):
        return jsonify({"error": "Quiz not found"}), 404

    with open(filepath, "r", encoding="utf-8") as f:
        quiz = json.load(f)

    return jsonify(quiz), 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)