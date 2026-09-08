"""Guard against overwriting the user's prose in WRITEUP.md.

The failure this exists to prevent: a section rewrite that replaced everything
between two headings, including a paragraph the user had written inside it. The
old check only compared text ABOVE the section being edited, so it could not
see the loss.

Usage
    python check_prose.py snapshot          # take a baseline before editing
    python check_prose.py verify            # after editing, list prose that vanished

"Prose" means any non-empty line that is not a heading, table row, blockquote
(scaffolding is marked with '> **[write this]**'), italic caption, code fence, or
display-math delimiter. Every such line in the baseline must still appear
somewhere in the new file, wherever it has moved to.

Blockquotes and captions run over several lines and only their FIRST line
carries the marker, so paragraphs are classified as a unit. Without that, every
edit to a caption or a scaffold block reports a false loss.
"""
import sys, os, re, json, difflib

DOC = 'README.md'
BASE = '.prose-snapshots/.prose_baseline.json'

PARA_SPLIT = re.compile(r"\n\s*\n")
SKIP_PREFIX = ("|", "#", "!", "$$", ">")


def _is_caption(lines):
    """An italic caption: starts with '*' and the block ends with '*'."""
    first, last = lines[0], lines[-1]
    if not first.startswith("*") or first.startswith("**"):
        return False
    return last.endswith("*") and not last.endswith("**")


def prose_lines(text):
    out = []
    in_fence = False
    for block in PARA_SPLIT.split(text):
        lines = [l.strip() for l in block.splitlines() if l.strip()]
        if not lines:
            continue
        fences = sum(1 for l in lines if l.startswith("```"))
        if in_fence:
            if fences % 2 == 1:
                in_fence = False
            continue
        if fences:
            if fences % 2 == 1:
                in_fence = True
            continue
        if lines[0].startswith(">"):          # scaffolding, whole paragraph
            continue
        if _is_caption(lines):                # figure/table caption
            continue
        for line in lines:
            if line.startswith(SKIP_PREFIX):
                continue
            if line.startswith("*") and line.endswith("*"):   # one-line caption
                continue
            if set(line) <= set("-= "):
                continue
            out.append(" ".join(line.split()))
    return out


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "verify"
    text = open(DOC, encoding="utf-8").read()
    lines = prose_lines(text)

    if cmd == "snapshot":
        os.makedirs(".prose-snapshots", exist_ok=True)
        json.dump(lines, open(BASE, "w", encoding="utf-8"))
        print(f"baseline saved: {len(lines)} prose lines")
        return 0

    if not os.path.exists(BASE):
        print('no baseline; run "python check_prose.py snapshot" first')
        return 2

    old = json.load(open(BASE, encoding="utf-8"))
    joined = " \n ".join(lines)
    missing = [l for l in old if l not in joined]

    if not missing:
        print(f"OK: all {len(old)} baseline prose lines still present "
              f"({len(lines)} now, {len(lines) - len(old):+d})")
        return 0

    print(f"WARNING: {len(missing)} prose line(s) from the baseline are gone.\n")
    for l in missing:
        print("  -", l[:110])
        near = difflib.get_close_matches(l, lines, n=1, cutoff=0.6)
        if near:
            print("    closest now:", near[0][:104])
    print("\nIf these were the user's words, restore them from a .BEFORE.md snapshot.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
