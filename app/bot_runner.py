# app/bot_runner.py
from telegram import Update, ReplyKeyboardRemove, ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CallbackContext, PicklePersistence, CommandHandler, ConversationHandler, MessageHandler, filters, CallbackQueryHandler
from .quiz_manager import QuizManager
from .question import Question
from app import main_menu_text, get_next_question_id
from config import LANGUAGE_SELECTION, TOPIC_SELECTION, QUESTION_NUMBER_SELECTION, CORRECT_ANSWER_WEIGHT, WRONG_ANSWER_WEIGHT, DEFAULT_NUMBER_OF_QUESTIONS
from .handlers import (State,
    make_inline_keyboard_for_choice, make_inline_keyboard_for_question_quiz,
    make_inline_keyboard_for_incorrect_solutions,
    _escape_markdown, make_inline_keyboard_from_list, make_inline_keyboard_for_list,
    extract_list_of_main_operations,
    CALLBACK_YES, CALLBACK_NO, CALLBACK_SKIP, CALLBACK_MAIN_MENU, CALLBACK_SOLUTION_PREFIX,
    CALLBACK_PAPER_PREFIX
)
import html
import os
import random, time


class QuizBot:
   
    def __init__(self, token, questions_json_path, logger):
        self.token = token
        self.default_questions_json_path = questions_json_path
        self.questions_data_dir = os.path.dirname(questions_json_path) or "."
        initial_questions_path = questions_json_path
        if not os.path.isfile(initial_questions_path):
            try:
                fallback_paths = [
                    os.path.join(self.questions_data_dir, name)
                    for name in sorted(os.listdir(self.questions_data_dir))
                    if name.lower().endswith(".json") and os.path.isfile(os.path.join(self.questions_data_dir, name))
                ]
            except OSError:
                fallback_paths = []
            if fallback_paths:
                initial_questions_path = fallback_paths[0]
                logger.warning(
                    "Configured questions file '%s' not found. Using '%s' instead.",
                    questions_json_path,
                    initial_questions_path,
                )

        self.quiz_manager = QuizManager(initial_questions_path,logger)
        self.persistence = PicklePersistence('quiz_bot_data.pkl')
        self.application = Application.builder().token(self.token).persistence(self.persistence).build()
        self.logger = logger
        self.state_handlers = {
            State.SELECT_PAPER: self.conv_quiz_selected_paper,
            State.SELECT_LANGUAGE: self.conv_quiz_selected_language,
            State.SELECT_TOPIC: self.conv_quiz_selected_topic,
            State.ANSWERING_QUESTION: self.conv_quiz_answer,
            State.REVIEW_INCORRECT: self.conv_quiz_review_incorrect_solution,
            State.IF_LANGUAGE: self.conv_quiz_language_selection,
            State.IF_TOPIC: self.conv_quiz_topic_selection,
            State.SELECT_CUSTOM_NQUESTION: self.conv_quiz_questions_selection,
        }

    def _trim_paper_label(self, filename: str, max_length: int = 40) -> str:
        label = os.path.splitext(filename)[0].replace("_", " ").strip() or filename
        if len(label) <= max_length:
            return label
        return f"{label[: max_length - 3]}..."

    def _discover_question_papers(self) -> list:
        papers = []
        try:
            all_names = sorted(os.listdir(self.questions_data_dir))
        except OSError as exc:
            self.logger.error(f"Cannot read questions directory '{self.questions_data_dir}': {exc}")
            return papers

        json_names = [name for name in all_names if name.lower().endswith(".json")]
        for index, filename in enumerate(json_names):
            full_path = os.path.join(self.questions_data_dir, filename)
            if not os.path.isfile(full_path):
                continue
            papers.append(
                {
                    "id": f"{CALLBACK_PAPER_PREFIX}{index}",
                    "path": full_path,
                    "label": self._trim_paper_label(filename),
                    "filename": filename,
                }
            )
        return papers

    def _use_selected_question_file(self, file_path: str) -> None:
        if not file_path:
            return
        if os.path.normcase(self.quiz_manager.question_file) == os.path.normcase(file_path):
            return
        self.quiz_manager = QuizManager(file_path, self.logger)

    async def _send_question_paper_selection(self, update: Update, context: CallbackContext):
        papers = self._discover_question_papers()
        context.user_data["available_question_papers"] = papers

        if not papers:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=_escape_markdown("_No JSON question paper found in data folder._"),
                parse_mode="MarkdownV2",
            )
            await self.command_restart(update, context)
            return

        if len(papers) == 1:
            selected_paper = papers[0]
            context.user_data["selected_question_file"] = selected_paper["path"]
            context.user_data["selected_question_paper_name"] = selected_paper["filename"]
            self._use_selected_question_file(selected_paper["path"])
            await self._handle_next_setup_step(update, context)
            return

        buttons = [(paper["id"], paper["label"]) for paper in papers]
        message = await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=_escape_markdown("_Select question paper:_"),
            parse_mode="MarkdownV2",
            reply_markup=make_inline_keyboard_from_list(buttons, row_size=1),
        )
        context.user_data["state"] = State.SELECT_PAPER
        context.user_data["last_message_id"] = message.message_id

    def start_bot(self):
        self.application.add_handler(CommandHandler("start", self.command_start))
        self.application.add_handler(CommandHandler("cancel", self.command_start))
        self.application.add_handler(CommandHandler("quiz", self.command_quiz))
        self.application.add_handler(CommandHandler("review", self.command_review))
        self.application.add_handler(CommandHandler("restart", self.command_restart))
        self.application.add_handler(CallbackQueryHandler(self.button))
        self.application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.make_conversation))
        self.application.run_polling()

# Functions for the main conversation

    async def button(self, update: Update, context: CallbackContext):
        query = update.callback_query
        state = context.user_data.get("state", State.SELECTING_ACTION)
        await query.answer()

        if 'last_message_id' in context.user_data:
            await context.bot.delete_message(chat_id=query.message.chat_id, message_id=context.user_data['last_message_id'])
            del context.user_data['last_message_id']

        if query.data == CALLBACK_MAIN_MENU:
            await self.command_restart(update, context)
        elif query.data == "start_quiz":
            await self.conv_quiz_start(update, context)
        elif query.data == "review_question":
            await self.conv_review_question_start(update, context)
        else:
            handler = self.state_handlers.get(state)
            if handler:
                await handler(update, context)

    async def make_conversation(self, update: Update, context: CallbackContext):
        state = context.user_data.get("state", State.SELECTING_ACTION)
        if state in [
            State.SELECT_LANGUAGE, State.SELECT_TOPIC, 
            State.SELECT_CUSTOM_NQUESTION, State.SELECT_NUMQUESTION, 
            State.REVIEW
        ]:
            if 'last_message_id' in context.user_data:
                await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=context.user_data['last_message_id'])
                del context.user_data['last_message_id']

        if update.message.text == "Cancel":
            await self.command_restart(update, context)
        elif state == State.SELECT_NUMQUESTION:
                await self.conv_quiz_selected_questions(update, context)
        elif state == State.REVIEW:
                await self.conv_review_question_selected_id(update, context)
            
    async def command_start(self, update: Update, context: CallbackContext):
        user_id = update.effective_user.id
        self.logger.info(f"Message : issued the /start command.")
        context.user_data.clear()
        message =await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=_escape_markdown(main_menu_text),
            parse_mode="MarkdownV2",
            reply_markup=make_inline_keyboard_from_list(extract_list_of_main_operations())
        )
        context.user_data["last_message_id"] = message.message_id
        context.user_data["state"] = State.SELECTING_ACTION

    async def command_quiz(self, update: Update, context: CallbackContext):
        user_id = update.effective_user.id
        self.logger.info(f"Message : issued the /quiz command.")
        context.user_data.clear()
        await self.conv_quiz_start(update, context)

    async def command_review(self, update: Update, context: CallbackContext):
        user_id = update.effective_user.id
        self.logger.info(f"Message : issued the /review command.")
        context.user_data.clear()
        await self.conv_review_question_start(update, context)

    async def command_restart(self, update: Update, context: CallbackContext):
        user_id = update.effective_user.id
        self.logger.info(f"Message : issued a restart/cancel command.")
        context.user_data.clear()
        message = await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=_escape_markdown(main_menu_text),
            parse_mode="MarkdownV2",
            reply_markup=make_inline_keyboard_from_list(extract_list_of_main_operations())
        )
        context.user_data["last_message_id"] = message.message_id
        context.user_data["state"] = State.SELECTING_ACTION


# Functions for the Quiz conversation

    async def _handle_next_setup_step(self, update: Update, context: CallbackContext):
        """
        Router function to decide the next step in the quiz setup conversation.
        This centralizes the logic and avoids code duplication.
        """
        chat_id = update.effective_chat.id

        if "selected_question_file" not in context.user_data:
            await self._send_question_paper_selection(update, context)
            return

        if LANGUAGE_SELECTION and "custom_language" not in context.user_data:
            message = await context.bot.send_message(
                chat_id=chat_id,
                text=_escape_markdown("_Do you want to choose a language?_"),
                parse_mode="MarkdownV2",
                reply_markup=make_inline_keyboard_for_choice()
            )
            context.user_data["state"] = State.IF_LANGUAGE
            context.user_data["last_message_id"] = message.message_id
            return

        if TOPIC_SELECTION and "custom_topic" not in context.user_data:
            message = await context.bot.send_message(
                chat_id=chat_id,
                text=_escape_markdown("_Do you want to select a specific topic?_"),
                parse_mode="MarkdownV2",
                reply_markup=make_inline_keyboard_for_choice()
            )
            context.user_data["state"] = State.IF_TOPIC
            context.user_data["last_message_id"] = message.message_id
            return

        if QUESTION_NUMBER_SELECTION and "custom_number" not in context.user_data:
            message = await context.bot.send_message(
                chat_id=chat_id,
                text=_escape_markdown("_Do you want to select the questions number?_"),
                parse_mode="MarkdownV2",
                reply_markup=make_inline_keyboard_for_choice()
            )
            context.user_data["state"] = State.SELECT_CUSTOM_NQUESTION
            context.user_data["last_message_id"] = message.message_id
            return

        await self.conv_quiz_start_for_user(update, context, context.user_data.get("selected_n_questions", DEFAULT_NUMBER_OF_QUESTIONS))
        context.user_data["state"] = State.ANSWERING_QUESTION

    async def conv_quiz_start(self, update: Update, context: CallbackContext):  
        await self._handle_next_setup_step(update, context)

    async def conv_quiz_selected_paper(self, update: Update, context: CallbackContext):
        query = update.callback_query
        selected_id = query.data
        available_papers = context.user_data.get("available_question_papers", [])
        selected_paper = next(
            (paper for paper in available_papers if paper.get("id") == selected_id),
            None,
        )

        if selected_paper is None:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=_escape_markdown("_Invalid paper selection\\. Please choose from the list\\._"),
                parse_mode="MarkdownV2",
            )
            await self._send_question_paper_selection(update, context)
            return

        context.user_data["selected_question_file"] = selected_paper["path"]
        context.user_data["selected_question_paper_name"] = selected_paper["filename"]
        self._use_selected_question_file(selected_paper["path"])
        await self._handle_next_setup_step(update, context)
    
    async def conv_quiz_language_selection(self, update: Update, context: CallbackContext):
        query = update.callback_query
        if query.data == CALLBACK_YES:
            context.user_data["custom_language"] = True
            message = await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=_escape_markdown("_Select your language from the keyboard:_"), 
                parse_mode="MarkdownV2",
                reply_markup=make_inline_keyboard_for_list(self.quiz_manager.extract_list_of_all_languages())
            )
            context.user_data["state"] = State.SELECT_LANGUAGE
            context.user_data["last_message_id"] = message.message_id
        else:
            context.user_data["custom_language"] = False
            context.user_data["selected_language"] = None
            await self._handle_next_setup_step(update, context)
        
    async def conv_quiz_selected_language(self, update: Update, context: CallbackContext):
        query = update.callback_query
        selected_language = query.data.lower()
        context.user_data["selected_language"] = selected_language
        await self._handle_next_setup_step(update, context)
        
    async def conv_quiz_topic_selection(self, update: Update, context: CallbackContext):
        query = update.callback_query
        if query.data == CALLBACK_YES:
            context.user_data["custom_topic"] = True
            message = await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=_escape_markdown("_Select your topic from the keyboard:_"), 
                parse_mode="MarkdownV2",
                reply_markup=make_inline_keyboard_for_list(self.quiz_manager.extract_list_of_all_topics())
            )
            context.user_data["state"] = State.SELECT_TOPIC
            context.user_data["last_message_id"] = message.message_id
        elif query.data == CALLBACK_NO:
            context.user_data["custom_topic"] = False
            context.user_data["selected_topic"] = None
            await self._handle_next_setup_step(update, context)

    async def conv_quiz_selected_topic(self, update: Update, context: CallbackContext):
        query = update.callback_query
        selected_topic = query.data.lower()
        context.user_data["selected_topic"] = selected_topic
        await self._handle_next_setup_step(update, context)
        
    async def conv_quiz_questions_selection(self, update: Update, context: CallbackContext):
        query = update.callback_query
        action = query.data

        if action == CALLBACK_YES:
            context.user_data["custom_number"] = True
            maxnumber = self.quiz_manager.get_number_of_questions(
                topic=context.user_data.get("selected_topic"),
                language=context.user_data.get("selected_language")
            )
            message = await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=_escape_markdown("_Insert the number of questions (1 - " + str(maxnumber) + "):_"), 
                parse_mode="MarkdownV2",
                reply_markup=ReplyKeyboardRemove()
            )
            context.user_data["last_message_id"] = message.message_id
            context.user_data["state"] = State.SELECT_NUMQUESTION
        else :
            context.user_data["custom_number"] = False
            context.user_data["selected_n_questions"] = DEFAULT_NUMBER_OF_QUESTIONS
            await self._handle_next_setup_step(update, context)
        
    async def conv_quiz_selected_questions(self, update: Update, context: CallbackContext):
        try:
            num = int(update.message.text)
            maxnumber = self.quiz_manager.get_number_of_questions(
                topic=context.user_data.get("selected_topic"),
                language=context.user_data.get("selected_language")
            )

            if 1 <= num <= maxnumber:
                context.user_data["selected_n_questions"] = num
                await self.conv_quiz_start_for_user(update, context, num)
                context.user_data["state"] = State.ANSWERING_QUESTION
            else:
                await update.message.reply_text(
                    text=_escape_markdown("_Number out of range (1 - {maxnumber}). Try again_"), 
                    parse_mode="MarkdownV2",
                    reply_markup=ReplyKeyboardRemove()
                )
        except ValueError:
            await update.message.reply_text(
                    text=_escape_markdown("_Please insert a valid number:_"), 
                    parse_mode="MarkdownV2",
                    reply_markup=ReplyKeyboardRemove()
                )
        
    async def conv_quiz_start_for_user(self, update: Update, context: CallbackContext, n_questions: int):
        user_id = update.effective_user.id
        selected_topic = context.user_data.get("selected_topic")
        selected_language = context.user_data.get("selected_language")
        excluded_keys_t = []
        excluded_keys_l = []
        if selected_topic:
            excluded_keys_t = self.quiz_manager.exclude_questions_not_related_to_selected_topic(selected_topic)
        if selected_language:
            excluded_keys_l = self.quiz_manager.exclude_questions_not_related_to_selected_language(selected_language)
        already_answered_ids = self.quiz_manager.get_answered_question_ids(user_id)
        excluded_keys_t = list(set(excluded_keys_t).union(already_answered_ids))
        questions_ids = self.quiz_manager.pick_questions(n_questions, excluded_keys_t, excluded_keys_l)
        if not questions_ids:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=_escape_markdown(
                    "_No new unanswered questions are available for your selection. Add more questions or clear history._"
                ),
                parse_mode="MarkdownV2",
            )
            await self.command_restart(update, context)
            return
        self.logger.info(f"Message : started a quiz with {len(questions_ids)} questions.")
        context.user_data["quiz"] = {
            "questions_ids": questions_ids,
            "current_question_scramble_map": {},
            "current_question_start_time": None,
            "current_index": 0,
            "correct_count": 0,
            "wrong_count" : 0,
            "skipped_count": 0,
            "question_results": []
        }
        context.user_data["start_time"] = time.time()
        await self.conv_quiz_send_question(update, context)

    async def conv_quiz_send_question(self, update: Update, context: CallbackContext):
        user_quiz = context.user_data["quiz"]
        current_index = user_quiz["current_index"]
        question_ids = user_quiz["questions_ids"]

        if current_index >= len(question_ids):
            await self.conv_quiz_finish(update, context)
            return

        question_id = question_ids[current_index]
        question = self.quiz_manager.get_question_data(question_id)

        scrambled_options_map = self.quiz_manager.scramble_options(question.options)
        user_quiz["current_question_scramble_map"] = scrambled_options_map
        user_quiz["current_question_start_time"] = time.time()

        scrambled_options = [question.options[scrambled_options_map[i]] for i in range(len(question.options))]
        message_text = f"❓ *Question {current_index + 1}/{len(question_ids)}*\n\n{question.question_to_string(scrambled_options_map)}"

        message = await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=message_text,
            parse_mode="Markdown",
            reply_markup=make_inline_keyboard_for_question_quiz(len(scrambled_options))
        )
        context.user_data["last_message_id"] = message.message_id

    def _build_question_review_html(
        self,
        question: Question,
        question_number: int = None,
        selected_display: str = None,
        time_taken_seconds: float = None,
    ) -> str:
        title = "<b>Question Review</b>"
        if question_number is not None:
            title = f"<b>Review - Question {question_number}</b>"

        options_lines = []
        for idx, option in enumerate(question.options):
            options_lines.append(f"{chr(65 + idx)} - {html.escape(str(option))}")

        correct_label = "N/A"
        correct_text = ""
        if isinstance(question.correct_index, int) and 0 <= question.correct_index < len(question.options):
            correct_label = chr(65 + question.correct_index)
            correct_text = str(question.options[question.correct_index])

        explanation_text = "Comment not available."
        if isinstance(question.explanation, str) and question.explanation.strip() and "None" not in question.explanation:
            explanation_text = question.explanation

        lines = [
            title,
            "",
            f"<b>Question:</b> {html.escape(str(question.text))}",
            "",
            "<b>Options:</b>",
        ]
        lines.extend(options_lines or ["No options available"])
        lines.extend(
            [
                "",
                f"<b>Correct Answer:</b> {correct_label} - {html.escape(correct_text)}",
                f"<b>Explanation:</b> {html.escape(explanation_text)}",
            ]
        )

        if selected_display is not None:
            lines.append(f"<b>Your answer:</b> {html.escape(selected_display)}")
        if time_taken_seconds is not None:
            lines.append(f"<b>Time taken:</b> {time_taken_seconds:.3f} sec")

        return "\n".join(lines)

    def _format_selected_answer_display(self, question: Question, selected_result: dict) -> str:
        selected_label = selected_result.get("selected_option_label", "N/A")
        if selected_label == CALLBACK_SKIP:
            return "Skipped"

        selected_index = selected_result.get("selected_option_index")
        if isinstance(selected_index, int) and 0 <= selected_index < len(question.options):
            normalized_label = chr(65 + selected_index)
            normalized_text = str(question.options[selected_index])
            if selected_label not in ("", None, "N/A") and selected_label != normalized_label:
                return f"{normalized_label} - {normalized_text} (selected as {selected_label} in shuffled options)"
            return f"{normalized_label} - {normalized_text}"

        selected_text = selected_result.get("selected_option_text", "")
        if selected_text:
            if selected_label not in ("", None, "N/A"):
                return f"{selected_label} - {selected_text}"
            return str(selected_text)
        return str(selected_label)

    def _build_solution_callback_data(self, result_index: int) -> str:
        return f"{CALLBACK_SOLUTION_PREFIX}{result_index}"

    async def _send_incorrect_solutions_menu(self, update: Update, context: CallbackContext):
        user_quiz = context.user_data.get("quiz", {})
        question_results = user_quiz.get("question_results", [])
        incorrect_results = [result for result in question_results if not result.get("is_correct")]

        if not incorrect_results:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=_escape_markdown("No incorrect questions to review."),
                parse_mode="MarkdownV2",
            )
            await self.command_restart(update, context)
            return

        solution_items = []
        for result_index, result in enumerate(incorrect_results):
            callback_data = self._build_solution_callback_data(result_index)
            result["callback_data"] = callback_data
            solution_items.append(
                {
                    "label": f"Q{result.get('question_number', result_index + 1)}",
                    "callback_data": callback_data,
                }
            )

        context.user_data["incorrect_question_results"] = incorrect_results
        message = await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=_escape_markdown(
                "_Select an incorrect question to view its solution:_"
            ),
            parse_mode="MarkdownV2",
            reply_markup=make_inline_keyboard_for_incorrect_solutions(solution_items),
        )
        context.user_data["state"] = State.REVIEW_INCORRECT
        context.user_data["last_message_id"] = message.message_id

    async def conv_quiz_answer(self, update: Update, context: CallbackContext):
        query = update.callback_query
        action = query.data
        user_quiz = context.user_data["quiz"]
        current_index = user_quiz["current_index"]
        question_ids = user_quiz["questions_ids"]

        question_id = question_ids[current_index]
        question = self.quiz_manager.get_question_data(question_id)
        scrambled_options_map = user_quiz["current_question_scramble_map"]
        question_start_time = user_quiz.get("current_question_start_time")
        time_taken_seconds = 0.0
        if question_start_time:
            time_taken_seconds = time.time() - question_start_time

        correct = next(
            (fake_idx for fake_idx, real_idx in scrambled_options_map.items() if real_idx == question.correct_index),
            None,
        )
        correct_option_label = chr(correct + ord('A')) if isinstance(correct, int) else "N/A"
        correct_option_text = (
            question.options[question.correct_index]
            if 0 <= question.correct_index < len(question.options)
            else ""
        )

        if action == CALLBACK_SKIP:
            selected_option_real_index = -1
            selected_option_text = ""
            is_correct = False
            user_quiz["skipped_count"] += 1
        else:
            chosen_option = ord(action) - ord('A')
            selected_option_real_index = scrambled_options_map.get(chosen_option, chosen_option)
            selected_option_text = (
                question.options[selected_option_real_index]
                if 0 <= selected_option_real_index < len(question.options)
                else ""
            )
            is_correct = self.quiz_manager.check_answer(question_id, chosen_option, scrambled_options_map)
            if is_correct:
                user_quiz["correct_count"] += 1
            else:
                user_quiz["wrong_count"] += 1

        result_entry = {
            "question_id": question_id,
            "question_number": current_index + 1,
            "is_correct": is_correct,
            "selected_option_label": action,
            "selected_option_index": selected_option_real_index,
            "selected_option_text": selected_option_text,
            "correct_option_label": correct_option_label,
            "correct_option_index": question.correct_index,
            "correct_option_text": correct_option_text,
            "time_taken_seconds": round(max(time_taken_seconds, 0), 3),
        }
        user_quiz["question_results"].append(result_entry)

        self.quiz_manager.save_answer_details(
            question_id=question_id,
            question_text=question.text,
            user_id=update.effective_user.id,
            selected_option_label=action,
            selected_option_index=selected_option_real_index,
            selected_option_text=selected_option_text,
            correct_option_label=correct_option_label,
            correct_option_index=question.correct_index,
            correct_option_text=correct_option_text,
            is_correct=is_correct,
            time_taken_seconds=time_taken_seconds,
            question_number=current_index + 1,
            total_questions=len(question_ids),
        )

        user_quiz["current_index"] += 1
        await self.conv_quiz_send_question(update, context)

    async def conv_quiz_finish(self, update: Update, context: CallbackContext):
        user_quiz = context.user_data.get("quiz", {})
        correct = user_quiz.get("correct_count", 0)
        wrong = user_quiz.get("wrong_count", 0)
        skipped = user_quiz.get("skipped_count", 0)
        total = len(user_quiz.get("questions_ids", []))
        incorrect_total = total - correct
        score = self.quiz_manager.quiz_score(correct, wrong)
        user_id = update.effective_user.id if update.effective_user else None
        overall_stats = self.quiz_manager.get_user_overall_answer_stats(user_id)

        total_time_text = "N/A"
        start_time = context.user_data.get("start_time")
        if start_time:
            time_taken = max(time.time() - start_time, 0)
            hours, remainder = divmod(time_taken, 3600)
            minutes, seconds = divmod(remainder, 60)
            total_time_text = f"{int(hours):02}:{int(minutes):02}:{int(seconds):02}"

        question_results = user_quiz.get("question_results", [])
        avg_time = 0.0
        if question_results:
            avg_time = sum(result.get("time_taken_seconds", 0.0) for result in question_results) / len(question_results)

        summary_text = (
            f"*Quiz finished!*\n\n"
            f"Total questions: {total}\n"
            f"Correct answers: {correct}/{total}\n"
            f"Incorrect/Skipped: {incorrect_total}/{total}\n"
            f"Wrong answers: {wrong}\n"
            f"Skipped: {skipped}\n"
            f"Final score: {score:.2f}\n"
            f"Total time: {total_time_text}\n"
            f"Average time/question: {avg_time:.2f} sec\n\n"
            f"Overall stats (all quizzes):\n"
            f"Unique questions answered: {overall_stats['unique_answered_questions']}/{overall_stats['total_available_questions']}\n"
            f"Remaining unanswered questions: {overall_stats['remaining_unanswered_questions']}\n"
            f"Total attempts: {overall_stats['total_attempts']}\n"
            f"Correct: {overall_stats['correct_answers']}\n"
            f"Wrong: {overall_stats['wrong_answers']}\n"
            f"Skipped: {overall_stats['skipped_answers']}\n"
            f"Accuracy (excluding skips): {overall_stats['accuracy_percent']:.2f}%"
        )

        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=_escape_markdown(summary_text),
            parse_mode="MarkdownV2",
        )

        if incorrect_total > 0:
            await self._send_incorrect_solutions_menu(update, context)
            return

        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=_escape_markdown("Great job! You have no incorrect answers to review."),
            parse_mode="MarkdownV2",
        )
        await self.command_restart(update, context)

    async def conv_quiz_review_incorrect_solution(self, update: Update, context: CallbackContext):
        query = update.callback_query
        callback_data = query.data
        incorrect_results = context.user_data.get("incorrect_question_results", [])
        selected_result = next(
            (result for result in incorrect_results if result.get("callback_data") == callback_data),
            None,
        )

        if selected_result is None:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=_escape_markdown("Invalid selection. Please choose a question from the list."),
                parse_mode="MarkdownV2",
            )
            await self._send_incorrect_solutions_menu(update, context)
            return

        question = self.quiz_manager.get_question_data(selected_result["question_id"])
        if question is None:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=_escape_markdown("Question not found in the current database."),
                parse_mode="MarkdownV2",
            )
            await self._send_incorrect_solutions_menu(update, context)
            return

        selected_display = self._format_selected_answer_display(question, selected_result)

        solution_text = self._build_question_review_html(
            question=question,
            question_number=selected_result.get("question_number"),
            selected_display=selected_display,
            time_taken_seconds=selected_result.get("time_taken_seconds", 0.0),
        )
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=solution_text,
            parse_mode="HTML",
        )
        await self._send_incorrect_solutions_menu(update, context)

   # Functions for review questions

    async def conv_review_question_start(self, update: Update, context: CallbackContext):
        message = await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=_escape_markdown("_Please provide the ID of the question you want to review:_"),
            parse_mode="MarkdownV2",
            reply_markup=ReplyKeyboardRemove()
        )
        context.user_data["last_message_id"] = message.message_id
        context.user_data["state"] = State.REVIEW

    async def conv_review_question_selected_id(self, update: Update, context: CallbackContext):
        try:
            user_id = update.effective_user.id
            question_number = int(update.message.text)
            self.logger.info(f"Message : requested question {question_number}")
            q = self.quiz_manager.get_question_data(question_number)
            if q is None:
                message = await context.bot.send_message(
                    chat_id=update.effective_chat.id, 
                    text=_escape_markdown("_Please provide a valid question ID:_"),
                    parse_mode="MarkdownV2",
                    reply_markup=ReplyKeyboardRemove()  
                )
                context.user_data["last_message_id"] = message.message_id
                return
            await context.bot.send_message(
                chat_id=update.effective_chat.id, 
                text=self._build_question_review_html(question=q),
                parse_mode="HTML",
                reply_markup=ReplyKeyboardRemove()
                )
        except ValueError:
            await context.bot.send_message(
                chat_id=update.effective_chat.id, 
                text="_Invalid question number!_",
                parse_mode="MarkdownV2",
                reply_markup=ReplyKeyboardRemove()
                )
        await self.command_restart(update, context)
