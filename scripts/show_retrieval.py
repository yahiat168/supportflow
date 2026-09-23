"""Show how the retriever ranks chunks for a question (debugging / demo).

    python scripts/show_retrieval.py "What is the difference between the Standard and Team plans?"
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.rag.retriever import search  # noqa: E402

q = " ".join(sys.argv[1:]) or "What is the difference between the Standard and Team plans?"
r = search(q)
print(f"Query: {q}\n")
for i, c in enumerate(r.chunks, 1):
    preview = " ".join(c.content.split())[:90]
    print(f"{i}. score={c.score:.3f}  {c.doc_id} (v{c.version}, {c.trust_level})  [{c.section}]\n   {preview}")
for c in r.conflicts:
    print(f"\nConflict: prefer {c.preferred_doc_id} over {c.superseded_doc_id} - {c.reason}")
if r.insufficient:
    print("No chunk passed MIN_RELEVANCE_SCORE -> the agent will say it has no evidence.")
