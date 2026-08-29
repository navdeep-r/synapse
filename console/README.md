# Enterprise Intelligence — Knowledge Base Console

This is the production frontend for curating and exporting knowledge graphs as versioned JSON snapshots, designed for consumption by a downstream AI platform.

## Tech Stack
- **Framework**: React 18 + TypeScript, Vite
- **Styling**: Tailwind CSS with custom CSS variables (Design Tokens)
- **Data Fetching**: TanStack Query
- **Forms**: React Hook Form + Zod
- **Visualizations**: react-force-graph
- **Animations**: Framer Motion

## Design Token System
The UI relies on a strict design token system defined in `src/index.css` and mapped in `tailwind.config.ts`.
- `--color-paper`: `#F6F6F4` (Backgrounds)
- `--color-ink`: `#1B1C1E` (Primary Text)
- `--color-ink-muted`: `#5B5D62` (Secondary Text)
- `--color-ledger`: `#2B4570` (Primary Accent/Action)
- `--color-signal`: `#B8863E` (Pending/Review)
- `--color-alert`: `#A23B2E` (Failed/Contradiction)
- `--color-confirmed`: `#3F6B4F` (Active/Verified)
- `--color-hairline`: `#DDDCD7` (Borders/Dividers)

**Typography Roles:**
- **IBM Plex Sans**: Interface elements (nav, labels, buttons, tables, forms).
- **IBM Plex Serif**: Community Report narrative text ONLY.
- **IBM Plex Mono**: IDs, scores, versions, hashes.

## Export Access-Control Policies
When generating a snapshot, one of the following Access-Control Handling policies MUST be selected:
1. **Filter by Access Group**: Omits facts the target downstream system lacks clearance for. Safest option.
2. **Tag for Downstream Enforcement**: Includes all facts, but embeds ACL tags in the payload schema. Defers enforcement to the downstream system.
3. **Export Unrestricted**: WARNING: This exports all data globally without ACL bounds. Requires an explicit typed confirmation phrase to proceed.

## Simulate Agent Query
The "Simulate Agent Query" panel found in the Knowledge Graph Viewer is an **INTERNAL QA TOOL**. It traces the raw `AssembledContext` (facts/confidence/provenance) without natural language generation. 
**CRITICAL**: This feature must NEVER be exposed to an unauthenticated or non-steward user in production.

## Generating Types
TypeScript types are generated from the backend's `kg_contracts` via OpenAPI.
To regenerate types, run:
```bash
npm run generate-types
```
