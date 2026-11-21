# Form 3: Wniosek o wydanie zaświadczenia (Urząd Miasta i Gminy Młynary)

- One subject page "Zaświadczenie": rodzaj, cel, "Opłata za zaświadczenie" (fee on the last subject page).
- Applicant: natural person only (imię i nazwisko, adres), `pesel: true` (paper asks for PESEL). `proxy: true` as Standards default; paper is silent. Phone -> generated contact page.
- "Proszę o wydanie zaświadczenia o:*" with 5 items -> CHECKBOX (one request can cover several certificates). A mentor could argue RADIO.
- Options rewritten to answer "Czego ma dotyczyć zaświadczenie?" in genitive; "ilość użytków" -> "Powierzchnia użytków" (correct Polish for land area).
- "inne ......" (two lines) -> TEXTAREA shown only when "Inne" is ticked.
- "Zaświadczenie potrzebne jest celem przedłożenia w" -> "Gdzie przedstawisz zaświadczenie?" (required: the purpose also decides fee exemption).
- Fee per convention: the paper states an exemption (art. 2 / art. 7), so RADIO "Czy wydanie zaświadczenia wymaga opłaty skarbowej?" comes first (exemption sentence in its description), then TEXT_BLOCK "Jak zapłacić" (17 zł per copy, before filing, cash or the account from the paper) and FILE_UPLOADER "Plik z potwierdzeniem przelewu", both shown only for Tak.
- Section is "Opłata za zaświadczenie", never "Opłata skarbowa" (reserved for the generated proxy-fee section).
- Not added: "liczba egzemplarzy" and an exemption-basis field (paper has neither).
- Dropped: date, signature, the raw legal wording of point 3 (summarized in the radio description).
