"""Dictionary candidates (bonus II): paper form text -> lists worth a new shared COI dictionary.

A separate model call after the planner, on the same text. It asks only one question:
which choice lists or closed-set blanks on this form deserve a dictionary that many
offices could share. The answer first reviews every choice list and blank field
(`fields`, so none is skipped), then lists `proposals`. Ollama emits keys in alphabetical
order, so the names keep the review first ("fields" < "proposals", "field" < "kind" < "shared"). The model proposes;
`app.dictionaries.filter_candidates` decides.

    from app.llm.dictionaries import ask_candidates
    candidates = ask_candidates(text)   # list[dict]: name, field, quote, values, reason
"""

from app.llm.client import LLMError, chat_json
from app.llm.planner import prepare_text

SYSTEM = """Jesteś analitykiem e-usług publicznych w Centralnym Ośrodku Informatyki (COI).
Dostajesz tekst pustego papierowego formularza urzędu. Szukasz w nim miejsc, w których warto użyć NOWEGO, WSPÓLNEGO słownika: ustandaryzowanej listy wartości, z której wiele urzędów w Polsce mogłoby korzystać w swoich e-usługach.

Jak działają słowniki COI:
- Słownik to zamknięta lista pozycji (struktura: kod + nazwa). Podpina się go tylko do pola typu lista rozwijana, radio albo checkbox. Użytkownik wybiera pozycję, nie wpisuje tekstu.
- Wspólny słownik daje spójne, porównywalne dane i usuwa różne zapisy tej samej wartości.
- COI MA JUŻ słowniki: Województwo, Powiat, Gmina, Miejscowość (dane adresowe), Kraje (kraj, obywatelstwo, państwo) i Numery kierunkowe (telefon). Nie proponuj niczego, co one pokrywają.

Zaproponuj słownik, gdy na formularzu jest:
1. Lista wyboru (kratki, opcje do zaznaczenia lub skreślenia), której pozycje to kategorie o znaczeniu ogólnym, wynikające z przepisów albo z praktyki wielu urzędów, a nie z organizacji tego jednego formularza. Wtedy w "values" przepisz pozycje DOKŁADNIE tak, jak są na papierze, wszystkie, w tej samej kolejności. Przykład: "Typ szkoły: [ ] podstawowa [ ] liceum [ ] technikum" -> słownik "Typy szkół" z trzema wartościami.
2. Puste pole do wpisania (np. "Nazwa: ____"), w którym poprawna odpowiedź zawsze pochodzi z zamkniętego, znanego zbioru (rejestru, klasyfikacji, katalogu z przepisów), a ludzie wpisują ją dziś na różne sposoby. Wtedy "values" zostaw PUSTE: wartości uzupełni urząd albo COI. Nie dopisuj wartości z własnej wiedzy, nawet jeśli je znasz. Przykłady: "Marka pojazdu: ____" -> słownik "Marki pojazdów"; "Kategoria prawa jazdy: ____" -> słownik "Kategorie prawa jazdy"; w obu "values" puste.

Nie proponuj słownika dla:
- pól z dowolną treścią: imię, nazwisko, nazwa firmy, opis, uzasadnienie, cel, uwagi, adres, numer dokumentu, PESEL, NIP, telefon, e-mail, data, kwota, liczba, powierzchnia;
- pytań Tak/Nie i par typu jest/brak;
- list, które mają sens tylko w tym jednym formularzu (np. "Załącznik A / Załącznik B", wybór jednej z części tego wniosku, oświadczenia, lista załączników);
- danych adresowych, krajów i numerów kierunkowych (te słowniki już są).

Dla każdej propozycji podaj:
- "name": nazwa słownika, rzeczownik w liczbie mnogiej, np. "Rodzaje ...", "Gatunki ...";
- "field": krótka etykieta pola formularza, do którego słownik pasuje;
- "quote": dosłowny fragment tekstu formularza z tym polem (etykieta, nagłówek listy albo zdanie z luką), skopiowany bez zmian;
- "values": pozycje listy przepisane z formularza albo pusta lista dla pustego pola;
- "reason": jedno zdanie, dlaczego to dobry wspólny słownik dla wielu urzędów.

Pracuj w dwóch krokach:
1. "fields": przejdź formularz linia po linii, od PIERWSZEJ do ostatniej linii tekstu, i wypisz KAŻDĄ listę wyboru i KAŻDE puste pole do wpisania (pomiń tylko dane wnioskodawcy, podpis i datę). Listą wyboru są też same kratki "[ ]" z opcjami bez osobnej etykiety, opcje numerowane "1. 2. 3." albo "a) b) c)" oraz warianty do skreślenia rozdzielone "/". Listy na samym początku i na samym końcu formularza też się liczą. Jedna lista to jeden wpis (nie wypisuj jej opcji osobno). Dla każdego podaj "field" (etykieta listy albo nagłówek sekcji, w której stoi), "kind" ("lista" albo "puste_pole") i "shared": czy odpowiedź (pozycja z listy albo wartość wpisywana w puste pole) pochodzi z ogólnego, zamkniętego zbioru, który powtarza się w sprawach innych urzędów w Polsce, bo wynika z przepisu, rejestru albo ogólnej klasyfikacji (true/false). Lista załączników, oświadczeń albo części tego jednego dokumentu to zawsze false.
2. "proposals": słowniki tylko dla pozycji z "shared": true, które spełniają reguły powyżej. Każde pole najwyżej raz. Jeśli żadna pozycja się nie nadaje, zwróć pustą listę "proposals"."""

TASK = "Wskaż kandydatów na nowe wspólne słowniki w tym formularzu."



def _text(limit: int) -> dict:
    return {"type": "string", "maxLength": limit}


# Length caps (enforced by the grammar) keep a looping answer from running into max_tokens.
SCHEMA = {
    "type": "object",
    "properties": {
        "fields": {
            "type": "array",
            "maxItems": 60,
            "items": {
                "type": "object",
                "properties": {
                    "field": _text(160),
                    "kind": {"type": "string", "enum": ["lista", "puste_pole"]},
                    "shared": {"type": "boolean"},
                },
                "required": ["field", "kind", "shared"],
            },
        },
        "proposals": {
            "type": "array",
            "maxItems": 12,
            "items": {
                "type": "object",
                "properties": {
                    "name": _text(120),
                    "field": _text(160),
                    "quote": _text(300),
                    "values": {"type": "array", "maxItems": 80, "items": _text(200)},
                    "reason": _text(400),
                },
                "required": ["name", "field", "quote", "values", "reason"],
            },
        }
    },
    "required": ["fields", "proposals"],
}


def ask_candidates(text: str, model: str | None = None) -> list[dict]:
    """One model call on the paper text -> raw candidates (unfiltered, may be empty)."""
    user = f"Tekst formularza:\n<<<\n{prepare_text(text)}\n>>>\n\n{TASK}"
    try:
        result = chat_json(SYSTEM, user, SCHEMA, model=model, max_tokens=8000, name="dictionaries")
    except LLMError:  # rare runaway answer: one retry with another seed
        result = chat_json(SYSTEM, user, SCHEMA, model=model, max_tokens=8000, seed=7, name="dictionaries")
    items = result.data.get("proposals")
    return [p for p in items if isinstance(p, dict)] if isinstance(items, list) else []
