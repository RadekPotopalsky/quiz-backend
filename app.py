from flask import Flask, request, jsonify
from flask_cors import CORS
import os
import json
from datetime import datetime

app = Flask(__name__)
CORS(app)  # povolí přístup z React frontendu (localhost:3000)

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

@app.route("/get_all_quizzes", methods=["GET"])
def get_all_quizzes():
    quizzes = []
    try:
        for filename in os.listdir(DATA_DIR):
            if filename.endswith(".json"):
                filepath = os.path.join(DATA_DIR, filename)
                with open(filepath, "r", encoding="utf-8") as f:
                    quiz_data = json.load(f)
                    quizzes.append({
                        "id": filename[:-5],
                        "title": quiz_data.get("title", "Bez názvu"),
                        "grade": quiz_data.get("grade", ""),
                        "question_count": len(quiz_data.get("questions", []))
                    })
        return jsonify(quizzes), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
