from collections.abc import Callable, Sequence

from config import settings
from ollama import ChatResponse, Client
from telegram_bot.helpers.logger import setup_logger
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
