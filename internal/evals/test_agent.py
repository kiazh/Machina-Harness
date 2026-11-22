"""DeepEval test suite for Machina answer quality.

Run with ``deepeval test run internal/evals/test_agent.py``. Requires the optional
``internal/evals/requirements.txt`` dependencies and an OpenAI-compatible key.
"""

from __future__ import annotations

import os

import pytest
from deepeval import assert_test
from deepeval.metrics import AnswerRelevancyMetric, FaithfulnessMetric
from deepeval.test_case import LLMTestCase

_MODEL = os.environ.get("MACHINA_EVAL_MODEL", "gpt-4.1-mini")


@pytest.mark.parametrize(
    ("question", "context", "answer", "expected"),
    [
        (
            "Which database does the project use?",
            "The project uses PostgreSQL with pgvector.",
            "It uses PostgreSQL with pgvector.",
            "PostgreSQL",
        ),
        (
            "What is the deployment command?",
            "Run `machina serve` to start the HTTP API.",
            "Run `machina serve`.",
            "machina serve",
        ),
    ],
)
def test_answer_is_grounded(question: str, context: str, answer: str, expected: str) -> None:
    assert expected in answer
    case = LLMTestCase(input=question, actual_output=answer, retrieval_context=[context])
    assert_test(
        case,
        metrics=[
            AnswerRelevancyMetric(threshold=0.7, model=_MODEL),
            FaithfulnessMetric(threshold=0.7, model=_MODEL),
        ],
    )
