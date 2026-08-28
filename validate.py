"""Coverage validation: the completeness guarantee.

Pure functions. The four checks from the spec, the repair-window picker, and
the merge/re-sort/renumber that follows a repair call.
"""
import logging

from prompts import ALL_TAGS, COURSE_TAGS, CUISINE_TAGS

log = logging.getLogger("cookalong.validate")

LATE_START_RATIO = 0.4
EARLY_FINISH_RATIO = 0.7
MAX_GAP_RATIO = 0.25
SEEK_LEAD_SECONDS = 2      # start playback just before the action
DEDUPE_WINDOW = 5          # two steps this close are the same step


def normalize_tags(tags):
    """Fixed vocabularies only. Off-list tags are dropped, cuisine capped at one."""
    seen, kept, cuisines = set(), [], 0
    for tag in tags or []:
        if tag not in ALL_TAGS or tag in seen:
            continue
        if tag in CUISINE_TAGS:
            if cuisines:
                continue
            cuisines += 1
        seen.add(tag)
        kept.append(tag)
    if not any(t in COURSE_TAGS for t in kept):
        kept.insert(0, "Main")   # course is required; Main is the safe default
    return kept


def finalize_steps(steps, duration_seconds):
    """Sort, clamp, dedupe, renumber, and fill end_seconds from the next start."""
    cleaned = []
    for step in steps or []:
        text = (step.get("text") or "").strip()
        if not text:
            continue
        start = max(0, int(step.get("start_seconds") or 0) - SEEK_LEAD_SECONDS)
        if duration_seconds:
            start = min(start, duration_seconds)
        terms = [
            {
                "term": t.get("term", ""),
                "start_seconds": max(0, int(t.get("start_seconds") or 0)
                                     - SEEK_LEAD_SECONDS),
            }
            for t in (step.get("terms") or [])
            if (t.get("term") or "").strip()
        ]
        cleaned.append({
            "text": text,
            "start_seconds": start,
            "narration": (step.get("narration") or text).strip(),
            "detail": step.get("detail") or None,
            "terms": terms,
        })

    cleaned.sort(key=lambda s: s["start_seconds"])

    deduped = []
    for step in cleaned:
        if deduped and step["start_seconds"] - deduped[-1]["start_seconds"] < DEDUPE_WINDOW:
            continue
        deduped.append(step)

    for i, step in enumerate(deduped):
        step["index"] = i + 1
        step["end_seconds"] = (
            deduped[i + 1]["start_seconds"] if i + 1 < len(deduped)
            else (duration_seconds or step["start_seconds"])
        )
    return deduped


def coverage(steps, duration_seconds):
    """-> (report, repair_window|None). Logs which check tripped."""
    report = {
        "first_step_at": steps[0]["start_seconds"] if steps else 0,
        "last_step_at": steps[-1]["start_seconds"] if steps else 0,
        "steps": len(steps),
    }
    if not steps or not duration_seconds:
        return report, None

    problems = []   # (uncovered_span, window_start, window_end, label)

    first, last = report["first_step_at"], report["last_step_at"]
    if first > LATE_START_RATIO * duration_seconds:
        problems.append((first, 0, first, "late start"))
    if last < EARLY_FINISH_RATIO * duration_seconds:
        problems.append((duration_seconds - last, last, duration_seconds, "early finish"))

    max_gap = MAX_GAP_RATIO * duration_seconds
    for a, b in zip(steps, steps[1:]):
        gap = b["start_seconds"] - a["start_seconds"]
        if gap > max_gap:
            problems.append((gap, a["start_seconds"], b["start_seconds"], "gap"))

    if not problems:
        return report, None

    # One repair call only - spend it on the largest uncovered window.
    span, start, end, label = max(problems, key=lambda p: p[0])
    log.warning(
        "coverage check failed: %s (%ss uncovered, window %s-%ss, duration %ss)",
        label, span, start, end, duration_seconds,
    )
    return report, {"start": start, "end": end, "reason": label}


def merge_steps(existing, repaired, duration_seconds):
    """Merge repair output back in, then re-sort and renumber."""
    return finalize_steps(list(existing) + list(repaired or []), duration_seconds)
