"""Predefined pages from the COI Standards: "Dane" (first), contact + consents
(second to last) and the finish page. Texts, techNames and dictionaries mirror the
reference JSONs; the Plan only switches blocks on and off."""

import html
import re

from app.builder import texts
from app.builder.core import (
    Ctx,
    all_of,
    answer_path,
    any_of,
    chosen,
    field,
    negate,
    section,
    tab,
)  # fmt: skip
from app.plan import Applicant, EntityId, Plan, Statement
from app.text import ascii_fold, html_paragraphs

DANE = "Dane"
CONTACT = "Kontakt i zgody"
FINISH = "Strona końcowa"

SELF, OTHER, ENTITY = "Swoim", "Innej osoby", "Osoby prawnej lub jednostki organizacyjnej"
PROXY, REPRESENTATIVE = "Pełnomocnik", "Reprezentant"
YES, NO = "Tak", "Nie"
WHO_TECH = "wybierz_w_czyim_imieniu_wnioskujesz"  # first use on "Dane", so ctx.tech keeps it as is

# Addressee starting with an office holder (any case form) -> "organ", else "urząd".
_ORGAN = re.compile(
    r"^(do\s+)?(pan[a]?\s+|pani\s+)?"
    r"(wojt|burmistrz|prezydent|starost|marszal|rad[ay]\b|dyrektor|kierownik|zarzad|"
    r"komendant|przewodnicz|prezes|naczelnik|sekretarz|skarbnik|wojewod)"
)


def is_organ(addressee: str) -> bool:
    return bool(_ORGAN.match(ascii_fold(addressee).strip().lower()))


def _text(ctx, title, tech, *, required=False, extra_validators=(), default=None, registry=None,
          read_only=False, query=None, description=""):
    """TEXT_FIELD; `registry` is an EXTERNAL_SYSTEM source like "PESEL.name"."""
    extra = {}
    if registry:
        extra = {"defaultValue": registry, "defaultValueType": "EXTERNAL_SYSTEM"}
    elif default is not None:
        extra = {"defaultValue": default}
    validation = (["REQUIRED"] if required else []) + list(extra_validators)
    return field(ctx, "TEXT_FIELD", title, ctx.tech(tech), description=description, validation=validation,
                 read_only=read_only, query=query, extra=extra)


PHONE_PREFIX_DICT = "Numery kierunkowe"  # Słowniki doc; no reference uses it, name by analogy with "Kraje"


def is_phone(f: dict) -> bool:
    """A TEXT_FIELD asking for a phone number (gets the hint and the prefix SELECT)."""
    return f["type"] == "TEXT_FIELD" and "telefon" in ascii_fold(f["title"]).lower()


def phone_prefix(ctx: Ctx, phone: dict) -> dict:
    """Bonus VI: SELECT "Numer kierunkowy" (COI dictionary "Numery kierunkowe"), placed right
    before `phone` with its SHOW condition and its REQUIRED. Default "+48": the Słowniki doc's own
    example item ("np. +48 dla Polski"), written like the Kraje default ["Polska"] (item name in a list)."""
    out = field(ctx, "SELECT", "Numer kierunkowy", ctx.tech("numer_kierunkowy"),
                validation=[v for v in phone["validation"] if v == "REQUIRED"], extra={
                    "dictionaryName": PHONE_PREFIX_DICT, "multiLevelTree": False,
                    "searchEnabled": True, "defaultValue": ["+48"]})
    out["states"] = [dict(st) for st in phone["states"]]
    return out


def _radio(ctx, title, tech, options, query=None):
    return field(ctx, "RADIO", title, ctx.tech(tech), options=options, validation=["REQUIRED"],
                 query=query)


def _file(ctx, title, tech, query=None, required=True):
    return field(ctx, "FILE_UPLOADER", title, ctx.tech(tech),
                 validation=["REQUIRED"] if required else [], query=query)


def _text_block(ctx, tech, html_text, query=None):
    return field(ctx, "TEXT_BLOCK", "", ctx.tech(tech), description=html_text, query=query)


MLPZ_ADDRESS = {k: f"MLPZ.{k}" for k in
                ("country", "place", "postCode", "street", "voivodeship", "building", "apartment")}


def address_section(ctx: Ctx, title: str, section_tech: str, suffix: str, *,
                    registry: dict[str, str] | None, query: str | None, tab_title: str = DANE,
                    description: str = "", register: bool = True) -> dict:
    """Address block per Standards: Kraj, then foreign or Polish fields by country.

    `registry` (the applicant's own address) maps each Standards field to one registry
    attribute (`MLPZ_ADDRESS`, or `registry.PERMANENT_ADDRESS` for "Dane zameldowania"); every
    field is then read-only, so the address cannot be half-edited. TERYT gets only the
    voivodeship, so a read-only Polish "Miejscowość" is added. A source without a country
    (a registered address is always Polish) fixes Kraj to "Polska" and drops the foreign fields.
    `register=False` when the caller already reserved `section_tech` via ctx.tech."""
    if register:
        section_tech = ctx.tech(section_tech)
    kraj = ctx.tech(f"kraj{suffix}")
    poland = chosen("Polska", answer_path(tab_title, section_tech, kraj))
    abroad = negate(poland)
    reg = (lambda attr: registry.get(attr)) if registry else (lambda attr: None)
    ro = bool(registry)  # Dane inicjalne: fields fed from a registry are read-only

    def select_default(attr, plain):  # SELECT prefill: registry attribute or a plain default
        if reg(attr):
            return {"defaultValue": reg(attr), "defaultValueType": "EXTERNAL_SYSTEM"}
        return {"defaultValue": plain}

    fields = [
        field(ctx, "SELECT", "Kraj", kraj, validation=["REQUIRED"], read_only=ro, extra={
            "dictionaryName": "Kraje", "multiLevelTree": False, "searchEnabled": True,
            **select_default("country", ["Polska"])}),
    ]
    if not registry or reg("country"):
        fields += [
            _text(ctx, "Miejscowość", f"miejscowosc{suffix}_zagranica", required=True,
                  registry=reg("place"), read_only=ro, query=abroad),
            _text(ctx, "Kod pocztowy", f"kod_pocztowy{suffix}_zagranica", registry=reg("postCode"),
                  read_only=ro, query=abroad),
            _text(ctx, "Ulica", f"ulica{suffix}_zagranica", registry=reg("street"), read_only=ro,
                  query=abroad),
        ]
    fields += [
        field(ctx, "SELECT", "TERYT_4G_DOWODOWY", ctx.tech(f"wojewodztwo{suffix}"),
              validation=["REQUIRED"], read_only=ro, query=poland, extra={
                  "dictionaryName": "TERYT_4G_DOWODOWY", "multiLevelTree": True,
                  "searchEnabled": True, **select_default("voivodeship", [])}),
    ]
    if registry:
        fields.append(_text(ctx, "Miejscowość", f"miejscowosc{suffix}", required=True,
                            registry=reg("place"), read_only=ro, query=poland))
    fields += [
        _text(ctx, "Kod pocztowy", f"kod_pocztowy{suffix}", required=True,
              extra_validators=["ZIP_CODE"], registry=reg("postCode"), read_only=ro, query=poland),
        _text(ctx, "Ulica", f"ulica{suffix}", registry=reg("street"), read_only=ro, query=poland),
        _text(ctx, "Numer budynku", f"numer_budynku{suffix}", required=True,
              registry=reg("building"), read_only=ro),
        _text(ctx, "Numer lokalu", f"numer_lokalu{suffix}", registry=reg("apartment"), read_only=ro),
    ]
    return section(ctx, title, section_tech, fields, query=query, description=description)


def _who_options(a: Applicant) -> list[str]:
    """Options of "Wybierz, w czyim imieniu wnioskujesz" (one option = the radio is left out)."""
    person = a.person or not a.entity  # nobody allowed makes no sense: default to a person
    who_opts = ([SELF] + ([OTHER] if a.proxy else [])) if person else []
    if a.entity:
        who_opts.append(ENTITY)
    return who_opts


def own_filing(a: Applicant) -> tuple[bool, str | None]:
    """Whether the logged-in person's registry data may feed a subject-page field about the
    applicant: (False, None) never (only an entity files), (True, None) always (the person
    always files in their own name), (True, query) only while "Wybierz, w czyim imieniu
    wnioskujesz" is "Swoim" (`query` = that SHOW condition)."""
    who_opts = _who_options(a)
    if SELF not in who_opts:
        return False, None
    if len(who_opts) == 1:
        return True, None
    return True, chosen(SELF, answer_path(DANE, "podmiot_wniosku", WHO_TECH))


def dane_page(ctx: Ctx, plan: Plan) -> dict:
    """Page 1: addressee, who files, own data, addresses, other person/entity, proxy, fees."""
    a: Applicant = plan.applicant
    who_opts = _who_options(a)
    role_opts = (([PROXY] if a.proxy else []) + [REPRESENTATIVE]) if a.entity else []

    who_tech, role_tech = WHO_TECH, "wybierz_jako_kto_wnioskujesz"

    def who(opt):  # None = always true (radio absent because it has one option)
        return chosen(opt, answer_path(DANE, "podmiot_wniosku", who_tech)) if len(who_opts) > 1 else None

    def role(opt):
        return chosen(opt, answer_path(DANE, "podmiot_wniosku", role_tech)) if len(role_opts) > 1 else None

    sections = []
    organ = is_organ(plan.addressee)
    noun = "organu" if organ else "urzędu"
    head = "Organ" if organ else "Urząd"
    sections.append(section(ctx, f"{head}, do którego jest składany wniosek",
                            ctx.tech(f"{head}_do_ktorego_jest_skladany_wniosek"), [
        _text(ctx, f"Nazwa {noun}", f"nazwa_{noun}", required=True, default=plan.addressee.strip(),
              read_only=True),
    ]))  # fmt: skip

    podmiot = []
    if len(who_opts) > 1:
        podmiot.append(_radio(ctx, "Wybierz, w czyim imieniu wnioskujesz", who_tech, who_opts))
    if len(role_opts) > 1:
        podmiot.append(_radio(ctx, "Wybierz, jako kto wnioskujesz", role_tech, role_opts,
                              query=who(ENTITY)))
    if podmiot:
        sections.append(section(ctx, "Podmiot wniosku", ctx.tech("podmiot_wniosku"), podmiot))

    # Standards (they win over the trimmed references): Imię, Nazwisko, PESEL, all from MLPZ, read-only.
    own = [
        _text(ctx, "Imię", "imie", required=True, registry="MLPZ.name", read_only=True),
        _text(ctx, "Nazwisko", "nazwisko", required=True, registry="MLPZ.surname", read_only=True),
        _text(ctx, "Numer PESEL", "numer_pesel", required=True, extra_validators=["PESEL"],
              registry="MLPZ.pesel", read_only=True),
    ]
    sections.append(section(ctx, "Twoje dane", ctx.tech("twoje_dane"), own))
    sections.append(address_section(ctx, "Adres zamieszkania", "adres_zamieszkania", "",
                                    registry=MLPZ_ADDRESS, query=None))

    if a.entity:
        on_entity = who(ENTITY)
        ids = a.entity_ids or [EntityId.NIP, EntityId.REGON, EntityId.KRS]
        ent = [_text(ctx, "Nazwa", "nazwa", required=True)]
        if EntityId.NIP in ids:
            ent.append(_text(ctx, "NIP", "nip", required=True, extra_validators=["NIP"]))
        if EntityId.REGON in ids:
            ent.append(_text(ctx, "REGON", "regon", required=True, extra_validators=["REGON"]))
        if EntityId.KRS in ids:
            ent.append(_text(ctx, "Numer KRS", "numer_krs"))
        sections.append(section(ctx, "Dane podmiotu", ctx.tech("dane_podmiotu"), ent, query=on_entity))
        sections.append(address_section(ctx, "Adres siedziby", "adres_siedziby", "_siedziba", registry=None,
                                 query=on_entity))
        rep_title = "Odpis z KRS lub innego rejestru" if a.registry_extract else "Potwierdzenie, że jesteś reprezentantem"
        sections.append(section(
            ctx, rep_title, ctx.tech("potwierdzenie_ze_jestes_reprezentantem"),
            [_file(ctx, "Plik z odpisem danych", "plik_z_odpisem_danych")],
            description=texts.REGISTRY_EXTRACT_DESC if a.registry_extract else texts.REPRESENTATIVE_DESC,
            query=on_entity if a.registry_extract else all_of(on_entity, role(REPRESENTATIVE))))

    if OTHER in who_opts:
        on_other = who(OTHER)
        sections.append(section(ctx, "Dane innej osoby", ctx.tech("dane_innej_osoby"), [
            _text(ctx, "Imię", "imie_inna_osoba", required=True),
            _text(ctx, "Nazwisko", "nazwisko_inna_osoba", required=True),
            _text(ctx, "Numer PESEL", "numer_pesel_inna_osoba", required=True, extra_validators=["PESEL"]),
        ], query=on_other))  # fmt: skip
        sections.append(address_section(ctx, "Adres zamieszkania innej osoby",
                                 "adres_zamieszkania_osoby_ktorej_jestes_pelnomocnikiem", "_inna_osoba",
                                 registry=None, query=on_other))

    if a.proxy:
        entity_proxy = all_of(who(ENTITY), role(PROXY)) if a.entity else None
        triggers = ([who(OTHER)] if OTHER in who_opts else []) + ([entity_proxy] if a.entity else [])
        sections.append(section(
            ctx, "Pełnomocnictwo", ctx.tech("pelnomocnictwo"),
            [_file(ctx, "Plik dokumentu z pełnomocnictwem", "plik_dokumentu_z_pelnomocnictwem")],
            description=texts.PROXY_DESC, query=any_of(*triggers)))
        title = html.escape(plan.title, quote=False)
        if OTHER in who_opts:
            fee_tech = ctx.tech("oplata_skarbowa_pelnomocnictwo_1")
            fee_q = ctx.tech("czy_zlozenie_pelnomocnictwa_wymaga_oplaty_skarbowej")
            paid = chosen(YES, answer_path(DANE, fee_tech, fee_q))
            sections.append(section(ctx, "Opłata skarbowa", fee_tech, [
                field(ctx, "RADIO", "Czy złożenie pełnomocnictwa wymaga opłaty skarbowej?", fee_q,
                      options=[YES, NO], validation=["REQUIRED"]),
                _text_block(ctx, "oplata_info_1", texts.FEE_PERSON_INFO.replace("{TITLE}", title), query=paid),
                _file(ctx, "Plik z potwierdzeniem przelewu", "plik_z_potwierdzeniem_przelewu", query=paid),
            ], description=texts.FEE_PERSON_SECTION_DESC, query=who(OTHER)))  # fmt: skip
        if a.entity:
            sections.append(section(ctx, "Opłata skarbowa za pełnomocnictwo",
                                    ctx.tech("oplata_skarbowa_pelnomocnictwo_2"), [
                _text_block(ctx, "oplata_info", texts.FEE_ENTITY_INFO.replace("{TITLE}", title)),
                _file(ctx, "Plik z potwierdzeniem przelewu", "plik_z_potwierdzeniem_przelewu"),
            ], query=entity_proxy))  # fmt: skip
    return tab(ctx, DANE, sections)


def contact_page(ctx: Ctx, statements: list[Statement]) -> dict:
    """Second to last page: contact data + consent section (RODO + the paper's statements)."""
    phone = _text(ctx, "Numer telefonu", "numer_telefonu", registry="MLPZ.phoneNumber",
                  description=html_paragraphs(texts.PHONE_HINT_TEXT))
    contact = section(ctx, "Dane do kontaktu", ctx.tech("dane_do_kontaktu"), [
        phone_prefix(ctx, phone),
        phone,
        _text(ctx, "Adres e-mail", "adres_e_mail", extra_validators=["EMAIL"], registry="MLPZ.email"),
    ], description=texts.CONTACT_DESC)  # fmt: skip
    consents = [{"description": texts.RODO_CONSENT, "required": True}]
    consents += [{"description": html_paragraphs(s.text), "required": s.required} for s in statements]
    title, tech = ("Zgody i oświadczenia", "zgody_i_oswiadczenia") if statements else ("Zgoda", "zgoda_wyrazenie")
    consent = section(ctx, title, ctx.tech(tech), [
        field(ctx, "CONSENT_SECTION", "", ctx.tech("zgoda"),
              extra={"consentSectionConfig": {"consents": consents, "files": []}}),
    ])  # fmt: skip
    return tab(ctx, CONTACT, [contact, consent])


def finish_page(ctx: Ctx) -> dict:
    """Last page: preview text only (no inputs, as the rules require)."""
    preview = section(ctx, "Podgląd uzupełnionego wniosku", ctx.tech("podglad_uzupelnionego_wniosku"),
                      [_text_block(ctx, "podglad_1", texts.PREVIEW_TEXT)])
    return tab(ctx, FINISH, [preview], finish=True)
