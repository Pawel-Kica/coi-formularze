# Poprawki urzędnika

Metoda: każdą e-usługę oceniło niezależnie trzech sędziów AI, patrząc na papierowy formularz i na naszą e-usługę; poprawka liczy się, gdy wypisało ją 2 z 3. Sędziów sprawdziliśmy na wpisanych celowo błędach: formularz 8 znalezione 10 z 10, formularz 7 9 z 10, formularz 3 4 z 4 w elementach z papieru, zmyślone 0.

Przed nocnymi zmianami: 19 poprawek na tych samych 7 formularzach, wtedy na jednym pliku na formularz, wybranym po zgodności ze wzorcem. Po: 13 (tak samo wybrane pliki dziś: 14).

Dla każdego formularza oceniliśmy dwa pliki z najlepszym wynikiem automatycznego porównania ze wzorcem i zostawiliśmy ten, który wymaga mniej poprawek.

| Formularz | Plik źródłowy | Poprawki urzędnika | Według kategorii |
| --- | --- | --- | --- |
| 2. Wniosek o zwrot opłaty skarbowej | `Wniosek o zwrot opłaty skarbowej.pdf` | 0 | - |
| 3. Wniosek o wydanie zaświadczenia | `Wniosek o wydanie zaświadczenia.jpeg` | 0 | - |
| 4. Wniosek o wynajęcie lub użyczenie świetlicy wiejskiej | `Wniosek o wynajęcie świetlicy.doc` | 1 | brakujące pole 1 |
| 6. Wniosek o zezwolenie na zajęcie pasa drogowego | `Wniosek o wydanie zezwolenia na zajęcie pasa drogowego.pdf` | 4 | złe „wymagane” 4 |
| 7. Wniosek o zezwolenie na psy ras agresywnych | `Wniosek o wydanie zezwolenia na hodowanie lub utrzymywanie psów ras uznawanychza agresywne.JPE` | 1 | brakujące pole 1 |
| 8. Deklaracja o wysokości opłaty za odpady | `Deklaracja o wysokości opłaty.jpeg` | 3 | brakujący warunek 2, brakujące pole 1 |
| 9. Wniosek o stypendium, nagrodę lub wyróżnienie sportowe | `Wniosek o przyznanie stypendium.ods` | 4 | brakujący warunek 2, brakujące pole 2 |
| razem | | 13 | brakujące pole 5, brakujący warunek 4, złe „wymagane” 4 |

Formularze 1 i 5 to materiał referencyjny COI (mają wzorcowe JSON-y od organizatorów), więc nie przechodziły oceny sędziów. Dla kompletu dołączyliśmy ich JSON z API, zrobiony z pliku PDF, bez poprawek: `adjusted/formularz-1.json` i `adjusted/formularz-5.json` są równe surowym.

## Formularz 2
Bez poprawek.

## Formularz 3
Bez poprawek.

## Formularz 4
- Brakujące pole: plik ze zbiorczym oświadczeniem osoby odpowiedzialnej (papier, pkt 4: „w przypadku innej osoby niż wnioskodawca, zbiorcze oświadczenie tej osoby załączyć do wniosku”). Dodany jako wymagany załącznik w sekcji „Dane osoby odpowiedzialnej”, widocznej przy „Inna osoba”.

## Formularz 6
- Złe „wymagane”: jezdnia przy zajęciu do 20% szerokości, długość, szerokość i powierzchnia już niewymagane (wypełnia się tylko wariant, który dotyczy wnioskodawcy).
- Złe „wymagane”: to samo dla jezdni przy zajęciu powyżej 20% do 50% szerokości.
- Złe „wymagane”: to samo dla jezdni przy zajęciu powyżej 50% szerokości.
- Złe „wymagane”: to samo dla chodnika.

## Formularz 7
- Brakujące pole: adres poprzedniego właściciela psa. Dodane pole tekstowe obok imienia i nazwiska poprzedniego właściciela, tak jak one niewymagane.

## Formularz 8
- Brakujące pole: data wystąpienia zmiany danych przy korekcie deklaracji. Dodane pole daty, wymagane, widoczne przy „Korekta złożonej deklaracji”.
- Brakujący warunek: sekcja D.2 „Miejsce gromadzenia odpadów” widoczna tylko dla nieruchomości z budynkiem wielorodzinnym. Sekcja przeszła pod pytanie o rodzaj nieruchomości, od którego zależy.
- Brakujący warunek: sekcja E.1 „Obliczenie opłaty dla nieruchomości” ukryta, gdy nieruchomość ma przydomowy kompostownik (wtedy liczy się E.2).

## Formularz 9
- Brakujące pole: seria i numer dowodu osobistego pełnomocnika klubu. Dodane w sekcji „Pełnomocnictwo”, widocznej tylko dla pełnomocnika.
- Brakujący warunek: opis osiągnięć sportowych kandydata tylko przy stypendium lub nagrodzie (wniosek o wyróżnienie trenera nie ma tej rubryki).
- Brakujący warunek: plik z zaświadczeniami Polskiego Związku Sportowego tylko przy stypendium lub nagrodzie (przy wyróżnieniu papier wymaga zaświadczeń podopiecznych, a to pole już jest).
- Brakujące pole: imię i nazwisko rodzica lub opiekuna prawnego niepełnoletniego kandydata. Dodane razem z pytaniem „Czy kandydat jest niepełnoletni?”, bo pole pokazuje się tylko przy „Tak”.
