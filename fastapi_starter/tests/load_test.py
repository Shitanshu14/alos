"""
Week 15 — Load testing.

A quick, dependency-free load test: fires concurrent requests at a
running ALOS instance and reports latency percentiles + error rate.
Not a replacement for a real tool (Locust/k6) before a real launch -
this is meant as a fast sanity check you can run with nothing but
Python's standard library.

Usage:
    python tests/load_test.py --url http://localhost:8000 --requests 200 --concurrency 20
"""
import argparse
import statistics
import time
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed


def _timed_request(url: str) -> tuple:
    """Returns (elapsed_ms, status_code_or_None)."""
    start = time.monotonic()
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            status_code = resp.status
    except urllib.error.HTTPError as e:
        status_code = e.code
    except Exception:
        status_code = None
    elapsed_ms = (time.monotonic() - start) * 1000
    return elapsed_ms, status_code


def run_load_test(base_url: str, endpoint: str, total_requests: int, concurrency: int) -> None:
    url = f"{base_url.rstrip('/')}{endpoint}"
    print(f"\n=== Load testing {url} ===")
    print(f"{total_requests} requests, {concurrency} concurrent workers")

    latencies = []
    errors = 0
    start = time.monotonic()

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [pool.submit(_timed_request, url) for _ in range(total_requests)]
        for future in as_completed(futures):
            elapsed_ms, status_code = future.result()
            latencies.append(elapsed_ms)
            if status_code is None or status_code >= 500:
                errors += 1

    total_time = time.monotonic() - start
    latencies.sort()

    def percentile(p):
        idx = min(int(len(latencies) * p / 100), len(latencies) - 1)
        return latencies[idx]

    print(f"Total wall time:   {total_time:.2f}s")
    print(f"Throughput:        {total_requests / total_time:.1f} req/s")
    print(f"Error rate:        {errors}/{total_requests} ({errors / total_requests * 100:.1f}%)")
    print(f"Latency (ms) min:  {min(latencies):.1f}")
    print(f"Latency (ms) p50:  {percentile(50):.1f}")
    print(f"Latency (ms) p95:  {percentile(95):.1f}")
    print(f"Latency (ms) p99:  {percentile(99):.1f}")
    print(f"Latency (ms) max:  {max(latencies):.1f}")
    print(f"Latency (ms) mean: {statistics.mean(latencies):.1f}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Quick load test for the ALOS API")
    parser.add_argument("--url", default="http://localhost:8000", help="Base URL of the running instance")
    parser.add_argument("--requests", type=int, default=200, help="Total requests per endpoint")
    parser.add_argument("--concurrency", type=int, default=20, help="Concurrent workers")
    args = parser.parse_args()

    run_load_test(args.url, "/health", args.requests, args.concurrency)
    run_load_test(args.url, "/content-library/", args.requests, args.concurrency)
