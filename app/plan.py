"""The plan: the contract between extraction, the local model and the builder.

The model reads the paper form and returns a short Plan (what the form asks for,
in plain language). Code then turns the Plan into the full COI JSON (UUIDs,
techNames, grid, predefined sections, JSONata, validators, dictionaries).

Rules every producer of a Plan must follow:
- Every field, condition and statement carries `quote`: a verbatim fragment of
  the paper text it comes from (pages and sections are design groupings, no quote). Code drops items whose quote is not found in the text.
  Synthesized trigger questions (paper says "fill only if ..." but has no field
  for it) quote that sentence.
- `id`s are short, stable and unique within the plan ("s1", "f3"). Conditions
  reference them.
- The "Dane" page (addressee, applicant, own data, address, other person,
  entity, proxy, stamp duty), the contact + consent page and the finish page are
  NOT in `pages`: code builds them from the COI Standards using `addressee`,
  `applicant` and `statements`. `pages` holds only the subject-matter pages.
- Labels are rewritten per COI Standards ("Głos i ton"): short, plain Polish,
  questions or nouns, compound paper fields split ("imię i nazwisko" -> two fields).
"""

from enum import StrEnum

from pydantic import BaseModel, Field


class FieldType(StrEnum):
    TEXT_FIELD = "TEXT_FIELD"
    TEXTAREA = "TEXTAREA"
    NUMBER = "NUMBER"
    DATEPICKER = "DATEPICKER"
    RADIO = "RADIO"  # one of few options
    CHECKBOX = "CHECKBOX"  # many of options
    SELECT = "SELECT"  # one of many (>5) options
    FILE_UPLOADER = "FILE_UPLOADER"  # attachments ("załączniki")
    TEXT_BLOCK = "TEXT_BLOCK"  # static info shown to the user, no input


class EntityId(StrEnum):
    NIP = "NIP"
    REGON = "REGON"
    KRS = "KRS"


class Condition(BaseModel):
    """Show the target when `field` (a RADIO/CHECKBOX/SELECT id) has any of `options`."""

    field: str
    options: list[str] = Field(min_length=1)
    negate: bool = False  # show when NONE of the options is chosen
    quote: str  # paper sentence that states the dependency


class PlanField(BaseModel):
    id: str
    label: str
    type: FieldType
    required: bool = False
    options: list[str] = []  # only for RADIO/CHECKBOX/SELECT, values as on paper
    description: str = ""  # short hint for the user (plain language), optional
    quote: str
    show_if: Condition | None = None
    synthesized: bool = False  # a Tak/Nie question the conditions pass added (no field on paper)


class Section(BaseModel):
    id: str
    title: str
    repeating: bool = False  # e.g. "kolejny pies", "kolejna nieruchomość"
    description: str = ""  # short intro shown under the section title (plain language), optional
    # The section is a postal address (e.g. "Adres korespondencyjny", "Adres nieruchomości"):
    # code replaces its fields with the Standards address block (Kraj/TERYT dictionaries,
    # foreign variant). List the paper's address lines as plain fields anyway (for quotes).
    address: bool = False
    fields: list[PlanField] = Field(min_length=1)
    show_if: Condition | None = None


class Page(BaseModel):
    title: str
    sections: list[Section] = Field(min_length=1)


class Applicant(BaseModel):
    """Who may file. Drives the predefined sections of the "Dane" page."""

    person: bool = True  # a natural person can file (own data, PESEL...)
    entity: bool = False  # a company/institution can file (NIP/REGON/KRS, "nazwa firmy")
    entity_ids: list[EntityId] = []  # identifiers the paper asks for; empty = Standards default
    pesel: bool = False  # the paper asks for the applicant's PESEL (informational: Twoje dane always has PESEL)
    proxy: bool = True  # filing by a proxy (pełnomocnik) is allowed
    registry_extract: bool = False  # the paper requires a KRS/registry extract from every entity filer


class Statement(BaseModel):
    """A declaration/consent the applicant confirms ("Oświadczam, że ...")."""

    text: str  # rewritten in plain language
    required: bool = True
    quote: str


class Plan(BaseModel):
    title: str  # e-service title, starts with Wniosek/Zgłoszenie/Deklaracja/Oświadczenie, no legal refs
    addressee: str  # e.g. "Wójt Gminy Elbląg", "Urząd Gminy Ulan-Majorat" (no street address); "" when the paper names none
    addressee_quote: str = ""
    applicant: Applicant = Applicant()
    pages: list[Page] = Field(min_length=1)
    statements: list[Statement] = []
