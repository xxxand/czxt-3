# Lab03: IPC 与 map-reduce

给定一个文本文件和一个单词，输出文件中包含该单词的行（按字母序排列）。同一个任务用三种 IPC 方式各实现一遍：

| 程序 | 父子进程间的 IPC | 子进程内部 |
|---|---|---|
| `pipe_grep` | 匿名管道 | 单线程 |
| `uds_grep` | Unix 域套接字 | 单线程 |
| `shm_grep` | POSIX 共享内存 + 信号量 | 4 个 mapper 线程 + 主线程作为唯一的 reducer（map-reduce） |

三个程序的分工相同：

- **父进程**：打开并读取文件，把内容交给子进程，收回结果，**排序并输出**。它把文件内容当作不透明的字节流，不检查单词。
- **子进程**：不打开文件（`fork` 之后第一件事是关闭继承来的文件描述符），只能通过 IPC 拿到内容，**负责检查单词**，把匹配的行交还给父进程。

每次运行都完整扫描一遍文件，不建立索引，可以处理任意文本文件。

## 1. 文件

| 文件 | 说明 |
|---|---|
| `Makefile` | 编译、测试、清理；产物输出到 `build/` |
| `build/` | 编译产物（可执行文件和 `.o` 文件，由 `make` 生成） |
| `common.c`, `common.h` | 三个程序共用的代码 |
| `pipe_grep.c` | 子项目 1：管道 |
| `uds_grep.c` | 子项目 2：Unix 域套接字 |
| `shm_grep.c` | 子项目 3：共享内存 + map-reduce |
| `tests/run_tests.py` | 测试脚本 |
| `bench/bench.py` | 端到端计时脚本：墙钟时间、CPU 时间、上下文切换、缺页次数的统计 |
| `bench/phases.py` | 耗时分解脚本：在源码副本里插入计时点，交付的源码不会被修改 |
| `bench/baseline.c` | 单进程基线程序，仅供计时对照，由 `bench/bench.py` 自动编译 |
| `REPORT.md` | 实验报告（设计、实验数据与分析、遇到的问题、IPC 与 map-reduce 的总结）；计时脚本的用法见其附录 A |
| `ANNA_KARENINA.txt`, `big.txt` | 样例输入 |

## 2. 编译与使用

### 环境

Linux、支持 `-std=gnu11` 的 gcc、make。`make test` 另需 python3 和 GNU grep。已在 Ubuntu（WSL2，gcc 15.2，glibc 2.43）上编译并通过测试。

### 编译

```bash
make          # 在 build/ 下生成 pipe_grep、uds_grep、shm_grep
make test     # 运行测试（可选）
make clean    # 删除 build 目录
```

可执行文件和 `.o` 文件都生成在 `build/` 下。编译和链接都带 `-pthread`；`shm_grep` 还链接了 `-lrt`（glibc 2.34 之前，`shm_open` 位于 librt）。

### 运行

```bash
./build/pipe_grep <文件路径> <单词>
./build/uds_grep  <文件路径> <单词>
./build/shm_grep  <文件路径> <单词>
```

三个程序的参数和输出完全相同。例如：

```bash
printf 'Hello World!\nHello worlds\nthe_world\nsay WORLD.\nworld2\nA world-wide web\nbefore: world\n' > /tmp/demo.txt
./build/pipe_grep /tmp/demo.txt world
```

输出如下。`Hello worlds`、`the_world`、`world2` 中不含单词 `world`，所以不输出：

```
A world-wide web
before: world
Hello World!
say WORLD.
```

样例文件：

```bash
./build/pipe_grep ANNA_KARENINA.txt world | wc -l     # 输出 134
./build/uds_grep big.txt Holmes | wc -l               # 输出 446
```

与 `grep` 对照（比较行的集合，不比较顺序）：

```bash
diff <(./build/shm_grep big.txt the | LC_ALL=C sort) <(grep -iw the big.txt | LC_ALL=C sort) && echo 一致
```

### 匹配、排序与输出约定

- **匹配**：不区分大小写的整词匹配，等价于 `grep -iw`。单词字符是字母、数字、下划线；其余字符（空格、标点、`-`、`'`、`\r`、非 ASCII 字节等）都视为分隔符。因此 `Hello World!` 和 `A world-wide web` 会被输出，`Hello worlds` 不会。单词参数本身含标点（如 `don't`）时按字面匹配，只检查匹配处两端相邻的字符。
- **排序**：逐字节比较，ASCII 字母不区分大小写（`Apple` 与 `apple` 相邻）；一行是另一行的前缀时，短的在前；两行只有大小写不同时，再按原始字节序决定先后（大写在前），使输出顺序是确定的。
- **输出**：每行以 `\n` 结尾（输入的最后一行没有换行符时也会补上），重复的行都保留。
- **退出码**：`0` 正常结束（没有匹配的行也是 `0`）；`1` 运行出错（文件打不开、读写出错、子进程异常等），错误信息写到 stderr；`2` 命令行参数不对。

## 3. 设计

### 3.1 共用代码（`common.c`）

三个程序共用同一份读写、匹配、排序代码，因此它们耗时上的差异主要来自 IPC 方式和子进程内部的并行度。按调用方分为两组：

- 父进程：`send_file`（以 64 KB 为单位把文件读出并写入通道）、`read_to_eof`、`wait_child`（回收子进程，非 0 退出或被信号杀死都会报错）、`sort_and_print`。
- 子进程：`child_filter_stream`（管道和套接字版本的子进程主体）、`grep_lines`（对一段完整的行做匹配）。

其余是出错退出、可增长缓冲区、`read_retry`/`write_all`（处理 `EINTR` 和短写）等辅助函数。

### 3.2 `pipe_grep`：匿名管道

```
父进程                                          子进程
read(file_fd, 64KB)
write(to_child[1])    ---- file content ---->   read(to_child[0])，边读边匹配
close(to_child[1])    ------- EOF ------->      read 返回 0，匹配最后一段
read(from_child[0])   <--- matched lines ---    write(from_child[1])，一次写回
waitpid；排序；输出
```

1. 父进程打开文件，创建两条管道（`to_child`：父到子，`from_child`：子到父），然后 `fork`。
2. 双方各自关闭用不到的那一端。一条管道的所有写端都关闭后，读端才会读到 EOF，所以这一步是必须的。
3. 父进程循环 `read`（文件）、`write`（`to_child`），发完后关闭 `to_child` 的写端，子进程由此读到 EOF。
4. 子进程分块读入，每次只匹配已经收齐的完整行；末尾不完整的半行留在缓冲区，和下一块拼起来。匹配的行先攒在内存里，读到 EOF 后一次性写入 `from_child`。回写放在读完之后：父进程在发完文件之前不读取结果，此时若子进程回写，两个方向的管道写满后双方会互相等待（死锁）。
5. 父进程读到 EOF 后 `waitpid`，排序并输出。

父进程在与子进程通信期间忽略 `SIGPIPE`，子进程提前退出时 `write` 返回 `EPIPE`，父进程报出明确的错误；排序输出前恢复默认处理，因此 `./build/pipe_grep ... | head` 的行为与普通命令一致。

### 3.3 `uds_grep`：Unix 域套接字

父子进程由 `fork` 关联，所以用 `socketpair(AF_UNIX, SOCK_STREAM, 0, sv)` 直接得到一对已连接的匿名套接字，不需要路径名，也不需要 `bind`/`listen`/`accept`。与管道版本的区别：

- 一条套接字就是一条双向通道：父进程用 `sv[0]`，子进程用 `sv[1]`，输入和结果走同一条连接。
- 父进程发完文件后调用 `shutdown(sv[0], SHUT_WR)` 半关闭：子进程读到 EOF，而父进程仍能从同一个套接字读回结果。

子进程复用 `child_filter_stream`，其余流程与 `pipe_grep` 相同。

### 3.4 `shm_grep`：共享内存与 map-reduce

子进程用 POSIX 线程（`pthread_create`/`pthread_join`）实现 map-reduce：4 个 mapper 线程（`shm_grep.c` 中的 `NUM_MAPPERS`），子进程的主线程是唯一的 reducer。

**共享内存布局**：一整块，由父进程创建，`fork` 后子进程继承映射。

```
+-------------------+------------------------+--------------------------+
| control block     | input area             | result area              |
| 4096 B            | N bytes                | N+1 bytes                |
+-------------------+------------------------+--------------------------+
```

- 控制块：两个信号量 `input_ready`、`result_ready`，以及 `input_len`、`result_len`。
- 输入区：父进程把文件内容读到这里，mapper 只读。
- 结果区：reducer 把匹配的行写到这里，父进程直接从这里排序输出。

`N` 是文件大小（`fstat`）。结果是输入行的子集，每行末尾补一个 `\n`，总长度最多比输入多 1 字节（最后一行没有换行符时），所以结果区 `N+1` 字节足够。

创建过程：`shm_open("/lab03_shm_<pid>", O_CREAT|O_EXCL)`，随即 `shm_unlink`，再 `ftruncate`、`mmap(MAP_SHARED)`，最后 `fork`。子进程通过继承得到映射，名字不再需要，所以创建后立即删除；之后即使进程被信号杀死，内核也会在两个进程都退出后回收这块内存。

**同步**：控制块里的两个信号量是进程间共享的 POSIX 信号量（`sem_init` 的 `pshared` 参数为 1，初值为 0）：

- `input_ready`：父到子，文件内容已经读进输入区（长度记在 `input_len`）。
- `result_ready`：子到父，结果已经写进结果区（长度记在 `result_len`）。

**流程**：

```
父进程                                          子进程
fstat；shm_open、mmap；fork
read(file_fd) 直接读进输入区
sem_post(input_ready)   ----------------->      sem_wait(input_ready)
                                                把输入区切成 4 段，创建 4 个 mapper 线程
                                                  mapper 0..3：各扫一段，匹配的行放进私有缓冲区   (map)
                                                主线程依次 pthread_join，把 4 份结果拼进结果区    (reduce)
sem_wait(result_ready)  <-----------------      sem_post(result_ready)
waitpid；直接从结果区排序并输出
```

- **map**（`map_thread`）：输入区按字节数四等分，每个分界点向后对齐到下一个行首（`align_to_line`），所以每个 mapper 拿到的都是完整的行，每行恰好属于一个 mapper。输入区只读，每个 mapper 的输出缓冲区是私有的，线程之间没有共享的可写数据，不需要加锁。
- **reduce**（`child_main` 中标注 reduce 的循环）：主线程按编号依次 `pthread_join` 各 mapper，把它们的结果拼接进结果区。这与 MapReduce 论文中的分布式 grep 相同：map 函数输出匹配的行，reduce 函数是恒等函数，只负责汇总。
- 排序放在父进程，与另外两个程序一致。

**异常处理**：

- 子进程崩溃或被杀死时，父进程的 `SIGCHLD` 处理函数会 `sem_post(result_ready)`，父进程被唤醒后 `waitpid` 并报告子进程的状态，不会永远阻塞在信号量上。
- 父进程先退出时（例如读文件出错），子进程通过 `prctl(PR_SET_PDEATHSIG, SIGKILL)` 随之终止，不会遗留在 `sem_wait` 上。

### 3.5 匹配与排序的实现

- **匹配**（`line_has_word`）：对一行中的每个起点 `i`，要求前一个字符不是单词字符，从 `i` 起的若干字符与单词逐字节相等（ASCII 字母不区分大小写），后一个字符不是单词字符；全部满足即命中。文件内容始终按“指针 + 长度”处理，不使用 C 字符串函数，因此 NUL 字节不会影响结果。
- **排序**（`sort_and_print`）：把结果按 `\n` 切成行，用 `qsort` 排序（比较规则见第 2 节），再用一次 `write` 输出。

## 4. 测试

`make test` 运行 `tests/run_tests.py`，把三个程序的输出与 Python 参考实现逐字节比较（参考实现本身也与 `grep -iw` 对照）。覆盖：

- 题目中的例子；单词参数大小写无关。
- 边界情况：空文件、最后一行没有换行符、CRLF、含 NUL 字节和 UTF-8 的内容、重复的行、带标点的单词。
- 3 MB 的超长行，以及长度各异的行（数据块边界落在行中间）。
- 30 万行全部命中（结果远大于管道和套接字的缓冲区）。
- 200 个随机小文件（覆盖 `shm_grep` 的分段边界）。
- 两个样例文件，每个 5 个单词。
- 错误处理：缺少参数、单词为空、文件不存在、参数是目录、没有读权限。
- `/dev/shm` 中没有残留的共享内存对象。

全部通过时最后一行是 `N 项通过, 0 项失败`。

## 5. 限制

- `shm_grep` 只接受普通文件（共享内存要在读文件之前按文件大小创建），并且 `/dev/shm` 要能容纳“文件内容 + 结果”。空间不足时程序以退出码 1 结束，错误信息形如 `read: Bad address` 或 `child process killed by signal 7`。`pipe_grep` 和 `uds_grep` 是流式读取，没有这个限制。
- 大小写折叠只针对 ASCII 字母；多字节 UTF-8 字符不是单词字符。
- 程序使用了 `prctl` 等 Linux 专有接口，只面向 Linux。
