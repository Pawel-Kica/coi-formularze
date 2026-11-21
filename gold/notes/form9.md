# Form 9: stypendium / nagroda / wyróżnienie sportowe (Zgierz) - gold plan notes

- One e-service "Wniosek o stypendium, nagrodę lub wyróżnienie sportowe": the paper opens with "Rodzaje wniosków: I, II, III" and the parts share most content, so "O co wnioskujesz?" drives SHOW conditions. Dyscypliny only in I; own "Osiągnięcia" in I and II; III (coach) has a REPEATING "Podopieczny trenera" section (paper has 4 fixed slots).
- The paper's "Wnioskodawca: Prezydent Miasta Zgierza / Klub sportowy" choice is dropped: an organ does not file via a citizen e-service, so the filer is always the club and club sections are unconditional.
- Applicant: entity only (club), proxy yes. Reprezentant/pełnomocnik + pełnomocnictwo + opłata skarbowa, club name and "Adres siedziby" map onto the Standards Dane page.
- Dropped: proxy's "Seria i numer dowodu osobistego", phones "(opcjonalnie)" (contact page), "Data sporządzenia wniosku", "Podpis i pieczątka", "4. Klauzula informacyjna", the zarządzenie header.
- KRS odpis is required by the paper for the club -> "Plik z aktualnym odpisem z KRS lub innego rejestru".
- "Korespondencja z urzędem": RADIO Poczta tradycyjna / Skrzynka ePUAP / e-Doręczenia (same spelling as the label); poczta -> siedziba / inny adres, "Inny adres korespondencyjny klubu" is an `address: true` section.
- Candidate: Imię, Nazwisko, Data urodzenia; "Adres zameldowania" is an `address: true` section with the paper's hint as its description; synthesized Tak/Nie RADIO, then "Adres do korespondencji kandydata" (`address: true`).
- Dyscypliny: rodzaje (3) and the list (20 + Inna) are CHECKBOXes, options in sentence case; "Inna dyscyplina" text shown for "Inna". No type-to-discipline mapping on paper, so no filtering.
- Consents (5.x, 6.x) are signed by the candidate/podopieczny or a parent and "Wypełniony formularz zgody przechowuje właściwy administrator" -> no uploads; one required filer statement "Mam podpisane zgody osób, których dotyczy wniosek (lub ich rodziców albo opiekunów prawnych), na przetwarzanie danych i akceptację regulaminu."
- "Opinia klubu o kandydacie" (required TEXTAREA, "Klub, w którym kandydat trenuje, potwierdza jego postawę etyczną i moralną."); additional info optional TEXTAREA.
- Uploads: required "Plik z zaświadczeniami potwierdzającymi osiągnięcia sportowe" (covers podopieczni in III), optional diplomas and club opinion file.
- Addressee "Urząd Miasta Zgierza": the paper names none; a mentor might write "Prezydent Miasta Zgierza" or "Rada Miasta Zgierza".
- No fee for the main request; only stamp duty for a pełnomocnictwo (Dane page).
