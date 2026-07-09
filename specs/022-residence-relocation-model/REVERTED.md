# ⛔ SPEC 022 — REVERTED / SUPERSEDED — DO NOT IMPLEMENT

**Status:** Implemented 2026-07, reverted 2026-07-09 at Jean Claude's request.
The `spec.md` in this folder is retained as history only. Claude Code MUST NOT
implement, re-implement, or reference it as architecture.

## Why it was reverted

The residence/relocation cost model automated **desirability judgment**
(cost sliders, salary floors, per-residence employer lists), which belongs to
the user, not the system. The Settings UI it produced was confusing:
duplicated country lists per residence, semantically void combinations
(an "EU" residence row with a remote work mode), and a broken seed rendering.

The model remains a valid *reasoning* artifact — the conversation that
produced it correctly untangled the Russia/France/Türkiye constraints — but
it should never have become product surface.

## What replaces it (prose path — no code)

Per Constitution Principle II, the same intent is expressed with the
pre-existing `work_mode_geography` plus `scoring_context`, edited directly
in Settings:

- `remote.countries` += Russia (+ Kazakhstan, Georgia, Armenia, Uzbekistan
  if the hh network areas should surface)
- `hybrid.countries` += France (+ Russia if Moscow-hybrid offers should be
  visible)
- `on-site.countries` += France
- `scoring_context` += "Relocation: I would move to France for an exceptional
  Web3 PM role — FR hybrid/on-site acceptable but must be outstanding.
  Russia only remote or hybrid, and only if clearly well paid; be skeptical
  when no salary is stated. Otherwise I stay Swiss-based; remote roles never
  imply relocation."

## Process rule adopted as a result

Any spec touching tracker/settings UI MUST include a visual mockup approved
by the user BEFORE implementation.

## Spec 023 note

Spec 023 (hh_network scraper) originally depended on this spec. It has been
amended to depend only on the prose-path edits above.
