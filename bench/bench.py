#!/usr/bin/env python3
"""端到端计时: 对三个程序(和单进程基线)反复运行同一个任务, 统计墙钟时间与资源使用。

用法: python3 bench/bench.py [--runs N] [--warmup K] [--cpu-runs M] [--cpus LIST] [--no-baseline]
                             [--json PATH] FILE WORD [WORD ...]

  - 每一轮把每个程序各运行一次, 并轮换起始程序, 使系统状态的漂移对各程序一视同仁;
  - 标准输出和标准错误都重定向到 /dev/null, 不包含终端输出的开销;
  - 墙钟时间从 posix_spawn 之前到 wait4 返回之后, 包含 exec、动态链接和进程退出;
  - 上下文切换(vcsw 自愿 / ivcsw 非自愿)和缺页次数(minflt)来自 wait4 返回的 rusage,
    包含被父进程回收过的子进程;
  - cpu 列是 `perf stat -e task-clock` 给出的总 CPU 时间(所有进程和线程之和, 含 exec 与退出开销),
    取 M 次的平均; 系统里没有 perf 时显示 n/a;
  - 正式计时之前, 先检查所有程序对该任务的输出完全一致。
"""
import argparse
import hashlib
import json
import math
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BIN_DIR = os.path.join(ROOT, "build")
PROGS = ["pipe_grep", "uds_grep", "shm_grep"]
DEVNULL = [(os.POSIX_SPAWN_OPEN, 1, os.devnull, os.O_WRONLY, 0),
           (os.POSIX_SPAWN_OPEN, 2, os.devnull, os.O_WRONLY, 0)]


def parse_cpus(spec):
    cpus = set()
    for part in spec.split(","):
        lo, _, hi = part.partition("-")
        cpus.update(range(int(lo), int(hi or lo) + 1))
    return cpus


def build_baseline(tmpdir):
    out = os.path.join(tmpdir, "baseline_grep")
    subprocess.run(["gcc", "-std=gnu11", "-O2", "-pthread", "-I", ROOT,
                    os.path.join(ROOT, "bench", "baseline.c"), os.path.join(ROOT, "common.c"), "-o", out],
                   check=True)
    return out


def run_once(argv):
    t0 = time.perf_counter_ns()
    pid = os.posix_spawn(argv[0], argv, os.environ, file_actions=DEVNULL)
    _, status, ru = os.wait4(pid, 0)
    t1 = time.perf_counter_ns()
    if status != 0:
        sys.exit("%s 异常退出 (状态 %d)" % (" ".join(argv), status))
    return (t1 - t0) / 1e6, ru


def perf_cpu_ms(argv):
    r = subprocess.run(["perf", "stat", "-x,", "-e", "task-clock", "--"] + argv,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    for line in r.stderr.splitlines():
        f = line.split(",")
        if len(f) > 2 and f[2].startswith("task-clock"):
            return float(f[0]) / 1e6
    return None


def output_digest(argv):
    r = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if r.returncode != 0:
        sys.exit("%s 失败: %s" % (" ".join(argv), r.stderr.decode(errors="replace")))
    return hashlib.md5(r.stdout).hexdigest(), r.stdout.count(b"\n")


def percentile(sorted_v, q):
    return sorted_v[min(len(sorted_v) - 1, math.ceil(q * len(sorted_v)) - 1)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=100)
    ap.add_argument("--warmup", type=int, default=5)
    ap.add_argument("--cpu-runs", type=int, default=20, help="用 perf 测量总 CPU 时间的次数, 0 表示不测")
    ap.add_argument("--cpus", help="限制可用的 CPU, 例如 0 或 0-3")
    ap.add_argument("--no-baseline", action="store_true")
    ap.add_argument("--json")
    ap.add_argument("file")
    ap.add_argument("words", nargs="+")
    args = ap.parse_args()

    if args.cpus:
        os.sched_setaffinity(0, parse_cpus(args.cpus))
    ncpu = len(os.sched_getaffinity(0))
    size = os.path.getsize(args.file)
    use_perf = args.cpu_runs > 0 and shutil.which("perf") is not None and perf_cpu_ms(["true"]) is not None

    with tempfile.TemporaryDirectory() as tmp:
        progs = {p: os.path.join(BIN_DIR, p) for p in PROGS}
        if not args.no_baseline:
            progs = {"baseline_grep": build_baseline(tmp), **progs}
        names = list(progs)
        dump = []

        for word in args.words:
            digests = {n: output_digest([progs[n], args.file, word]) for n in names}
            if len(set(digests.values())) != 1:
                sys.exit("输出不一致: %r" % digests)
            matches = digests[names[0]][1]

            samples = {n: [] for n in names}
            for i in range(args.warmup + args.runs):
                for k in range(len(names)):
                    n = names[(i + k) % len(names)]
                    wall, ru = run_once([progs[n], args.file, word])
                    if i >= args.warmup:
                        samples[n].append((wall, ru.ru_nvcsw, ru.ru_nivcsw, ru.ru_minflt))

            cpu = {n: [] for n in names}
            if use_perf:
                for i in range(args.cpu_runs):
                    for k in range(len(names)):
                        n = names[(i + k) % len(names)]
                        cpu[n].append(perf_cpu_ms([progs[n], args.file, word]))

            print("file: %s (%d bytes)  word: %s  matches: %d  runs: %d  cpus: %d"
                  % (os.path.basename(args.file), size, word, matches, args.runs, ncpu))
            print("%-14s %8s %8s %7s %8s %8s | %7s | %6s %6s %7s | %s"
                  % ("program", "median", "mean", "stdev", "min", "p95", "cpu", "vcsw", "ivcsw", "minflt", "vs_pipe"))
            base_median = statistics.median(s[0] for s in samples["pipe_grep"])
            entry = {"file": args.file, "word": word, "matches": matches, "size": size, "cpus": ncpu, "results": {}}
            for n in names:
                walls = sorted(s[0] for s in samples[n])
                col = lambda j: statistics.mean(s[j] for s in samples[n])
                med = statistics.median(walls)
                cpu_ms = statistics.mean(cpu[n]) if cpu[n] else None
                print("%-14s %8.2f %8.2f %7.2f %8.2f %8.2f | %7s | %6.1f %6.1f %7.0f | %.2fx"
                      % (n, med, statistics.mean(walls), statistics.stdev(walls), walls[0], percentile(walls, 0.95),
                         "%.2f" % cpu_ms if cpu_ms is not None else "n/a", col(1), col(2), col(3), base_median / med))
                entry["results"][n] = {"wall_ms": [s[0] for s in samples[n]], "cpu_ms": cpu[n],
                                       "nvcsw": [s[1] for s in samples[n]], "nivcsw": [s[2] for s in samples[n]],
                                       "minflt": [s[3] for s in samples[n]]}
            print()
            dump.append(entry)

        if args.json:
            with open(args.json, "w") as f:
                json.dump(dump, f)


if __name__ == "__main__":
    main()
