# Findings: Property Revenue Dashboard

This investigation and the code changes were done with AI coding assistance and reviewed by the candidate.

Three client reports were real, and they shared one path: `GET /api/v1/dashboard/summary`. March totals were computed in UTC and, until the database pool was fixed, were not computed from the ledger at all. The cache key ignored the tenant, so two companies that both use `prop-001` could see each other's revenue after a refresh. Money was cast through binary float instead of decimal cents.

## Bug 1: March totals ignored the property timezone

**Symptom.** Sunset Properties (Beach House Alpha, `prop-001`, `Europe/Paris`) did not match their March records.

**How it was found.** Reservation `res-tz-1` checks in at `2024-02-29 23:30:00+00` for `1250.000`. In Paris that is `2024-03-01 00:30`. A UTC March window drops it. A Paris March window keeps it.

| Window | Bookings | Total |
| --- | --- | --- |
| March 2024, UTC | 3 | 1000.000 |
| March 2024, Europe/Paris | 4 | 2250.000 |
| February 2024, Europe/Paris | 0 | 0.00 |

The other bookings on that property are `333.333 + 333.333 + 333.334 = 1000.000`. Adding the local-March stay makes `2250.000`.

Before the fix, the API never ran that query. `calculate_monthly_revenue` built naive `datetime(year, month, 1)` values (interpreted as UTC) and then returned `Decimal("0")` without executing SQL. The dashboard called a different function, `calculate_total_revenue`, whose pool could not connect, so it returned a hardcoded mock. For `prop-001` that mock was `1000.00` and 3 bookings: the UTC March figure, shared with every tenant.

Logged in as `sunset@propertyflow.com`, the live response was:

```json
{"property_id":"prop-001","total_revenue":1000.0,"currency":"USD","reservations_count":3}
```

**Root cause.**

- Month bounds did not use `properties.timezone`. The old code was the naive `datetime(year, month, 1)` window in `backend/app/services/reservations.py`.
- The SQL string referenced `tenant_id`, but the function did not take a tenant argument, and the query was never executed.
- `backend/app/core/database_pool.py` built a URL from Supabase settings this app does not have, used the sync `QueuePool` (invalid on an async engine), and `get_session` was not an async context manager. Callers wrote `async with pool.get_session()`, which cannot open a session. `greenlet` was also missing, which SQLAlchemy asyncio requires. Any of those failures fell through to the mock.

**Fix.** `month_bounds` (`backend/app/services/reservations.py`, lines 10-26) builds a half-open month in the property's IANA zone via `zoneinfo`. The query loads that zone with `property_id` and `tenant_id` together (lines 54-63), then sums `check_in_date >= start AND check_in_date < end` (lines 90-111). The pool uses `DATABASE_URL` with the asyncpg driver (`backend/app/core/database_pool.py`, lines 9-16 and 28-40) and yields a real session (lines 65-70). `sqlalchemy[asyncio]` and `tzdata` are in `backend/requirements.txt` so the slim image can open sessions and resolve `Europe/Paris`. The summary endpoint reports March 2024 unless `month` and `year` are passed (`backend/app/api/v1/dashboard.py`, lines 12-14). February in Paris is `0.00`, which shows the `1250.000` stay moved into March instead of being counted twice.

**Verified.** After the fix, Sunset's Beach House Alpha:

```json
{"property_id":"prop-001","total_revenue":"2250.00","currency":"USD","reservations_count":4,"month":3,"year":2024,"timezone":"Europe/Paris"}
```

Same property, February 2024: `"0.00"`, 0 bookings, timezone still `Europe/Paris`. City Apartment Downtown is `"4975.50"` (4). Country Villa Estate is `"6100.50"` (2). Those two were already inside March in both zones, so only the boundary stay changed.

## Bug 2: A refresh showed another company's revenue

**Symptom.** Ocean Rentals sometimes saw Sunset's numbers. The same thing happened in the other direction.

**How it was found.** `prop-001` is Beach House Alpha for `tenant-a` and Mountain Lodge Beta for `tenant-b`. Ocean has no reservations on `prop-001`. After Sunset loaded `prop-001`, Redis held `revenue:prop-001` and Ocean's next request returned Sunset's total. After Ocean loaded `prop-004`, Sunset's request for `prop-004` returned `1776.5`, which is Lakeside Cottage. Sunset also received Urban Loft's `3256.0` for `prop-005`.

Before the timezone fix, with the mock still in place, Ocean's `prop-001` response was Sunset's mock:

```json
{"property_id":"prop-001","total_revenue":1000.0,"currency":"USD","reservations_count":3}
```

After March was corrected but the cache key was still only the property id, the leak got worse. Ocean's refresh returned Sunset's real Paris total, including the timezone:

```json
{"property_id":"prop-001","total_revenue":2250.0,"reservations_count":4,"timezone":"Europe/Paris"}
```

Redis key at that point: `revenue:prop-001:2024-03`.

**Root cause.**

- `backend/app/services/cache.py` keyed only on `revenue:{property_id}`. Property ids are reused. The primary key of `properties` is `(id, tenant_id)` (`database/schema.sql`).
- When the database raised, `calculate_total_revenue` caught the error and returned one mock dictionary keyed only by property id. `prop-001` was always `1000.00` / 3 bookings, for every company.
- `backend/app/api/v1/dashboard.py` used `getattr(current_user, "tenant_id", "default_tenant") or "default_tenant"`, so a missing tenant still ran a query.
- `TenantResolver.resolve_tenant_id` accepted a token and ignored it, then returned `tenant-a` for every unknown email (`backend/app/core/tenant_resolver.py`).
- The dashboard dropdown listed every company's properties to every login. The revenue request also sent `X-Simulated-Tenant: candidate`, which the API ignored, but it was a client-chosen tenant on the same call.

**Fix.** The cache key is `revenue:{tenant}:{property}:{year}-{month}` (`backend/app/services/cache.py`, lines 14-18). A cached body is used only when its `tenant_id` and `property_id` match (lines 36-39). A Redis failure is logged and the ledger is queried anyway (lines 45-46 and 54-55). The shared mock is gone. The summary returns 403 when the session has no tenant (`dashboard.py`, lines 22-24). The resolver reads the JWT first and returns `None` for an unknown user instead of `tenant-a` (`tenant_resolver.py`, lines 79-104). Login puts `tenant_id` on the token (`login.py`, lines 47-52 for Sunset, and the same for Ocean). The dropdown is split by tenant (`frontend/src/components/Dashboard.tsx`, lines 5-16), so Ocean sees Mountain Lodge Beta, not Beach House Alpha. The revenue card no longer sends a simulated tenant header.

**Verified.** Sunset loads Beach House first, then Ocean refreshes `prop-001`:

```json
{"property_id":"prop-001","total_revenue":"0.00","currency":"USD","reservations_count":0,"month":3,"year":2024,"timezone":"America/New_York"}
```

A second refresh of each user stays on `2250.00` / Paris / 4 bookings and `0.00` / New York / 0 bookings. Ocean's Lakeside Cottage is `"1776.50"` (4). Sunset asking for `prop-004` is `"0.00"`, `timezone: null`, 0 bookings. Urban Loft for Ocean is `"3256.00"` (3). Keys are `revenue:tenant-a:prop-001:2024-03` and `revenue:tenant-b:prop-001:2024-03`.

## Bug 3: Totals were off at the cent

**Symptom.** Finance saw totals that did not sit on a clean cent.

**How it was found.** `total_amount` is `NUMERIC(10, 3)`. Beach House has `333.333`, `333.333`, and `333.334`. The dashboard cast the sum with `float()` and the card did `Math.round(data.total_revenue * 100) / 100`.

Those three lines sum to `1000.000` exactly, so the cent they make together is real. Rounding each line to a cent first, the way the old `Math.round(n * 100) / 100` path does, yields `333.33 + 333.33 + 333.33 = 999.99` and drops that cent. Separately, several stored amounts are not exact in binary: `float(Decimal("1080.40"))` is `1080.40000000000009094947`, and `float(Decimal("1255.60"))` is `1255.59999999999990905053`. `round(1.005, 2)` in Python is `1.0`, not `1.01`. The API was also emitting JSON numbers, so `1776.50` left the server as `1776.5`.

The seed sums happen to land on values that binary float can still represent after they are added (`2250`, `4975.50`, `6100.50`, `1776.50`, `3256.00`). The error shows up on the line items and on any amount that is not a binary fraction, which is why it looked like a few cents rather than a consistent offset.

**Root cause.** `float(revenue_data["total"])` in `backend/app/api/v1/dashboard.py`, and `Math.round(data.total_revenue * 100) / 100` in `frontend/src/components/RevenueSummary.tsx`. Currency was also hardcoded to `USD` instead of being read from the rows.

**Fix.** `format_money` (`reservations.py`, lines 39-42) quantizes with `Decimal` and `ROUND_HALF_UP` to `0.01` and returns a string. The sum is quantized once, so `333.333 + 333.333 + 333.334` stays `"1000.00"`, and with the Paris stay the property total is `"2250.00"`. The endpoint returns that string (`dashboard.py`, line 30). The card formats the string by grouping digits (`RevenueSummary.tsx`, `formatMoneyAmount`) and does not multiply by 100 in floating point. If a month contains more than one currency, the query refuses to add them (`reservations.py`, lines 113-117). Every seeded row is USD.

**Verified.** Response field types are JSON strings, not numbers:

| Who | Property | March 2024 |
| --- | --- | --- |
| Sunset, tenant-a | prop-001 Beach House Alpha, Europe/Paris | `"2250.00"`, 4 bookings |
| Sunset | prop-002 City Apartment Downtown | `"4975.50"`, 4 |
| Sunset | prop-003 Country Villa Estate | `"6100.50"`, 2 |
| Ocean, tenant-b | prop-001 Mountain Lodge Beta, America/New_York | `"0.00"`, 0 |
| Ocean | prop-004 Lakeside Cottage | `"1776.50"`, 4 |
| Ocean | prop-005 Urban Loft Modern | `"3256.00"`, 3 |

## How to run and test

Docker is the setup the repo ships with:

```bash
docker-compose up --build
# Frontend http://localhost:3000
# API http://localhost:8000/docs
```

This machine had no Docker. The same schema and seed were loaded into local Postgres, with Redis on `127.0.0.1:6379`, and the API was started with:

```bash
export DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5432/propertyflow
export REDIS_URL=redis://127.0.0.1:6379/0
export SECRET_KEY=debug_challenge_secret
cd backend && uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Regression tests (15 passed here):

```bash
cd backend
DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5432/propertyflow \
REDIS_URL=redis://127.0.0.1:6379/0 \
SECRET_KEY=debug_challenge_secret \
python -m unittest discover -s tests -v
```

Logins: `sunset@propertyflow.com` / `client_a_2024` (tenant-a), and `ocean@propertyflow.com` / `client_b_2024` (tenant-b). The dashboard requests March 2024. Pass `month` and `year` on the summary URL to check another month. February for Beach House Alpha is `0.00`.

I also clicked through the dashboard at `http://127.0.0.1:5173` in a browser. Sunset's dropdown is only Beach House Alpha, City Apartment Downtown, and Country Villa Estate, with the card showing USD 2,250.00 / 4 bookings / Europe/Paris, then USD 4,975.50 / 4, then USD 6,100.50 / 2. Ocean's dropdown is only Mountain Lodge Beta, Lakeside Cottage, and Urban Loft Modern. Mountain Lodge stays USD 0.00 / 0 bookings / America/New_York after a reload. Lakeside is USD 1,776.50 / 4. Urban Loft is USD 3,256.00 / 3. Docker was not installed on this machine, so this was local Postgres, Redis, and uvicorn rather than `docker-compose`.
