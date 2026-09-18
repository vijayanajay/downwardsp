# CR-2026-001 empirical probes (read-only)

One-shot scripts used before implementing CR-2026-001 to verify its claims and
size its fixes against the production store (`data/db/nse_market.duckdb`,
2.3M bar rows). Kept because every number cited in
`docs/changes/CR-2026-001-*.md` §2bis is reproducible from here:

    .venv/Scripts/python.exe experiments/cr001/empirical_review_cr001.py
    # ...then _b.py and _c.py (each writes its cr001_probe*_output.txt here)

Note: the frozen-anchor numbers in _b.py's E3/E6 predate a window-frame fix in
the probe itself; the corrected, implementation-exact measurement (2,234 full
fires, 1.52%) was re-run ad hoc and is recorded in the CR. The probe scripts
are otherwise unmodified evidence.
