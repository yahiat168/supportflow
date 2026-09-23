"""Check that your model key works, then calibrate the retrieval threshold.

    python scripts/check_setup.py            # 1) test chat model + structured output + embeddings (no Docker needed)
    python scripts/check_setup.py --kb       # 2) after ingest: measure scores and suggest MIN_RELEVANCE_SCORE
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pydantic import BaseModel  # noqa: E402

from app.config import get_settings  # noqa: E402

s = get_settings()
print(f"LLM_PROVIDER={s.llm_provider}  MODEL_NAME={s.model_name}  BASE_URL={s.model_base_url or '(OpenAI default)'}")
print(f"EMBEDDING_PROVIDER={s.embedding_provider}  EMBEDDING_MODEL={s.embedding_model}")
if s.is_offline:
    sys.exit("LLM_PROVIDER is 'offline'. Set it to gemini (or openai) in .env first.")
if not s.model_api_key:
    sys.exit("MODEL_API_KEY is empty in .env")


class Ping(BaseModel):
    answer: str
    confidence: float


def check_keys() -> None:
    from app.graph import llm

    print("\n[1/3] Chat model ...", end=" ")
    msg = llm.chat_model().invoke("Reply with the single word: ready")
    print("OK ->", str(msg.content).strip()[:60])

    print("[2/3] Structured output ...", end=" ")
    out = llm.structured(Ping, "Answer briefly.", "What is 2+2? Give confidence 0-1.", name="setup_check")
    print("OK ->", out.model_dump())

    print("[3/3] Embeddings ...", end=" ")
    from app.rag.embeddings import embed_query
    v = embed_query("hello world")
    print(f"OK -> {len(v)} dimensions")
    print("\nAll model checks passed.")


def calibrate() -> None:
    from app.rag import store
    from app.rag.embeddings import embed_query

    if store.count_points() == 0:
        sys.exit("The vector collection is empty. Run: python -m app.rag.ingest --reset")
    relevant = ["What is the difference between the Standard and Team plans?", "How do I restore an older version of a file?",
                "My files are not syncing", "Does CloudBox integrate with Trello?", "How do I share a file with a coworker?"]
    unrelated = ["Can I pay with PayPal or cryptocurrency?", "What is the capital of France?",
                 "Recommend a good pizza recipe", "Who won the football world cup?", "How tall is Mount Everest?"]

    def top(q):
        pts = store.query(embed_query(q), {"trust_level": ["official", "internal"]}, limit=1)
        return round(float(pts[0].score), 3) if pts else 0.0

    r = [top(q) for q in relevant]
    u = [top(q) for q in unrelated]
    print("\nTop similarity for RELEVANT questions: ", r)
    print("Top similarity for UNRELATED questions:", u)
    low_rel, high_unrel = min(r), max(u)
    if low_rel > high_unrel:
        suggestion = round((low_rel + high_unrel) / 2, 2)
        print(f"\nSuggested: MIN_RELEVANCE_SCORE={suggestion}   (put this line in .env)")
    else:
        print("\nScores overlap; keep MIN_RELEVANCE_SCORE around", round(high_unrel, 2),
              "- the answering model's 'insufficient evidence' check handles the rest.")


if __name__ == "__main__":
    calibrate() if "--kb" in sys.argv else check_keys()
