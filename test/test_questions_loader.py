import json
from unittest.mock import MagicMock

from utils.questions_loader import QuestionsLoader


def test_load_from_file_supports_test_key_and_duplicate_negative_ids(tmp_path):
    source_path = tmp_path / "questions.json"
    source_path.write_text(
        json.dumps(
            [
                {
                    "id": -1,
                    "language": "en",
                    "text": "Q1",
                    "options": ["A", "B", "C", "D"],
                    "correct_index": 1,
                    "verified": True,
                    "explanation": "E1",
                    "Test": "SSC CGL Tier 1",
                },
                {
                    "id": -1,
                    "language": "en",
                    "text": "Q2",
                    "options": ["A", "B", "C", "D"],
                    "correct_index": 2,
                    "verified": True,
                    "explanation": "E2",
                    "Test": "SSC CHSL Tier 1",
                },
            ]
        ),
        encoding="utf-8",
    )

    loader = QuestionsLoader(MagicMock())
    questions = loader.load_from_file(str(source_path))

    assert len(questions) == 2
    ids = sorted(questions.keys())
    assert ids[0] != ids[1]
    assert questions[ids[0]].topic == "SSC CGL Tier 1"
    assert questions[ids[1]].topic == "SSC CHSL Tier 1"
