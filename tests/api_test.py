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
        url = path if path.startswith(("/api", "/v1")) else f"/v1{path}"
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


def check(R, client, group, name, method, path, expected=200,
          extract=None, verbose=False, skip_if=None, **kw):
    """Run one API call, record pass/fail, optionally extract fields into S."""
    if skip_if and skip_if(S):
        R.record(group, name, "skip", detail="precondition")
        return None

    t0 = time.time()
    try:
        path = resolve(path)
        r = client.req(method, path, **kw)
        dt = time.time() - t0
        ok = r.status_code == expected
        if ok:
            R.record(group, name, "pass", r.status_code, dt)
        else:
            d = f"Expected {expected}, got {r.status_code}"
            try:
                d += f" — {r.json()}"
            except Exception:
                d += f" — {r.text[:200]}"
            R.record(group, name, "fail", r.status_code, dt, d)

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
        # Store last response for manual extraction
        S["_last_resp"] = r
        return r
    except Exception as e:
        dt = time.time() - t0
        R.record(group, name, "fail", detail=str(e))
        print(f"  ❌ {name}... EXCEPTION: {e}")
        return None


# ── Test groups ───────────────────────────────────────────────────────────
# Endpoints derived from OpenAPI spec at /openapi.json (v0.90.0)

def test_auth(R, c, cfg, v):
    u = cfg["testUser"]

    # Register — response shape: {success, data: {access_token, user: {id, ...}}}
    check(R, c, "auth", "Register test user", "POST", "/api/auth/register",
          expected=201, json=dict(email=u["email"], password=u["password"], name=u["name"]))
    # Extract nested fields manually
    if S.get("_last_resp") and S["_last_resp"].status_code == 201:
        try:
            d = S["_last_resp"].json()["data"]
            S["userId"] = d["user"]["id"]
            S["authToken"] = d["access_token"]
        except Exception:
            pass
    if "authToken" in S:
        c.set_auth(S["authToken"])

    # Login
    check(R, c, "auth", "Login", "POST", "/api/auth/login",
          expected=200, json=dict(email=u["email"], password=u["password"]))
    if S.get("_last_resp") and S["_last_resp"].status_code == 200:
        try:
            d = S["_last_resp"].json()["data"]
            S["userId"] = d["user"]["id"]
            if "authToken" not in S:
                S["authToken"] = d["access_token"]
                c.set_auth(S["authToken"])
        except Exception:
            pass

    # Get current user (GET /api/auth/me)
    check(R, c, "auth", "Get current user", "GET", "/api/auth/me",
          expected=200, verbose=v,
          skip_if=lambda s: "authToken" not in s)

    # Create API key (POST /api/auth/api-keys) — returns 201
    check(R, c, "auth", "Create API key", "POST", "/api/auth/api-keys",
          expected=201, json={"name": "API Test Key"}, verbose=v,
          skip_if=lambda s: "authToken" not in s)
    if S.get("_last_resp") and S["_last_resp"].status_code == 201:
        try:
            body = S["_last_resp"].json()
            # API key response is flat {id, key, name} — not wrapped in data
            if "key" in body:
                S["apiKey"] = body["key"]
            elif "data" in body and "key" in body["data"]:
                S["apiKey"] = body["data"]["key"]
        except Exception:
            pass
    if "apiKey" in S:
        c.set_api_key(S["apiKey"])
        # v1 endpoints only accept X-API-Key; remove Authorization to avoid conflicts
        c.h.headers.pop("Authorization", None)


def test_lists(R, c, cfg, v):
    td = cfg["testData"]
    ts = datetime.now(timezone.utc).strftime("%H%M%S")
    nm = f"{td.get('listName', 'Test List')} {ts}"

    # GET /v1/lists
    check(R, c, "lists", "Get all lists", "GET", "/v1/lists",
          expected=200, verbose=v)
    # Extract lists from response
    if S.get("_last_resp") and S["_last_resp"].status_code == 200:
        try:
            body = S["_last_resp"].json()
            S["allLists"] = body.get("data", body) if isinstance(body, dict) else body
        except Exception:
            pass

    # Verify defaults exist
    if "allLists" in S:
        for d in ["To-Do", "Do soon!", "Journal", "Quick Takes"]:
            found = any(l["name"] == d for l in S["allLists"])
            R.record("lists", f"Default: {d}", "pass" if found else "fail",
                     detail="not found" if not found else None)

    # POST /v1/lists
    check(R, c, "lists", "Create list", "POST", "/v1/lists",
          expected=201, json=dict(name=nm, description="Created by API test"), verbose=v)
    # Extract list ID from nested response
    if S.get("_last_resp") and S["_last_resp"].status_code == 201:
        try:
            d = S["_last_resp"].json()["data"]
            S["testListId"] = d.get("id") or d.get("_id")
        except Exception:
            pass

    # GET /v1/lists/{list_id}
    check(R, c, "lists", "Get list by ID", "GET", "/v1/lists/{testListId}",
          expected=200, verbose=v, skip_if=lambda s: "testListId" not in s)

    # PUT /v1/lists/{list_id}
    check(R, c, "lists", "Update list", "PUT", "/v1/lists/{testListId}",
          expected=200, json=dict(name=f"{nm} (updated)"), verbose=v,
          skip_if=lambda s: "testListId" not in s)


def test_items(R, c, cfg, v):
    td = cfg["testData"]
    si = lambda s: "testListId" not in s

    # POST /v1/lists/{list_id}/items — type, status required; listId NOT in body (derived from URL)
    check(R, c, "items", "Add item", "POST", "/v1/lists/{testListId}/items",
          expected=201, json=dict(content=td.get("itemText", "test item"),
                                    type="text", status="new", isPriority=True),
          verbose=v, skip_if=si)
    if S.get("_last_resp") and S["_last_resp"].status_code == 201:
        try:
            d = S["_last_resp"].json()["data"]
            S["testItemId"] = d.get("id") or d.get("_id")
        except Exception:
            pass

    # Add second item for move/delete
    check(R, c, "items", "Add second item", "POST", "/v1/lists/{testListId}/items",
          expected=201, json=dict(content="test item #2", type="text", status="new"),
          verbose=v, skip_if=si)
    if S.get("_last_resp") and S["_last_resp"].status_code == 201:
        try:
            d = S["_last_resp"].json()["data"]
            S["testItemId2"] = d.get("id") or d.get("_id")
        except Exception:
            pass

    # GET /v1/lists/{list_id}/items
    check(R, c, "items", "Get items in list", "GET", "/v1/lists/{testListId}/items",
          expected=200, verbose=v, skip_if=si)

    # PATCH /v1/items/{item_id} — update item
    check(R, c, "items", "Update item text", "PATCH", "/v1/items/{testItemId}",
          expected=200, json=dict(content="test item (updated)"), verbose=v,
          skip_if=lambda s: "testItemId" not in s)

    # POST /v1/items/{item_id}/move — requires targetListId
    if "allLists" in S and S["allLists"]:
        tid = S["allLists"][0].get("id") or S["allLists"][0].get("_id")
        check(R, c, "items", "Move item", "POST",
              f"/v1/items/{{testItemId2}}/move",
              expected=200, json=dict(targetListId=tid), verbose=v,
              skip_if=lambda s: "testItemId2" not in s)

    # PATCH /v1/items/{item_id} — mark complete
    check(R, c, "items", "Mark complete", "PATCH", "/v1/items/{testItemId}",
          expected=200, json=dict(status="complete"), verbose=v,
          skip_if=lambda s: "testItemId" not in s)


def test_priority(R, c, cfg, v):
    # GET /v1/items/priority
    check(R, c, "priority", "Get priority items", "GET", "/v1/items/priority",
          expected=200, verbose=v)

    # Un-priority the test item
    check(R, c, "priority", "Un-priority item", "PATCH", "/v1/items/{testItemId}",
          expected=200, json=dict(isPriority=False), verbose=v,
          skip_if=lambda s: "testItemId" not in s)


def test_notes(R, c, cfg, v):
    td = cfg["testData"]
    si = lambda s: "testItemId" not in s

    # POST /v1/items/{item_id}/notes — uses `content` not `text`, returns 201
    check(R, c, "notes", "Add note", "POST", "/v1/items/{testItemId}/notes",
          expected=201, json=dict(content=td.get("noteText", "test note")),
          verbose=v, skip_if=si)
    if S.get("_last_resp") and S["_last_resp"].status_code == 201:
        try:
            d = S["_last_resp"].json()["data"]
            S["testNoteId"] = d.get("id") or d.get("_id")
        except Exception:
            pass

    # Add second note for delete test
    check(R, c, "notes", "Add second note", "POST", "/v1/items/{testItemId}/notes",
          expected=201, json=dict(content="test note #2"), verbose=v, skip_if=si)
    if S.get("_last_resp") and S["_last_resp"].status_code == 201:
        try:
            d = S["_last_resp"].json()["data"]
            S["testNoteId2"] = d.get("id") or d.get("_id")
        except Exception:
            pass

    # PUT /v1/items/{item_id}/notes/{note_id} — uses `content` field
    check(R, c, "notes", "Update note", "PUT", "/v1/items/{testItemId}/notes/{testNoteId}",
          expected=200, json=dict(content="test note (updated)"), verbose=v, skip_if=si)

    # PATCH /v1/items/{item_id}/notes/{note_id}/status — status must be 'new' or 'complete'
    check(R, c, "notes", "Update note status", "PATCH",
          "/v1/items/{testItemId}/notes/{testNoteId}/status",
          expected=200, json=dict(status="complete"), verbose=v, skip_if=si)

    # DELETE /v1/items/{item_id}/notes/{note_id}
    check(R, c, "notes", "Delete note", "DELETE",
          "/v1/items/{testItemId}/notes/{testNoteId2}",
          expected=200, verbose=v, skip_if=si)


def test_search(R, c, cfg, v):
    q = cfg["testData"].get("searchQuery", "test")
    # GET /v1/search
    check(R, c, "search", "Basic search", "GET", "/v1/search",
          expected=200, params=dict(q=q), verbose=v)
    check(R, c, "search", "Search with limit", "GET", "/v1/search",
          expected=200, params=dict(q=q, limit=2), verbose=v)
    check(R, c, "search", "Search with notes", "GET", "/v1/search",
          expected=200, params=dict(q=q, includeNotes="true"), verbose=v)


def test_summary(R, c, cfg, v):
    # GET /v1/lists/summary
    check(R, c, "summary", "Get summary", "GET", "/v1/lists/summary",
          expected=200, verbose=v)
    check(R, c, "summary", "Summary with archived", "GET", "/v1/lists/summary",
          expected=200, params=dict(include_archived="true"), verbose=v)


def test_archive(R, c, cfg, v):
    ts = datetime.now(timezone.utc).strftime("%H%M%S")

    # Create a list to archive
    check(R, c, "archive", "Create list for archive", "POST", "/v1/lists",
          expected=201, json=dict(name=f"Archive Test {ts}"), verbose=v)
    if S.get("_last_resp") and S["_last_resp"].status_code == 201:
        try:
            d = S["_last_resp"].json()["data"]
            S["archiveListId"] = d.get("id") or d.get("_id")
        except Exception:
            pass

    # PUT /v1/lists/{list_id}/archive — archive
    check(R, c, "archive", "Archive list", "PUT", "/v1/lists/{archiveListId}/archive",
          expected=200, json=dict(archived=True), verbose=v,
          skip_if=lambda s: "archiveListId" not in s)

    # GET /v1/lists?includeArchived=true — verify it appears
    check(R, c, "archive", "Get archived lists", "GET", "/v1/lists",
          expected=200, params=dict(includeArchived="true"), verbose=v,
          skip_if=lambda s: "archiveListId" not in s)

    # PUT /v1/lists/{list_id}/archive — unarchive
    check(R, c, "archive", "Unarchive list", "PUT", "/v1/lists/{archiveListId}/archive",
          expected=200, json=dict(archived=False), verbose=v,
          skip_if=lambda s: "archiveListId" not in s)


def test_sharing(R, c, cfg, v):
    ts = datetime.now(timezone.utc).strftime("%H%M%S")
    se = f"apitest2+{ts}@test.lister.ai"
    pw = cfg["testUser"]["password"]

    # Register a second user to share with
    check(R, c, "sharing", "Register share target", "POST", "/api/auth/register",
          expected=201, json=dict(email=se, password=pw, name="Share Target"), verbose=v)
    if S.get("_last_resp") and S["_last_resp"].status_code == 201:
        try:
            d = S["_last_resp"].json()["data"]
            S["shareUserId"] = d["user"]["id"]
        except Exception:
            pass

    # Create a list to share
    check(R, c, "sharing", "Create share list", "POST", "/v1/lists",
          expected=201, json=dict(name=f"Share Test {ts}"), verbose=v)
    if S.get("_last_resp") and S["_last_resp"].status_code == 201:
        try:
            d = S["_last_resp"].json()["data"]
            S["shareListId"] = d.get("id") or d.get("_id")
        except Exception:
            pass

    # POST /v1/lists/{list_id}/share — requires userId (not email), permission: read/edit/admin
    if "shareUserId" in S and "shareListId" in S:
        check(R, c, "sharing", "Share list", "POST", "/v1/lists/{shareListId}/share",
              expected=200,
              json=dict(userId=S["shareUserId"], permission="read"),
              verbose=v, skip_if=lambda s: "shareListId" not in s)
    else:
        R.record("sharing", "Share list", "skip", detail="shareUserId not available")

    # GET /v1/lists/{list_id}/users
    check(R, c, "sharing", "List users with access", "GET",
          "/v1/lists/{shareListId}/users",
          expected=200, verbose=v,
          skip_if=lambda s: "shareListId" not in s)

    # Remove shared user — DELETE /v1/lists/{list_id}/users/{user_id}
    if "shareUserId" in S and "shareListId" in S:
        check(R, c, "sharing", "Remove user", "DELETE",
              f"/v1/lists/{{shareListId}}/users/{{shareUserId}}",
              expected=200, verbose=v)
    else:
        R.record("sharing", "Remove user", "skip", detail="user ID not found")

    S["shareTargetEmail"] = se


def test_export(R, c, cfg, v):
    si = lambda s: "testListId" not in s
    # Export uses POST with body
    check(R, c, "export", "Export JSON", "POST", "/v1/lists/{testListId}/export",
          expected=200, json=dict(format="json"), verbose=v, skip_if=si)
    check(R, c, "export", "Export HTML", "POST", "/v1/lists/{testListId}/export",
          expected=200, json=dict(format="html"), verbose=v, skip_if=si)
def test_cleanup(R, c, cfg, v):
    print("\n🧹 Cleaning up...")

    # Delete test items
    for k in ["testItemId", "testItemId2"]:
        if k in S:
            check(R, c, "cleanup", f"Delete item {k}", "DELETE",
                  f"/v1/items/{S[k]}", expected=200, verbose=v)

    # Delete test lists
    for k in ["testListId", "archiveListId", "shareListId"]:
        if k in S:
            check(R, c, "cleanup", f"Delete list {k}", "DELETE",
                  f"/v1/lists/{S[k]}", expected=200, verbose=v)

    # DELETE /api/auth/me — user self-deletion (now functional)
    # Do this BEFORE deleting API keys so auth still works
    if "authToken" in S:
        # Delete share target user first (use their own token)
        if "shareTargetEmail" in S:
            try:
                login_resp = c.req("POST", "/api/auth/login",
                                   json=dict(email=S["shareTargetEmail"],
                                             password=cfg["testUser"]["password"]))
                if login_resp.status_code == 200:
                    share_token = login_resp.json()["data"]["access_token"]
                    # Switch to share target user's auth
                    c.h.headers.pop("X-API-Key", None)
                    c.set_auth(share_token)
                    check(R, c, "cleanup", "Delete share target user", "DELETE",
                          "/api/auth/me", expected=200, verbose=v)
            except Exception as e:
                R.record("cleanup", "Delete share target user", "fail", detail=str(e))
        else:
            R.record("cleanup", "Delete share target user", "skip",
                     detail="No share target email")

        # Delete main test user (switch back to main auth)
        c.h.headers.pop("X-API-Key", None)
        c.set_auth(S["authToken"])
        check(R, c, "cleanup", "Delete test user", "DELETE",
              "/api/auth/me", expected=200, verbose=v)
    else:
        R.record("cleanup", "Delete test user", "skip",
                 detail="No auth token available")
        R.record("cleanup", "Delete share target user", "skip",
                 detail="No auth token available")

    # Delete API keys last
    if "authToken" in S:
        # List API keys
        resp = c.req("GET", "/api/auth/api-keys")
        if resp.status_code == 200:
            try:
                keys = resp.json()
                keys_list = keys if isinstance(keys, list) else keys.get("data", keys.get("keys", []))
                for k in keys_list:
                    kid = k.get("id") or k.get("_id")
                    if kid:
                        check(R, c, "cleanup", f"Delete API key {kid}", "DELETE",
                              f"/api/auth/api-keys/{kid}", expected=200, verbose=v)
            except Exception:
                pass


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