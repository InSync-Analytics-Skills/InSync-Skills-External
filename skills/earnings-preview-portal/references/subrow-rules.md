# STRICT RULE 5 — sub-row engine (database-first) and its amendments

Referenced from SKILL.md STRICT CONTENT RULES 5. Enforced in `compute_portal.py`; this
file is the full statement.

5. **Sub-rows are DATABASE-FIRST:** the DB's own -Y/-Q/-M
   derived items are shown wherever InSync carries them, and their consensus values
   are authoritative. On top, FIXED importance defaults are computed where the DB
   lacks them: Revenue & EPS -> Y/Y + Q/Q; EBITDA/EBIT (incl. Adj) -> Y/Y + Margin;
   every currency ($M) KPI -> Y/Y, Q/Q and "% of Total Revenue"; unit/volume KPIs
   (fmt "#": GWh, units, subscribers, deliveries...) -> Y/Y + Q/Q ONLY — a computed
   "% of Total Revenue" NEVER appears on a non-currency line item (units / dollars
   is meaningless). A computed "% of Total Revenue"
   likewise NEVER appears on a balance-sheet STOCK code (`BS_*` ltcodes) — a stock
   divided by a quarterly flow is equally meaningless (a bank once printed Gross Loans
   at 2,940% of revenue). A "% Rev" derived item carried by the DB itself still
   renders regardless of prefix (DB-first wins). An EBIT/profit currency KPI additionally gets a true "Margin"
   when the metric set carries its matching segment-revenue KPI. Never label a
   total-revenue ratio as "Margin". %-format rows get no sub-rows.
