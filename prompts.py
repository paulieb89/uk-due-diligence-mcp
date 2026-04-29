"""MCP prompts — workflow templates for UK due diligence investigations."""
from fastmcp import FastMCP


def register_prompts(mcp: FastMCP) -> None:

    @mcp.prompt(
        title="UK Company Due Diligence",
        description="Full due diligence check on a UK company across all registers.",
    )
    def due_diligence(entity_name: str) -> str:
        return (
            f"Run a full UK due diligence check on '{entity_name}'. "
            "Use the available tools in this order:\n"
            "1. company_search — find the entity and confirm the company number\n"
            "2. In parallel: company_profile, company_officers, company_psc, "
            "gazette_insolvency\n"
            "3. For each officer from company_officers, call disqualified_search "
            "with their personal name (not the company name)\n"
            "4. For any disqualified_search hits, call disqualified_profile for "
            "the full record\n"
            "5. For any gazette notices, you may call gazette_notice to retrieve the full legal wording\n"
            "Summarise all findings with a risk assessment."
        )

    @mcp.prompt(
        title="UK Charity Due Diligence",
        description="Due diligence check on a UK registered charity.",
    )
    def charity_due_diligence(charity_name: str) -> str:
        return (
            f"Run a due diligence check on UK charity '{charity_name}'. "
            "Use the available tools:\n"
            "1. charity_search — find the charity and confirm the charity number\n"
            "2. charity_profile — fetch the full record including trustees, "
            "income/expenditure, and insolvency flags\n"
            "3. gazette_insolvency — search by the charity name for any insolvency notices\n"
            "Summarise findings including financial health and governance flags."
        )

    @mcp.prompt(
        title="Director Disqualification Check",
        description="Check whether a named individual is disqualified as a UK company director.",
    )
    def director_check(director_name: str) -> str:
        return (
            f"Check whether '{director_name}' is disqualified as a UK company director.\n"
            "1. disqualified_search — search by the person's full name\n"
            "2. For any results, call disqualified_profile with the officer_id "
            "to get the full disqualification record including period and reason\n"
            "Report clearly whether a disqualification was found and its details."
        )
