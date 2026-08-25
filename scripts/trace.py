"""Walk the live site and dump each page plus its form controls.

A discovery aid, not part of the package: use it to refresh test fixtures or
to re-find selectors after aDIS is redesigned.

    uv run python scripts/trace.py /tmp/voebb-trace
"""

from __future__ import annotations

import sys
from pathlib import Path

from voebb.config import load_credentials
from voebb.session import AdisSession, _attr


def dump(session: AdisSession, out: Path, step: str) -> None:
    page, form = session.page, session.form
    if page is None or form is None:
        raise SystemExit(f"{step}: no page loaded")

    (out / f"{step}.html").write_text(page.decode(), encoding="utf-8")
    print(f"\n===== {step} =====")
    print("url   :", session.url)
    print("action:", form.action)
    print("title :", page.title.get_text(strip=True) if page.title else "?")
    print("hidden:", form.fields)
    for field in page.select("form input"):
        if _attr(field, "type", "text").lower() != "hidden":
            print(
                f"  input type={field.get('type')} name={field.get('name')!r} "
                f"id={field.get('id')!r} value={field.get('value')!r}"
            )
    for link in page.select("a[fld], a[data-fld]"):
        fld = link.get("fld") or link.get("data-fld")
        print(f"  link fld={fld!r} text={link.get_text(strip=True)[:60]!r}")


def main(argv: list[str]) -> int:
    out = Path(argv[1] if len(argv) > 1 else "trace")
    out.mkdir(parents=True, exist_ok=True)

    session = AdisSession()
    session.start()
    dump(session, out, "01-start")

    session.navigate("*SBK")
    dump(session, out, "02-konto")

    credentials = load_credentials()
    session.submit(
        {
            "L#AUSW": credentials.user,
            "LPASSW": credentials.password,
            "LLOGIN": "Anmelden",
        }
    )
    dump(session, out, "03-after-login")

    session.navigate("*SZA")
    dump(session, out, "04-ausleihen")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
