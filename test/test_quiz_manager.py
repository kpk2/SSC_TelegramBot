import json
from unittest.mock import MagicMock

from app.quiz_manager import QuizManager


def test_save_answer_details_appends_history(tmp_path):
    questions_path = tmp_path / "questions.json"
    questions_path.write_text(
        json.dumps(
            [
                {
                    "id": 101,
                    "language": "en",
                    "text": "What is 2 + 2?",
                    "options": ["3", "4", "5", "6"],
                    "correct_index": 1,
                    "verified": True,
                    "explanation": "2 + 2 is 4",
                    "topic": "math",
                }
            ]
        ),
        encoding="utf-8",
    )

    manager = QuizManager(str(questions_path), MagicMock())
    manager.save_answer_details(
        question_id=101,
        question_text="What is 2 + 2?",
        user_id=12345,
        selected_option_label="B",
        selected_option_index=1,
        selected_option_text="4",
        correct_option_label="B",
        correct_option_index=1,
        correct_option_text="4",
        is_correct=True,
        time_taken_seconds=2.34567,
        question_number=1,
        total_questions=10,
    )

    persisted = json.loads(questions_path.read_text(encoding="utf-8"))
    history = persisted[0]["answer_history"]
    assert len(history) == 1
    assert history[0]["user_id"] == 12345
    assert history[0]["is_correct"] is True
    assert history[0]["time_taken_seconds"] == 2.346
    assert history[0]["answer_record_file"].startswith("answer_records/questions/user_12345/")


def test_save_answer_details_matches_by_text_when_id_is_generated(tmp_path):
    questions_path = tmp_path / "questions.json"
    questions_path.write_text(
        json.dumps(
            [
                {
                    "id": -1,
                    "language": "en",
                    "text": "Generated id question",
                    "options": ["A", "B", "C", "D"],
                    "correct_index": 1,
                    "verified": True,
                    "explanation": "Explanation",
                    "Test": "Uncategorized",
                }
            ]
        ),
        encoding="utf-8",
    )

    manager = QuizManager(str(questions_path), MagicMock())
    # Runtime id will be generated (likely 0), while source JSON id stays -1.
    generated_id = next(iter(manager.questions_db.keys()))
    assert generated_id != -1

    manager.save_answer_details(
        question_id=generated_id,
        question_text="Generated id question",
        user_id=999,
        selected_option_label="A",
        selected_option_index=0,
        selected_option_text="A",
        correct_option_label="B",
        correct_option_index=1,
        correct_option_text="B",
        is_correct=False,
        time_taken_seconds=1.2,
        question_number=1,
        total_questions=1,
    )

    persisted = json.loads(questions_path.read_text(encoding="utf-8"))
    assert "answer_history" in persisted[0]
    assert len(persisted[0]["answer_history"]) == 1
    assert persisted[0]["answer_history"][0]["user_id"] == 999


def test_save_answer_details_creates_separate_json_record(tmp_path):
    questions_path = tmp_path / "questions.json"
    questions_path.write_text(
        json.dumps(
            [
                {
                    "id": 7,
                    "language": "en",
                    "text": "Which option is correct?",
                    "options": ["Alpha", "Beta", "Gamma", "Delta"],
                    "correct_index": 2,
                    "verified": True,
                    "explanation": "Gamma is the expected answer",
                    "topic": "logic",
                }
            ]
        ),
        encoding="utf-8",
    )

    manager = QuizManager(str(questions_path), MagicMock())
    manager.save_answer_details(
        question_id=7,
        question_text="Which option is correct?",
        user_id=55,
        selected_option_label="B",
        selected_option_index=1,
        selected_option_text="Beta",
        correct_option_label="C",
        correct_option_index=2,
        correct_option_text="Gamma",
        is_correct=False,
        time_taken_seconds=4.2,
        question_number=3,
        total_questions=10,
    )

    answer_record_files = list(
        (tmp_path / "answer_records" / "questions" / "user_55").glob("*.json")
    )

    assert len(answer_record_files) == 1

    record = json.loads(answer_record_files[0].read_text(encoding="utf-8"))
    assert record["user"]["id"] == 55
    assert record["quiz_context"]["question_number"] == 3
    assert record["quiz_context"]["total_questions"] == 10
    assert record["answer"]["selected_option_label"] == "B"
    assert record["answer"]["selected_option_text"] == "Beta"
    assert record["answer"]["correct_option_text"] == "Gamma"
    assert record["answer"]["is_correct"] is False
    assert record["answer"]["time_taken_seconds"] == 4.2
    assert record["question"]["id"] == 7
    assert record["question"]["text"] == "Which option is correct?"
    assert record["question"]["topic"] == "logic"
    assert record["question"]["options"][2]["label"] == "C"
    assert record["question"]["options"][2]["text"] == "Gamma"


def test_pick_questions_skips_invalid_correct_index(tmp_path):
    questions_path = tmp_path / "questions.json"
    questions_path.write_text(
        json.dumps(
            [
                {
                    "id": 1,
                    "language": "en",
                    "text": "Valid question",
                    "options": ["A", "B", "C", "D"],
                    "correct_index": 1,
                    "verified": True,
                    "explanation": "E1",
                    "topic": "grammar",
                },
                {
                    "id": 2,
                    "language": "en",
                    "text": "Invalid question",
                    "options": ["A", "B", "C", "D"],
                    "correct_index": -1,
                    "verified": False,
                    "explanation": "E2",
                    "topic": "grammar",
                },
            ]
        ),
        encoding="utf-8",
    )

    manager = QuizManager(str(questions_path), MagicMock())
    selected = manager.pick_questions(10)

    assert len(selected) == 1
    assert selected[0] == 1
    assert manager.get_number_of_questions() == 1


def test_get_answered_question_ids_filters_by_user(tmp_path):
    questions_path = tmp_path / "questions.json"
    questions_path.write_text(
        json.dumps(
            [
                {
                    "id": -1,
                    "language": "en",
                    "text": "Already answered by user 1",
                    "options": ["A", "B", "C", "D"],
                    "correct_index": 1,
                    "verified": True,
                    "explanation": "E1",
                    "Test": "Uncategorized",
                    "answer_history": [{"user_id": 1}],
                },
                {
                    "id": -1,
                    "language": "en",
                    "text": "Answered by another user",
                    "options": ["A", "B", "C", "D"],
                    "correct_index": 1,
                    "verified": True,
                    "explanation": "E2",
                    "Test": "Uncategorized",
                    "answer_history": [{"user_id": 2}],
                },
            ]
        ),
        encoding="utf-8",
    )

    manager = QuizManager(str(questions_path), MagicMock())
    answered_for_user_1 = manager.get_answered_question_ids(1)

    assert len(answered_for_user_1) == 1
    answered_question_texts = {
        manager.get_question_data(question_id).text for question_id in answered_for_user_1
    }
    assert "Already answered by user 1" in answered_question_texts
    assert "Answered by another user" not in answered_question_texts


def test_get_user_overall_answer_stats(tmp_path):
    questions_path = tmp_path / "questions.json"
    questions_path.write_text(
        json.dumps(
            [
                {
                    "id": 1,
                    "language": "en",
                    "text": "Q1",
                    "options": ["A", "B", "C", "D"],
                    "correct_index": 1,
                    "verified": True,
                    "explanation": "E1",
                    "topic": "grammar",
                    "answer_history": [
                        {
                            "user_id": 10,
                            "selected_option_label": "B",
                            "is_correct": True,
                        },
                        {
                            "user_id": 10,
                            "selected_option_label": "Skip",
                            "is_correct": False,
                        },
                    ],
                },
                {
                    "id": 2,
                    "language": "en",
                    "text": "Q2",
                    "options": ["A", "B", "C", "D"],
                    "correct_index": 2,
                    "verified": True,
                    "explanation": "E2",
                    "topic": "grammar",
                    "answer_history": [
                        {
                            "user_id": 10,
                            "selected_option_label": "A",
                            "is_correct": False,
                        },
                        {
                            "user_id": 20,
                            "selected_option_label": "C",
                            "is_correct": True,
                        },
                    ],
                },
            ]
        ),
        encoding="utf-8",
    )

    manager = QuizManager(str(questions_path), MagicMock())
    stats = manager.get_user_overall_answer_stats(10)

    assert stats["total_attempts"] == 3
    assert stats["correct_answers"] == 1
    assert stats["wrong_answers"] == 1
    assert stats["skipped_answers"] == 1
    assert stats["unique_answered_questions"] == 2
    assert stats["total_available_questions"] == 2
    assert stats["remaining_unanswered_questions"] == 0
    assert stats["accuracy_percent"] == 50.0
