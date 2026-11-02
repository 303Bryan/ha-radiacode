"""Check repository-relative Markdown destinations and heading fragments.

Uses only the standard library. HTTP links are deliberately not requested.
"""
import re
import subprocess
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parent.parent


def prose(text):
    """Remove fenced examples so example links/headings are not interpreted."""
    output = []
    fence = None
    for line in text.splitlines():
        marker = re.match(r"^\s*(`{3,}|~{3,})", line)
        if marker:
            value = marker.group(1)
            if fence is None:
                fence = value
            elif value[0] == fence[0] and len(value) >= len(fence):
                fence = None
            output.append("")
        else:
            output.append(line if fence is None else "")
    return "\n".join(output)


def anchors(path):
    """GitHub-style slugs for ATX headings, including duplicate suffixes."""
    seen = set()
    for heading in re.findall(r"^#{1,6}\s+(.+?)\s*#*\s*$", prose(path.read_text()), re.M):
        heading = re.sub(r"\[([^]]+)\]\([^)]*\)", r"\1", heading)
        slug = re.sub(r"[^\w\- ]", "", heading.lower()).replace(" ", "-")
        candidate = slug
        count = 0
        while candidate in seen:
            count += 1
            candidate = f"{slug}-{count}"
        seen.add(candidate)
    return seen


def main():
    tracked = subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "*.md"],
        cwd=ROOT, text=True,
    ).splitlines()
    failures = []
    checked = 0
    for relative in sorted(set(tracked)):
        source = ROOT / relative
        text = prose(source.read_text())
        # Inline links/images and reference-link definitions.
        targets = re.findall(r"\]\(([^\n)]+)\)", text)
        targets += re.findall(r"^\s*\[[^]]+\]:\s*(\S+)", text, re.M)
        for raw in targets:
            href = raw.split()[0].strip("<>")
            url = urlsplit(href)
            if url.scheme or url.netloc:
                continue
            target = (source.parent / unquote(url.path)).resolve() if url.path else source
            checked += 1
            if not target.is_relative_to(ROOT) or not target.exists():
                failures.append(f"{relative}: missing or outside repository: {href}")
            elif url.fragment and target.suffix == ".md":
                if unquote(url.fragment) not in anchors(target):
                    failures.append(f"{relative}: missing heading: {href}")
    if failures:
        print("\n".join(failures))
        return 1
    print(f"Checked {len(set(tracked))} Markdown files and {checked} local destinations.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
