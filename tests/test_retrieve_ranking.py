"""Coverage for the retrieval ranking score helper. Run: pytest tests/ -q

Regression for the ranking bug: hybrid search returns `_relevance_score`
(higher=better) but the old sort key only read `rerank_score`/`_distance`, so on
the hybrid path it discarded relevance entirely, and on the vector-only path it
used `_distance` (lower=better) with reverse=True — inverting the order.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rag.pipelines.retrieve import _relevance


def test_rerank_score_used_as_is():
    assert _relevance({"rerank_score": 0.9}) == 0.9


def test_hybrid_relevance_score_used_when_no_rerank():
    # the key the old code never read
    assert _relevance({"_relevance_score": 0.7}) == 0.7


def test_distance_is_negated_so_nearer_is_higher():
    near = _relevance({"_distance": 0.1})
    far = _relevance({"_distance": 0.9})
    assert near > far          # nearer (smaller distance) ranks higher
    assert near == -0.1


def test_rerank_wins_over_relevance_and_distance():
    c = {"rerank_score": 0.5, "_relevance_score": 0.2, "_distance": 0.9}
    assert _relevance(c) == 0.5


def test_ordering_matches_relevance_on_hybrid_path():
    # simulate feedback-boost re-sort input: hybrid candidates, no rerank_score
    cands = [
        {"chunk_id": "a", "_relevance_score": 0.2, "feedback_boost": 0.0},
        {"chunk_id": "b", "_relevance_score": 0.9, "feedback_boost": 0.0},
        {"chunk_id": "c", "_relevance_score": 0.5, "feedback_boost": 0.0},
    ]
    cands.sort(key=lambda c: _relevance(c) + c.get("feedback_boost", 0.0),
               reverse=True)
    assert [c["chunk_id"] for c in cands] == ["b", "c", "a"]


def test_missing_scores_default_zero():
    assert _relevance({"chunk_id": "x"}) == 0.0
