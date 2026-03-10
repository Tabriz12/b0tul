from collections.abc import Callable, Sequence

from config import settings
from ollama import ChatResponse, Client
from telegram_bot.helpers.logger import setup_logger
from telegram_bot.llm.prompts.cover_letter import cover_letter_prompt
from telegram_bot.llm.prompts.revision_cover_letter import (
    revisioned_cover_letter_prompt,
)
from telegram_bot.parsers.djinni import DjinniParser

logger = setup_logger("ollama")


class OllamaHandler:
    def __init__(self) -> None:
        self.client = Client(
            "ollama",
            headers={
                "Authorization": f"Bearer {settings.get('OLLAMA_WEBSEARCH_API_KEY')}"
            },
        )

        self.tools: dict[str, Callable] = {
            "_websearch": self._websearch,
        }

    def send_message(self, message: str) -> str | None:
        messages = [
            {
                "role": "user",
                "content": message,
            }
        ]

        while True:
            response: ChatResponse = self.client.chat(
                model=settings.get("OLLAMA_MODEL"),
                messages=messages,
                tools=[self._websearch],
            )

            messages.append(response.message)

            if not response.message.tool_calls:
                return response.message.content

            for tool_call in response.message.tool_calls:
                result = self._execute_tool_call(tool_call)

                messages.append(
                    {
                        "role": "tool",
                        "name": tool_call.function.name,
                        "content": str(result),
                    }
                )

    def _websearch(self, query: str) -> Sequence:
        try:
            response = self.client.web_search(
                query=query,
                max_results=3,
            )
            logger.info(f"Websearch results: {response.results}")

            return response.results

        except Exception as e:
            logger.error(f"Error in websearch: {e}")
            raise e

    def _check_djinni_jobs(self, query: str) -> None:
        djinni_parser = DjinniParser(
            email=settings.get("DJINNI_EMAIL"),
            password=settings.get("DJINNI_PASSWORD"),
        )

        djinni_parser.go_to_dashboard()

    def _execute_tool_call(self, tool_call) -> Sequence:  # noqa: ANN001
        name = tool_call.function.name
        args = tool_call.function.arguments or {}

        logger.info(f"Executing tool '{name}' with args {args}")

        tool = self.tools.get(name)
        if not tool:
            raise ValueError(f"Tool '{name}' is not registered")

        try:
            return tool(**args)
        except Exception as e:
            logger.error(f"Error executing tool '{name}': {e}")
            raise e

    def generate_cover_letter(self, cv: str, job_description: str) -> str:
        messages = [
            {"role": "system", "content": cover_letter_prompt},
            {
                "role": "user",
                "content": (
                    "User CV:\n"
                    f"{cv}\n\n"
                    "Job description:\n"
                    f"{job_description}\n\n"
                    "Write a tailored cover letter."
                ),
            },
        ]

        response: ChatResponse = self.client.chat(
            model=settings.get("OLLAMA_MODEL"),
            messages=messages,
        )

        return response.message.content or ""

    def revise_cover_letter(self, cover_letter: str, change_request: str) -> str:
        messages = [
            {"role": "system", "content": revisioned_cover_letter_prompt},
            {
                "role": "user",
                "content": (
                    "Current cover letter:\n"
                    f"{cover_letter}\n\n"
                    "Requested changes:\n"
                    f"{change_request}\n\n"
                    "Return the revised cover letter."
                ),
            },
        ]

        response: ChatResponse = self.client.chat(
            model=settings.get("OLLAMA_MODEL"),
            messages=messages,
        )

        return response.message.content or ""

    def generate_question_answer_template(
        self,
        cv: str,
        job_description: str,
        question_text: str,
        question_type: str,
        options: list[str] | None = None,
        user_preferences: str = "",
        answer_examples: list[dict[str, str]] | None = None,
    ) -> str:
        options_text = ", ".join(options or [])
        examples_lines: list[str] = []
        for example in answer_examples or []:
            q = example.get("question", "").strip()
            a = example.get("answer", "").strip()
            if q and a:
                examples_lines.append(f"- Q: {q}\n  A: {a}")
        examples_block = "\n".join(examples_lines) or "None"

        system_prompt = """
            You draft short, truthful answers for job application form questions.
            Match the user's likely style and preferences when available.
            Avoid fabricated claims. Keep answers concise and ready to submit.
            If options are provided, choose one valid option and return only the answer.
            """

        messages = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": (
                    "User CV:\n"
                    f"{cv}\n\n"
                    "User preferences/style:\n"
                    f"{user_preferences or 'Not provided'}\n\n"
                    "Similar past answers:\n"
                    f"{examples_block}\n\n"
                    "Job description:\n"
                    f"{job_description}\n\n"
                    "Question type:\n"
                    f"{question_type}\n\n"
                    "Question text:\n"
                    f"{question_text}\n\n"
                    "Allowed options:\n"
                    f"{options_text or 'Not specified'}\n\n"
                    "Return a single answer text only."
                ),
            },
        ]

        response: ChatResponse = self.client.chat(
            model=settings.get("OLLAMA_MODEL"),
            messages=messages,
        )

        return (response.message.content or "").strip()

    def revise_question_answer(
        self, answer_draft: str, question_text: str, change_request: str
    ) -> str:
        system_prompt = """
            You revise a draft answer for a single job application question.
            Apply requested edits and return only the updated answer text.
            Keep it concise and truthful.
            """

        messages = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": (
                    "Question:\n"
                    f"{question_text}\n\n"
                    "Current answer draft:\n"
                    f"{answer_draft}\n\n"
                    "Requested changes:\n"
                    f"{change_request}\n\n"
                    "Return the revised answer."
                ),
            },
        ]

        response: ChatResponse = self.client.chat(
            model=settings.get("OLLAMA_MODEL"),
            messages=messages,
        )

        return (response.message.content or "").strip()
