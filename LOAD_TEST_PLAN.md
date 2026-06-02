# Lister API Load Test Plan (Outline)

> **Status:** Outline only — do NOT start until Maik has set up a separate test database.

## Prerequisites
- Separate test database (not production staging) — Maik will provision
- Lister API instance pointed at test DB
- API key with full permissions on test instance
- Baseline response times from functional test suite

## Tool Options
| Tool | Pros | Cons |
|------|------|------|
| **httpx + asyncio** | Consistent with existing test suite, no extra deps | Manual concurrency, no built-in reporting |
| **locust** | Web UI, real-time stats, Python-based | More setup, needs a locustfile |
| **k6** | Excellent reporting, JS scripting, built-in thresholds | Separate runtime, not Python |
| **artillery** | YAML config, good for API load testing | Node.js dependency |

**Recommendation:** Start with `httpx + asyncio` for tight integration with existing tests, then graduate to `locust` or `k6` for dedicated load testing runs.

## Test Scenarios

### 1. Read-Heavy (Baseline)
- **Goal:** Measure baseline latency under typical read load
- **Pattern:** 90% reads, 10% writes
- **Endpoints:** `GET /v1/lists`, `GET /v1/lists/{id}/items`, `GET /v1/items/priority`
- **Concurrency:** 10 → 50 → 100 → 200 concurrent users
- **Duration:** 5 min per level
- **Metrics:** p50, p95, p99 latency; error rate; throughput (req/s)

### 2. Write-Heavy (Stress)
- **Goal:** Find write throughput ceiling
- **Pattern:** 50% creates, 30% updates, 20% reads
- **Endpoints:** `POST /v1/lists`, `POST /v1/lists/{id}/items`, `PATCH /v1/items/{id}`
- **Concurrency:** 10 → 50 → 100 → 200
- **Duration:** 5 min per level
- **Metrics:** Write latency, conflict errors, DB connection pool saturation

### 3. Concurrent List Operations
- **Goal:** Test isolation under concurrent modifications to same list
- **Pattern:** 20 clients modifying the same list simultaneously
- **Endpoints:** Same list — add items, reorder, move completed, share
- **Concurrency:** 20 clients, 1000 ops each
- **Metrics:** Data integrity (no lost updates), error rate, deadlocks

### 4. Search at Scale
- **Goal:** Measure search performance with large datasets
- **Precondition:** Seed 10,000+ items across 100+ lists
- **Pattern:** 80% search, 20% CRUD
- **Endpoints:** `GET /v1/search?q=...`, `GET /v1/items/priority`
- **Concurrency:** 50 → 200
- **Metrics:** Search latency vs dataset size, index utilization

### 5. Priority Endpoint Hot Path
- **Goal:** Stress the most-used endpoint in production
- **Pattern:** 100% `GET /v1/items/priority` with occasional updates
- **Concurrency:** 100 → 500
- **Duration:** 10 min sustained
- **Metrics:** Response time degradation, cache hit rate (if any)

### 6. Auth Token Throughput
- **Goal:** Ensure auth isn't a bottleneck
- **Pattern:** Rapid login/token generation cycles
- **Endpoints:** `POST /api/auth/register`, `POST /api/auth/login`, `POST /api/auth/api-keys`
- **Concurrency:** 10 → 50 → 100
- **Metrics:** Auth latency, token validity, rate limiting behavior

## Pass/Fail Criteria
| Metric | Threshold |
|--------|-----------|
| p50 latency (read) | < 100ms |
| p95 latency (read) | < 500ms |
| p99 latency (read) | < 1000ms |
| p50 latency (write) | < 200ms |
| p95 latency (write) | < 1000ms |
| Error rate | < 1% under 100 concurrent users |
| Data integrity | Zero lost updates under concurrent modifications |
| Auth latency | < 200ms p50 |

## Execution Plan
1. **Phase 1:** Seed test DB with realistic data (100 lists, 5000 items, 50 users)
2. **Phase 2:** Run scenarios 1-3 sequentially with increasing concurrency
3. **Phase 3:** Seed 10k+ items, run scenarios 4-5
4. **Phase 4:** Run scenario 6 independently
5. **Phase 5:** Combine worst-case mix (50% search, 20% priority, 20% writes, 10% auth)
6. **Report:** Generate markdown report with charts, pass/fail, and recommendations

## Data Seeding Script
Create `seed.py` in this repo:
- Creates 50 test users with API keys
- Creates 100 lists per user (mix of active/archived)
- Creates 50 items per list (mix of new/in-progress/complete)
- Adds notes to 30% of items
- Shares 10% of lists across users
- Total: ~250,000 items, ~75,000 notes

## Blockers
- [ ] Maik provisions separate test DB
- [ ] Confirm Lister API instance URL for test environment
- [ ] Decide on test tool (httpx/asyncio vs locust vs k6)