#!/usr/bin/env python3
"""耗时分解: 在源码副本里插入计时点, 把一次运行拆成若干阶段, 报告各阶段耗时的中位数。

交付的源码不会被修改: 脚本把源码复制到临时目录, 用字符串替换插入计时点(每个锚点必须恰好出现一次,
否则报错), 再用与 Makefile 相同的优化选项编译。所有时间戳取自 CLOCK_MONOTONIC, 父子进程之间可以直接对齐。

用法: python3 bench/phases.py [--runs N] [--warmup K] [--cpus LIST] [--no-match] [--pipe-size BYTES]
                              FILE WORD [WORD ...] [--mappers K [K ...]]

  --mappers    额外构建 mapper 线程数不同的 shm_grep, 比较映射阶段如何随线程数变化(放在位置参数之后)。
  --no-match   子进程只接收数据、不做匹配, 用来单独测量 IPC 传输本身的耗时(此时输出为空)。
  --pipe-size  把 pipe_grep 的两条管道容量设为指定字节数, 用来检验管道容量对传输耗时的影响。
"""
import argparse
import hashlib
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

TIMING_H = r"""#ifndef TIMING_H
#define TIMING_H
#include <stdio.h>
#include <time.h>
#include <unistd.h>

struct ts_entry { const char *tag; long long v; };
extern struct ts_entry g_ts[128];
extern int g_ts_n;
extern long long g_t_read, g_t_match, g_t_wb;

static inline long long clock_ns(int clk)
{
    struct timespec t;
    clock_gettime(clk, &t);
    return t.tv_sec * 1000000000LL + t.tv_nsec;
}
static inline long long now_ns(void) { return clock_ns(CLOCK_MONOTONIC); }
#define TS(name) do { g_ts[g_ts_n].tag = (name); g_ts[g_ts_n].v = now_ns(); g_ts_n++; } while (0)
#define TSV(name, val) do { g_ts[g_ts_n].tag = (name); g_ts[g_ts_n].v = (val); g_ts_n++; } while (0)
void ts_dump(const char *role);
#endif
"""

TS_DUMP_C = r"""
struct ts_entry g_ts[128];
int g_ts_n;
long long g_t_read, g_t_match, g_t_wb;

void ts_dump(const char *role)
{
    char buf[4096];
    int n = 0;
    for (int i = 0; i < g_ts_n; i++)
        n += snprintf(buf + n, sizeof buf - n, "%s %s %lld\n", role, g_ts[i].tag, g_ts[i].v);
    n += snprintf(buf + n, sizeof buf - n, "%s t_read %lld\n%s t_match %lld\n%s t_wb %lld\n%s cpu_process %lld\n",
                  role, g_t_read, role, g_t_match, role, g_t_wb, role, clock_ns(CLOCK_PROCESS_CPUTIME_ID));
    if (write(2, buf, n) < 0)
        _exit(1);
}
"""

INCLUDE = ('#include "common.h"', '#include "common.h"\n#include "timing.h"')

COMMON_PATCH = [
    INCLUDE,
    ("        size_t n = read_retry(in_fd, buf + have, cap - have);",
     "        long long _t = now_ns();\n        size_t n = read_retry(in_fd, buf + have, cap - have);\n"
     "        g_t_read += now_ns() - _t;"),
    ("        grep_lines(buf, done, word, wn, &result);",
     "        _t = now_ns();\n        grep_lines(buf, done, word, wn, &result);\n        g_t_match += now_ns() - _t;"),
    ("        grep_lines(buf, have, word, wn, &result); /* 最后一行没有 '\\n' */",
     "        { long long _t2 = now_ns(); grep_lines(buf, have, word, wn, &result); g_t_match += now_ns() - _t2; }"),
    ("    write_all(out_fd, result.data, result.len);",
     "    { long long _t3 = now_ns(); write_all(out_fd, result.data, result.len); g_t_wb += now_ns() - _t3; }"),
    ("void grep_lines(const char *data, size_t len, const char *word, size_t wn, struct buf *out)\n{",
     "void grep_lines(const char *data, size_t len, const char *word, size_t wn, struct buf *out)\n{\n"
     "#ifdef NO_MATCH\n    (void)data; (void)len; (void)word; (void)wn; (void)out;\n    return;\n#endif"),
]


def stream_patch(send_call, read_call, child_call):
    """pipe_grep 和 uds_grep 的结构相同, 只有传给 IPC 通道的参数不同"""
    return [
        INCLUDE,
        ("    check_args(argc, argv);", '    TS("main");\n    check_args(argc, argv);'),
        ("    if (pid == 0) {\n        close(file_fd);",
         '    TS("forked");\n    if (pid == 0) {\n        g_ts_n = 0;\n        TS("start");\n        close(file_fd);'),
        (child_call + "\n        _exit(0);", child_call + '\n        TS("end");\n        ts_dump("c");\n        _exit(0);'),
        (send_call, send_call + '\n    TS("sent");'),
        (read_call, read_call + '\n    TS("result");'),
        ("    wait_child(pid);", '    wait_child(pid);\n    TS("reaped");'),
        ("    sort_and_print(result.data, result.len);", '    sort_and_print(result.data, result.len);\n    TS("sorted");'),
        ("    return 0;\n}", '    ts_dump("p");\n    return 0;\n}'),
    ]


PIPE_PATCH = stream_patch("    send_file(file_fd, to_child[1]);", "    read_to_eof(from_child[0], &result);",
                          "        child_filter_stream(to_child[0], from_child[1], word);")
UDS_PATCH = stream_patch("    send_file(file_fd, sv[0]);", "    read_to_eof(sv[0], &result);",
                         "        child_filter_stream(sv[1], sv[1], word);")

SHM_PATCH = [
    INCLUDE,
    ("    struct buf out; /* 中间结果: 本线程私有, 无需加锁 */\n};",
     "    struct buf out; /* 中间结果: 本线程私有, 无需加锁 */\n    long long t0, t1, c0, c1;\n};"),
    ("    grep_lines(m->begin, (size_t)(m->end - m->begin), m->word, m->wn, &m->out);",
     "    m->t0 = now_ns();\n    m->c0 = clock_ns(CLOCK_THREAD_CPUTIME_ID);\n"
     "    grep_lines(m->begin, (size_t)(m->end - m->begin), m->word, m->wn, &m->out);\n"
     "    m->c1 = clock_ns(CLOCK_THREAD_CPUTIME_ID);\n    m->t1 = now_ns();"),
    ("    sem_wait_retry(&ctl->input_ready);", '    sem_wait_retry(&ctl->input_ready);\n    TS("input");'),
    ("    /* reduce */", '    TS("created");\n    /* reduce */'),
    ("        pthread_join(m[i].tid, NULL);",
     '        pthread_join(m[i].tid, NULL);\n        TSV("m_start", m[i].t0);\n        TSV("m_wall", m[i].t1 - m[i].t0);\n'
     '        TSV("m_cpu", m[i].c1 - m[i].c0);'),
    ("    ctl->result_len = off;", '    TS("reduced");\n    ctl->result_len = off;'),
    ("    check_args(argc, argv);", '    TS("main");\n    check_args(argc, argv);'),
    ("    if (pid == 0) {\n        close(file_fd);\n        child_main(ctl, in, out, out_cap, word, parent);\n        _exit(0);",
     '    TS("forked");\n    if (pid == 0) {\n        g_ts_n = 0;\n        TS("start");\n        close(file_fd);\n'
     '        child_main(ctl, in, out, out_cap, word, parent);\n        TS("end");\n        ts_dump("c");\n        _exit(0);'),
    ("    close(file_fd);\n    ctl->input_len = got;", '    close(file_fd);\n    TS("read");\n    ctl->input_len = got;'),
    ("    sem_wait_retry(&ctl->result_ready);\n    wait_child(pid);",
     '    sem_wait_retry(&ctl->result_ready);\n    TS("result");\n    wait_child(pid);\n    TS("reaped");'),
    ("    sort_and_print(out, ctl->result_len);", '    sort_and_print(out, ctl->result_len);\n    TS("sorted");'),
    ("    return 0;\n}", '    ts_dump("p");\n    return 0;\n}'),
]


def patch(text, pairs, name):
    for old, new in pairs:
        if text.count(old) != 1:
            sys.exit("%s: 锚点出现 %d 次 (应为 1 次): %r" % (name, text.count(old), old))
        text = text.replace(old, new)
    return text


def build(tmp, pipe_size=0):
    """返回打过补丁的 shm_grep 源码文本(各线程数的变体由调用方替换 NUM_MAPPERS 得到)"""
    def src(name):
        with open(os.path.join(ROOT, name), encoding="utf-8") as f:
            return f.read()

    def write(name, text):
        with open(os.path.join(tmp, name), "w", encoding="utf-8") as f:
            f.write(text)

    pipe_patch = list(PIPE_PATCH)
    if pipe_size:
        old = '    if (pipe(to_child) < 0 || pipe(from_child) < 0)\n        die_errno("pipe");'
        pipe_patch.append((old, old + "\n    fcntl(to_child[1], F_SETPIPE_SZ, %d);\n    fcntl(from_child[1], F_SETPIPE_SZ, %d);"
                           % (pipe_size, pipe_size)))
    write("timing.h", TIMING_H)
    shutil.copy(os.path.join(ROOT, "common.h"), tmp)
    write("common.c", patch(src("common.c"), COMMON_PATCH, "common.c") + TS_DUMP_C)
    write("pipe_grep.c", patch(src("pipe_grep.c"), pipe_patch, "pipe_grep.c"))
    write("uds_grep.c", patch(src("uds_grep.c"), UDS_PATCH, "uds_grep.c"))
    shm = patch(src("shm_grep.c"), SHM_PATCH, "shm_grep.c")
    if "#define NUM_MAPPERS 4" not in shm:
        sys.exit("shm_grep.c: 找不到 NUM_MAPPERS 的定义")
    return shm


def compile_prog(tmp, name, source_file, defs=()):
    out = os.path.join(tmp, name)
    subprocess.run(["gcc", "-std=gnu11", "-O2", "-pthread", *defs, "-I", tmp, source_file,
                    os.path.join(tmp, "common.c"), "-o", out, "-lrt"], check=True)
    return out


def run_instrumented(binary, args):
    r, w = os.pipe()
    actions = [(os.POSIX_SPAWN_OPEN, 1, os.devnull, os.O_WRONLY, 0), (os.POSIX_SPAWN_DUP2, w, 2),
               (os.POSIX_SPAWN_CLOSE, r), (os.POSIX_SPAWN_CLOSE, w)]
    t0 = time.monotonic_ns()
    pid = os.posix_spawn(binary, [binary] + args, os.environ, file_actions=actions)
    os.close(w)
    _, status, _ = os.wait4(pid, 0)
    t_end = time.monotonic_ns()
    data = b""
    while True:
        chunk = os.read(r, 65536)
        if not chunk:
            break
        data += chunk
    os.close(r)
    if status != 0:
        sys.exit("%s 异常退出 (状态 %d)" % (binary, status))
    d = {}
    for line in data.decode().splitlines():
        role, tag, val = line.split()
        d.setdefault((role, tag), []).append(int(val))
    return t0, t_end, d


def breakdown(kind, t0, t_end, d):
    """父进程时间线上各阶段的耗时(ms); 各阶段之和等于总耗时"""
    g = lambda role, tag: d[(role, tag)][0]
    ms = lambda a, b: (b - a) / 1e6
    ph = {"启动(spawn+exec)": ms(t0, g("p", "main")), "初始化(open/IPC/fork)": ms(g("p", "main"), g("p", "forked"))}
    sent = g("p", "read") if kind == "shm" else g("p", "sent")
    ph["传输(读文件并交给子进程)"] = ms(g("p", "forked"), sent)
    ph["等待子进程完成"] = ms(sent, g("p", "result"))
    ph["回收子进程(waitpid)"] = ms(g("p", "result"), g("p", "reaped"))
    ph["排序并输出"] = ms(g("p", "reaped"), g("p", "sorted"))
    ph["退出"] = ms(g("p", "sorted"), t_end)
    ph["总耗时"] = ms(t0, t_end)
    return ph


def child_detail(kind, d):
    g = lambda tag: d[("c", tag)][0]
    ms = lambda v: v / 1e6
    cpu = ms(d[("p", "cpu_process")][0] + d[("c", "cpu_process")][0])
    if kind != "shm":
        return {"子进程: read(阻塞+拷贝)": ms(g("t_read")), "子进程: 匹配": ms(g("t_match")),
                "子进程: 回写结果": ms(g("t_wb")), "进程内 CPU 时间合计": cpu}
    starts, walls, cpus = d[("c", "m_start")], d[("c", "m_wall")], d[("c", "m_cpu")]
    return {"子进程: 等待输入": ms(g("input") - g("start")), "子进程: 创建线程": ms(g("created") - g("input")),
            "子进程: map 到 reduce 完成": ms(g("reduced") - g("created")),
            "mapper 最晚开工(距收到输入)": ms(max(starts) - g("input")),
            "mapper 墙钟 最短": ms(min(walls)), "mapper 墙钟 最长": ms(max(walls)),
            "mapper CPU 合计": ms(sum(cpus)), "进程内 CPU 时间合计": cpu}


def med(rows, key):
    return statistics.median(r[key] for r in rows)


def parse_cpus(spec):
    cpus = set()
    for part in spec.split(","):
        lo, _, hi = part.partition("-")
        cpus.update(range(int(lo), int(hi or lo) + 1))
    return cpus


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=100)
    ap.add_argument("--warmup", type=int, default=5)
    ap.add_argument("--cpus")
    ap.add_argument("--mappers", type=int, nargs="*", default=[])
    ap.add_argument("--no-match", action="store_true", help="子进程只接收数据、不做匹配, 用来单独测量 IPC 传输的耗时")
    ap.add_argument("--pipe-size", type=int, default=0, help="把 pipe_grep 的两条管道容量设为该字节数(默认 64 KiB)")
    ap.add_argument("file")
    ap.add_argument("words", nargs="+")
    args = ap.parse_args()
    if args.cpus:
        os.sched_setaffinity(0, parse_cpus(args.cpus))
    defs = ["-DNO_MATCH"] if args.no_match else []

    with tempfile.TemporaryDirectory() as tmp:
        shm_src = build(tmp, args.pipe_size)
        progs = {}
        for name in ("pipe_grep", "uds_grep"):
            progs[name] = (name.split("_")[0], compile_prog(tmp, name, os.path.join(tmp, name + ".c"), defs))
        variants = sorted(set([4] + args.mappers))
        for k in variants:
            path = os.path.join(tmp, "shm_grep_%d.c" % k)
            with open(path, "w", encoding="utf-8") as f:
                f.write(shm_src.replace("#define NUM_MAPPERS 4", "#define NUM_MAPPERS %d" % k))
            progs["shm_grep" if k == 4 else "shm_grep/%d" % k] = ("shm", compile_prog(tmp, "shm_grep_%d" % k, path, defs))
        main_names = ["pipe_grep", "uds_grep", "shm_grep"]
        order = main_names + [n for n in progs if n not in main_names]

        for word in args.words:
            if not args.no_match:
                want = hashlib.md5(subprocess.run([os.path.join(ROOT, "pipe_grep"), args.file, word],
                                                  capture_output=True).stdout).hexdigest()
                for n in order:
                    out = subprocess.run([progs[n][1], args.file, word], capture_output=True).stdout
                    if hashlib.md5(out).hexdigest() != want:
                        sys.exit("%s 的输出与交付版本不一致" % n)

            rows = {n: [] for n in order}
            for i in range(args.warmup + args.runs):
                for k in range(len(order)):
                    n = order[(i + k) % len(order)]
                    kind, path = progs[n]
                    t0, t_end, d = run_instrumented(path, [args.file, word])
                    if i >= args.warmup:
                        row = breakdown(kind, t0, t_end, d)
                        row.update(child_detail(kind, d))
                        rows[n].append(row)

            print("file: %s  word: %s  runs: %d  (各项为中位数, 单位 ms)" % (os.path.basename(args.file), word, args.runs))
            keys = list(rows["pipe_grep"][0])
            print("%-26s %10s %10s %10s" % ("", *main_names))
            for key in keys[:keys.index("总耗时") + 1]:
                vals = ["%10.2f" % med(rows[n], key) if key in rows[n][0] else "%10s" % "-" for n in main_names]
                print("%-24s %s" % (key, " ".join(vals)))
            print()
            for n in main_names:
                print("%s:" % n)
                for key in rows[n][0]:
                    if key.startswith(("子进程", "mapper", "进程内")):
                        print("    %-30s %8.2f" % (key, med(rows[n], key)))
            print()
            if len(variants) > 1:
                print("shm_grep 映射线程数 (mappers) 对比:")
                print("%8s %9s %12s %14s %14s %14s %12s" % ("mappers", "总耗时", "传输", "等待子进程", "最长 mapper",
                                                           "mapper CPU合计", "创建线程"))
                for k in variants:
                    n = "shm_grep" if k == 4 else "shm_grep/%d" % k
                    r = rows[n]
                    print("%8d %9.2f %12.2f %14.2f %14.2f %14.2f %12.2f"
                          % (k, med(r, "总耗时"), med(r, "传输(读文件并交给子进程)"), med(r, "等待子进程完成"),
                             med(r, "mapper 墙钟 最长"), med(r, "mapper CPU 合计"), med(r, "子进程: 创建线程")))
                print()


if __name__ == "__main__":
    main()
