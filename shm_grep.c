/*
 * shm_grep: 用 POSIX 共享内存在父子进程之间传递文件内容和匹配结果;
 *           子进程内部用 4 个 mapper 线程 + 主线程(唯一的 reducer)实现 map-reduce。
 *
 * 共享内存是一整块, 由父进程创建, 布局为:
 *   [ 控制块 (4096B) | 输入区 (N 字节) | 结果区 (N+1 字节) ]
 * 控制块里放两个进程间共享的信号量:
 *   input_ready   父 -> 子: 文件内容已经写进输入区
 *   result_ready  子 -> 父: 匹配结果已经写进结果区
 * 结果区只需 N+1 字节: 结果是输入行的子集, 每行末尾补 '\n', 至多比输入多 1 字节。
 *
 * map:    4 个 mapper 线程各处理输入区的 1/4 (边界对齐到行首), 把匹配的行放进各自的私有缓冲区
 * reduce: 子进程主线程依次等待各 mapper 结束, 把 4 份中间结果拼接进共享内存的结果区
 *         (和 MapReduce 论文里的分布式 grep 一样, reduce 是恒等函数, 只负责汇总)
 * 父进程负责读文件、排序并输出; 子进程拿不到文件, 只能从共享内存读数据。
 */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <pthread.h>
#include <semaphore.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/prctl.h>
#include <sys/stat.h>
#include <unistd.h>

#include "common.h"

#define NUM_MAPPERS 4
#define CTL_SIZE 4096

struct shm_ctl {
    sem_t input_ready;
    sem_t result_ready;
    size_t input_len;
    size_t result_len;
};
_Static_assert(sizeof(struct shm_ctl) <= CTL_SIZE, "control block must fit in CTL_SIZE");

struct mapper {
    pthread_t tid;
    const char *begin, *end; /* 负责的输入范围 [begin, end), 两端都在行边界上 */
    const char *word;
    size_t wn;
    struct buf out; /* 中间结果: 本线程私有, 无需加锁 */
};

static sem_t *g_result_ready;

/* 子进程无论正常结束还是异常死亡都会唤醒父进程, 避免父进程永远等不到结果 */
static void on_sigchld(int sig)
{
    (void)sig;
    int saved = errno;
    sem_post(g_result_ready);
    errno = saved;
}

static void sem_wait_retry(sem_t *s)
{
    while (sem_wait(s) < 0)
        if (errno != EINTR)
            die_errno("sem_wait");
}

/* 把偏移 pos 对齐到不小于它的最近一个行首, 保证每个 mapper 拿到的都是完整的行 */
static size_t align_to_line(const char *in, size_t n, size_t pos)
{
    if (pos == 0 || in[pos - 1] == '\n')
        return pos;
    const char *nl = memchr(in + pos, '\n', n - pos);
    return nl ? (size_t)(nl - in) + 1 : n;
}

static void *map_thread(void *arg)
{
    struct mapper *m = arg;
    grep_lines(m->begin, (size_t)(m->end - m->begin), m->word, m->wn, &m->out);
    return NULL;
}

static void child_main(struct shm_ctl *ctl, const char *in, char *out, size_t out_cap,
                       const char *word, pid_t parent)
{
    /* 父进程如果先退出(例如读文件出错), 子进程会收到 SIGKILL, 不会永远阻塞在信号量上 */
    if (prctl(PR_SET_PDEATHSIG, SIGKILL) < 0)
        die_errno("prctl");
    if (getppid() != parent)
        _exit(1);

    sem_wait_retry(&ctl->input_ready);
    size_t n = ctl->input_len;
    size_t wn = strlen(word);

    size_t bound[NUM_MAPPERS + 1];
    bound[0] = 0;
    for (int i = 1; i < NUM_MAPPERS; i++)
        bound[i] = align_to_line(in, n, n / NUM_MAPPERS * i);
    bound[NUM_MAPPERS] = n;

    /* map */
    struct mapper m[NUM_MAPPERS] = {0};
    for (int i = 0; i < NUM_MAPPERS; i++) {
        m[i].begin = in + bound[i];
        m[i].end = in + bound[i + 1];
        m[i].word = word;
        m[i].wn = wn;
        int err = pthread_create(&m[i].tid, NULL, map_thread, &m[i]);
        if (err) {
            errno = err;
            die_errno("pthread_create");
        }
    }

    /* reduce */
    size_t off = 0;
    for (int i = 0; i < NUM_MAPPERS; i++) {
        pthread_join(m[i].tid, NULL);
        if (m[i].out.len > out_cap - off)
            die("result area overflow");
        if (m[i].out.len)
            memcpy(out + off, m[i].out.data, m[i].out.len);
        off += m[i].out.len;
        free(m[i].out.data);
    }
    ctl->result_len = off;
    if (sem_post(&ctl->result_ready) < 0)
        die_errno("sem_post");
}

int main(int argc, char **argv)
{
    check_args(argc, argv);
    const char *path = argv[1], *word = argv[2];

    int file_fd = open(path, O_RDONLY);
    if (file_fd < 0)
        die_errno("cannot open '%s'", path);
    struct stat st;
    if (fstat(file_fd, &st) < 0)
        die_errno("fstat '%s'", path);
    if (!S_ISREG(st.st_mode))
        die("'%s' is not a regular file", path);

    size_t in_cap = (size_t)st.st_size;
    size_t out_cap = in_cap + 1;
    size_t total = CTL_SIZE + in_cap + out_cap;

    char name[64];
    snprintf(name, sizeof name, "/lab03_shm_%d", (int)getpid());
    int shm_fd = shm_open(name, O_CREAT | O_EXCL | O_RDWR, 0600);
    if (shm_fd < 0)
        die_errno("shm_open");
    shm_unlink(name); /* 子进程通过 fork 继承映射, 名字不再需要; 现在就删掉, 异常退出也不会残留 */
    if (ftruncate(shm_fd, (off_t)total) < 0)
        die_errno("ftruncate");
    void *base = mmap(NULL, total, PROT_READ | PROT_WRITE, MAP_SHARED, shm_fd, 0);
    if (base == MAP_FAILED)
        die_errno("mmap");
    close(shm_fd);

    struct shm_ctl *ctl = base;
    char *in = (char *)base + CTL_SIZE;
    char *out = in + in_cap;
    if (sem_init(&ctl->input_ready, 1, 0) < 0 || sem_init(&ctl->result_ready, 1, 0) < 0)
        die_errno("sem_init");

    g_result_ready = &ctl->result_ready;
    struct sigaction sa = {0};
    sa.sa_handler = on_sigchld;
    sa.sa_flags = SA_RESTART | SA_NOCLDSTOP;
    sigemptyset(&sa.sa_mask);
    sigaction(SIGCHLD, &sa, NULL);

    pid_t parent = getpid();
    pid_t pid = fork();
    if (pid < 0)
        die_errno("fork");
    if (pid == 0) {
        close(file_fd);
        child_main(ctl, in, out, out_cap, word, parent);
        _exit(0);
    }

    size_t got = 0;
    while (got < in_cap) {
        size_t n = read_retry(file_fd, in + got, in_cap - got);
        if (n == 0)
            break;
        got += n;
    }
    close(file_fd);
    ctl->input_len = got;
    if (sem_post(&ctl->input_ready) < 0)
        die_errno("sem_post");

    sem_wait_retry(&ctl->result_ready);
    wait_child(pid);
    signal(SIGCHLD, SIG_DFL); /* 处理函数的任务已完成, 之后共享内存会被解除映射 */

    sort_and_print(out, ctl->result_len);

    sem_destroy(&ctl->input_ready);
    sem_destroy(&ctl->result_ready);
    munmap(base, total);
    return 0;
}
