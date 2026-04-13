# Dual-Business Hub — Lilieth Sovereign Network

A production-ready, self-hosted platform combining a **Recruitment Agency** (Module 1) and a **Property Pressure** business (Module 2), orchestrated by the **Lilieth Orchestrator** FastAPI backend and protected by a sovereign Docker network.

---

## Repository Structure

```
.
├── schema.sql                  # PostgreSQL schema — all tables, triggers, audit log
├── docker-compose.yml          # Sovereign network stack
├── nginx/
│   └── nginx.conf              # Nginx reverse proxy + TLS gateway
├── lilieth_guard/
│   ├── rams_generator.py       # RAMS document generator (Paul Cassidy special)
│   ├── main.py                 # FastAPI Lilieth Orchestrator entry point
│   ├── Dockerfile              # Container image for the API
│   └── requirements.txt        # Python dependencies
├── rams/                       # Generated RAMS markdown files (runtime output)
└── .env.example                # Template for environment variables
```

---

## Module 1 — Recruitment

PostgreSQL tables:

| Table | Purpose |
|-------|---------|
| `candidates` | Operatives with CSCS/NRSWA ticket tracking and `night_shift_ready` flag |
| `jobs` | Vacancies with motorway zone classification |
| `placements` | Candidate ↔ Job assignments with status lifecycle |

## Module 2 — Property Pressure

| Table | Purpose |
|-------|---------|
| `leads` | Sales leads with status tracking |
| `residential_jobs` | Residential pressure-wash/cleaning jobs with `sq_meters` and `surface_type` |
| `commercial_contracts` | Commercial site contracts with renewal tracking |

## Core Layer

| Table | Purpose |
|-------|---------|
| `rams_vault` | Site-specific RAMS documents linked to every job (both modules) |
| `audit_logs` | Immutable audit trail for every INSERT/UPDATE/DELETE across all tables |

All primary keys are **UUIDs**. All tables have full audit triggers.

---

## Quick Start

### 1. Configure environment

```bash
cp .env.example .env
# Edit .env and set strong passwords / secret key
```

### 2. Place TLS certificates

```bash
mkdir -p nginx/certs
# Copy fullchain.pem and privkey.pem into nginx/certs/
# For local dev, generate self-signed certs with mkcert or openssl
```

### 3. Start the sovereign stack

```bash
docker compose up -d
```

The stack brings up:

| Service | Role | Network |
|---------|------|---------|
| `postgres` | Dual-business database | Internal mesh only |
| `redis` | Zero-lag candidate matching cache | Internal mesh only |
| `lilieth_orchestrator` | FastAPI backend | Mesh + frontend |
| `nginx` | Encrypted TLS gateway | Frontend (443/80 exposed) |
| `gemini_bridge` | Local LLM node (Ollama placeholder) | Internal mesh only |

Only ports **80** and **443** are exposed to the host. All database traffic is restricted to the internal `mesh` network.

### 4. Apply the schema

The schema is automatically applied on first PostgreSQL startup via the Docker init script. To apply manually:

```bash
docker compose exec postgres psql -U lilieth -d lilieth_hub -f /docker-entrypoint-initdb.d/01_schema.sql
```

---

## Lilieth Guard — RAMS Generator

Generate a fully compliant **Risk Assessment & Method Statement** from a single job description:

```bash
# From the repository root
python lilieth_guard/rams_generator.py "Roof Clean, Covent Garden, 3 stories"

# With attribution
python lilieth_guard/rams_generator.py \
  --job "High-pressure clean, M25 Junction 9, night shift" \
  --prepared-by "Paul Cassidy" \
  --reviewed-by "Dean Mitchell"
```

Output files are written to the `rams/` directory as Markdown.

### Safety checks built-in

- 🌊 **High-pressure water hazards** — exclusion zones, PPE, run-off management
- 🪜 **Working at height** — WAH Regs 2005, PASMA/IPAF, harness requirements
- 🚶 **Public footfall management** — Piccadilly, Covent Garden and all high-footfall zones
- 🚗 **Traffic management** — Chapter 8, NHSS 12AB, motorway night-shift controls
- ⚗️ **COSHH** — biocides, softwash chemicals
- ⚡ **Electrical proximity** — LV/HV controls
- 🕳️ **Confined spaces** — entry permits, atmospheric testing

---

## Threadripper / Local Sovereignty Notes

- The stack is stateless and portable — volumes can be snapshotted and moved.
- To enable GPU acceleration for the LLM node, uncomment the `deploy.resources` block in `docker-compose.yml`.
- The `gemini_bridge` service uses Ollama as a placeholder; swap the image for any OpenAI-compatible local inference server.

---

## Licence

[MIT](LICENSE)
