"""cv_agent — the standalone CV + Cover Letter agent (spec 029).

A CLI-only LangGraph agent that turns a single job reference — an existing
`job_id`, a live careers/ATS URL, or pasted posting text — into a tailored CV,
and conditionally a cover letter and/or a recruiter message, through a fixed
pipeline with two human-in-the-loop gates.

It reuses the repo's unified LLM path (`llm.call`) and the deterministic
`.cv_pipeline` render harness, but reads and writes **only** through the FastAPI
v1 API (`core.cv_agent.api_client`) — no direct database access (spec 044, step
6b). It runs independently of the Streamlit tracker.

Run as::

    python -m core.cv_agent.cli <job_id|url> [--letter] [--paste] [--profile <id>]

The checkpointer→interrupt→resume topology in :mod:`cv_agent.graph` is the
project's first real LangGraph agent and its reusable foundation (FR-017).
"""

from core.cv_agent.graph import compile_graph

__all__ = ["compile_graph"]
