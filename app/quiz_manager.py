# app/quiz_manager.py

from typing import Dict, List
from .question import Question
from utils import QuestionsLoader
from app import set_last_question_id
from config import CORRECT_ANSWER_WEIGHT, WRONG_ANSWER_WEIGHT
from datetime import datetime
import json
import random

class QuizManager:
    
    def __init__(self, questions_json_pat: str,logger):
        self.question_loader = QuestionsLoader(logger)
        self.questions_db: Dict[int, Question] = self.question_loader.load_from_file(questions_json_pat)
        set_last_question_id(max(self.questions_db.keys(), default=0))
        self.logger = logger
        self.question_file=questions_json_pat
        
    def get_number_of_questions(self, topic=None, language=None) -> int:
        if topic is None and language is None:
            return sum(1 for question in self.questions_db.values() if self._is_answerable_question(question))
        elif topic is None and language is not None:
            return sum(
                1 for question in self.questions_db.values()
                if self._is_answerable_question(question) and question.language.lower() == language.lower()
            )
        elif topic is not None and language is None:
            return sum(
                1 for question in self.questions_db.values()
                if self._is_answerable_question(question) and question.topic.lower() == topic.lower()
            )
        else:
            return sum(
                1 for question in self.questions_db.values()
                if self._is_answerable_question(question)
                and question.topic.lower() == topic.lower()
                and question.language.lower() == language.lower()
            )

    def pick_questions(self, n: int, excludet=None, excludel=None) -> list:
        if excludet is None:
            excludet = []
        if excludel is None:
            excludel = []
        available_questions = [
            q_id
            for q_id, question in self.questions_db.items()
            if q_id not in excludet
            and q_id not in excludel
            and self._is_answerable_question(question)
        ]
        random.seed()
        random.shuffle(available_questions)
        return random.sample(available_questions, min(n, len(available_questions)))

    def check_answer(self, question_id: int, answer_index: int, scramble_map: dict) -> bool:
        question = self.questions_db.get(question_id)
        ans = answer_index
        if scramble_map:
            ans = scramble_map.get(answer_index)
        return question and question.correct_index == ans
    
    def get_question_data(self, question_id: int) -> Question:
        return self.questions_db.get(question_id, None)

    def extract_list_of_all_topics(self) -> list:
        return list({
            question.topic.lower()
            for question in self.questions_db.values()
            if isinstance(question.topic, str) and question.topic.strip()
        })
        
    def extract_list_of_all_languages(self) -> list:
        languages_list = list({
            question.language.lower()
            for question in self.questions_db.values()
            if isinstance(question.language, str) and question.language.strip()
        })
        return languages_list

    def _is_answerable_question(self, question: Question) -> bool:
        if not question:
            return False
        if not isinstance(question.options, list) or len(question.options) < 2:
            return False
        if not isinstance(question.correct_index, int):
            return False
        return 0 <= question.correct_index < len(question.options)
    
    def exclude_questions_not_related_to_selected_topic(self, topic: str) -> list:
        return [question.id for question in self.questions_db.values() if question.topic.lower() != topic.lower()]
    
    def exclude_questions_not_related_to_selected_language(self, language: str) -> list:
        return [question.id for question in self.questions_db.values() if question.language.lower() != language.lower()]

    def quiz_score(self, correct: int, wrong: int):
        score = wrong*(-WRONG_ANSWER_WEIGHT) + correct*(CORRECT_ANSWER_WEIGHT)
        return score

    def scramble_options(self, options: List[str]) -> Dict[int, int]:
        scrambled_map = {}
        available_indices = list(range(len(options)))
        for i in range(len(options)):
            random_index = random.choice(available_indices)
            scrambled_map[i] = random_index
            available_indices.remove(random_index)
        return scrambled_map

    def get_answered_question_ids(self, user_id: int) -> set:
        if user_id is None:
            return set()

        try:
            with open(self.question_file, "r", encoding="utf-8") as file:
                questions_list = json.load(file)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return set()

        if not isinstance(questions_list, list):
            return set()

        answered_ids = set()
        answered_texts = set()
        for item in questions_list:
            answer_history = item.get("answer_history")
            if not isinstance(answer_history, list):
                continue

            answered_by_user = any(
                isinstance(entry, dict) and entry.get("user_id") == user_id
                for entry in answer_history
            )
            if not answered_by_user:
                continue

            question_id = item.get("id")
            if isinstance(question_id, int):
                answered_ids.add(question_id)

            question_text = item.get("text")
            if isinstance(question_text, str):
                answered_texts.add(question_text)

        excluded_runtime_ids = set()
        for question_id, question in self.questions_db.items():
            if question_id in answered_ids or question.text in answered_texts:
                excluded_runtime_ids.add(question_id)

        return excluded_runtime_ids

    def save_answer_details(
        self,
        question_id: int,
        question_text: str,
        user_id: int,
        selected_option_label: str,
        selected_option_index: int,
        selected_option_text: str,
        correct_option_label: str,
        correct_option_index: int,
        correct_option_text: str,
        is_correct: bool,
        time_taken_seconds: float,
        question_number: int,
        total_questions: int,
    ) -> None:
        answer_entry = {
            "answered_at": datetime.now().isoformat(timespec="seconds"),
            "user_id": user_id,
            "question_number": question_number,
            "total_questions": total_questions,
            "selected_option_label": selected_option_label,
            "selected_option_index": selected_option_index,
            "selected_option_text": selected_option_text,
            "correct_option_label": correct_option_label,
            "correct_option_index": correct_option_index,
            "correct_option_text": correct_option_text,
            "is_correct": is_correct,
            "time_taken_seconds": round(max(time_taken_seconds, 0), 3),
        }

        try:
            with open(self.question_file, "r", encoding="utf-8") as file:
                questions_list = json.load(file)
        except FileNotFoundError:
            self.logger.error(f"File {self.question_file} not found")
            return
        except json.JSONDecodeError as exc:
            self.logger.error(f"Invalid JSON format in {self.question_file}: {exc}")
            return
        except OSError as exc:
            self.logger.error(f"Failed to read {self.question_file}: {exc}")
            return

        if not isinstance(questions_list, list):
            self.logger.error(f"Unexpected JSON structure in {self.question_file}: root must be a list")
            return

        matching_question = next(
            (
                item for item in questions_list
                if item.get("id") == question_id and item.get("text") == question_text
            ),
            None,
        )
        if matching_question is None:
            matching_question = next(
                (item for item in questions_list if item.get("id") == question_id),
                None,
            )
        if matching_question is None:
            matching_question = next(
                (item for item in questions_list if item.get("text") == question_text),
                None,
            )

        if matching_question is None:
            self.logger.warning(f"Question with id {question_id} not found in {self.question_file}")
            return

        answer_history = matching_question.get("answer_history")
        if not isinstance(answer_history, list):
            answer_history = []

        answer_history.append(answer_entry)
        matching_question["answer_history"] = answer_history

        try:
            with open(self.question_file, "w", encoding="utf-8") as file:
                json.dump(questions_list, file, ensure_ascii=False, indent=4)
        except OSError as exc:
            self.logger.error(f"Failed to write {self.question_file}: {exc}")
