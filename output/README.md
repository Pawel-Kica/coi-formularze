# E-usługi w formacie COI dla formularzy 1-9

| | Co to jest | Gdzie |
|---|---|---|
| **Surowe** | JSON prosto z naszego API, bajt w bajt, bez ręcznych zmian. Tak działa samo rozwiązanie. | ten folder, `formularz-<N>.json` |
| **Poprawione** | Ten sam JSON po weryfikacji przez nas w edytorze: poprawki, które urzędnik by zrobił. | [`adjusted/`](adjusted) |

- Ile poprawek wymagał każdy formularz i jakich: [`POPRAWKI.md`](POPRAWKI.md). Na formularzach 2 i 3 nie było żadnych, więc obie wersje są równe.
- Formularze 1 i 5 to materiał referencyjny COI, bez oceny sędziów: wersja poprawiona = surowa.
- Wszystkie pliki przechodzą reguły techniczne COI bez żadnego problemu.
