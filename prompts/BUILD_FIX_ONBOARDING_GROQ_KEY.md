# Build plan — Fix: set the Groq key inside the onboarding wizard (+ refresh the in-process scorer client)

One Claude Code prompt, four ordered steps. This is a bug/design fix against the
**already-built** Phase 2 code, not new feature work.

Repo root: `/Users/jeanclaudevd/AI-Suite/job_agent`
Branch: `feat/phase2-onboarding` (where onboarding.py, profile_generator.py,
scorer.generate_json all live).

## The bug

`tracker_views/onboarding.py` `_render_generate()` (around lines 355–365) guards
on `os.getenv("GROQ_API_KEY")` and, when missing, shows:
"Go to **Settings → 🔧 Setup** and add your Groq API key, then return here."
But first-run gating renders ONLY the onboarding page — Settings is unreachable
until onboarding completes, which can't happen without the key. Dead-end loop.

The Groq key is the minimum required to generate a profile, so it must be settable
**in the wizard**. Other keys (DeepSeek/Gemini/Gmail/RapidAPI) stay in Settings.

## The hidden second bug (must fix together)

`scorer.py` builds its client once at import: `client = Groq(api_key=api_key, ...)`
(line 15), and `_call_groq` uses that module-level `client`. The wizard runs
`generate_profile()` **in-process**. So even after a key is written to `.env` and
`os.environ` at runtime, the already-built `client` is still keyless for this
process — generation would fail with an auth error right after "Save key", which is
worse than the current visible warning. `settings.py` `_render_setup()` has the
same latent issue: it sets `os.environ[key]` but never refreshes the scorer client
(it's masked today only because scrape/score run as fresh subprocesses).

Fix: add `scorer.reload_client()` and call it whenever a key is set at runtime.

---

## Prompt — Fix onboarding key entry + in-process client refresh

```
Repo: /Users/jeanclaudevd/AI-Suite/job_agent
Branch: feat/phase2-onboarding

INVESTIGATION FIRST
1. scorer.py: confirm `client = Groq(api_key=api_key, max_retries=0)` at module
   top (~line 15), that `_call_groq` calls `client.chat.completions.create(...)`,
   and that `generate_json` already exists.
2. tracker_views/settings.py: confirm `_upsert_env(key, value)` (~line 35) writes
   ../.env preserving other lines, and that _render_setup's save sites do
   `_upsert_env(...); os.environ[key] = ...; st.success(...); st.rerun()`
   (~lines 130–135) with NO scorer refresh.
3. tracker_views/onboarding.py: confirm _render_generate() (~lines 340–393), the
   dead-end block (~357–365), the in-process call `from profile_generator import
   generate_profile; generate_profile(q, cv_text)`, and the existing import line
   `from tracker_views.shared import ensure_db, get_db, SECTOR_LABELS, COUNTRY_OPTIONS`.

STEP 1 — scorer.py: add a client reloader
Add, just after the client is first built:

    def reload_client() -> None:
        """Rebuild the Groq client from the current environment. Call after a key
        is set at runtime (onboarding / Settings) so the in-process client picks it
        up without restarting Streamlit."""
        global client
        client = Groq(api_key=os.getenv("GROQ_API_KEY") or os.getenv("GROQ_APIKEY"),
                      max_retries=0)

(os is already imported in scorer.py — confirm.)

STEP 2 — tracker_views/shared.py: one secret writer for both pages
Move the env writer out of settings.py into shared.py so the wizard and Settings
share a single, secure writer, and have it refresh the scorer client:

    def env_is_set(key: str) -> bool:
        import os
        return bool(os.getenv(key))

    def upsert_env(key: str, value: str) -> None:
        """Upsert one KEY=VALUE line in repo-root .env, preserving other lines."""
        # (move the existing _upsert_env body here verbatim; the path
        #  os.path.join(os.path.dirname(__file__), "..", ".env") is still correct
        #  because shared.py is also in tracker_views/.)

    def set_secret(key: str, value: str) -> None:
        """Persist a secret to .env AND the current process, then refresh the
        scorer's Groq client so in-process callers see it immediately.
        Never logs or returns the value."""
        value = value.strip()
        upsert_env(key, value)
        import os
        os.environ[key] = value
        try:
            from scorer import reload_client   # local import: avoid load cost/cycle
            reload_client()
        except Exception:
            pass  # scorer import/refresh best-effort; .env+environ already set

Then refactor settings.py: delete its local `_upsert_env`, import
`from tracker_views.shared import set_secret`, and replace each
`_upsert_env(key, v); os.environ[key] = v` save site with `set_secret(key, v)`.
Behaviour is unchanged except the Groq client now refreshes in-process too.

STEP 3 — tracker_views/onboarding.py: set the Groq key in the wizard
Add to the shared import: `set_secret, env_is_set`.
Replace the dead-end block (the `if not os.getenv("GROQ_API_KEY"): st.error(... Go
to Settings → Setup ...) _back_button(3); return`) with an in-wizard key entry:

    if not env_is_set("GROQ_API_KEY"):
        st.info(
            "To write your scoring rubric, the app needs a free Groq API key. "
            "It's used only on your machine."
        )
        st.markdown(
            "Create a free account (no credit card) and generate a key at "
            "[console.groq.com/keys](https://console.groq.com/keys) — it starts "
            "with `gsk_`."
        )
        new_key = st.text_input("Groq API key", type="password",
                                placeholder="gsk_…", label_visibility="collapsed")
        c1, c2 = st.columns([1, 4])
        with c1:
            if st.button("Save key", type="primary"):
                if new_key.strip():
                    set_secret("GROQ_API_KEY", new_key)   # writes .env+environ, reloads client
                    st.success("Key saved.")
                    st.rerun()
                else:
                    st.warning("Paste a key first.")
        _back_button(3)
        return   # do not show Generate until the key is set

Leave the rest of _render_generate() as-is, with one tweak to the failure path:
in the `except Exception` after generate_profile(), if the error text looks like an
auth failure (e.g. contains "401", "invalid", "api key", "unauthorized"), clear
st.session_state's key check by NOT pointing to Settings — instead show "That key
didn't work — re-enter it below." and re-render the key input on the next run
(simplest: drop the "Settings → Setup" wording from that message and call
st.rerun() so the key guard re-evaluates). Keep the generic quota message for
non-auth errors.

STEP 4 — verify Settings parity
Confirm Settings → Setup saving the Groq key now also refreshes the in-process
client (via set_secret from Step 2), so a user who sets the key there before any
scoring run doesn't hit a stale-client auth error in-process either.

DONE WHEN
- With GROQ_API_KEY unset, the wizard's Generate step shows a password field and a
  working link to console.groq.com/keys — NOT a pointer to Settings.
- Pasting a valid key + "Save key" → the key is written to .env and os.environ, the
  scorer client is rebuilt, the page reruns, and "✨ Generate my profile" appears
  and succeeds in the SAME session (no restart, no Settings visit).
- An invalid key surfaces an inline "re-enter" message, not a Settings pointer.
- The key is entered via type="password", never echoed back or logged; it lands
  only in .env (gitignored) and os.environ.
- settings.py has no local _upsert_env; both pages write secrets through
  shared.set_secret. Saving the Groq key in Settings also refreshes the client.
- streamlit run tracker.py launches; pytest stays green.
```

## One-line summary for the commit

> fix(phase2): allow setting the Groq key inside the onboarding wizard and refresh
> the in-process scorer client on runtime key changes (no Settings dead-end).
