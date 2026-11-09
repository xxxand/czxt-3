# AI 使用与分工说明

本项目在开发过程中使用了 AI 编程助手。以下说明用户与 AI 各自完成的工作。AI 生成的内容均经过用户核查，最终提交内容由用户确认。

## 用户完成的工作

- 明确实验要求、程序行为约定和最终提交范围。
- 核查全部代码与文档，包括 `README.md`、`REPORT.md`、测试脚本和计时脚本。
- 修改文档内容，根据提交要求精简部分附录与参考资料，并补充必要说明。
- 添加构建、终端演示、自动化测试和性能测试的终端截图：
  - `image-1.png`：构建过程
  - `image.png`：功能演示
  - `image-2.png`：自动化测试
  - `image-3.png`：性能测试
- 亲自运行构建、检查和性能测试命令，并核对输出，例如：
  - `make clean && make`
  - `make test`
  - `python3 bench/bench.py ...`
- 检查三个程序在样例文件和随机输入上的结果，以及与 `grep -iw` 的一致性。
- 确认 Git 提交内容并完成最终推送。

## AI 完成的工作

- 提出整体实现思路：三个程序共同使用 `common.c`，分别用匿名管道、Unix 域套接字和 POSIX 共享内存完成父子进程通信；共享内存版使用 4 个 mapper 线程和主线程 reducer。
- 编写程序代码：`common.c`、`common.h`、`pipe_grep.c`、`uds_grep.c`、`shm_grep.c` 和 `Makefile`。
- 编写测试与计时工具：`tests/run_tests.py`、`bench/bench.py`、`bench/phases.py`、`bench/baseline.c`。
- 起草 README、实验报告和复现脚本 `report_demo.sh`。
- 在开发过程中运行测试、变异测试、sanitizer、valgrind 和性能测量，用于定位问题并验证实现。
- 根据用户的核查意见修改代码、文档和构建输出位置。
- 按用户要求协助完成 Git 提交与推送。

## 提交声明

最终提交的代码、文档和截图均由用户核验。AI 仅作为开发辅助工具，用户对提交内容的正确性负责。
