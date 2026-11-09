#!/usr/bin/env python3
"""Lab03 自动测试: 把三个程序的输出与 Python 参考实现逐字节比较。

用法: python3 tests/run_tests.py        (make test 会调用)
环境变量 LAB03_BIN_DIR 可指定可执行文件所在目录, 默认是项目根目录下的 build/。
"""
import glob
import os
import random
import re
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BIN_DIR = os.environ.get("LAB03_BIN_DIR", os.path.join(ROOT, "build"))
PROGS = ["pipe_grep", "uds_grep", "shm_grep"]
TIMEOUT = 60

failed = 0
passed = 0


def sort_key(line):
    return (line.lower(), line)  # bytes.lower() 只转换 ASCII 字母


def expected(data, word):
    """参考实现: 整词(字母/数字/下划线)、不区分大小写; 结果按字母序(先忽略大小写, 再按字节)排序"""
    pat = re.compile(rb"(?<![A-Za-z0-9_])" + re.escape(word) + rb"(?![A-Za-z0-9_])", re.IGNORECASE)
    lines = data.split(b"\n")
    if lines[-1] == b"":
        lines.pop()
    return b"".join(l + b"\n" for l in sorted((l for l in lines if pat.search(l)), key=sort_key))


def grep_expected(path, word):
    """用 grep -iw 独立地得到匹配的行, 再按同样的规则排序, 用来验证上面的参考实现"""
    r = subprocess.run(["grep", "-aiwF", "--", word, path], env=dict(os.environ, LC_ALL="C"),
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    lines = r.stdout.split(b"\n")
    if lines[-1] == b"":
        lines.pop()
    return b"".join(l + b"\n" for l in sorted(lines, key=sort_key))


def run(prog, args):
    return subprocess.run([os.path.join(BIN_DIR, prog)] + args,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=TIMEOUT)


def report(label, problems):
    global failed, passed
    if problems:
        failed += 1
        print("FAIL  %s" % label)
        for p in problems:
            print("        %s" % p)
    else:
        passed += 1
        print("PASS  %s" % label)


def check(label, path, word, want, quiet=False):
    """三个程序都应以 0 退出、stderr 为空、stdout 与 want 完全一致"""
    problems = []
    for prog in PROGS:
        try:
            r = run(prog, [path, word.decode("latin-1")])
        except subprocess.TimeoutExpired:
            problems.append("%s: 超时 (可能死锁)" % prog)
            continue
        if r.returncode != 0:
            problems.append("%s: 退出码 %d: %s" % (prog, r.returncode, r.stderr.decode(errors="replace").strip()))
        elif r.stderr:
            problems.append("%s: stderr 不为空: %r" % (prog, r.stderr[:200]))
        elif r.stdout != want:
            problems.append("%s: 输出不一致 (得到 %d 字节, 期望 %d 字节)" % (prog, len(r.stdout), len(want)))
    if quiet:
        return problems
    report(label, problems)


def check_data(label, tmp, name, data, word, want=None):
    path = os.path.join(tmp, name)
    with open(path, "wb") as f:
        f.write(data)
    check(label, path, word, expected(data, word) if want is None else want)


def check_fails(label, args, expect_code=None):
    problems = []
    for prog in PROGS:
        r = run(prog, args)
        if r.returncode == 0 or (expect_code is not None and r.returncode != expect_code):
            problems.append("%s: 退出码 %d, 期望失败" % (prog, r.returncode))
        elif r.stdout or not r.stderr:
            problems.append("%s: 失败时不应有 stdout, 且应有 stderr 说明" % prog)
    report(label, problems)


def test_spec_semantics(tmp):
    data = (b"Hello World!\nHello worlds\nthe_world\nsay WORLD.\nworld2\n2world\n"
            b"A world-wide web\nworld\n")
    want = b"A world-wide web\nHello World!\nsay WORLD.\nworld\n"
    check_data("题目示例: 整词、不区分大小写、字母序", tmp, "spec.txt", data, b"world", want)
    check_data("单词参数大小写无关", tmp, "spec.txt", data, b"WoRLd", want)


def test_edge_cases(tmp):
    check_data("最后一行没有换行符", tmp, "a.txt", b"b world\nA world\nc world", b"world")
    check_data("CRLF 换行(\\r 原样保留)", tmp, "b.txt", b"Hello World!\r\nfoo\r\nworld\r\n", b"world")
    check_data("空文件", tmp, "c.txt", b"", b"world")
    check_data("只有换行符", tmp, "d.txt", b"\n\n\n", b"world")
    check_data("没有匹配", tmp, "e.txt", b"hello\nworlds\n", b"world")
    check_data("重复的行都保留", tmp, "f.txt", b"world\nworld\nWorld\n", b"world")
    check_data("含 NUL 字节和 UTF-8", tmp, "g.txt",
               b"a\x00world\n\xe2\x80\x9cworld\xe2\x80\x9d\ncaf\xc3\xa9 world\n", b"world")
    check_data("带标点的单词", tmp, "h.txt", b"I don't know\ndon'tx\nfoo.bar\n", b"don't")


def test_long_lines(tmp):
    big = b"x" * 3_000_000
    check_data("3MB 的超长行, 单词在行尾(无换行)", tmp, "l1.txt", big + b" needle", b"needle")
    check_data("3MB 的超长行, 单词被字母粘连", tmp, "l2.txt", big + b"needle\n", b"needle")
    rnd = random.Random(7)
    lines = []
    for _ in range(300):
        n = rnd.choice([0, 1, 10, 1000, 65535, 65536, 65537, 100000])
        body = bytearray(b"ab " * (n // 3 + 1))
        if rnd.random() < 0.5:
            body += b" needle "
        lines.append(bytes(body[:n]) + (b" needle" if rnd.random() < 0.3 else b""))
    check_data("长短不一的行, 数据块边界落在行中间", tmp, "l3.txt", b"\n".join(lines), b"needle")


def test_large_output(tmp):
    rnd = random.Random(11)
    data = b"".join(b"%08x the end\n" % rnd.getrandbits(32) for _ in range(300_000))
    check_data("30万行全部命中(结果远大于管道/套接字缓冲区)", tmp, "big_out.txt", data, b"the")


def test_random_small(tmp):
    rnd = random.Random(2024)
    tokens = [b"world", b"World", b"WORLD", b"worlds", b"a_world", b"world_", b"1world", b"world9",
              b"hello", b"x", b" ", b" ", b"-", b".", b"!", b"'", b"\t", b"\r",
              b"\xc3\xa9", b"\xe2\x80\x9c", b"\x00"]
    words = [b"world", b"WORLD", b"hello", b"wor", b"d", b"a_world", b"x"]
    path = os.path.join(tmp, "rand.txt")
    problems = []
    count = 200
    for i in range(count):
        lines = [b"".join(rnd.choice(tokens) for _ in range(rnd.randint(0, 8))) for _ in range(rnd.randint(0, 30))]
        data = b"\n".join(lines) + (b"\n" if rnd.random() < 0.5 else b"")
        word = rnd.choice(words)
        with open(path, "wb") as f:
            f.write(data)
        want = expected(data, word)
        if want != grep_expected(path, word):
            problems.append("#%d: 参考实现与 grep -iw 不一致, data=%r word=%r" % (i, data, word))
        problems += ["#%d: %s (data=%r word=%r)" % (i, p, data, word) for p in check("", path, word, want, quiet=True)]
        if len(problems) > 5:
            break
    report("%d 个随机小文件(覆盖 mapper 切分边界), 同时对照 grep -iw" % count, problems)


def test_sample_files():
    for name in ["ANNA_KARENINA.txt", "big.txt"]:
        path = os.path.join(ROOT, name)
        if not os.path.exists(path):
            print("SKIP  %s 不存在" % name)
            continue
        with open(path, "rb") as f:
            data = f.read()
        for word in [b"world", b"Anna", b"the", b"Holmes", b"zzzzqq"]:
            want = expected(data, word)
            problems = []
            if want != grep_expected(path, word):
                problems.append("参考实现与 grep -iw 不一致")
            problems += check("", path, word, want, quiet=True)
            report("%s  %s  (%d 行)" % (name, word.decode(), want.count(b"\n")), problems)


def test_errors(tmp):
    check_fails("缺少参数", [], 2)
    check_fails("单词为空", [os.path.join(tmp, "spec.txt"), ""], 2)
    check_fails("文件不存在", [os.path.join(tmp, "no_such_file"), "x"], 1)
    check_fails("参数是目录", [tmp, "x"], 1)
    if os.geteuid() != 0:
        path = os.path.join(tmp, "secret.txt")
        with open(path, "wb") as f:
            f.write(b"world\n")
        os.chmod(path, 0)
        check_fails("文件无读权限", [path, "world"], 1)


def main():
    with tempfile.TemporaryDirectory() as tmp:
        test_spec_semantics(tmp)
        test_edge_cases(tmp)
        test_long_lines(tmp)
        test_large_output(tmp)
        test_random_small(tmp)
        test_sample_files()
        test_errors(tmp)
    leftover = glob.glob("/dev/shm/lab03_shm_*")
    report("没有残留的共享内存对象", ["残留: %s" % leftover] if leftover else [])
    print("\n%d 项通过, %d 项失败" % (passed, failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
