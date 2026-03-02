from collections.abc import Callable, Sequence

from config import settings
from ollama import ChatResponse, Client
from telegram_bot.helpers.logger import setup_logger

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
        from telegram_bot.parsers.djinni import DjinniParser

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
        system_prompt = (
            "You are an assistant that writes concise, professional cover letters "
            "based on a user's CV and a job description. Keep it under 250 words, "
            "use a clear structure, and avoid fabrication."
        )

        messages = [
            {"role": "system", "content": system_prompt},
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
