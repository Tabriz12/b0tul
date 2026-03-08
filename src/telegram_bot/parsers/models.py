from typing import Literal

from pydantic import BaseModel, Field


class ApplicationQuestion(BaseModel):
    type: Literal["text", "number", "select", "radio"]
    name: str
    text: str
    options: list[str] = Field(default_factory=list)
