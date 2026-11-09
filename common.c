/*
 * 三个程序共用的代码。
 *
 * 父进程只调用: send_file / read_to_eof / sort_and_print / wait_child
 *               (搬运数据、排序输出, 不检查单词)
 * 子进程只调用: child_filter_stream / grep_lines
 *               (检查单词; 子进程从不打开文本文件, 数据只来自 IPC)
 */
#define _GNU_SOURCE
#include <errno.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/wait.h>
#include <unistd.h>

#include "common.h"

/* ---------- 错误处理、内存、缓冲区、I/O ---------- */

/* 用 _exit 而不是 exit: 子进程里调用也不会重复刷新从父进程继承来的 stdio 缓冲 */
static void __attribute__((noreturn)) vdie(int err, const char *fmt, va_list ap)
{
    fprintf(stderr, "%s: ", program_invocation_short_name);
    vfprintf(stderr, fmt, ap);
    if (err)
        fprintf(stderr, ": %s", strerror(err));
    fputc('\n', stderr);
    _exit(1);
}

void die(const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    vdie(0, fmt, ap);
}

void die_errno(const char *fmt, ...)
{
    int err = errno;
    va_list ap;
    va_start(ap, fmt);
    vdie(err, fmt, ap);
}

void check_args(int argc, char **argv)
{
    if (argc != 3 || argv[2][0] == '\0') {
        fprintf(stderr, "usage: %s <file> <word>\n", argv[0]);
        fprintf(stderr, "  print the lines of <file> that contain <word>, in alphabetical order\n");
        exit(2);
    }
}

void *xmalloc(size_t n)
{
    void *p = malloc(n ? n : 1);
    if (!p)
        die("out of memory");
    return p;
}

void *xrealloc(void *p, size_t n)
{
    p = realloc(p, n ? n : 1);
    if (!p)
        die("out of memory");
    return p;
}

void buf_reserve(struct buf *b, size_t extra)
{
    if (b->cap - b->len >= extra)
        return;
    size_t cap = b->cap ? b->cap : 4096;
    while (cap - b->len < extra)
        cap *= 2;
    b->data = xrealloc(b->data, cap);
    b->cap = cap;
}

void buf_append(struct buf *b, const void *p, size_t n)
{
    buf_reserve(b, n);
    memcpy(b->data + b->len, p, n);
    b->len += n;
}

size_t read_retry(int fd, void *p, size_t n)
{
    ssize_t r;
    do
        r = read(fd, p, n);
    while (r < 0 && errno == EINTR);
    if (r < 0)
        die_errno("read");
    return (size_t)r;
}

void write_all(int fd, const void *p, size_t n)
{
    const char *c = p;
    while (n > 0) {
        ssize_t w = write(fd, c, n);
        if (w < 0) {
            if (errno == EINTR)
                continue;
            die_errno("write");
        }
        c += w;
        n -= (size_t)w;
    }
}

void read_to_eof(int fd, struct buf *b)
{
    for (;;) {
        buf_reserve(b, CHUNK_SIZE);
        size_t n = read_retry(fd, b->data + b->len, b->cap - b->len);
        if (n == 0)
            return;
        b->len += n;
    }
}

void wait_child(pid_t pid)
{
    int st;
    while (waitpid(pid, &st, 0) < 0)
        if (errno != EINTR)
            die_errno("waitpid");
    if (WIFSIGNALED(st))
        die("child process killed by signal %d", WTERMSIG(st));
    if (WEXITSTATUS(st) != 0)
        die("child process exited with status %d", WEXITSTATUS(st));
}

/* ---------- 行的切分 ---------- */

static unsigned char to_lower(unsigned char c)
{
    return (c >= 'A' && c <= 'Z') ? (unsigned char)(c + 'a' - 'A') : c;
}

/* 取出 p 开头的一行: *line_end 为行尾(不含 '\n'), 返回值为下一行的起点 */
static const char *next_line(const char *p, const char *end, const char **line_end)
{
    const char *nl = memchr(p, '\n', (size_t)(end - p));
    *line_end = nl ? nl : end;
    return nl ? nl + 1 : end;
}

/* ---------- 父进程: 搬运数据, 排序输出 ---------- */

void send_file(int file_fd, int out_fd)
{
    char *chunk = xmalloc(CHUNK_SIZE);
    size_t n;
    while ((n = read_retry(file_fd, chunk, CHUNK_SIZE)) > 0)
        write_all(out_fd, chunk, n);
    free(chunk);
}

struct line {
    const char *p;
    size_t n;
};

/* 字母序: 先不区分大小写比较; 完全相同时再按字节序决定先后, 使输出顺序确定 */
static int line_cmp(const void *a, const void *b)
{
    const struct line *x = a, *y = b;
    size_t m = x->n < y->n ? x->n : y->n;
    for (size_t i = 0; i < m; i++) {
        int d = (int)to_lower((unsigned char)x->p[i]) - (int)to_lower((unsigned char)y->p[i]);
        if (d)
            return d;
    }
    if (x->n != y->n)
        return x->n < y->n ? -1 : 1;
    return memcmp(x->p, y->p, x->n);
}

/* data 是若干行(子进程保证每行以 '\n' 结尾), 排序后一次性写到 stdout */
void sort_and_print(const char *data, size_t len)
{
    const char *end = data + len, *p, *e;
    size_t n = 0, i = 0;

    for (p = data; p < end; n++)
        p = next_line(p, end, &e);

    struct line *v = xmalloc(n * sizeof *v);
    for (p = data; p < end; i++) {
        const char *next = next_line(p, end, &e);
        v[i].p = p;
        v[i].n = (size_t)(e - p);
        p = next;
    }
    qsort(v, n, sizeof *v, line_cmp);

    struct buf out = {0};
    buf_reserve(&out, len + 1);
    for (i = 0; i < n; i++) {
        buf_append(&out, v[i].p, v[i].n);
        buf_append(&out, "\n", 1);
    }
    write_all(STDOUT_FILENO, out.data, out.len);
    free(out.data);
    free(v);
}

/* ---------- 子进程: 检查单词 ---------- */

/* 单词由字母、数字、下划线组成(与 grep -w 相同) */
static int is_word_char(unsigned char c)
{
    return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || c == '_';
}

/* 这一行是否含有 w: 不区分大小写, 且 w 的前一个、后一个字符都不是单词字符 */
static int line_has_word(const char *s, size_t n, const char *w, size_t wn)
{
    for (size_t i = 0; i + wn <= n; i++) {
        if (i > 0 && is_word_char((unsigned char)s[i - 1]))
            continue;
        size_t k = 0;
        while (k < wn && to_lower((unsigned char)s[i + k]) == to_lower((unsigned char)w[k]))
            k++;
        if (k < wn)
            continue;
        if (i + wn < n && is_word_char((unsigned char)s[i + wn]))
            continue;
        return 1;
    }
    return 0;
}

/* data 由完整的行组成(最后一行可以没有 '\n'); 把含有 word 的行连同 '\n' 追加到 out */
void grep_lines(const char *data, size_t len, const char *word, size_t wn, struct buf *out)
{
    const char *end = data + len, *p = data, *e;
    while (p < end) {
        const char *next = next_line(p, end, &e);
        if (line_has_word(p, (size_t)(e - p), word, wn)) {
            buf_append(out, p, (size_t)(e - p));
            buf_append(out, "\n", 1);
        }
        p = next;
    }
}

/*
 * 管道/套接字版本的子进程主体: 一边读一边匹配, 结果先攒在内存里, 读到 EOF 后再整体写回。
 * 父进程发完输入之前不会读取结果; 若子进程边读边回写, 两个方向的缓冲区写满后双方会互相等待(死锁)。
 */
void child_filter_stream(int in_fd, int out_fd, const char *word)
{
    size_t wn = strlen(word);
    size_t cap = CHUNK_SIZE, have = 0; /* buf[0, have) 是上一轮剩下的、还不完整的行 */
    char *buf = xmalloc(cap);
    struct buf result = {0};

    for (;;) {
        if (cap - have < CHUNK_SIZE / 2) {
            cap *= 2;
            buf = xrealloc(buf, cap);
        }
        size_t n = read_retry(in_fd, buf + have, cap - have);
        if (n == 0)
            break;
        /* 剩余部分里没有 '\n', 只需在新读入的数据里找最后一个换行 */
        const char *nl = memrchr(buf + have, '\n', n);
        have += n;
        if (!nl)
            continue;
        size_t done = (size_t)(nl - buf) + 1;
        grep_lines(buf, done, word, wn, &result);
        memmove(buf, buf + done, have - done);
        have -= done;
    }
    if (have)
        grep_lines(buf, have, word, wn, &result); /* 最后一行没有 '\n' */

    write_all(out_fd, result.data, result.len);
    free(result.data);
    free(buf);
}
