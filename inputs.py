"""
models/inputs.py — Pydantic v2 input models for all uk-due-diligence-mcp tools.

All models use:
  - ConfigDict(str_strip_whitespace=True, validate_assignment=True, extra='forbid')
  - Field() with explicit descriptions and constraints
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ---------------------------------------------------------------------------
# Shared config
# ---------------------------------------------------------------------------

BASE_CFG = ConfigDict(
    str_strip_whitespace=True,
    validate_assignment=True,
    extra="forbid",
)


# ---------------------------------------------------------------------------
# Shared enums
# ---------------------------------------------------------------------------

class ResponseFormat(str, Enum):
    """Output format preference."""
    MARKDOWN = "markdown"
    JSON = "json"


class CompanyStatus(str, Enum):
    """Companies House entity status filter."""
    ACTIVE = "active"
    DISSOLVED = "dissolved"
    LIQUIDATION = "liquidation"
    ADMINISTRATION = "administration"
    RECEIVERSHIP = "receivership"
    CONVERTED_CLOSED = "converted-closed"
    VOLUNTARY_ARRANGEMENT = "voluntary-arrangement"
    INSOLVENCY_PROCEEDINGS = "insolvency-proceedings"


class CompanyType(str, Enum):
    """Companies House company type filter."""
    LTD = "ltd"
    PLC = "plc"
    LLP = "llp"
    PARTNERSHIP = "partnership"
    SOLE_TRADER = "sole-trader"
    INDUSTRIAL_PROVIDENT_SOCIETY = "industrial-provident-society"
    REGISTERED_SOCIETY = "registered-society"
    CHARITABLE_INCORPORATED_ORGANISATION = "charitable-incorporated-organisation"


class CharityRegistrationStatus(str, Enum):
    """Charity Commission registration status filter."""
    REGISTERED = "Registered"
    REMOVED = "Removed"


class GazetteNoticeType(str, Enum):
    """Gazette notice type codes for corporate insolvency."""
    WINDING_UP_PETITION = "2441"
    WINDING_UP_ORDER = "2443"
    ADMINISTRATION_ORDER = "2448"
    ADMINISTRATIVE_RECEIVER = "2449"
    LIQUIDATION_APPOINTMENT = "2452"
    STRIKING_OFF = "2460"
    VOLUNTARY_LIQUIDATION = "2455"
    CREDITORS_VOLUNTARY_LIQUIDATION = "2456"


# ---------------------------------------------------------------------------
# Companies House tools
# ---------------------------------------------------------------------------

class CompanySearchInput(BaseModel):
    """Input for company_search."""
    model_config = BASE_CFG

    query: str = Field(
        ...,
        description="Company name or keyword to search for (e.g., 'Acme Ltd', 'WidgetCo')",
        min_length=2,
        max_length=200,
    )
    company_status: Optional[CompanyStatus] = Field(
        default=None,
        description="Filter by company status. Omit to search all statuses.",
    )
    company_type: Optional[CompanyType] = Field(
        default=None,
        description="Filter by company type (e.g., 'ltd', 'llp'). Omit to search all types.",
    )
    items_per_page: int = Field(
        default=20,
        description="Number of results to return (max 100)",
        ge=1,
        le=100,
    )
    start_index: int = Field(
        default=0,
        description="Pagination offset — number of results to skip",
        ge=0,
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="Output format: 'markdown' for human-readable, 'json' for structured data",
    )


class CompanyProfileInput(BaseModel):
    """Input for company_profile."""
    model_config = BASE_CFG

    company_number: str = Field(
        ...,
        description="Companies House company number, e.g. '12345678' or 'SC123456'",
        min_length=6,
        max_length=10,
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="Output format: 'markdown' or 'json'",
    )

    @field_validator("company_number")
    @classmethod
    def normalise_company_number(cls, v: str) -> str:
        """Pad numeric company numbers to 8 digits; leave prefix numbers unchanged."""
        if v.isdigit():
            return v.zfill(8)
        return v.upper()


class CompanyOfficersInput(BaseModel):
    """Input for company_officers."""
    model_config = BASE_CFG

    company_number: str = Field(
        ...,
        description="Companies House company number",
        min_length=6,
        max_length=10,
    )
    include_resigned: bool = Field(
        default=False,
        description="If true, include resigned officers alongside active ones",
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="Output format: 'markdown' or 'json'",
    )

    @field_validator("company_number")
    @classmethod
    def normalise_company_number(cls, v: str) -> str:
        if v.isdigit():
            return v.zfill(8)
        return v.upper()


class CompanyPSCInput(BaseModel):
    """Input for company_psc."""
    model_config = BASE_CFG

    company_number: str = Field(
        ...,
        description="Companies House company number",
        min_length=6,
        max_length=10,
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="Output format: 'markdown' or 'json'",
    )

    @field_validator("company_number")
    @classmethod
    def normalise_company_number(cls, v: str) -> str:
        if v.isdigit():
            return v.zfill(8)
        return v.upper()


# ---------------------------------------------------------------------------
# Charity Commission tools
# ---------------------------------------------------------------------------

class CharitySearchInput(BaseModel):
    """Input for charity_search."""
    model_config = BASE_CFG

    query: str = Field(
        ...,
        description="Charity name or keyword to search for (e.g., 'Oxfam', 'local food bank')",
        min_length=2,
        max_length=200,
    )
    registration_status: Optional[CharityRegistrationStatus] = Field(
        default=CharityRegistrationStatus.REGISTERED,
        description="Filter by registration status. Default: 'Registered' (active charities only).",
    )
    page_size: int = Field(
        default=20,
        description="Number of results to return per page (max 100)",
        ge=1,
        le=100,
    )
    page_num: int = Field(
        default=1,
        description="Page number for pagination (1-indexed)",
        ge=1,
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="Output format: 'markdown' or 'json'",
    )


class CharityProfileInput(BaseModel):
    """Input for charity_profile."""
    model_config = BASE_CFG

    charity_number: str = Field(
        ...,
        description="Charity Commission registration number, e.g. '1234567' or '1234567-1' for a subsidiary",
        min_length=6,
        max_length=12,
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="Output format: 'markdown' or 'json'",
    )


# ---------------------------------------------------------------------------
# Land Registry tool
# ---------------------------------------------------------------------------

class LandTitleSearchInput(BaseModel):
    """Input for land_title_search."""
    model_config = BASE_CFG

    address_or_postcode: str = Field(
        ...,
        description=(
            "Freehold/leasehold property address or UK postcode. "
            "Postcode is most reliable: e.g. 'NG1 1AB'. "
            "Full address also accepted: '1 Example Street, Nottingham, NG1 1AB'."
        ),
        min_length=4,
        max_length=200,
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="Output format: 'markdown' or 'json'",
    )


# ---------------------------------------------------------------------------
# Gazette tool
# ---------------------------------------------------------------------------

class GazetteInsolvencyInput(BaseModel):
    """Input for gazette_insolvency."""
    model_config = BASE_CFG

    entity_name: str = Field(
        ...,
        description="Company name or individual name to search for in Gazette insolvency notices",
        min_length=2,
        max_length=200,
    )
    notice_type: Optional[GazetteNoticeType] = Field(
        default=None,
        description=(
            "Filter by specific notice type. Omit to return all corporate insolvency types. "
            "Options: winding_up_petition, winding_up_order, administration_order, "
            "administrative_receiver, liquidation_appointment, striking_off, "
            "voluntary_liquidation, creditors_voluntary_liquidation."
        ),
    )
    start_date: Optional[str] = Field(
        default=None,
        description="Filter notices from this date (ISO format: YYYY-MM-DD, e.g. '2023-01-01')",
        pattern=r"^\d{4}-\d{2}-\d{2}$",
    )
    end_date: Optional[str] = Field(
        default=None,
        description="Filter notices up to this date (ISO format: YYYY-MM-DD, e.g. '2024-12-31')",
        pattern=r"^\d{4}-\d{2}-\d{2}$",
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="Output format: 'markdown' or 'json'",
    )


# ---------------------------------------------------------------------------
# HMRC VAT tool
# ---------------------------------------------------------------------------

class VATValidateInput(BaseModel):
    """Input for vat_validate."""
    model_config = BASE_CFG

    vat_number: str = Field(
        ...,
        description=(
            "UK VAT registration number. Accepts formats: "
            "'GB123456789', '123456789', 'GB 123 456 789'. "
            "GB prefix and spaces are normalised automatically."
        ),
        min_length=9,
        max_length=15,
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="Output format: 'markdown' or 'json'",
    )

    @field_validator("vat_number")
    @classmethod
    def normalise_vat(cls, v: str) -> str:
        """Strip 'GB' prefix, spaces, and hyphens to get bare 9-digit number."""
        clean = v.upper().replace("GB", "").replace(" ", "").replace("-", "")
        if not clean.isdigit() or len(clean) != 9:
            raise ValueError(
                f"VAT number must be 9 digits after removing 'GB' prefix and spaces. Got: '{v}'"
            )
        return clean


# ---------------------------------------------------------------------------
# Composite due diligence tool
# ---------------------------------------------------------------------------

class EntityDueDiligenceInput(BaseModel):
    """Input for entity_due_diligence."""
    model_config = BASE_CFG

    entity_name: str = Field(
        ...,
        description=(
            "Full or partial name of the company, charity, or entity to investigate. "
            "The agent will resolve the best match across registers. "
            "Example: 'Acme Construction Ltd', 'Riverside Housing Association'."
        ),
        min_length=2,
        max_length=200,
    )
    company_number: Optional[str] = Field(
        default=None,
        description=(
            "If known, provide the Companies House number to skip the search step "
            "and go straight to profile lookup. Dramatically improves accuracy."
        ),
        min_length=6,
        max_length=10,
    )
    include_property: bool = Field(
        default=False,
        description=(
            "If true, attempt a Land Registry title search for this entity "
            "(useful for property companies, housing associations, etc.). "
            "Requires a registered address postcode to be resolvable."
        ),
    )
    include_charity: bool = Field(
        default=False,
        description=(
            "If true, also search the Charity Commission register for this entity name. "
            "Useful for organisations that operate as both a company and a charity."
        ),
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="Output format: 'markdown' or 'json'",
    )

    @field_validator("company_number")
    @classmethod
    def normalise_company_number(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        if v.isdigit():
            return v.zfill(8)
        return v.upper()
