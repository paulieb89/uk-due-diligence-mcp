"""
models.py — Pydantic v2 output models for uk-due-diligence-mcp tools.

Wrapper + item models returned by the 11 tools in this server. Each
domain has its own section. Every Field has a description so the
auto-generated outputSchema advertised by FastMCP is self-documenting.

Added as part of the Phase 2 return-type migration: tools now return
`-> PydanticModel` directly instead of `-> str + json.dumps(...)`.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

BASE_CFG = ConfigDict(str_strip_whitespace=True)


# =============================================================================
# Companies House
# =============================================================================


class CompanySearchItem(BaseModel):
    """A single entry in a Companies House search result."""

    model_config = BASE_CFG

    company_number: str | None = Field(
        None, description="Companies House company number (e.g. '12345678')."
    )
    title: str | None = Field(
        None, description="Registered company name."
    )
    company_status: str | None = Field(
        None,
        description="Company status (e.g. 'active', 'dissolved', 'liquidation').",
    )
    company_type: str | None = Field(
        None,
        description="Companies House company type code (e.g. 'ltd', 'plc', 'llp').",
    )
    date_of_creation: str | None = Field(
        None, description="Incorporation date in ISO format (YYYY-MM-DD)."
    )
    sic_codes: list[str] = Field(
        default_factory=list,
        description="Standard Industrial Classification codes associated with the company.",
    )
    address: dict[str, Any] = Field(
        default_factory=dict,
        description="Registered office address as returned by the Companies House API.",
    )
    description: str | None = Field(
        None,
        description="Short upstream description (usually number + status + creation date).",
    )


class CompanySearchResult(BaseModel):
    """Paginated result of a Companies House company search."""

    model_config = BASE_CFG

    query: str = Field(..., description="The query string that was searched.")
    total_results: int = Field(
        ..., description="Total matching companies in Companies House (server-side)."
    )
    start_index: int = Field(
        ...,
        description="Number of results skipped before this page (upstream start_index).",
    )
    items_per_page: int = Field(
        ..., description="Page size requested from the API for this call."
    )
    returned: int = Field(
        ..., description="Number of items actually returned on this page."
    )
    has_more: bool = Field(
        ...,
        description=(
            "True if more results exist beyond this page. Re-call with "
            "start_index=start_index+items_per_page to fetch the next page."
        ),
    )
    items: list[CompanySearchItem] = Field(
        default_factory=list,
        description=(
            "Matching companies. Use the `company_number` field to call "
            "company_profile, company_officers, or company_psc for full detail."
        ),
    )


class CompanyAccountsSummary(BaseModel):
    """Summary of a company's accounts filing status."""

    model_config = BASE_CFG

    overdue: bool = Field(
        False,
        description="True if accounts are past their due date at Companies House.",
    )
    last_accounts_made_up_to: str | None = Field(
        None,
        description="ISO date the most recent filed accounts were made up to.",
    )
    next_due: str | None = Field(
        None, description="ISO date the next set of accounts is due."
    )


class CompanyConfirmationStatementSummary(BaseModel):
    """Summary of a company's confirmation statement filing status."""

    model_config = BASE_CFG

    overdue: bool = Field(
        False,
        description="True if the confirmation statement is past its due date.",
    )
    next_due: str | None = Field(
        None, description="ISO date the next confirmation statement is due."
    )


class CompanyProfile(BaseModel):
    """Full Companies House profile for a single company number."""

    model_config = BASE_CFG

    company_number: str = Field(..., description="Companies House company number.")
    company_name: str | None = Field(None, description="Registered company name.")
    company_status: str | None = Field(
        None, description="Current status (active, dissolved, in liquidation, etc.)."
    )
    company_type: str | None = Field(
        None, description="Companies House company type code."
    )
    date_of_creation: str | None = Field(
        None, description="Incorporation date (ISO YYYY-MM-DD)."
    )
    sic_codes: list[str] = Field(
        default_factory=list,
        description="Standard Industrial Classification codes.",
    )
    registered_office_address: dict[str, Any] = Field(
        default_factory=dict,
        description="Registered office address as returned by Companies House.",
    )
    has_charges: bool = Field(
        False,
        description=(
            "True if the company has outstanding registered charges (secured debt), "
            "derived from the /charges endpoint. A due diligence signal."
        ),
    )
    accounts: CompanyAccountsSummary = Field(
        default_factory=CompanyAccountsSummary,
        description="Accounts filing status and due dates.",
    )
    confirmation_statement: CompanyConfirmationStatementSummary = Field(
        default_factory=CompanyConfirmationStatementSummary,
        description="Confirmation statement filing status and next due date.",
    )


class CompanyOfficer(BaseModel):
    """A single officer (director, secretary, etc.) of a company."""

    model_config = BASE_CFG

    name: str | None = Field(None, description="Officer name as recorded at CH.")
    officer_role: str | None = Field(
        None,
        description="Officer role (e.g. 'director', 'secretary', 'llp-member').",
    )
    appointed_on: str | None = Field(
        None, description="Date of appointment (ISO YYYY-MM-DD)."
    )
    resigned_on: str | None = Field(
        None,
        description="Date of resignation if resigned, otherwise null.",
    )
    nationality: str | None = Field(None, description="Declared nationality.")
    country_of_residence: str | None = Field(
        None, description="Declared country of residence."
    )
    occupation: str | None = Field(None, description="Declared occupation.")
    date_of_birth: dict[str, Any] = Field(
        default_factory=dict,
        description="Partial date of birth (month/year) as returned by CH.",
    )
    appointment_count: int | None = Field(
        None,
        description=(
            "Total number of other active appointments held by this officer, or null "
            "if unavailable. The Companies House officer list endpoint does not include "
            "this count; a separate per-officer call is required to populate it."
        ),
    )
    address: dict[str, Any] = Field(
        default_factory=dict,
        description="Officer correspondence address.",
    )
    links: dict[str, Any] = Field(
        default_factory=dict,
        description="Upstream relational links (e.g. officer profile URL).",
    )


class CompanyOfficersResult(BaseModel):
    """List of officers for a given company."""

    model_config = BASE_CFG

    company_number: str = Field(..., description="Companies House company number.")
    include_resigned: bool = Field(
        ...,
        description="Whether resigned officers were included in this result.",
    )
    total: int = Field(
        ..., description="Total officers returned (filtered by include_resigned)."
    )
    high_appointment_count_flag: int | None = Field(
        None,
        description=(
            "Number of active officers with 10+ total appointments, or null "
            "if appointment counts were not fetched. Non-zero values are a "
            "nominee/phoenix director risk signal."
        ),
    )
    officers: list[CompanyOfficer] = Field(
        default_factory=list,
        description="Officer records.",
    )


class CompanyPSCEntry(BaseModel):
    """A single Person with Significant Control record."""

    model_config = BASE_CFG

    kind: str | None = Field(
        None,
        description=(
            "Upstream 'kind' (e.g. 'individual-person-with-significant-control', "
            "'corporate-entity-person-with-significant-control')."
        ),
    )
    name: str | None = Field(None, description="PSC name (individual or entity).")
    notified_on: str | None = Field(
        None, description="Date notified as a PSC (ISO YYYY-MM-DD)."
    )
    ceased_on: str | None = Field(
        None,
        description="Date PSC status ceased, if applicable.",
    )
    nationality: str | None = Field(
        None, description="Declared nationality for individual PSCs."
    )
    country_of_residence: str | None = Field(
        None,
        description="Declared country of residence for individual PSCs.",
    )
    natures_of_control: list[str] = Field(
        default_factory=list,
        description=(
            "List of 'nature of control' descriptors (e.g. "
            "'ownership-of-shares-75-to-100-percent'). Individual entries may "
            "be truncated to 300 characters each."
        ),
    )
    identification: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Identification block for corporate PSCs: place_registered, "
            "registration_number, country_registered, legal_authority, etc."
        ),
    )
    address: dict[str, Any] = Field(
        default_factory=dict,
        description="PSC correspondence address.",
    )


class CompanyPSCResult(BaseModel):
    """List of Persons with Significant Control for a company."""

    model_config = BASE_CFG

    company_number: str = Field(..., description="Companies House company number.")
    total: int = Field(
        ..., description="Total PSC entries returned for this company."
    )
    overseas_corporate_psc_flag: int = Field(
        0,
        description=(
            "Number of corporate PSCs registered outside the UK. Non-zero "
            "values indicate an offshore beneficial ownership chain."
        ),
    )
    psc: list[CompanyPSCEntry] = Field(
        default_factory=list,
        description="Persons with Significant Control records.",
    )
    note: str | None = Field(
        None,
        description=(
            "Explanatory note when total=0. Typical for widely-held listed PLCs "
            "where no single person or entity holds 25%+ of shares or voting rights."
        ),
    )


# =============================================================================
# Disqualified directors
# =============================================================================


class DisqualifiedSearchItem(BaseModel):
    """A single hit in a disqualified officers search."""

    model_config = BASE_CFG

    officer_id: str | None = Field(
        None,
        description=(
            "Companies House officer ID extracted from the self link. Pass to "
            "disqualified_profile for the full disqualification record."
        ),
    )
    title: str | None = Field(
        None, description="Display title (typically the officer's name)."
    )
    date_of_birth: str | None = Field(
        None, description="Date of birth as returned by the search API."
    )
    snippet: str | None = Field(
        None, description="Upstream match snippet highlighting query terms."
    )
    address: dict[str, Any] = Field(
        default_factory=dict,
        description="Last known address of the disqualified officer.",
    )
    links: dict[str, Any] = Field(
        default_factory=dict,
        description="Upstream relational links (self, etc.).",
    )


class DisqualifiedSearchResult(BaseModel):
    """Paginated disqualified officers search result."""

    model_config = BASE_CFG

    query: str = Field(..., description="Search query applied.")
    total_results: int = Field(
        ..., description="Total matching records upstream at Companies House."
    )
    start_index: int = Field(..., description="Pagination offset for this page.")
    items_per_page: int = Field(..., description="Page size requested.")
    returned: int = Field(..., description="Items actually returned on this page.")
    has_more: bool = Field(
        ...,
        description=(
            "True if more items may exist beyond this page. Re-call with "
            "start_index=start_index+items_per_page to continue."
        ),
    )
    items: list[DisqualifiedSearchItem] = Field(
        default_factory=list,
        description="Matching disqualified officer records.",
    )


class DisqualificationOrder(BaseModel):
    """A single disqualification order attached to a disqualified director."""

    model_config = BASE_CFG

    disqualified_from: str | None = Field(
        None, description="Start date of the disqualification period."
    )
    disqualified_until: str | None = Field(
        None, description="End date of the disqualification period."
    )
    reason: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Reason block as returned upstream: act, section, "
            "description_identifier, article, etc."
        ),
    )
    company_names: list[str] = Field(
        default_factory=list,
        description=(
            "Companies associated with this disqualification. The list may be "
            "truncated to 20 entries."
        ),
    )
    company_names_truncated: bool = Field(
        False,
        description="True if the company_names list was truncated to max_companies.",
    )
    company_names_total: int = Field(
        0,
        description=(
            "Total number of companies originally attached to this order before "
            "truncation (may equal `len(company_names)`)."
        ),
    )
    address: dict[str, Any] = Field(
        default_factory=dict,
        description="Address on record at the time of the order.",
    )
    case_identifier: str | None = Field(
        None,
        description="Upstream case_identifier, when provided.",
    )
    heard_on: str | None = Field(
        None, description="Date the order was heard, when provided."
    )
    last_variation: dict[str, Any] = Field(
        default_factory=dict,
        description="Details of the last variation of this order, if any.",
    )
    undertaken_on: str | None = Field(
        None, description="Date the undertaking was given, when provided."
    )


class DisqualifiedProfile(BaseModel):
    """Full disqualification record for a disqualified officer."""

    model_config = BASE_CFG

    officer_id: str = Field(..., description="Companies House officer ID looked up.")
    officer_kind: str = Field(
        ...,
        description=(
            "Which CH endpoint returned the record: 'natural' (individual) or "
            "'corporate' (legal entity)."
        ),
    )
    name: str | None = Field(None, description="Officer name.")
    forename: str | None = Field(None, description="Given name, if split upstream.")
    surname: str | None = Field(None, description="Family name, if split upstream.")
    date_of_birth: str | None = Field(None, description="Date of birth on record.")
    nationality: str | None = Field(None, description="Declared nationality.")
    disqualifications: list[DisqualificationOrder] = Field(
        default_factory=list,
        description="All disqualification orders attached to this officer.",
    )


# =============================================================================
# Charity Commission
# =============================================================================


class CharitySearchItem(BaseModel):
    """A single hit in a Charity Commission name search."""

    model_config = BASE_CFG

    charity_number: str | None = Field(
        None,
        description=(
            "Charity Commission registration number. Pass to charity_profile "
            "for the full record."
        ),
    )
    charity_name: str | None = Field(None, description="Registered charity name.")
    reg_status: str | None = Field(
        None,
        description=(
            "Registration status code as returned upstream: 'R' registered, "
            "'RM' removed."
        ),
    )
    reg_status_label: str | None = Field(
        None,
        description="Human-readable registration status ('Registered', 'Removed').",
    )
    date_of_registration: str | None = Field(
        None, description="Date of first registration (ISO YYYY-MM-DD)."
    )


class CharitySearchResult(BaseModel):
    """Paginated result of a Charity Commission name search."""

    model_config = BASE_CFG

    query: str = Field(..., description="Search term applied.")
    total: int = Field(..., description="Total matches returned by upstream.")
    offset: int = Field(
        ..., description="Number of items skipped before this page (client-side)."
    )
    limit: int = Field(..., description="Max items requested for this page.")
    returned: int = Field(..., description="Items actually returned on this page.")
    has_more: bool = Field(
        ...,
        description=(
            "True if more items may exist beyond this page. Re-call with "
            "offset=offset+returned to continue."
        ),
    )
    charities: list[CharitySearchItem] = Field(
        default_factory=list,
        description="Matching charity records.",
    )


class CharityTrustee(BaseModel):
    """A single charity trustee record."""

    model_config = BASE_CFG

    trustee_name: str | None = Field(None, description="Trustee name.")


class CharityClassification(BaseModel):
    """Who/What/Where classification descriptor."""

    model_config = BASE_CFG

    classification_type: str | None = Field(
        None,
        description="Classification axis: 'What', 'Who', or 'Where'.",
    )
    classification_desc: str | None = Field(
        None, description="Classification description text."
    )


class CharityProfile(BaseModel):
    """Full Charity Commission profile for a single charity."""

    model_config = BASE_CFG

    charity_number: str = Field(..., description="Charity registration number.")
    charity_name: str | None = Field(None, description="Registered charity name.")
    reg_status: str | None = Field(
        None, description="Registration status code ('R', 'RM')."
    )
    reg_status_label: str | None = Field(
        None, description="Human-readable registration status."
    )
    charity_type: str | None = Field(None, description="Charity type.")
    charity_co_reg_number: str | None = Field(
        None,
        description=(
            "Companies House number for charities also registered as companies "
            "(Charitable Incorporated Organisations, etc.)."
        ),
    )
    date_of_registration: str | None = Field(
        None, description="Date of first registration."
    )
    address: str | None = Field(
        None,
        description="Registered address of the charity (joined address lines).",
    )
    latest_income: float | None = Field(
        None, description="Latest filed annual income in GBP."
    )
    latest_expenditure: float | None = Field(
        None, description="Latest filed annual expenditure in GBP."
    )
    insolvent: bool = Field(
        False, description="True if the charity is flagged as insolvent."
    )
    in_administration: bool = Field(
        False, description="True if the charity is in administration."
    )
    trustee_names: list[CharityTrustee] = Field(
        default_factory=list,
        description=(
            "Trustees on record. Truncated to 30 entries."
        ),
    )
    trustee_names_truncated: bool = Field(
        False, description="True if the trustee list was truncated."
    )
    trustee_names_total: int = Field(
        0, description="Total trustees upstream before truncation."
    )
    who_what_where: list[CharityClassification] = Field(
        default_factory=list,
        description=(
            "Who/What/Where classification entries. The list may be truncated "
            "truncated to 50 entries."
        ),
    )
    who_what_where_truncated: bool = Field(
        False,
        description="True if the classification list was truncated.",
    )
    who_what_where_total: int = Field(
        0,
        description="Total classification entries upstream before truncation.",
    )
    countries_of_operation: list[str] = Field(
        default_factory=list,
        description="Countries the charity operates in (capped at 10 upstream).",
    )


# =============================================================================
# Land Registry
# =============================================================================


class LandTitleTransaction(BaseModel):
    """A single Price Paid transaction for a property."""

    model_config = BASE_CFG

    price_paid: int | None = Field(
        None, description="Sale price in GBP (integer pounds)."
    )
    transaction_date: str | None = Field(
        None, description="Transaction date (ISO YYYY-MM-DD)."
    )
    postcode: str | None = Field(None, description="Property postcode.")
    paon: str | None = Field(
        None,
        description="Primary addressable object name (house number or name).",
    )
    saon: str | None = Field(
        None,
        description="Secondary addressable object name (flat/unit identifier).",
    )
    street: str | None = Field(None, description="Street name.")
    town: str | None = Field(None, description="Town/city.")
    county: str | None = Field(None, description="County.")
    property_type: str | None = Field(
        None,
        description=(
            "Property type label extracted from the HMLR URI "
            "(e.g. 'Terraced', 'Semi Detached', 'Flat')."
        ),
    )
    estate_type: str | None = Field(
        None,
        description="Tenure / estate type label (e.g. 'Freehold', 'Leasehold').",
    )


class LandTitleSearchResult(BaseModel):
    """HMLR Price Paid Index search result for a given postcode."""

    model_config = BASE_CFG

    postcode: str = Field(
        ...,
        description="Normalised UK postcode extracted from the input.",
    )
    total: int = Field(
        ...,
        description=(
            "Number of Price Paid transactions returned. Capped at 10 by the "
            "upstream SPARQL query."
        ),
    )
    transactions: list[LandTitleTransaction] = Field(
        default_factory=list,
        description=(
            "Recent Price Paid transactions for the postcode, sorted newest first."
        ),
    )


# =============================================================================
# The Gazette
# =============================================================================


class GazetteNotice(BaseModel):
    """A single corporate insolvency notice from The Gazette."""

    model_config = BASE_CFG

    notice_id: str | None = Field(
        None, description="Gazette notice URI (e.g. 'https://www.thegazette.co.uk/id/notice/5122793')."
    )
    notice_numeric_id: str | None = Field(
        None,
        description=(
            "Numeric notice ID. Read full notice content via the "
            "notice://{notice_numeric_id} resource."
        ),
    )
    notice_code: str | None = Field(
        None,
        description=(
            "Gazette notice code (e.g. '2443' winding-up order, '2448' "
            "administration order)."
        ),
    )
    notice_type: str | None = Field(
        None,
        description="Human-readable notice type label (e.g. 'Winding-Up Order').",
    )
    severity: int = Field(
        0,
        description=(
            "Internal severity score 0-10. Higher = more serious (10 = "
            "Winding-Up Order, 9 = Administration Order / Receiver, 0 = "
            "unclassified)."
        ),
    )
    date: str | None = Field(
        None,
        description="Publication date (ISO YYYY-MM-DD).",
    )
    title: str | None = Field(None, description="Notice title.")
    content: str | None = Field(
        None,
        description=(
            "Brief notice excerpt from the search feed (HTML stripped). "
            "For full legal wording read notice://{notice_numeric_id}."
        ),
    )


class GazetteInsolvencyResult(BaseModel):
    """Aggregated Gazette insolvency notice search result."""

    model_config = BASE_CFG

    entity_name: str = Field(..., description="Entity name that was searched.")
    notice_type_filter: str | None = Field(
        None,
        description="Notice code filter applied, or null if all codes searched.",
    )
    start_date: str | None = Field(
        None, description="Lower bound of the date range filter, if any."
    )
    end_date: str | None = Field(
        None, description="Upper bound of the date range filter, if any."
    )
    total_notices: int = Field(
        ..., description="Total notices returned after deduplication, sorting, and cap."
    )
    max_notices_cap: int = Field(
        ..., description="The max_notices cap applied. Upstream may have more matching notices."
    )
    notices: list[GazetteNotice] = Field(
        default_factory=list,
        description=(
            "Matching notices, sorted by severity (desc) then date (desc)."
        ),
    )


# =============================================================================
# HMRC VAT
# =============================================================================


class VATValidationResult(BaseModel):
    """HMRC VAT validation result."""

    model_config = BASE_CFG

    valid: bool = Field(
        ...,
        description=(
            "True if HMRC confirmed the VAT number is currently registered. "
            "False means HMRC returned 404 (not registered / deregistered)."
        ),
    )
    vat_number: str = Field(
        ...,
        description="Canonical VAT number in 'GB<9 digits>' format.",
    )
    trading_name: str | None = Field(
        None,
        description=(
            "Trading name registered with HMRC for VAT. Compare with the "
            "Companies House name — discrepancies are a due diligence signal."
        ),
    )
    registered_address: str | None = Field(
        None,
        description=(
            "VAT-registered trading address. May differ from the Companies "
            "House registered office address."
        ),
    )
    consultation_number: str | None = Field(
        None,
        description="HMRC consultation reference number for this lookup.",
    )
