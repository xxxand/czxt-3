/*
 * pipe_grep: 用匿名管道在父子进程之间传递文件内容和匹配结果。
 *
 *   父进程 --[to_child 管道: 文件原始内容]--> 子进程(检查单词)
 *   父进程 <-[from_child 管道: 匹配的行]----- 子进程
 *
 * 父进程负责读文件、搬运数据、排序并输出; 子进程拿不到文件, 只能从管道读数据。
 */
#define _GNU_SOURCE
#include <fcntl.h>
#include <signal.h>
#include <stdlib.h>
#include <unistd.h>

#include "common.h"

int main(int argc, char **argv)
{
    check_args(argc, argv);
    const char *path = argv[1], *word = argv[2];

    int file_fd = open(path, O_RDONLY);
    if (file_fd < 0)
        die_errno("cannot open '%s'", path);

    int to_child[2], from_child[2];
    if (pipe(to_child) < 0 || pipe(from_child) < 0)
        die_errno("pipe");

    pid_t pid = fork();
    if (pid < 0)
        die_errno("fork");
    if (pid == 0) {
        close(file_fd);
        close(to_child[1]);
        close(from_child[0]);
        child_filter_stream(to_child[0], from_child[1], word);
        _exit(0);
    }

    close(to_child[0]);
    close(from_child[1]);
    signal(SIGPIPE, SIG_IGN); /* 子进程提前退出时, write 返回 EPIPE 而不是悄悄杀死父进程 */

    send_file(file_fd, to_child[1]);
    close(file_fd);
    close(to_child[1]); /* 关闭写端, 子进程才能读到 EOF */

    struct buf result = {0};
    read_to_eof(from_child[0], &result);
    close(from_child[0]);
    wait_child(pid);

    signal(SIGPIPE, SIG_DFL); /* 输出到 `| head` 之类的管道时, 行为和普通命令一致 */
    sort_and_print(result.data, result.len);
    free(result.data);
    return 0;
}
