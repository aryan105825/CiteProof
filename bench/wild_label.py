# @exports: main() -> int
# @imports: none
# @env: none
# @schema: CLI python -m bench.wild_label  (reads and rewrites data/wild_labeled.csv; saves after every answer)
# @schema: for each row with an empty label it opens the reference's DOI in your browser, asks valid / corrupted / fabricated / skip / quit, then asks for the evidence URL (Enter keeps the DOI link) and, for corrupted rows, which field is wrong
import csv
import re
import sys
import webbrowser

PATH = "data/wild_labeled.csv"
KEYS = {"v": "valid", "c": "corrupted", "f": "fabricated"}


def _save(rows: list[dict], cols: list[str]) -> None:
    with open(PATH, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=cols)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    with open(PATH, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        cols = list(reader.fieldnames or [])
        rows = list(reader)
    todo = [r for r in rows if not r["label"].strip()]
    if not todo:
        print("No empty labels left.")
        return 0
    print(f"{len(todo)} rows to label. On each page compare the authors, title, venue and year with the reference,")
    print("and check that the DOI leads to this paper.")
    for n, r in enumerate(todo, start=1):
        m = re.search(r"10\.\d{4,9}/\S+", r["raw"])
        link = "https://doi.org/" + m.group(0).rstrip(".,;)") if m else ""
        print(f"\n[{n}/{len(todo)}] {r['id']}\n{r['raw']}\n")
        if link:
            print("opening", link)
            if not webbrowser.open(link):
                print("could not open a browser here; open the link by hand")
        else:
            print("no DOI in this reference: search the title on https://search.crossref.org")
        while True:
            ans = input("[v]alid  [c]orrupted  [f]abricated  [s]kip  [q]uit > ").strip().lower()
            if ans in KEYS or ans in ("s", "q"):
                break
            print("type v, c, f, s or q")
        if ans == "q":
            break
        if ans == "s":
            continue
        url = ""
        while not url:
            url = input(f"evidence URL (Enter = {link or 'a URL is required'}) > ").strip() or link
        note = f"checked by author on {url}"
        if ans == "c":
            wrong = ""
            while not wrong:
                wrong = input("which field is wrong (authors, title, venue, year, DOI)? > ").strip()
            note += f"; wrong: {wrong}"
        r["label"], r["evidence_url"], r["notes"] = KEYS[ans], url, note
        _save(rows, cols)
        print(f"saved {r['id']} as {KEYS[ans]}")
    left = sum(1 for r in rows if not r["label"].strip())
    print(f"\n{left} empty labels left.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
