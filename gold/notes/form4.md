# Form 4: Wniosek o wynajęcie lub użyczenie świetlicy wiejskiej (Wójt Gminy Płoskinia)

- One subject page "Informacje o rezerwacji", 4 sections: świetlica i termin, wydarzenie, osoba odpowiedzialna, dane osoby odpowiedzialnej (conditional).
- Applicant: natural person only ("imię i nazwisko", "adres zamieszkania"); no PESEL. `proxy: true` as default (paper silent). Phone -> contact page.
- "wynajęcie/użyczenie* ... * niepotrzebne skreślić" -> RADIO "Czego dotyczy wniosek?" (Wynajęcie / Użyczenie świetlicy).
- "wraz z wyposażeniem" -> s1 description "Świetlicę udostępniamy razem z wyposażeniem." (it describes the section, not the miejscowość field).
- "w terminie od ... do ..." -> two DATEPICKERs; the end date quotes the "do" part. No hours on paper, so none added.
- "Cel wynajęcia (rodzaj uroczystości, np. ...)" -> "Rodzaj wydarzenia" with the paper's examples as hint. "ilość uczestników" -> NUMBER.
- Responsible person: RADIO "Wnioskodawca / Inna osoba"; "Inna osoba" shows Imię, Nazwisko and one required upload "Plik ze zgodą i oświadczeniem osoby odpowiedzialnej" (the paper says to attach both the zgoda and the person's zbiorcze oświadczenie; both named in the description). "pełnoletnia" -> hint.
- The 4 declarations of point 4 -> 4 statements, gender-neutral, saying "wynajmu lub użyczenia" (the paper says only najem but the form covers both); the first names the Regulamin korzystania ze świetlic wiejskich from the header. Sanitary one: "Zobowiązuję się przestrzegać przepisów sanitarnych, BHP i przeciwpożarowych w czasie wydarzenia."
- Dropped: "Załącznik nr 2 do Regulaminu..." header as a field, miejscowość i data, signature.
