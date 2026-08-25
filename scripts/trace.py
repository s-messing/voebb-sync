"""Walk the live site and dump each page + its form controls, for selector discovery."""
import sys, pathlib
from voebb.session import AdisSession

OUT = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "trace")
OUT.mkdir(parents=True, exist_ok=True)

def dump(s, step):
    (OUT / f"{step}.html").write_text(s.page.decode(), encoding="utf-8")
    print(f"\n===== {step} =====")
    print("url   :", s.url)
    print("action:", s.form.action)
    print("title :", s.page.title.get_text(strip=True) if s.page.title else "?")
    print("hidden:", s.form.fields)
    for inp in s.page.select("form input"):
        if (inp.get("type") or "text").lower() not in ("hidden",):
            print(f"  input type={inp.get('type')} name={inp.get('name')!r} id={inp.get('id')!r} value={inp.get('value')!r}")
    for a in s.page.select("a[fld], a[data-fld]"):
        print(f"  link fld={a.get('fld') or a.get('data-fld')!r} text={a.get_text(strip=True)[:60]!r}")

s = AdisSession()
s.start(); dump(s, "01-start")
s.navigate("*SBK"); dump(s, "02-konto")

from voebb.config import load_credentials
c = load_credentials()
s.submit({"L#AUSW": c.user, "LPASSW": c.password, "LLOGIN": "Anmelden"})
dump(s, "03-after-login")
s.navigate("*SZA")
dump(s, "04-ausleihen")
