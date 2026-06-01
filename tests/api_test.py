#!/usr/bin/env python3
"""
Comprehensive API test suite for Lister.ai.

Sequential test runner — registers a unique test user, tests all major
endpoints, then deletes the test user and data.

Usage:
    python api_test.py                    # Run all tests
    python api_test.py -v                 # Verbose output
    python api_test.py --group auth       # Run specific group
    python api_test.py --base-url URL     # Override base URL
    python api_test.py --api-key KEY      # Use existing API key (skip reg)
    python api_test.py --no-cleanup       # Leave test data behind
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx


# ── Config ────────────────────────────────────────────────────────────────

CONFIG_PATH = Path(__file__).parent / "test-config.json"


def load_config():
    with open(CONFIG_PATH) as f:
        cfg = json.load(f)
    email = os.environ.get("LISTER_TEST_USER_EMAIL", cfg["testUser"].get("email"))
    if not email:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        email = f"apitest+{ts}@test.lister.ai"
    return {
        "baseUrl": os.environ.get("LISTER_BASE_URL", cfg["baseUrl"]).rstrip("/"),
        "testUser": {
            "email": email,
            "password": os.environ.get("LISTER_TEST_USER_PASSWORD", cfg["testUser"]["password"]),
            "name": cfg["testUser"].get("name", "API Test Bot"),
        },
        "testData": cfg.get("testData", {}),
        "apiKey": os.environ.get("LISTER_API_KEY"),
    }


# ── Result tracking ───────────────────────────────────────────────────────

class Results:
    def __init__(self):
        self.items = []
        self.t0 = time.time()

    def record(self, group, name, status, code=None, secs=None, detail=None):
        self.items.append(dict(group=group, name=name, status=status,
                               code=code, secs=secs, detail=detail))

    @property
    def passed(self):
        return sum(1 for r in self.items if r["status"] == "pass")

    @property
    def failed(self):
        return sum(1 for r in self.items if r["status"] == "fail")

    @property
    def skipped(self):
        return sum(1 for r in self.items if r["status"] == "skip")

    def summary(self):
        t = time.time() - self.t0
        print("\n" + "═" * 50)
        if self.failed == 0:
            print(f"Results: {self.passed} passed ✅ | {self.failed} failed ❌ | {self.skipped} skipped ⏭️")
        else:
            print(f"Results: {self.passed} passed ✅ | {self.failed} failed ❌ | {self.skipped} skipped ⏭️")
            for r in self.items:
                if r["status"] == "fail":
                    print(f"  ❌ [{r['group']}] {r['name']} — {r['detail']}")
        print(f"Total: {t:.1f}s")
        print("═" * 50)


# ── Client ────────────────────────────────────────────────────────────────

class Client:
    def __init__(self, base_url):
        self.base = base_url
        self.h = httpx.Client(base_url=base_url, timeout=30.0)

    def set_auth(self, token):
        self.h.headers["Authorization"] = f"Bearer {token}"

    def set_api_key(self, key):
        self.h.headers["X-API-Key"] = key

    def req(self, method, path, **kw):
        url = path if path.startswith("/api") else f"/api{path}"
        return self.h.request(method, url, **kw)

    def close(self):
        self.h.close()


# ── State & helpers ──────────────────────────────────────────────────────

S = {}  # shared state across tests (populated by extract)


def resolve(path):
    """Replace {key} placeholders with values from state."""
    for k, v in S.items():
        path = path.replace("{" + k + "}", str(v))
    return path


def check(results, client, group, name, method, path, expected=200,
          extract=None, verbose=False, skip_if=None, **kw):
    """Run one API call, record pass/fail, optionally extract fields into S."""
    if skip_if and not skip_if(S):
        results.record(group, name, "skip", detail="precondition")
        return None

    t0 = time.time()
    try:
        path = resolve(path)
        r = client.req(method, path, **kw)
        dt = time.time() - t0
        ok = r.status_code == expected
        if ok:
            results.record(group, name, "pass", r.status_code, dt)
        else:
            d = f"Expected {expected}, got {r.status_code}"
            try:
                d += f" — {r.json()}"
            except Exception:
                d += f" — {r.text[:200]}"
            results.record(group, name, "fail", r.status_code, dt, d)

        icon = "✅" if ok else "❌"
        if verbose:
            print(f"  {icon} {name}... {r.status_code} ({dt:.2f}s)")
        else:
            print(f"  {icon} {r.status_code} ({dt:.2f}s)")

        if extract and ok:
            try:
                body = r.json()
                for jq, sk in extract.items():
                    v = body
                    for part in jq.split("."):
                        v = v[part] if isinstance(v, dict) and part in v else None
                        if v is None:
                            break
                    if v is not None:
                        S[sk] = v
            except Exception:
                pass
        return r
    except Exception as e:
        dt = time.time() - t0
        results.record(group, name, "fail", detail=str(e))
        print(f"  ❌ {name}... EXCEPTION: {e}")
        return None


# ── Test groups ───────────────────────────────────────────────────────────

def test_auth(R, c, cfg, v):
    u = cfg["testUser"]
    check(R, c, "auth", "Register test user", "POST", "/api/auth/register",
          expected=201, json=dict(email=u["email"], password=u["password"], name=u["name"]),
          extract={"_id": "userId", "token": "authToken"}, verbose=v)
    if "authToken" in S:
        c.set_auth(S["authToken"])

    check(R, c, "auth", "Login", "POST", "/api/auth/login",
          expected=200, json=dict(email=u["email"], password=u["password"]),
          extract={"token": "loginToken"}, verbose=v)
    if "loginToken" in S and "authToken" not in S:
        c.set_auth(S["loginToken"]); S["authToken"] = S["loginToken"]

    check(R, c, "auth", "Get current user", "GET", "/api/auth/me",
          expected=200, extract={"email": "userEmail"}, verbose=v,
          skip_if=lambda s: "authToken" not in s)

    check(R, c, "auth", "Create API key", "POST", "/api/keys",
          expected=201, json={"name": "API Test Key"},
          extract={"key": "apiKey"}, verbose=v,
          skip_if=lambda s: "authToken" not in s)
    if "apiKey" in S:
        c.set_api_key(S["apiKey"])


def test_lists(R, c, cfg, v):
    td = cfg["testData"]
    ts = datetime.now(timezone.utc).strftime("%H%M%S")
    nm = f"{td.get('listName', 'Test List')} {ts}"

    check(R, c, "lists", "Get all lists", "GET", "/api/lists",
          expected=200, extract={"lists": "allLists"}, verbose=v)

    # Verify defaults
    if "allLists" in S:
        for d in ["To-Do", "Do soon!", "Journal", "Quick Takes"]:
            found = any(l["name"] == d for l in S["allLists"])
            R.record("lists", f"Default: {d}", "pass" if found else "fail",
                     detail="not found" if not found else None)

    check(R, c, "lists", "Create list", "POST", "/api/lists",
          expected=201, json=dict(name=nm, description="Created by API test"),
          extract={"_id": "testListId"}, verbose=v)

    check(R, c, "lists", "Get list by ID", "GET", "/api/lists/{testListId}",
          expected=200, verbose=v, skip_if=lambda s: "testListId" not in s)

    check(R, c, "lists", "Update list", "PUT", "/api/lists/{testListId}",
          expected=200, json=dict(name=f"{nm} (updated)"), verbose=v,
          skip_if=lambda s: "testListId" not in s)


def test_items(R, c, cfg, v):
    td = cfg["testData"]
    check(R, c, "items", "Add item", "POST", "/api/lists/{testListId}/items",
          expected=201, json=dict(content=td.get("itemText", "test item"), isPriority=True),
          extract={"_id": "testItemId"}, verbose=v, skip_if=lambda s: "testListId" not in s)

    check(R, c, "items", "Add second item", "POST", "/api/lists/{testListId}/items",
          expected=201, json=dict(content="test item #2"),
          extract={"_id": "testItemId2"}, verbose=v, skip_if=lambda s: "testListId" not in s)

    check(R, c, "items", "Get items in list", "GET", "/api/lists/{testListId}/items",
          expected=200, verbose=v, skip_if=lambda s: "testListId" not in s)

    check(R, c, "items", "Get item by ID", "GET", "/api/items/{testItemId}",
          expected=200, verbose=v, skip_if=lambda s: "testItemId" not in s)

    check(R, c, "items", "Update item", "PUT", "/api/items/{testItemId}",
          expected=200, json=dict(content="test item (updated)"), verbose=v,
          skip_if=lambda s: "testItemId" not in s)

    # Move item to first list
    if "allLists" in S and S["allLists"]:
        tid = S["allLists"][0]["_id"]
        check(R, c, "items", "Move item", "PUT",
              f"/api/items/{{testItemId2}}/move",
              expected=200, json=dict(listId=tid), verbose=v,
              skip_if=lambda s: "testItemId2" not in s)

    check(R, c, "items", "Mark complete", "PUT", "/api/items/{testItemId}",
          expected=200, json=dict(status="complete"), verbose=v,
          skip_if=lambda s: "testItemId" not in s)


def test_priority(R, c, cfg, v):
    check(R, c, "priority", "Get priority items", "GET", "/api/items/priority",
          expected=200, verbose=v)
    check(R, c, "priority", "Un-priority item", "PUT", "/api/items/{testItemId}",
          expected=200, json=dict(isPriority=False), verbose=v,
          skip_if=lambda s: "testItemId" not in s)


def test_notes(R, c, cfg, v):
    td = cfg["testData"]
    si = lambda s: "testItemId" in s

    check(R, c, "notes", "Add note", "POST", "/api/items/{testItemId}/notes",
          expected=201, json=dict(text=td.get("noteText", "test note")),
          extract={"_id": "testNoteId"}, verbose=v, skip_if=si)

    check(R, c, "notes", "Add second note", "POST", "/api/items/{testItemId}/notes",
          expected=201, json=dict(text="test note #2"),
          extract={"_id": "testNoteId2"}, verbose=v, skip_if=si)

    check(R, c, "notes", "Update note", "PUT", "/api/items/{testItemId}/notes/{testNoteId}",
          expected=200, json=dict(text="test note (updated)"), verbose=v, skip_if=si)

    check(R, c, "notes", "Update note status", "PATCH",
          "/api/items/{testItemId}/notes/{testNoteId}/status",
          expected=200, json=dict(status="done"), verbose=v, skip_if=si)

    check(R, c, "notes", "Delete note", "DELETE",
          "/api/items/{testItemId}/notes/{testNoteId2}",
          expected=200, verbose=v, skip_if=si)


def test_search(R, c, cfg, v):
    q = cfg["testData"].get("searchQuery", "test")
    check(R, c, "search", "Basic search", "GET", "/api/search",
          expected=200, params=dict(q=q), verbose=v)
    check(R, c, "search", "Search with limit", "GET", "/api/search",
          expected=200, params=dict(q=q, limit=2), verbose=v)
    check(R, c, "search", "Search with notes", "GET", "/api/search",
          expected=200, params=dict(q=q, includeNotes="true"), verbose=v)


def test_summary(R, c, cfg, v):
    check(R, c, "summary", "Get summary", "GET", "/api/lists/summary",
          expected=200, verbose=v)
    check(R, c, "summary", "Summary with archived", "GET", "/api/lists/summary",
          expected=200, params=dict(include_archived="true"), verbose=v)


def test_archive(R, c, cfg, v):
    ts = datetime.now(timezone.utc).strftime("%H%M%S")
    check(R, c, "archive", "Create list for archive", "POST", "/api/lists",
          expected=201, json=dict(name=f"Archive Test {ts}"),
          extract={"_id": "archiveListId"}, verbose=v)

    check(R, c, "archive", "Archive list", "PUT", "/api/lists/{archiveListId}/archive",
          expected=200, json=dict(archived=True), verbose=v,
          skip_if=lambda s: "archiveListId" not in s)

    check(R, c, "archive", "Get archived lists", "GET", "/api/lists",
          expected=200, params=dict(includeArchived="true"), verbose=v,
          skip_if=lambda s: "archiveListId" not in s)

    check(R, c, "archive", "Unarchive list", "PUT", "/api/lists/{archiveListId}/archive",
          expected=200, json=dict(archived=False), verbose=v,
          skip_if=lambda s: "archiveListId" not in s)


def test_sharing(R, c, cfg, v):
    ts = datetime.now(timezone.utc).strftime("%H%M%S")
    se = f"apitest2+{ts}@test.lister.ai"
    pw = cfg["testUser"]["password"]

    check(R, c, "sharing", "Register share target", "POST", "/api/auth/register",
          expected=201, json=dict(email=se, password=pw, name="Share Target"),
          extract={"_id": "shareUserId"}, verbose=v)

    check(R, c, "sharing", "Create share list", "POST", "/api/lists",
          expected=201, json=dict(name=f"Share Test {ts}"),
          extract={"_id": "shareListId"}, verbose=v)

    check(R, c, "sharing", "Share list", "POST", "/api/lists/{shareListId}/share",
          expected=200, json=dict(email=se, permission="view"), verbose=v,
          skip_if=lambda s: "shareListId" not in s)

    check(R, c, "sharing", "List users", "GET", "/api/lists/{shareListId}/share",
          expected=200, extract={"users": "shareUsers"}, verbose=v,
          skip_if=lambda s: "shareListId" not in s)

    # Remove shared user
    uid = None
    if "shareUsers" in S:
        for u in S["shareUsers"]:
            if u.get("email") == se or u.get("user", {}).get("email") == se:
                uid = u.get("userId") or u.get("_id")
                break
    if uid and "shareListId" in S:
        check(R, c, "sharing", "Remove user", "DELETE",
              f"/api/lists/{S['shareListId']}/share/{uid}", expected=200, verbose=v)
    else:
        R.record("sharing", "Remove user", "skip", detail="user ID not found")
    S["shareTargetEmail"] = se


def test_export(R, c, cfg, v):
    si = lambda s: "testListId" not in s
    check(R, c, "export", "Export JSON", "GET", "/api/lists/{testListId}/export",
          expected=200, params=dict(format="json"), verbose=v, skip_if=si)
    check(R, c, "export", "Export HTML", "GET", "/api/lists/{testListId}/export",
          expected=200, params=dict(format="html"), verbose=v, skip_if=si)


def test_cleanup(R, c, cfg, v):
    print("\n🧹 Cleaning up...")
    for k in ["testItemId", "testItemId2"]:
        if k in S:
            check(R, c, "cleanup", f"Delete item {k}", "DELETE", f"/api/items/{S[k]}", expected=200, verbose=v)
    for k in ["testListId", "archiveListId", "shareListId"]:
        if k in S:
            check(R, c, "cleanup", f"Delete list {k}", "DELETE", f"/api/lists/{S[k]}", expected=200, verbose=v)
    # Delete test user
    if "authToken" in S:
        check(R, c, "cleanup", "Delete test user", "DELETE", "/api/auth/account",
              expected=200, verbose=v)
    # Delete share target user (if we can)
    if "shareTargetEmail" in S and "shareTargetEmail" not in S.get("deleted_users", []):
        # Log in as share target — can't easily without their token
        R.record("cleanup", "Delete share target user", "skip",
                 detail="no token for 2nd user")


# ── Registry ──────────────────────────────────────────────────────────────

GROUPS = [
    ("auth",     test_auth),
    ("lists",    test_lists),
    ("items",    test_items),
    ("priority", test_priority),
    ("notes",    test_notes),
    ("search",   test_search),
    ("summary",  test_summary),
    ("archive",  test_archive),
    ("sharing",  test_sharing),
    ("export",   test_export),
    ("cleanup",  test_cleanup),
]


# ── Main ─────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(description="Lister API Test Suite")
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument("--group", type=str, help="Run one group only")
    p.add_argument("--base-url", type=str, help="Override base URL")
    p.add_argument("--api-key", type=str, help="Existing API key")
    p.add_argument("--no-cleanup", action="store_true", help="Leave test data")
    args = p.parse_args()

    cfg = load_config()
    if args.base_url:
        cfg["baseUrl"] = args.base_url.rstrip("/")
    if args.api_key:
        cfg["apiKey"] = args.api_key

    global S
    S = {}
    if cfg.get("apiKey"):
        S["apiKey"] = cfg["apiKey"]

    client = Client(cfg["baseUrl"])
    R = Results()

    if cfg.get("apiKey"):
        client.set_api_key(cfg["apiKey"])

    print("🧪 Lister API Test Suite")
    print("═" * 50)
    print(f"Base URL: {cfg['baseUrl']}")
    print(f"Test user: {cfg['testUser']['email']}")
    print()

    groups = GROUPS
    if args.group:
        groups = [(n, fn) for n, fn in GROUPS if n == args.group]
        if not groups:
            print(f"❌ Unknown group: {args.group}")
            print(f"Available: {', '.join(g[0] for g in GROUPS)}")
            sys.exit(1)
        if args.group != "cleanup" and not args.no_cleanup:
            groups += [("cleanup", test_cleanup)]
    if args.no_cleanup:
        groups = [(n, fn) for n, fn in groups if n != "cleanup"]

    for i, (name, fn) in enumerate(groups, 1):
        print(f"\n[{i}/{len(groups)}] {name.upper()}")
        print("─" * 30)
        try:
            fn(R, client, cfg, args.verbose)
        except Exception as e:
            print(f"  ❌ Group crashed: {e}")

    R.summary()
    client.close()
    sys.exit(1 if R.failed > 0 else 0)


if __name__ == "__main__":
    main()