---
status: signed-off
as_of: 2026-11-21
signed_off: 2026-11-21
review_by: 2027-11-21
covers: "#84 gazette_personal_insolvency, #85 individual_insolvency_search"
owner: Paul Boucherat (controller)
---

# Legitimate interests assessment: personal-insolvency lookups

> **Signed off 2026-11-21 by the controller.** Drafted by an agent from the
> 2026-11-21 research; the controller's decisions are marked **Decision
> (2026-11-21)**. It is not legal advice. Neither tool merges until `PRIVACY.md`
> is updated (see Actions).

Structure follows the ICO's three-part test: purpose, necessity, balancing.
https://ico.org.uk/for-organisations/uk-gdpr-guidance-and-resources/lawful-basis/legitimate-interests/

## The processing

Two proposed tools in the hosted `uk-due-diligence-mcp` server:

| Tool | Source | Personal data returned |
|---|---|---|
| `gazette_personal_insolvency` (#84) | The Gazette, personal-insolvency notices (category 25) | Name, notice type, publish date, notice text (which can include address, date of birth, occupation) |
| `individual_insolvency_search` (#85) | Insolvency Service, Individual Insolvency Register (England & Wales) | Name, birth date, case type, status, order date, court and case number. **Decision (2026-11-21):** birth date is returned, because it is the register's own way to tell apart people with a common name, and a false match is the main due-diligence risk. Postcode, address, gender and occupation are not returned. |

How it happens: an AI client calls the tool with a person's name. The server
queries the public source, returns the result and keeps nothing: no database,
no cache, no query content in logs (`PRIVACY.md`). The hosting provider's
standard server logs record client IP addresses, as `PRIVACY.md` already states.

Data subjects: individuals who are, or were recently, subject to a personal
insolvency process.

Licence position: both sources publish under the Open Government Licence v3.0,
which "does not cover the re-use of personal data". The lawful basis for
processing the personal data is therefore this assessment, not the licence.

## 1. Purpose test: is there a legitimate interest?

**Interest:** letting users run due-diligence checks on the people behind a
company (directors, persons with significant control, proposed counterparties):
whether a person is currently bankrupt or subject to another insolvency
procedure, or has had a recent insolvency event.

- **Users' interest:** avoiding fraud and credit risk; checking whether a
  director is eligible to act (an undischarged bankrupt generally may not act as
  a company director without the court's permission); KYC and onboarding checks.
- **Wider public interest:** these registers exist so that people dealing with
  an individual can find this out. The Insolvency Service's register is
  published for exactly that purpose.
- **Our interest:** offering a complete due-diligence tool. Without it, the
  server's company-only coverage pushes users to less controlled sources.

**Decision (2026-11-21):** these are the intended uses: due diligence on the
officers, persons with significant control and counterparties of a business.
Consumer credit decisions and tenant screening are **excluded**: they raise
different expectations and fall outside this assessment. The tool descriptions
and `PRIVACY.md` say so.

## 2. Necessity test: is this processing necessary for that purpose?

- The purpose is answering "is this named person insolvent?". That cannot be
  done without processing the person's name and insolvency status.
- **Less intrusive alternative:** the user searches the register website
  themselves. That gives the same data with no aggregation, so this tool adds
  convenience and agent access, not new disclosure. The register remains the
  authoritative source and the tools point to it.
- **Minimisation applied:**
  - name queries only, no browsing or bulk listing;
  - only DD-relevant fields returned (#85: no postcode, address, gender or occupation);
  - Gazette results limited to the last **15 months** (below);
  - nothing stored.

## 3. Balancing test: do the individual's interests override ours?

**Nature of the data.** Financial distress is sensitive in practice, although it
is not special-category data. Exposure can cause embarrassment, credit and
employment harm. This weighs against.

**Reasonable expectations.** The individual knows their insolvency is published
in the Gazette and on the register; that is a statutory consequence. They can
reasonably expect a lookup **while it is current**. They can **not** reasonably
expect it to stay findable indefinitely. Both publishers limit this themselves:

- The Gazette marks notices *no-index* 15 months after publication, because
  discharge usually follows after one year and the register entry is removed
  three months later. https://www.thegazette.co.uk/policies/removing-notices
- The register deletes a bankruptcy 3 months after discharge (28 days if the
  order was rescinded or should not have been made), and treats copies that
  persist elsewhere online as a problem.
  https://www.gov.uk/guidance/technical-guidance-for-official-receivers/5-the-individual-insolvency-register

**Accuracy and staleness.** A Gazette notice is a historical record; status can
change (discharge, annulment). The ICO's accuracy guidance allows historical
records if "you must be clear that it is a historical record".

**Safeguards that tip the balance:**

1. **Gazette window:** default and maximum of 15 months from publication,
   matching the Gazette's own limit (#84).
2. **Register as the current-status source:** #85 reflects the register as it is
   now, so a discharged or annulled case drops out when the register removes it.
3. **Historical labelling:** every Gazette result carries
   `is_historical_record: true`, and the description points to the register for
   current status.
4. **No storage, no profiling:** nothing kept or combined across calls by the
   server. **Verified 2026-11-21:** metrics carry only tool, client, transport,
   region and status labels; argument-validation warnings log error *types*,
   not values; no query content appears in the prod logs. The hosting
   provider's access logs record client IP addresses (disclosed in
   `PRIVACY.md`).
5. **Name-led lookup only:** no listing, no "all bankruptcies this week".
6. **Annulment handling:** open question in #84. A Gazette bankruptcy order
   followed by an annulment notice should not be presented as live. Until
   matching notices to the same case is reliable, the historical label and the
   register pointer are the mitigation. **Decision (2026-11-21):** accepted. No
   cross-notice suppression, because matching notices by name alone is
   unreliable; every result carries the historical label and the pointer to the
   register for current status.
7. **Scope stated:** England & Wales only for #85; Scotland (Accountant in
   Bankruptcy) not covered, and the tool says so.

**Conclusion (signed off 2026-11-21).** With safeguards 1–7, and consumer and
tenant screening excluded, the processing falls within reasonable expectations
and is proportionate for due-diligence use.

## Individuals' rights

- **Transparency (Art. 14):** the data is not collected from the individual,
  so `PRIVACY.md` must name the sources, the purpose, this basis and the retention
  stance (nothing stored).
- **Right to object (Art. 21):** `PRIVACY.md` explains how to object
  (paul@bouch.dev). Because nothing is stored, an objection is handled by
  explaining the processing and, where it is upheld, excluding that person's
  name from results. **Decision (2026-11-21):** no suppression list for now; it
  would be the first stored personal data. Objections are handled case by case,
  and the question is revisited if one is upheld.
- **Correction:** errors in the source data are corrected by the publisher;
  `PRIVACY.md` already points people to the registers.

## Actions before either tool ships

- [x] Controller reviews this draft, resolves every open item, sets `status: signed-off` with a date (2026-11-21)
- [x] `PRIVACY.md`: add the Gazette personal-insolvency notices and the Individual Insolvency Register as sources of personal data; the 15-month window; the right to object; update "Last updated" (same PR)
- [x] #85: decide the returned fields (birth date yes; postcode no)
- [x] #84: decide annulment handling (historical label + register pointer; no suppression)
- [ ] Review this assessment if either tool's scope, sources or retention changes, and at least yearly
