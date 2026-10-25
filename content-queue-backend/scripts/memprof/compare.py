#!/usr/bin/env python
# ruff: noqa: E741
"""usage: python scripts/memprof/compare.py before.json after.json > table.md

Markdown comparison of two run_benchmark.py result files (medians over repetitions)."""

import json
import sys


def med(sc, k):
    s = (sc.get("summary") or {}).get(k)
    return s["median"] if s else None


def rng(sc, k):
    s = (sc.get("summary") or {}).get(k)
    return f"{s['min']:g}-{s['max']:g}" if s else ""


def fmt(v):
    return "-" if v is None else f"{v:,.1f}"


def delta(b, a):
    if b is None or a is None or b == 0:
        return "-"
    return f"{(a - b) / abs(b) * 100:+.0f}%"


def outcome(sc):
    """One-line correctness summary of the worker outcome (first rep) or HTTP result."""
    reps = sc.get("reps") or []
    if not reps:
        return ""
    r = reps[0]
    if "outcome" in r:
        o = r["outcome"]
        if "n_items" in o:
            return (
                f"{o['n_completed']}/{o['n_items']} completed, {o['n_with_chunks']} with chunks ({o['total_chunks']} total), "
                f"{o['n_with_embedding']} embedded, {o['n_with_error']} with error"
            )
        s = f"{o['processing_status']}, words={o['word_count']}, chunks={o['content_chunks']}, emb={o['embedding_present']}"
        if o.get("processing_error"):
            s += f", err={o['processing_error'][:40]!r}"
        if r.get("killed_at_mb"):
            s += f", KILLED@{r['killed_at_mb']:.0f}MB"
        if r.get("pipeline_state") not in (None, "drained"):
            s += f", state={r['pipeline_state']}"
        return s
    h = r.get("http") or {}
    if "requests" in h:
        return f"status {h['statuses']}"
    return ", ".join(f"{k}={h[k]}" for k in ("status", "items", "bytes") if k in h)


def main():
    b, a = (json.load(open(p)) for p in sys.argv[1:3])
    print(f"# Memory benchmark: {b['label']} vs {a['label']}\n")
    for d in (b, a):
        print(
            f"- **{d['label']}**: commit `{d['commit']}`, source hash `{d['source_hash']}`, {d['timestamp']}, "
            f"run {d.get('total_runtime_s', 0) / 60:.0f} min"
        )
    print(
        "\nMB, median over repetitions (range in parentheses). Peak delta = peak RSS above the settled pre-scenario baseline; "
        "settled = macOS footprint 30 s after the work.\n"
    )
    print(
        "| Scenario | peak delta before | peak delta after | change | peak RSS before | peak RSS after | settled fp before | settled fp after | wall s before | wall s after |"
    )
    print("|---|---|---|---|---|---|---|---|---|---|")
    ids = list(dict.fromkeys(list(b["scenarios"]) + list(a["scenarios"])))
    for sid in ids:
        sb, sa = b["scenarios"].get(sid, {}), a["scenarios"].get(sid, {})
        if (
            sb.get("skipped")
            or sa.get("skipped")
            or not sb.get("reps")
            or not sa.get("reps")
        ):
            note = "skipped before" if sb.get("skipped") else ""
            note += (" " if note else "") + (
                "skipped after" if sa.get("skipped") else ""
            )
            if not note:
                note = "missing in " + ("before" if not sb.get("reps") else "after")
            if sb.get("reps") and not sa:
                note = "missing in after"
            print(
                f"| {sid} | {fmt(med(sb, 'peak_delta_mb'))} | {fmt(med(sa, 'peak_delta_mb'))} | {note} | | | | | | |"
            )
            continue
        row = [sid]
        pd_b, pd_a = med(sb, "peak_delta_mb"), med(sa, "peak_delta_mb")
        row += [
            f"{fmt(pd_b)} ({rng(sb, 'peak_delta_mb')})",
            f"{fmt(pd_a)} ({rng(sa, 'peak_delta_mb')})",
            delta(pd_b, pd_a),
        ]
        for k in ("peak_rss_mb", "settled_footprint_mb"):
            row += [fmt(med(sb, k)), fmt(med(sa, k))]
        row += [fmt(med(sb, "wall_s")), fmt(med(sa, "wall_s"))]
        print("| " + " | ".join(row) + " |")
    print("\n## Outcomes (first repetition)\n")
    print("| Scenario | before | after |")
    print("|---|---|---|")
    for sid in ids:
        sb, sa = b["scenarios"].get(sid, {}), a["scenarios"].get(sid, {})
        ob = sb.get("skipped") and f"skipped: {sb['skipped']}" or outcome(sb)
        oa = sa.get("skipped") and f"skipped: {sa['skipped']}" or outcome(sa)
        print(f"| {sid} | {ob} | {oa} |")
    print("\n## Watchdog kills\n")
    kills = [
        (d["label"], sid, r["killed_at_mb"])
        for d in (b, a)
        for sid, sc in d["scenarios"].items()
        for r in sc.get("reps", [])
        if r.get("killed_at_mb")
    ]
    print(
        "\n".join(f"- {l} {sid}: killed at {mb:.0f} MB" for l, sid, mb in kills)
        or "none"
    )


if __name__ == "__main__":
    main()
