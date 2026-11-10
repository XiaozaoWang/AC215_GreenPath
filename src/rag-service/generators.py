"""Answer generation is independent of retrieval and the public API schema."""
import os
from typing import Protocol

import cli


class AnswerGenerator(Protocol):
    def generate(self, question: str, results: dict) -> str: ...


class BaseGeminiGenerator:
    def generate(self, question: str, results: dict) -> str:
        return cli.answer_from_results(question, results)


class FineTunedGenerator:
    def __init__(self):
        raise NotImplementedError(
            "RAG_GENERATOR=finetuned is not implemented. Configure RAG_GENERATOR=base."
        )

    def generate(self, question: str, results: dict) -> str:
        raise NotImplementedError("Connect the future fine-tuned model endpoint here.")


def get_generator() -> AnswerGenerator:
    name = os.getenv("RAG_GENERATOR", "base")
    if name == "base":
        return BaseGeminiGenerator()
    if name == "finetuned":
        return FineTunedGenerator()
    raise ValueError(f"Unknown RAG_GENERATOR: {name}")
