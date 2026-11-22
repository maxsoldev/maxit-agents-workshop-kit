"""LLM judge: an OpenAI model scores one result field 1-5 against a rubric."""
from __future__ import annotations

import json

from kit.config import settings

SYSTEM = (
    "You grade one field of the result of an AI agent. Read the rubric, the case input and the field value. "
    "Give a score from 1 (fails the rubric) to 5 (fully meets it). "
    'Answer only JSON: {"score": <1-5>, "reason": "<one short sentence>"}.'
)


def score(value, rubric: str, case_input: dict) -> tuple[int, str]:
    """Return (score 1-5, reason). Raise an error with a clear message if the judge cannot run."""
    s = settings()
    if not s.openai_api_key:
        raise RuntimeError("no OPENAI_API_KEY: set it in .env to run judge checks")
    from openai import BadRequestError, OpenAI

    client = OpenAI(api_key=s.openai_api_key, timeout=60)
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=1)
    user = (f"Rubric:\n{rubric}\n\nCase input:\n{json.dumps(case_input, ensure_ascii=False)[:8000]}\n\n"
            f"Field value:\n{text[:8000]}")
    args = dict(model=s.judge_model, response_format={"type": "json_object"},
                messages=[{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}])
    try:
        r = client.chat.completions.create(temperature=0, **args)
    except BadRequestError as e:
        if "temperature" not in str(e):
            raise
        r = client.chat.completions.create(**args)  # some models (gpt-5 family) allow only the default
    data = json.loads(r.choices[0].message.content or "{}")
    return max(1, min(5, int(data.get("score", 1)))), str(data.get("reason", "")).strip()
