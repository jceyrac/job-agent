"""tracker_views/action_sets.py — Contextual action-bar labels and per-state sets (FR-009).

Pure data module with no Streamlit / storage / scorer imports, so tests can
import it directly (T021) and assert the mapping against the FR-009 table
without rendering anything.
"""

ACTION_LABELS = {
    "extract":      "🔍 Extract",
    "score":        "🎯 Score",
    "queue":        "🚀 Queue",
    "prepare":      "📝 Prepare",
    "applied":      "✅ Applied",
    "interviewing": "🎤 Interviewing",
    "interview":    "🎤 + Interview",
    "offer":        "🎉 Offer",
    "rejected":     "❌ Rejected",
    "withdrawn":    "🏳️ Withdrawn",
    "expired":      "⏰ Expired",
    "not_relevant": "🚫 Not relevant",
}

# FR-009: derived state → (primary, secondaries, ⋯ menu). ``primary`` is None
# when a state has no primary action; an empty ⋯ tuple means no popover.
ACTION_SETS = {
    "scraped":      ("extract",      ("applied",),                    ("expired", "not_relevant")),
    "extracted":    ("score",        ("queue", "prepare", "applied"), ("expired", "not_relevant")),
    "scored":       ("queue",        ("prepare", "applied"),          ("expired", "not_relevant")),
    "queued":       ("prepare",      ("applied",),                    ("expired", "not_relevant")),
    "prepared":     ("applied",      (),                              ("expired", "not_relevant")),
    "applied":      ("interviewing", ("rejected",),                   ("withdrawn", "not_relevant")),
    "interviewing": ("interview",    ("offer", "rejected"),           ("withdrawn",)),
    "offer":        (None,           (),                              ("withdrawn", "rejected")),
    "rejected":     (None,           (),                              ("not_relevant",)),
    "withdrawn":    (None,           (),                              ("not_relevant",)),
    "archived":     (None,           (),                              ()),
    "expired":      (None,           (),                              ()),
}
