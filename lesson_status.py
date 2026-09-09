"""Gemeinsame Ausfallerkennung für Anzeige und Raumbelegung."""


def has_value(value):
    return str(value or "").strip() not in ("", "-", "--", "---", "–", "—")


def is_cancelled(lesson):
    return (
        not has_value(getattr(lesson, "fach", None))
        and not any(has_value(t) for t in getattr(lesson, "lehrer", ()))
        and not any(has_value(room) for room in getattr(lesson, "räume", ()))
        and bool(str(getattr(lesson, "info", "") or "").strip())
    )


def detail_signature(lesson):
    return (is_cancelled(lesson), getattr(lesson, "änderung", False),
            getattr(lesson, "info", None))
