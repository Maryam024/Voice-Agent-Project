"""Makes text sound right when spoken: no ISO dates, no 24-hour clock, no 'Asia/Karachi'."""
import re
from datetime import date

_DASHES = str.maketrans({"\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2019": "'"})
_ISO = re.compile(r"\b(20\d\d)-(\d\d)-(\d\d)(?:T(\d\d):(\d\d))?")
_ZONE = re.compile(r"\b(?:Africa|America|Asia|Atlantic|Australia|Europe|Indian|Pacific)/([A-Z][A-Za-z_]+)\b")
_TIME = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)\b(?!\s*[AaPp]\.?[Mm])")


def _clock(hour, minute):
    h12 = hour % 12 or 12
    ampm = "AM" if hour < 12 else "PM"
    return f"{h12} {ampm}" if int(minute) == 0 else f"{h12}:{minute} {ampm}"


def _iso(m):
    try:
        d = date(int(m[1]), int(m[2]), int(m[3]))
    except ValueError:
        return m[0]
    out = f"{d:%A} {d.day} {d:%B}"
    return f"{out} at {_clock(int(m[4]), m[5])}" if m[4] else out


def spoken(text):
    """'2026-10-16T16:30 Asia/Karachi' -> 'Friday 16 October at 4:30 PM Karachi time'."""
    text = (text or "").translate(_DASHES)
    text = _ISO.sub(_iso, text)
    text = _ZONE.sub(lambda m: m[1].replace("_", " ") + " time", text)
    return _TIME.sub(lambda m: _clock(int(m[1]), m[2]), text)