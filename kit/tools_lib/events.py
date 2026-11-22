"""German date ranges from the event overview, e.g. `14.01.2027`, `02.–03.02.2027`, `30.06.–02.07.2027`."""
from __future__ import annotations

import re
from datetime import date

# day . [month .] [year]  –  day . month . year   (the first half can leave out month and year)
_RANGE = re.compile(r"(\d{1,2})\.(?:(\d{1,2})\.)?(\d{4})?\s*[–-]\s*(\d{1,2})\.(\d{1,2})\.(\d{4})")
_ONE = re.compile(r"(\d{1,2})\.(\d{1,2})\.(\d{4})")


def parse_range(text: str) -> tuple[date | None, date | None]:
    """(start, end) of a German date or date range; (None, None) when there is no date ("noch offen")."""
    text = (text or "").strip()
    try:
        if m := _RANGE.search(text):
            d1, m1, y1, d2, m2, y2 = m.groups()
            end = date(int(y2), int(m2), int(d2))
            return date(int(y1 or y2), int(m1 or m2), int(d1)), end
        if m := _ONE.fullmatch(text):
            d = date(int(m[3]), int(m[2]), int(m[1]))
            return d, d
    except ValueError:  # e.g. 31.02.
        pass
    return None, None
