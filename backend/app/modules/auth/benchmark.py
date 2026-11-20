"""Local Argon2 measurement; does not connect to SQL or read passwords."""

import argparse
import platform
import secrets
import statistics
import time

from app.modules.auth.security import password_hasher


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--iterations", type=int, default=20)
    args = parser.parse_args()
    if args.iterations < 2:
        parser.error("Use at least two iterations")
    password = secrets.token_urlsafe(32)
    encoded = password_hasher.hash(password)
    samples = []
    for _ in range(args.iterations):
        started = time.perf_counter()
        password_hasher.verify(encoded, password)
        samples.append((time.perf_counter() - started) * 1000)
    samples.sort()
    print(f"OS={platform.system()} {platform.release()}; Python={platform.python_version()}")
    print(f"CPU={platform.processor()}; architecture={platform.machine()}")
    print(f"Argon2id m=19456KiB t=2 p=1; verify iterations={args.iterations}")
    print(
        f"min={min(samples):.2f}ms median={statistics.median(samples):.2f}ms "
        f"p95={samples[min(len(samples) - 1, int(len(samples) * 0.95))]:.2f}ms "
        f"max={max(samples):.2f}ms"
    )
    print("Measurement only; not an NFR acceptance result.")


if __name__ == "__main__":
    main()
