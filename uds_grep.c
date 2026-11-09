/*
 * uds_grep: 用 Unix 域套接字(AF_UNIX, SOCK_STREAM)在父子进程之间传递文件内容和匹配结果。
 *
 * 父子进程由 fork 关联, 因此用 socketpair 直接得到一对已连接的套接字, 不需要路径名。
 * 与管道不同, 一条套接字即可双向通信; 父进程写完后用 shutdown(SHUT_WR) 半关闭,
 * 子进程读到 EOF, 而父进程仍可从同一个套接字读回结果。
 *
 * 父进程负责读文件、搬运数据、排序并输出; 子进程拿不到文件, 只能从套接字读数据。
 */
#define _GNU_SOURCE
#include <fcntl.h>
#include <signal.h>
#include <stdlib.h>
#include <sys/socket.h>
#include <unistd.h>

#include "common.h"

int main(int argc, char **argv)
{
    check_args(argc, argv);
    const char *path = argv[1], *word = argv[2];

    int file_fd = open(path, O_RDONLY);
    if (file_fd < 0)
        die_errno("cannot open '%s'", path);

    int sv[2]; /* sv[0] 归父进程, sv[1] 归子进程 */
    if (socketpair(AF_UNIX, SOCK_STREAM, 0, sv) < 0)
        die_errno("socketpair");

    pid_t pid = fork();
    if (pid < 0)
        die_errno("fork");
    if (pid == 0) {
        close(file_fd);
        close(sv[0]);
        child_filter_stream(sv[1], sv[1], word);
        _exit(0);
    }

    close(sv[1]);
    signal(SIGPIPE, SIG_IGN); /* 子进程提前退出时, write 返回 EPIPE 而不是悄悄杀死父进程 */

    send_file(file_fd, sv[0]);
    close(file_fd);
    if (shutdown(sv[0], SHUT_WR) < 0) /* 子进程读到 EOF, 但本端仍可读 */
        die_errno("shutdown");

    struct buf result = {0};
    read_to_eof(sv[0], &result);
    close(sv[0]);
    wait_child(pid);

    signal(SIGPIPE, SIG_DFL); /* 输出到 `| head` 之类的管道时, 行为和普通命令一致 */
    sort_and_print(result.data, result.len);
    free(result.data);
    return 0;
}
