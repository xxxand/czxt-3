#ifndef COMMON_H
#define COMMON_H

#include <stddef.h>
#include <sys/types.h>

#define CHUNK_SIZE (64 * 1024)

struct buf {
    char *data;
    size_t len, cap;
};

void die(const char *fmt, ...) __attribute__((noreturn, format(printf, 1, 2)));
void die_errno(const char *fmt, ...) __attribute__((noreturn, format(printf, 1, 2)));
void check_args(int argc, char **argv);
void *xmalloc(size_t n);
void *xrealloc(void *p, size_t n);

void buf_reserve(struct buf *b, size_t extra);
void buf_append(struct buf *b, const void *p, size_t n);

size_t read_retry(int fd, void *p, size_t n);
void write_all(int fd, const void *p, size_t n);
void read_to_eof(int fd, struct buf *b);
void wait_child(pid_t pid);

/* 父进程使用: 只搬运数据和排序输出, 不检查单词 */
void send_file(int file_fd, int out_fd);
void sort_and_print(const char *data, size_t len);

/* 子进程使用: 负责检查单词 */
void grep_lines(const char *data, size_t len, const char *word, size_t wn, struct buf *out);
void child_filter_stream(int in_fd, int out_fd, const char *word);

#endif
