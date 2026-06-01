# Lister API Tests

Comprehensive API test suite for [Lister.ai](https://lister.ai). Run after deployments to catch regressions.

## Quick Start

```bash
# Install dependencies
pip install httpx

# Set config via env vars (or edit test-config.json)
export LISTER_BASE_URL="https://lister-api-staging.up.railway.app"
export LISTER_API_KEY="your-api-key-here"

# Run all tests
python tests/api_test.py

# Run with verbose output
python tests/api_test.py -v

# Run a specific test group
python tests/api_test.py --group auth
python tests/api_test.py --group lists
python tests/api_test.py --group items
python tests/api_test.py --group notes
python tests/api_test.py --group search
python tests/api_test.py --group archive
python tests/api_test.py --group sharing
python tests/api_test.py --group export
```

## What It Tests

| Group | Endpoints | Description |
|-------|-----------|-------------|
| auth | `/api/auth/register`, `/api/auth/login`, `/api/auth/me` | Register test user, login, verify identity |
| lists | `/api/lists`, CRUD + defaults | Create, read, update, delete lists; verify defaults |
| items | `/api/lists/{id}/items`, `/api/items/{id}` | Add, get, update, move, delete items |
| priority | `/api/items/priority` | Priority flag, get priority items |
| notes | `/api/items/{id}/notes`, CRUD | Add, update, delete notes |
| search | `/api/search` | Search across lists and items |
| summary | `/api/lists/summary` | List summary with counts |
| archive | `/api/lists/{id}/archive` | Archive and unarchive lists |
| sharing | `/api/lists/{id}/share`, users, remove | Share, list users, remove user |
| export | `/api/lists/{id}/export` | Export as JSON and HTML |
| cleanup | — | Delete all test data, delete test user |

## Test Flow

Tests run **sequentially** because later tests depend on data created by earlier ones:

1. Register a unique test user (email includes timestamp)
2. Login and get auth token
3. Create API key for the test user
4. Create test lists, add items, notes
5. Test all CRUD operations
6. Test search, summary, archive, sharing
7. Export lists
8. **Delete all test lists and items**
9. **Delete the test user**

## Config

Edit `tests/test-config.json` or set environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `LISTER_BASE_URL` | `https://lister-api-staging.up.railway.app` | API base URL |
| `LISTER_API_KEY` | — | Existing API key (for non-auth tests) |
| `LISTER_TEST_USER_EMAIL` | Auto-generated | Test user email |
| `LISTER_TEST_USER_PASSWORD` | `TestP@ss123!` | Test user password |

## Output

```
🧪 Lister API Test Suite
═══════════════════════════
Base URL: https://lister-api-staging.up.railway.app

[1/12] AUTH — Register test user... ✅ 201 (0.42s)
[2/12] AUTH — Login... ✅ 200 (0.31s)
[3/12] LISTS — Create list... ✅ 201 (0.28s)
...
═══════════════════════════
Results: 24/24 passed ✅ | 0 failed ❌ | 2 skipped ⏭️
Total time: 8.7s
```

## License

MIT