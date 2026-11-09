/*
 * baseline_grep: 单进程、无 IPC 的基线。读文件、匹配、排序、输出都在同一个进程里完成,
 * 使用与三个程序相同的 common.c, 用来衡量 IPC 和并行带来的额外开销与收益。
 * 仅供计时实验使用 (bench/bench.py 会自动编译)。
 */
#define _GNU_SOURCE
#include <fcntl.h>
#include <string.h>
#include <unistd.h>

#include "common.h"

int main(int argc, char **argv)
{
    check_args(argc, argv);

    int fd = open(argv[1], O_RDONLY);
    if (fd < 0)
        die_errno("cannot open '%s'", argv[1]);
    struct buf in = {0}, out = {0};
    read_to_eof(fd, &in);
    close(fd);

    grep_lines(in.data, in.len, argv[2], strlen(argv[2]), &out);
    sort_and_print(out.data, out.len);
    return 0;
}
