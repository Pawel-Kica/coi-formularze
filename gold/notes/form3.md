# Form 3: Wniosek o wydanie zaświadczenia (Urząd Miasta i Gminy Młynary)

- One subject page "Zaświadczenie": rodzaj, cel, opłata skarbowa (fee on the last subject page per contract).
- Applicant: natural person only (imię i nazwisko, adres), `pesel: true` (paper asks for PESEL). `proxy: true` as Standards default; paper is silent. Phone -> generated contact page.
- "Proszę o wydanie zaświadczenia o:*" with 5 arrow items -> CHECKBOX (one request can cover several certificates; the "*" has no footnote, likely "zaznacz właściwe"). A mentor could argue RADIO (one certificate per request); I chose CHECKBOX because nothing forbids several and the fee is per copy anyway.
- Options rewritten to answer "Czego ma dotyczyć zaświadczenie?" in genitive; "ilość użytków" -> "Powierzchnia użytków" (correct Polish for land area). Matching against model output should be fuzzy here.
- "inne ......" (two lines) -> TEXTAREA shown only when "Inne" is ticked.
- "Zaświadczenie potrzebne jest celem przedłożenia w" -> "Gdzie przedstawisz zaświadczenie?" (required: the purpose also decides fee exemption).
- Fee 17 zł per copy -> TEXT_BLOCK (amount, pay before filing, cash or the bank account from the paper, exemption articles) + FILE_UPLOADER "Potwierdzenie zapłaty".
- Judgment call: synthesized RADIO "Czy musisz zapłacić opłatę skarbową?" (quote: the exemption sentence) gating the uploader, mirroring the Standards proxy-fee pattern. Without it exempt applicants would be forced to upload something.
- Not added: "liczba egzemplarzy" (fee is per copy, but the paper has no field for it). A mentor might add it.
- Dropped: date, signature, the raw legal wording of point 3 (summarized in the TEXT_BLOCK).
