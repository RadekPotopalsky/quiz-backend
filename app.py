from flask import Flask, jsonify
import json
import os

app = Flask(__name__)
filepath = os.path.join(os.path.dirname(__file__), "quiz_text.json")

def get_quiz():
    with open(filepath, "r", encoding="utf-8") as f:
        quiz = json.load(f)
    return jsonify(quiz), 200

@app.route('/')
def home():
    return 'Quiz API is running!'

@app.route('/quizzes', methods=['GET'])
def get_quizzes():
    return get_quiz()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)