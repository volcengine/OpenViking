#!/usr/bin/env python3
"""Incrementally update stat.csv (date,new,total per UTC day) with volcengine/OpenViking stargazers.

Walks stargazers newest-first via GraphQL and stops once past the last date already in the CSV,
so a routine run costs a few API points. No CSV yet -> walks everything (~400 points for ~40k stars).
Token: $GITHUB_TOKEN / $GH_TOKEN, else `gh auth token`. Any token that can read a public repo works.
"""
import collections, csv, datetime as dt, json, os, subprocess, sys, urllib.request

REPO_OWNER, REPO_NAME, OPEN_SOURCED = "volcengine", "OpenViking", dt.date(2026, 1, 5)
PATH = sys.argv[1] if len(sys.argv) > 1 else "stat.csv"
QUERY = """query($owner:String!,$name:String!,$after:String){repository(owner:$owner,name:$name){
  stargazers(first:100,after:$after,orderBy:{field:STARRED_AT,direction:DESC}){
    pageInfo{hasNextPage endCursor} edges{starredAt}}}}"""

token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or \
    subprocess.check_output(["gh", "auth", "token"], text=True).strip()

def page(after):
    body = json.dumps({"query": QUERY, "variables": {"owner": REPO_OWNER, "name": REPO_NAME, "after": after}})
    req = urllib.request.Request("https://api.github.com/graphql", body.encode(),
                                 {"Authorization": f"bearer {token}", "Content-Type": "application/json"})
    data = json.load(urllib.request.urlopen(req, timeout=60))
    if "errors" in data: sys.exit(f"GraphQL error: {data['errors']}")
    return data["data"]["repository"]["stargazers"]

rows = list(csv.DictReader(open(PATH))) if os.path.exists(PATH) else []
# The last stored day may have been partial, so it is re-counted from scratch.
# ponytail: rows before the cutoff are frozen, so later unstars of old stars are not subtracted;
# delete stat.csv to rebuild from scratch if total drifts from the repo's star count.
cutoff = rows.pop()["date"] if rows else None
counts, after = collections.Counter(), None
while True:
    s = page(after)
    days = [e["starredAt"][:10] for e in s["edges"]]
    counts.update(d for d in days if not cutoff or d >= cutoff)
    if not s["pageInfo"]["hasNextPage"] or (cutoff and days and days[-1] < cutoff): break
    after = s["pageInfo"]["endCursor"]

total = int(rows[-1]["total"]) if rows else 0
day = dt.date.fromisoformat(cutoff) if cutoff else min([OPEN_SOURCED, *map(dt.date.fromisoformat, counts)])
today = dt.datetime.now(dt.timezone.utc).date()
while day <= today:  # one row per UTC day, zero-star days included
    n = counts[day.isoformat()]; total += n
    rows.append({"date": day.isoformat(), "new": n, "total": total}); day += dt.timedelta(days=1)

with open(PATH, "w", newline="") as f:
    w = csv.DictWriter(f, ["date", "new", "total"], lineterminator="\n"); w.writeheader(); w.writerows(rows)
print(f"{PATH}: {rows[-1]['date']} total={total} (re-counted from {cutoff or 'the beginning'})")
