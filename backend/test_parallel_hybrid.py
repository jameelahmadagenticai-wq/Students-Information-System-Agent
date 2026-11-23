import urllib.request
import json
import time
import sys

BASE_URL = "http://localhost:8000/api/v1"

def login(email, password, tenant_slug="westlake"):
    data = json.dumps({"email": email, "password": password, "tenant_slug": tenant_slug}).encode("utf-8")
    req = urllib.request.Request(
        f"{BASE_URL}/auth/login",
        data=data,
        headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode("utf-8"))["access_token"]

def query_ask(token, question):
    data = json.dumps({"question": question}).encode("utf-8")
    req = urllib.request.Request(
        f"{BASE_URL}/query/ask",
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}"
        }
    )
    start = time.time()
    with urllib.request.urlopen(req) as resp:
        duration = time.time() - start
        body = json.loads(resp.read().decode("utf-8"))
        body["client_duration_ms"] = int(duration * 1000)
        return body

def main():
    print("=" * 60)
    print("TESTING 2-TIER ROUTER & PARALLEL HYBRID PIPELINE")
    print("=" * 60)

    token = login("admin@westlake.edu", "admin123")
    print("[PASS] Logged in as SuperAdmin")

    # Test 1: Pure SQL Query
    q1 = "What is the total district budget allocated and spent?"
    print(f"\n[Test 1] Pure SQL: '{q1}'")
    r1 = query_ask(token, q1)
    print(f"  -> Intent: {r1.get('intent')}")
    print(f"  -> Rows: {r1.get('row_count')}")
    print(f"  -> Summary: {r1.get('summary')[:120]}...")
    print(f"  -> Latency: {r1.get('duration_ms')}ms (client: {r1.get('client_duration_ms')}ms)")
    assert r1.get("intent") in ["budget", "sql"], f"Expected SQL intent, got {r1.get('intent')}"
    assert r1.get("row_count") >= 1, "Expected row_count >= 1"
    print("  [OK] Pure SQL query passed.")

    # Test 2: Pure RAG / Policy Query
    q2 = "What is the unexcused absence policy and SARB referral rules?"
    print(f"\n[Test 2] Pure RAG: '{q2}'")
    r2 = query_ask(token, q2)
    print(f"  -> Intent: {r2.get('intent')}")
    print(f"  -> Citations count: {len(r2.get('citations') or [])}")
    print(f"  -> Sources used: {r2.get('sources_used')}")
    print(f"  -> Summary: {r2.get('summary')[:150]}...")
    print(f"  -> Latency: {r2.get('duration_ms')}ms (client: {r2.get('client_duration_ms')}ms)")
    assert r2.get("intent") == "rag", f"Expected 'rag' intent, got {r2.get('intent')}"
    assert len(r2.get("citations") or []) > 0, "Expected citations from RAG"
    print("  [OK] Pure RAG query passed.")

    # Test 3: Parallel Hybrid Query (Policy + SQL live count)
    q3 = "What is the unexcused absence policy and how many students are enrolled?"
    print(f"\n[Test 3] Parallel Hybrid: '{q3}'")
    r3 = query_ask(token, q3)
    print(f"  -> Intent: {r3.get('intent')}")
    print(f"  -> SQL Rows: {r3.get('row_count')}")
    print(f"  -> Citations count: {len(r3.get('citations') or [])}")
    print(f"  -> Summary: {r3.get('summary')[:200]}...")
    print(f"  -> Latency: {r3.get('duration_ms')}ms (client: {r3.get('client_duration_ms')}ms)")
    assert r3.get("intent") == "hybrid", f"Expected 'hybrid' intent, got {r3.get('intent')}"
    assert r3.get("row_count") >= 1, "Expected SQL rows in hybrid result"
    assert len(r3.get("citations") or []) > 0, "Expected RAG citations in hybrid result"
    print("  [OK] Parallel Hybrid query passed.")

    # Test 4: Cache hit on Hybrid Query
    print(f"\n[Test 4] Cache hit on Hybrid: '{q3}'")
    r4 = query_ask(token, q3)
    print(f"  -> Cached: {r4.get('cached')}")
    print(f"  -> Latency: {r4.get('duration_ms')}ms (client: {r4.get('client_duration_ms')}ms)")
    assert r4.get("cached") == True or r4.get("duration_ms") < 100, "Expected fast cache hit"
    print("  [OK] Hybrid Semantic Cache hit verified.")

    print("\n" + "=" * 60)
    print("ALL 4 END-TO-END PIPELINE TESTS PASSED SUCCESSFULLY!")
    print("=" * 60)

if __name__ == "__main__":
    main()
