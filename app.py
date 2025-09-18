from flask import Flask, request, jsonify
from flask_cors import CORS
import os
import json
import uuid
from datetime import datetime

app = Flask(__name__)
# CORS povolen pro všechny originy (frontend i konektory)
CORS(app, resources={r"/*": {"origins": "*"}}, supports_credentials=True)

DATA_DIR = "quizzes"
os.makedirs(DATA_DIR, exist_ok=True)

@app.route("/")
def index():
    return jsonify({"message": "Quiz API is running!"})

def normalize_quiz(payload: dict):
    """
    Zkontroluje strukturu kvízu a sjednotí 'correct':
    - pokud je 'correct' text, převede ho na index v 'options'
    - pokud je 'correct' číslo, ověří rozsah
    """
    if "title" not in payload or "questions" not in payload or not isinstance(payload["questions"], list):
        return None, "Invalid format: required keys: 'title', 'questions' (list)."

    for i, q in enumerate(payload["questions"]):
        if not all(k in q for k in ("question", "options", "correct")):
            return None, f"Question {i}: missing one of 'question', 'options', 'correct'."

        if not isinstance(q["options"], list) or len(q["options"]) != 4:
            return None, f"Question {i}: 'options' must be an array of 4 strings."

        correct = q["correct"]

        # 'correct' jako text -> najít index v options
        if isinstance(correct, str):
            try:
                idx = q["options"].index(correct)
            except ValueError:
                return None, f"Question {i}: 'correct' value not found in options."
            q["correct"] = idx

        # 'correct' jako číslo -> zkontrolovat rozsah
        elif isinstance(correct, int):
            if correct < 0 or correct >= len(q["options"]):
                return None, f"Question {i}: 'correct' index out of range."
        else:
            return None, f"Question {i}: 'correct' must be string or integer."

    return payload, None

@app.route("/create_quiz", methods=["POST"])
def create_quiz():
    # Přijmi JSON i když konektor pošle špatný Content-Type
    data = request.get_json(force=True, silent=True)
    if data is None:
        return jsonify({"error": "Invalid or missing JSON body"}), 400

    # Doplníme datum+čas před title (aktuální serverový čas)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    data["title"] = f"{timestamp} - {data.get('title','Untitled')}"

    # Sjednotíme / ověříme strukturu
    data, err = normalize_quiz(data)
    if err:
        return jsonify({"error": err}), 400

    quiz_id = uuid.uuid4().hex
    filepath = os.path.join(DATA_DIR, f"{quiz_id}.json")
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    return jsonify({"message": "Quiz saved successfully", "id": quiz_id}), 200

@app.route("/quizzes", methods=["GET"])
def list_quizzes():
    files = [f for f in os.listdir(DATA_DIR) if f.endswith(".json")]
    # Seřadíme od nejnovějších podle mtime
    files.sort(key=lambda x: os.path.getmtime(os.path.join(DATA_DIR, x)), reverse=True)

    result = []
    for filename in files:
        path = os.path.join(DATA_DIR, filename)
        try:
            with open(path, "r", encoding="utf-8") as f:
                q = json.load(f)
            result.append({"id": filename[:-5], "title": q.get("title", filename[:-5])})
        except Exception:
            # když by se náhodou nepovedlo přečíst soubor, přeskoč
            continue

    return jsonify(result), 200

@app.route("/quizzes/<quiz_id>", methods=["GET"])
def get_quiz(quiz_id: str):
    filepath = os.path.join(DATA_DIR, f"{quiz_id}.json")
    if not os.path.exists(filepath):
        return jsonify({"error": "Quiz not found"}), 404

    with open(filepath, "r", encoding="utf-8") as f:
        quiz = json.load(f)
    return jsonify(quiz), 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)