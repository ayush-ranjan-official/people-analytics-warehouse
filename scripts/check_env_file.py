"""Fail early, by line number only, if .env has a line the dotenv parsers can't read.

uv and python-dotenv quote the offending text in their warnings, which for .env means printing a
secret. This check runs first and never prints values.
"""

import re
import sys
from pathlib import Path

LINE = re.compile(r"""^(?:export\s+)?[A-Za-z_][A-Za-z0-9_]*=(?:'[^']*'|"(?:[^"\\]|\\.)*"|[^'"\s#][^\s#]*|)\s*(?:#.*)?$""")


def main() -> int:
    path = Path(sys.argv[1] if len(sys.argv) > 1 else ".env")
    if not path.exists():
        return 0
    bad = [n for n, line in enumerate(path.read_text().splitlines(), start=1)
           if line.strip() and not line.lstrip().startswith("#") and not LINE.match(line.strip())]
    if bad:
        print(f"{path}: can't parse line(s) {', '.join(map(str, bad))}. Use KEY='value' with single quotes "
              "around the whole value (values aren't shown here on purpose).")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
