"""graph.py — StateGraph wiring for the CV agent (spec 029, FR-017).

Fixed pipeline (also in ``contracts/node-io-contract.md``)::

    START
      → resolve_reference → refresh_context → extract_requirements
      → analyze_and_plan → analysis_gate
          ├─ abort ───────────► mark_skipped ─► END
          └─ proceed/adjust ──► tailor_cv → draft_cover_letter
                                → draft_recruiter_message → self_critique
                                    ├─ needs_revision && revision_count < 3 ─► tailor_cv
                                    └─ else ─► render → publish → approval_gate
                                        ├─ approve ─► file_and_record ─► END
                                        └─ reject (bounded) ─► tailor_cv

**HITL mechanics (the reusable foundation).** The two gates are terminal
interactions. Each gate node calls ``langgraph.types.interrupt(payload)`` to pause
mid-node and hand the CLI a payload; the CLI resumes with
``graph.invoke(Command(resume=<decision>), config)``, whose value is returned from
``interrupt()``. We deliberately do **not** use ``interrupt_before`` here — the
``interrupt()`` call inside the node is the pause; combining both mechanisms would
pause twice per gate.

The checkpointer is a ``SqliteSaver`` opened by the caller as a context manager
(``from_conn_string`` returns an iterator, not a saver):

    with SqliteSaver.from_conn_string(paths.data_path("cv_agent_checkpoints.sqlite")) as saver:
        app = compile_graph(checkpointer=saver)
"""

from langgraph.graph import END, START, StateGraph

from cv_agent import nodes
from cv_agent.state import CVAgentState

# Node execution order (declaration order == the fixed pipeline).
_NODE_NAMES = (
    "resolve_reference", "refresh_context", "extract_requirements",
    "analyze_and_plan", "analysis_gate", "mark_skipped", "tailor_cv",
    "draft_cover_letter", "draft_recruiter_message", "self_critique",
    "render", "publish", "approval_gate", "file_and_record",
)


def compile_graph(checkpointer):
    """Build and compile the CV agent graph with the given checkpointer.

    ``checkpointer`` must be an open ``SqliteSaver`` (see the module docstring for
    the ``with`` block the caller owns). Returns the compiled graph; the two gate
    nodes pause via ``interrupt()``, so the CLI drives resume with ``Command``.
    """
    g = StateGraph(CVAgentState)

    # Register every node so the graph compiles with the full topology.
    for name in _NODE_NAMES:
        g.add_node(name, getattr(nodes, name))

    # Linear edges (START → … → analysis_gate).
    g.add_edge(START, "resolve_reference")
    g.add_edge("resolve_reference", "refresh_context")
    g.add_edge("refresh_context", "extract_requirements")
    g.add_edge("extract_requirements", "analyze_and_plan")
    g.add_edge("analyze_and_plan", "analysis_gate")

    # Conditional edge out of analysis_gate: abort → archive; else → tailor.
    g.add_conditional_edges(
        "analysis_gate",
        nodes.route_after_analysis,
        {"mark_skipped": "mark_skipped", "tailor_cv": "tailor_cv"},
    )
    g.add_edge("mark_skipped", END)

    # Tailoring chain: tailor → (conditional drafts) → self_critique.
    g.add_edge("tailor_cv", "draft_cover_letter")
    g.add_edge("draft_cover_letter", "draft_recruiter_message")
    g.add_edge("draft_recruiter_message", "self_critique")

    # Conditional edge out of self_critique: bounded revise loop vs render.
    g.add_conditional_edges(
        "self_critique",
        nodes.route_after_critique,
        {"tailor_cv": "tailor_cv", "render": "render"},
    )

    # Render → publish → approval_gate → conditional edge (approve vs bounded reject).
    g.add_edge("render", "publish")
    g.add_edge("publish", "approval_gate")
    g.add_conditional_edges(
        "approval_gate",
        nodes.route_after_approval,
        {"file_and_record": "file_and_record", "tailor_cv": "tailor_cv"},
    )
    g.add_edge("file_and_record", END)

    return g.compile(checkpointer=checkpointer)
