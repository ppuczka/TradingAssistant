"""Conservative headline relevance for company-name news searches."""

import re
import unicodedata

EXCLUDED = re.compile(
    r"\b(ekstraklas\w*|mecz\w*|pilkar\w*|football\w*|soccer\w*|sport\w*|"
    r"dyktand\w*|konkurs\w*|webinar\w*|szkoleni\w*|promocj\w*|coupon\w*|"
    r"kod rabatowy|konto osobiste|bonus\w*)\b"
)
FINANCIAL = re.compile(
    r"\b(akcj\w*|gield\w*|gpw|wig\w*|dywidend\w*|wynik\w*|zysk\w*|strat\w*|"
    r"przychod\w*|marz\w*|kapital\w*|wycen\w*|notowan\w*|kurs\w*|raport\w*|"
    r"rekomendac\w*|anality\w*|akcjonar\w*|zarzad\w*|prezes\w*|przejec\w*|"
    r"fuzj\w*|kontrakt\w*|zamowien\w*|produkc\w*|sankcj\w*|knf|"
    r"regulac\w*|proces\w*|frankow\w*|odpis\w*|earnings|revenue|profit\w*|"
    r"shares?|stocks?|dividend\w*|valuation|guidance|acquisition|merger|"
    r"contract\w*|orders?|ceo|lawsuit\w*|results)\b"
)


def financially_relevant(title: str, publisher: str = "") -> bool:
    # Publisher names such as StockWatch must not make an unrelated headline relevant.
    headline = title
    if publisher and title.casefold().endswith(f" - {publisher}".casefold()):
        headline = title[: -(len(publisher) + 3)]
    normalized = "".join(
        c
        for c in unicodedata.normalize("NFKD", headline.casefold())
        if not unicodedata.combining(c)
    ).replace("ł", "l")
    return not EXCLUDED.search(normalized) and bool(FINANCIAL.search(normalized))
