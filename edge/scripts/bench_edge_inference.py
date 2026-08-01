#!/usr/bin/env python3
"""Benchmark inference-only p95 (architecture gate: ≤150 ms @ 320 INT8 on target HW)."""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path

import numpy as np

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "edge"))

from rasaops_edge.inference.factory import create_backend


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--backend", default="auto")
    p.add_argument("--model", default=None)
    p.add_argument("--size", type=int, default=320)
    p.add_argument("--iters", type=int, default=30)
    p.add_argument("--warmup", type=int, default=5)
    p.add_argument("--gate-ms", type=float, default=150.0, help="p95 gate (x86 CI soft)")
    p.add_argument("--enforce-gate", action="store_true")
    args = p.parse_args()

    backend = create_backend(backend=args.backend, model_path=args.model, input_size=args.size)
    print(f"backend={type(backend).__name__} model_version={backend.model_version}")

    frame = np.random.randint(0, 255, (args.size, args.size, 3), dtype=np.uint8)
    for _ in range(args.warmup):
        backend.predict(frame)

    times: list[float] = []
    n_dets = 0
    for _ in range(args.iters):
        t0 = time.perf_counter()
        dets = backend.predict(frame)
        times.append((time.perf_counter() - t0) * 1000.0)
        n_dets = len(dets)

    times_sorted = sorted(times)
    p50 = statistics.median(times_sorted)
    p95 = times_sorted[max(0, int(0.95 * (len(times_sorted) - 1)))]
    print(f"iters={args.iters} last_dets={n_dets}")
    print(f"inference_ms p50={p50:.2f} p95={p95:.2f} mean={statistics.mean(times):.2f}")

    if args.enforce_gate and p95 > args.gate_ms:
        print(f"FAIL gate p95 {p95:.2f} > {args.gate_ms}")
        return 2
    print("bench_edge_inference: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
