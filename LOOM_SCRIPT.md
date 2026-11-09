# Loom script (about 7 minutes)

Read this at a steady pace. The checklist at the end is the click path. Say the numbers out loud. They are the point.

Logins:

- Sunset Properties: `sunset@propertyflow.com` / `client_a_2024`
- Ocean Rentals: `ocean@propertyflow.com` / `client_b_2024`

Page: Property Management Dashboard, March 2024 revenue card.

## Intro (about 40 seconds)

Sunset Properties called because March did not match their books. Ocean Rentals called because a refresh sometimes showed another company's revenue. Finance said the totals were off by a few cents. All three are in the revenue summary, and I did not rebuild the app. I fixed the month window, the cache key, and the money type.

I used AI coding assistance for the investigation and the edits, and I reviewed the diff.

## Bug 1: March is the property's month, not UTC (about 2 minutes)

What I saw. As Sunset, Beach House Alpha came back as 1,000.00 dollars from 3 bookings. Their ledger for March is 2,250.00 from 4 bookings.

The odd stay is `res-tz-1`. It checks in at 29 February 2024, 23:30 UTC, for 1,250.00. Beach House Alpha is in Europe/Paris. That instant is 1 March, 00:30 local time. A UTC March window starts at 1 March 00:00 UTC, so it drops the stay and keeps only the three 333-dollar lines, which sum to 1,000.00. Paris March starts at 29 February 23:00 UTC, so it keeps the stay. 1,250 plus 1,000 is 2,250.

The code built that window with naive datetimes, which Postgres treats as UTC, and then it did not run the query at all. It returned zero from the function, and the dashboard fell through to a mock of 1,000.00 and 3 bookings because the database pool was pointed at Supabase settings we do not have.

The fix loads the property timezone for that tenant and builds the month in that zone. The dashboard asks for March 2024, which is the month in the seed and the month Sunset disputed.

After the fix, Beach House Alpha shows USD 2,250.00, 4 bookings, Europe/Paris. February for the same property is 0.00, so the 1,250 moved into March. It is not counted twice. City Apartment is still 4,975.50 from 4 bookings. Country Villa is still 6,100.50 from 2. Those stays were already in March in both zones.

## Bug 2: The cache key was only the property id (about 2 minutes)

What I saw. Ocean and Sunset both have a property called `prop-001`. Sunset's is Beach House Alpha in Paris. Ocean's is Mountain Lodge Beta in New York, and it has no March stays, so the honest total is 0.00.

I loaded Sunset first. Redis stored `revenue:prop-001`. Ocean refreshed and got Sunset's 1,000.00, then, after the month fix, Sunset's 2,250.00 and the timezone Europe/Paris. The other way around, Sunset opened Lakeside Cottage's id and got Ocean's 1,776.50. That is the privacy bug. "Sometimes" is just whoever filled the cache first. The mock made it worse, because on a database error every company got the same property totals.

The fix puts the tenant, the property, and the month in the key: `revenue:tenant-a:prop-001:2024-03` versus `revenue:tenant-b:prop-001:2024-03`. A cached row is thrown away if the tenant inside it does not match. Unknown users are no longer dumped into tenant-a. The dropdown only lists that company's properties, so Ocean sees Mountain Lodge Beta, not Beach House Alpha.

After the fix I load Sunset, then Ocean, then refresh both. Sunset stays on 2,250.00 and Paris. Ocean stays on 0.00 and America/New_York. Ocean's Lakeside Cottage is 1,776.50 from 4 bookings. Ocean's Urban Loft is 3,256.00 from 3. Sunset cannot see the cottage total.

## Bug 3: Cents were going through float (about 90 seconds)

What I saw. The column stores thousandths of a dollar. Beach House has 333.333, 333.333, and 333.334. Added as decimals that is exactly 1,000.00. The API did `float()` on the sum, and the card did `Math.round(amount * 100) / 100`.

Two things go wrong there. Rounding each of those lines to a cent first makes 333.33 three times, which is 999.99, one cent under the ledger, because the thousandths only become a cent when you add them. And binary float cannot hold amounts like 1,080.40 or 1,255.60 exactly. `round(1.005, 2)` comes out as 1.00, not 1.01. The JSON also dropped the trailing zero, so 1,776.50 left the server as 1776.5.

The fix sums in Decimal, rounds half-up to the cent once, and returns a string. Beach House March is the string `"2250.00"`. Lakeside is `"1776.50"`. Urban Loft is `"3256.00"`. The card prints that string. It does not multiply by 100 in floating point.

## Demo checklist

Do this on the fixed build. Say the before numbers from the investigation. You do not need the old build on screen.

1. Log in as `sunset@propertyflow.com` / `client_a_2024`.
2. Open the dashboard. The card title is March 2024 Revenue.
3. Beach House Alpha, property `prop-001`. Show USD 2,250.00, 4 bookings, Europe/Paris. Say: before, this was 1,000.00 from 3 bookings, because the 1,250.00 stay at 29 February 23:30 UTC was left in February.
4. City Apartment Downtown. USD 4,975.50, 4 bookings.
5. Country Villa Estate. USD 6,100.50, 2 bookings.
6. Open the property dropdown. Lakeside Cottage and Urban Loft are not there.
7. Log out. Log in as `ocean@propertyflow.com` / `client_b_2024`.
8. Mountain Lodge Beta, also property `prop-001`. Show USD 0.00, 0 bookings, America/New_York. Refresh the page. It is still 0.00. Say: before, this refresh showed Sunset's 1,000.00, and after the month fix but before the cache fix it showed Sunset's 2,250.00 and Europe/Paris.
9. Lakeside Cottage. USD 1,776.50, 4 bookings, America/New_York.
10. Urban Loft Modern. USD 3,256.00, 3 bookings.
11. Optional, in a terminal, so the raw types are visible:

```bash
curl -s -X POST http://localhost:8000/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"sunset@propertyflow.com","password":"client_a_2024"}'
```

Use the access token:

```bash
curl -s -H "Authorization: Bearer TOKEN" \
  "http://localhost:8000/api/v1/dashboard/summary?property_id=prop-001&month=3&year=2024"
```

Point at `"total_revenue":"2250.00"` with the quotes. Then the same URL with `month=2`. That body is `"0.00"`. Then log in as Ocean and request `prop-001` again. `"0.00"`, `America/New_York`.

```bash
redis-cli keys 'revenue:*'
```

Point at `revenue:tenant-a:prop-001:2024-03` and `revenue:tenant-b:prop-001:2024-03`.

## Wrap-up (about 30 seconds)

March follows the property clock, so Beach House is 2,250.00, not 1,000.00. The cache key includes the tenant, so Ocean's lodge stays at 0.00 after a refresh. Totals are decimal strings rounded half-up to the cent, so the thousandths add to 1,000.00 instead of being shoved through float. Same three paths, no rewrite. Tests are in `backend/tests`, and the write-up is `FINDINGS.md`.
