# Lab03：进程间通信与 Map-Reduce

## 截止日期

请参阅实验作业要求。

## 目标

本项目旨在练习各种 IPC 方法（用于数据传递和同步），并学习 map-reduce（并行计算）。这两者都是工业界经常使用的重要技术。

## 详情

本项目由三个独立的子项目组成，每个子项目执行相同的任务：给定一个文本文件，程序输出包含给定单词的那些行。例如，给定一行 "Hello World!"，并假设关注的单词是 "world"，那么这一行应当被输出。（注意，如果某一行是 "Hello worlds"，则该行不应被输出。）

你的程序（即父进程）将创建一个子进程。父进程可以打开文本文件、读取内容，并通过以下方法之一将内容传递给子进程，但不能检查单词；而子进程不能打开该文件，但可以检查单词。最后，父进程（而不是子进程）应按字母顺序输出这些行。以下是三个子项目的要求：

1. 使用管道（Pipe）作为传递文件内容和结果的方法。
2. 使用 Unix 域套接字（Unix Domain Socket）作为传递文件内容和结果的方法。
3. 使用共享内存（Shared Memory）作为传递文件内容和结果的方法。此外，子进程创建 4 个线程，每个线程充当一个 Mapper；子进程的主线程作为唯一的 reducer。Map-reduce 仅在**此**子项目中要求。你不得使用 Hadoop 作为 map-reduce 基础设施；相反，必须使用 POSIX 线程编程来实现 map-reduce。

## 示例输入文本文件

- 《安娜·卡列尼娜》。列夫·托尔斯泰，1870 年。（见附件：ANNA_KARENINA.txt）
- 一个 6.5M 的文件。（见附件：big.txt）

这些只是一些示例文本文件。你的程序应接受文件路径作为参数。换言之，它应当能处理任意文本文件。因此，为输入文件创建索引不是好主意，也不被允许。

## 提交内容

你的提交应包括：(1) 代码（必须包含 makefile）；(2) 一个 readme 文件，描述你的设计以及如何编译/使用你的代码；(3) 针对以下课后作业的报告：

- 对你的三个程序进行计时，并分析造成性能差异的原因。
- 你的程序设计。
- 实验结果的快照（统计数据）及分析。
- 遇到的问题及其解决方案。
- 总结 Linux 提供的不同 IPC 方法，并说明何时使用哪一种。
- 写一段关于 map-reduce 的简短文字。
- 写一段关于 Hadoop 的简短文字，并说明你对它为何在工业界流行且重要的理解。
- 参考资料。
- 你的建议和意见。

## 实验环境

Linux（推荐 Ubuntu 18.04/16.04）和 C/C++。

## 参考资料

以下文章可能对你有用：

- A simple makefile tutorial. Colby University. [链接](http://www.cs.colby.edu/maxwell/courses/tutorials/maketutor/)
- An Introduction to Linux IPC. Kerrisk, 2013. [链接](http://citel.bjtu.edu.cn/moodle/file.php/152/Materials/IPC-Overview-2013.pdf)
- Beej's Guide to Unix IPC. Beej, 2010. [链接](http://beej.us/guide/bgipc/)
- Linux Interprocess Communications. Goldt, Meer, Burkett, and Welsh, 1995. [链接](http://www.tldp.org/LDP/lpg/node7.html)
- MapReduce: Simplified Data Processing on Large Clusters. Dean and Ghemawat, 2004. [链接](http://static.googleusercontent.com/media/research.google.com/en/archive/mapreduce-osdi04.pdf)
- POSIX Threads Programming. Barney, 2015. [链接](https://computing.llnl.gov/tutorials/pthreads/)
- Beej's Quick Guide to GDB. Beej, 2009. [链接](http://beej.us/guide/bggdb/)
