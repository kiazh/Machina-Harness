"""Ragas regression gate for retrieval-grounded answers.

Install ``internal/evals/requirements.txt`` and provide ``OPENAI_API_KEY``. The case
file is data, not code: each line has ``user_input``, ``response``,
``retrieved_contexts``, and ``reference``. The gate fails when any configured
metric falls below its floor or below the committed baseline.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

__all__ = ["load_cases", "main"]


def load_cases(path: Path) -> list[dict]:
    rows: list[dict] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        required = {"user_input", "response", "retrieved_contexts", "reference"}
        missing = required - set(row)
        if missing:
            raise ValueError(f"{path}:{line_number}: missing {sorted(missing)}")
        rows.append(row)
    if not rows:
        raise ValueError(f"{path} has no evaluation cases")
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cases", default="internal/evals/ragas_cases.jsonl")
    parser.add_argument("--baseline", default="internal/evals/ragas_baseline.json")
    parser.add_argument("--tolerance", type=float, default=0.02)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    from datasets import Dataset
    from langchain_openai import ChatOpenAI, OpenAIEmbeddings
    from ragas import evaluate
    from ragas.embeddings import LangchainEmbeddingsWrapper
    from ragas.llms import LangchainLLMWrapper
    from ragas.metrics import answer_relevancy, context_precision, faithfulness

    dataset = Dataset.from_list(load_cases(Path(args.cases)))
    result = evaluate(
        dataset,
        metrics=[faithfulness, answer_relevancy, context_precision],
        llm=LangchainLLMWrapper(
            ChatOpenAI(
                model=os.environ.get("MACHINA_EVAL_MODEL", "gpt-4.1-mini"),
                temperature=0,
            )
        ),
        embeddings=LangchainEmbeddingsWrapper(OpenAIEmbeddings()),
    )
    current = {
        key: float(value)
        for key, value in result.items()
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    }
    baseline_path = Path(args.baseline)
    baseline = (
        json.loads(baseline_path.read_text(encoding="utf-8")) if baseline_path.exists() else {}
    )
    failures = [
        f"{key}: {current.get(key, 0.0):.4f} < baseline {float(floor):.4f} - {args.tolerance}"
        for key, floor in baseline.items()
        if current.get(key, 0.0) < float(floor) - args.tolerance
    ]
    payload = {"metrics": current, "failures": failures, "passed": not failures}
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
