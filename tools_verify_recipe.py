"""Validate one authored recipe JSON file. Usage: python verify_recipe.py <file.json>"""
import io, json, pathlib, re, sys

COURSE = {"Breakfast","Appetizer","Salad","Soup","Main","Side","Dessert","Snack","Drink","Sauce"}
CUISINE = {"Italian","Mexican","Chinese","Japanese","Indian","Thai","Mediterranean",
           "American","French","Korean","Middle Eastern","Other"}
ATTRS = {"Vegetarian","Vegan","Gluten-Free","Quick","One-Pot","Baked","Grilled","No-Cook"}
CATEGORIES = {"Produce","Protein","Dairy","Pantry","Spices","Other"}
HERE = pathlib.Path(__file__).parent


def verify(path):
    errs = []
    raw = io.open(path, encoding="utf-8").read()
    try:
        r = json.loads(raw)
    except Exception as exc:
        return [f"not valid JSON: {exc}"]
    if isinstance(r, list):
        return ["file must contain a single object, not a list"]

    for key in ("video_id","title","channel","thumbnail","duration_seconds","tags",
                "servings","total_time_minutes","ingredients","steps","coverage"):
        if key not in r:
            errs.append(f"missing key: {key}")
    if errs:
        return errs

    vid, dur = r["video_id"], r["duration_seconds"]

    # cross-check header facts against the corpus brief
    brief = HERE / "corpus" / f"{vid}.txt"
    if not brief.exists():
        errs.append(f"no corpus file for video_id {vid}")
    else:
        text = brief.read_text(encoding="utf-8")
        real = int(re.search(r"DURATION_SECONDS: (\d+)", text).group(1))
        if dur != real:
            errs.append(f"duration_seconds {dur} != corpus {real}")
        stamps = {int(m) for m in re.findall(r"^\[(\d+)\]", text, re.M)}
        maxstamp = max(stamps) if stamps else 0
    if r["thumbnail"] != f"https://i.ytimg.com/vi/{vid}/maxresdefault.jpg":
        errs.append("thumbnail URL does not match video_id")

    tags = r["tags"]
    if not any(t in COURSE for t in tags):
        errs.append(f"tags need at least one course: {tags}")
    if sum(1 for t in tags if t in CUISINE) > 1:
        errs.append(f"at most one cuisine tag: {tags}")
    for t in tags:
        if t not in COURSE | CUISINE | ATTRS:
            errs.append(f"tag off-vocabulary: {t!r}")

    for ing in r["ingredients"]:
        for k in ("name","quantity","unit","normalized","category"):
            if k not in ing:
                errs.append(f"ingredient missing {k}: {ing}")
        if ing.get("category") not in CATEGORIES:
            errs.append(f"bad category {ing.get('category')!r} on {ing.get('name')!r}")
    if len(r["ingredients"]) < 3:
        errs.append("suspiciously few ingredients")

    steps = r["steps"]
    if len(steps) < 8:
        errs.append(f"only {len(steps)} steps - too few to cook from")
    starts = [s.get("start_seconds") for s in steps]
    if starts != sorted(starts):
        errs.append("start_seconds not strictly increasing")
    if len(set(starts)) != len(starts):
        errs.append("duplicate start_seconds")

    for i, s in enumerate(steps):
        for k in ("index","text","start_seconds","end_seconds","narration","detail","terms"):
            if k not in s:
                errs.append(f"step {i+1} missing {k}")
        if s.get("index") != i + 1:
            errs.append(f"step at position {i+1} has index {s.get('index')}")
        want_end = starts[i+1] if i + 1 < len(steps) else dur
        if s.get("end_seconds") != want_end:
            errs.append(f"step {i+1} end_seconds {s.get('end_seconds')} should be {want_end}")
        if brief.exists():
            if s.get("start_seconds", 0) > maxstamp:
                errs.append(f"step {i+1} start_seconds beyond the transcript")
            # must be a REAL transcript line stamp, not an interpolated guess
            if s.get("start_seconds") not in stamps:
                errs.append(f"step {i+1} start_seconds {s.get('start_seconds')} is not an "
                            f"actual transcript timestamp")
            for t in s.get("terms") or []:
                if t.get("start_seconds") not in stamps:
                    errs.append(f"step {i+1} term {t.get('term')!r} start_seconds "
                                f"{t.get('start_seconds')} is not an actual transcript timestamp")
        d = s.get("detail")
        if d is not None:
            if not isinstance(d, dict) or "how" not in d or "watch_for" not in d:
                errs.append(f"step {i+1} detail must have how + watch_for, or be null")
        for t in s.get("terms") or []:
            term = (t.get("term") or "").lower()
            if not term:
                errs.append(f"step {i+1} empty term")
            elif not re.search(r"\b" + re.escape(term) + r"\b", s.get("text","").lower()):
                errs.append(f"step {i+1} term {term!r} not present in its text -> renders nothing")

    if steps:
        if starts[0] > 0.4 * dur:
            errs.append(f"late start: first step {starts[0]}s of {dur}s")
        if starts[-1] < 0.55 * dur:
            errs.append(f"stopped early: last step {starts[-1]}s of {dur}s - keep reading")
        gaps = [(b - a, a) for a, b in zip(starts, starts[1:])]
        for g, at in gaps:
            if g > 0.25 * dur:
                errs.append(f"gap of {g}s after {at}s exceeds a quarter of the video")

    cov = r["coverage"]
    if steps and (cov.get("first_step_at") != starts[0] or cov.get("last_step_at") != starts[-1]
                  or cov.get("steps") != len(steps)):
        errs.append("coverage block does not match the steps")

    if not re.fullmatch(r"[\x09\x0a\x0d\x20-\x7e]*", raw):
        bad = sorted({c for c in raw if not (c == "\n" or c == "\t" or 0x20 <= ord(c) <= 0x7e)})
        errs.append(f"non-ASCII characters present: {bad[:8]}")
    return errs


if __name__ == "__main__":
    bad = 0
    for path in sys.argv[1:]:
        errs = verify(path)
        name = pathlib.Path(path).name
        if errs:
            bad += 1
            print(f"FAIL {name}")
            for e in errs:
                print("   -", e)
        else:
            print(f"OK   {name}")
    sys.exit(1 if bad else 0)
