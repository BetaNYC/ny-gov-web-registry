# EAC-CPF crosswalk

[EAC-CPF](https://eac.staatsbibliothek-berlin.de/) (Encoded Archival Context — Corporate bodies,
Persons, Families) is the archival community's XML standard for **authority records**: it models the
same things this registry does — legal mandates, existence dates, name variants, relations between
bodies. This registry does **not** adopt EAC-CPF as its native format (XML authority-record shape is
wrong for a JSON web registry), but it **commits to a lossless conceptual mapping** so archival
consumers can interoperate instead of duplicating.

This page is the documented crosswalk. **A working XML exporter is out of scope** (follow-on); this is
the field-level mapping the exporter would implement, and the contract the schema is designed against.

## Field mapping

| Registry field | EAC-CPF element | Notes |
|---|---|---|
| `id` | `<control><recordId>` (steward-scoped) | The registry's own opaque key. |
| `identifiers[] {scheme, identifier}` | `<control><otherRecordId>` / `<cpfDescription><identity><entityId>` | Each `{scheme, identifier}` becomes an `otherRecordId` (with the scheme as its `localType`); authority-file schemes (`snac_ark`, `viaf`, `lcnaf`) are the canonical `entityId`s. |
| `name` | `<identity><nameEntry>` (preferred) | Marked authorized/preferred. |
| `other_names[]` | `<identity><nameEntry>` (alternative) | One `nameEntry` per variant; `note` → `useDates`/`descriptiveNote`. |
| `classification` / `government_level` | `<description><localDescription localType="…">` | Coarse and fine organizational classification. |
| `founding_date` / `dissolution_date` | `<description><existDates><dateRange>` | `fromDate` / `toDate`. |
| `mandates[]` | `<description><mandates><mandate>` | `citation` → `<citation>` (with `@xlink:href` = `url`); `authority_type` → `mandate/@localType`; `date` → the mandate's date; `role` (establishing/amending/abolishing) → descriptive note or a typed mandate. |
| `relations[]` | `<relations><cpfRelation>` | `type` (`predecessor_of` / `successor_of`) → `cpfRelation/@cpfRelationType` (`temporal-earlier` / `temporal-later`); `target_id` → the related record; `date` → `<dateRange>`. |
| `web_properties[]` | `<relations><resourceRelation cpfRelationType="…">` | Each domain (with `valid_from`/`valid_to`) as a related web resource; also the Internet Archive join surface (`docs/schemes.md`). |
| `links[]` | `<relations><resourceRelation>` | Non-owned related resources. |
| `contact_details[]` | `<description><place>` / `<descriptiveNote>` | Contact scaffolding (phase 2). |
| `provenance` | `<control><maintenanceHistory>` / `<sources>` | Which upstreams asserted the record, and when verified. |

## Forward target: RiC-O

ICA's [Records in Contexts — Ontology (RiC-O)](https://www.ica.org/standards/RiC/ontology) is the
linked-data successor to EAC-CPF. It is noted here as the **forward-looking archival LOD target**: any
JSON-LD `@context` the registry publishes later must not preclude a RiC-O mapping (`rico:Agent`,
`rico:Mandate`, `rico:AgentRelation`, `rico:hasOrHadName`). Publishing a hosted vocabulary is out of
scope for now.
