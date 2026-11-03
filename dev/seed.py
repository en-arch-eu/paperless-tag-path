"""Seed the throwaway local Paperless from dev/compose.yml (localhost:8010)
with tags (including malicious names), a tag_path storage path and a few
text documents.  Run: uv run --no-project --with httpx python dev/seed.py
"""

import sys
import time

import httpx

API = "http://localhost:8010/api"
c = httpx.Client(auth=("admin", "admin"), timeout=60, headers={"Accept": "application/json; version=10"})

TEMPLATE = """{%- if "health" in tag_name_list -%}
health/{{ tag_name_list | tag_path("people", strip_root=True, default="general") or "shared" }}/{{ tag_name_list | tag_path("health", strip_root=True, default="") }}
{%- else -%}
{{ tag_name_list | tag_path(["things", "events", "weird"], default="unsorted") }}
{%- endif -%}
/{{ created }} - {{ title }}"""


def tag(name, parent=None):
    r = c.get(f"{API}/tags/", params={"name__iexact": name})
    r.raise_for_status()
    if r.json()["count"]:
        return r.json()["results"][0]["id"]
    body = {"name": name, "matching_algorithm": 0}
    if parent:
        body["parent"] = parent
    r = c.post(f"{API}/tags/", json=body)
    if r.status_code >= 400:
        sys.exit(f"tag {name}: {r.status_code} {r.text}")
    return r.json()["id"]


T = {}
T["things"] = tag("things")
T["computer"] = tag("computer", T["things"])
T["monitor"] = tag("monitor", T["things"])
T["events"] = tag("events")
T["bought"] = tag("2025-01-01 - bought a computer", T["events"])
T["health"] = tag("health")
T["Hypertension"] = tag("Hypertension", T["health"])
T["people"] = tag("people")
T["Alice"] = tag("Alice", T["people"])
T["Bob"] = tag("Bob", T["people"])
T["weird"] = tag("weird")
T["aux"] = tag("AUX", T["weird"])
T["trav"] = tag("..", T["aux"])
T["colon"] = tag('a:b*c?"d"', T["trav"])
T["inbox"] = tag("plain flat tag")
print("tags:", T)

# storage path: bad template is rejected, good one accepted
for name, path in [
    ("bad: tags variable", '{{ tags | tag_path("things") }}/{{ title }}'),
    ("bad on_branch", '{{ tag_name_list | tag_path("things", on_branch="nope") }}/{{ title }}'),
]:
    r = c.post(f"{API}/storage_paths/", json={"name": name, "path": path, "matching_algorithm": 0})
    print(f"storage path [{name}]: {r.status_code} {r.text[:200]}")

r = c.get(f"{API}/storage_paths/", params={"name__iexact": "tag_path test"})
if r.json()["count"]:
    sp = r.json()["results"][0]["id"]
else:
    r = c.post(f"{API}/storage_paths/", json={"name": "tag_path test", "path": TEMPLATE, "matching_algorithm": 0})
    print("storage path [tag_path test]:", r.status_code, r.text[:300])
    sp = r.json()["id"]

DOCS = [
    ("Invoice computer", ["computer", "bought"]),
    ("Order computer and monitor", ["computer", "monitor"]),
    ("Event only", ["bought"]),
    ("Blood pressure Alice", ["Hypertension", "Alice"]),
    ("Blood pressure Alice and Bob", ["Hypertension", "Alice", "Bob"]),
    ("Health without person", ["Hypertension"]),
    ("Health without problem", ["health", "Bob"]),
    ("Malicious tag names", ["colon"]),
    ("No root at all", ["inbox"]),
]

existing = {d["title"] for d in c.get(f"{API}/documents/", params={"page_size": 100}).json()["results"]}
tasks = []
for i, (title, tags) in enumerate(DOCS):
    if title in existing:
        continue
    data = {
        "title": title,
        "storage_path": str(sp),
        "created": f"2025-0{1 + i % 9}-1{i}",
        "tags": [str(T[t]) for t in tags],
    }
    files = {"document": (f"doc{i}.txt", f"Test document {i}: {title}\n".encode(), "text/plain")}
    r = c.post(f"{API}/documents/post_document/", data=data, files=files)
    r.raise_for_status()
    tasks.append(r.json())
print("queued", len(tasks), "documents")

for _ in range(60):
    res = c.get(f"{API}/tasks/", params={"page_size": 100}).json()["results"]
    mine = [t for t in res if t["task_id"] in tasks]
    if len(mine) == len(tasks) and all(t["status"] in ("success", "failure") for t in mine):
        break
    time.sleep(2)
for t in mine:
    if t["status"] != "success":
        print("task", t["status"], t.get("result"))

for d in sorted(c.get(f"{API}/documents/", params={"page_size": 100}).json()["results"], key=lambda d: d["id"]):
    print(f'{d["id"]:>3}  {d["title"]}')
