# Evaluation gates

These gates are optional quality checks, not part of the runtime dependency
set. They need an OpenAI-compatible model endpoint and a key because their
metrics judge generated answers.

| Gate | Command | What it proves |
|---|---|---|
| Deterministic metric baseline | `python tools/eval_gate.py --baseline evals/ragas_baseline.json --current results.json` | A saved metric set did not regress |
| Promptfoo | `npx --yes promptfoo@latest eval -c evals/promptfooconfig.yaml` | Grounded-answer assertions pass |
| Ragas | `python evals/ragas_gate.py` | Faithfulness, answer relevancy, and context precision stay above baseline |
| DeepEval | `python -m deepeval test run evals/test_agent.py` | Answer relevancy and faithfulness pass per test case |

Install the Python gates:

```bash
pip install -r evals/requirements.txt
export OPENAI_API_KEY=...
```

The gate script can run any single framework:

```bash
python tools/eval_gate.py --promptfoo-config evals/promptfooconfig.yaml
python tools/eval_gate.py --ragas
python tools/eval_gate.py --deepeval
```

For the JSON baseline path, the baseline is committed and the current result
is emitted by the evaluation runner. Floats compare directly; tolerance and
absolute floors are explicit:

```bash
python tools/eval_gate.py \
  --baseline evals/ragas_baseline.json \
  --current evals/results.json \
  --tolerance answer_relevancy=0.02 \
  --require faithfulness=0.70
```

Update a baseline only after reviewing the metric delta and the underlying
cases. A gate that silently updates itself is not a gate.
