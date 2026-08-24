## What this changes

<!-- One or two sentences. Why, not just what. -->

## Checklist

- [ ] `pytest` passes (11 suites)
- [ ] `CHANGELOG.md` updated under `## [Unreleased]`
- [ ] `orderflow/__init__.py` version **untouched** — bumping it is a release, not a PR
- [ ] No session frame, CSV, or `capture_raw.jsonl` content in the diff or the description

## If this touches capture or the archive

- [ ] Any new writer takes the lock (`capture.take_lock`) and heartbeats it
- [ ] Existing archives still replay — the CSV columns are a compatibility surface

## If this adds a panel

- [ ] One class in `panels.py` with `@register`, one line in `app.py`'s `PANEL_MENU`
- [ ] `panels_smoke` covers it automatically; confirm it passes
