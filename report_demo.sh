#!/usr/bin/env bash
# 重放 REPORT.md 第 3.1 节「终端演示」里的命令，供手动截图替换报告文字。
#
# 用法:
#   cd /home/jxxyyy/project/czxt-3
#   clear
#   bash report_demo.sh
#
# 脚本会先打印 "$ 命令"，再执行该命令，输出顺序与报告中的终端演示一致。

set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT" || exit 1

for prog in pipe_grep uds_grep shm_grep; do
    if [[ ! -x "build/$prog" ]]; then
        echo "缺少 build/$prog，请先运行 make。" >&2
        exit 1
    fi
done

run() {
    printf '$ %s\n' "$1"
    eval "$1"
}

run "printf 'Hello World!\\nHello worlds\\nthe_world\\nsay WORLD.\\nworld2\\nA world-wide web\\nbefore: world\\n' > /tmp/demo.txt"
run "./build/pipe_grep /tmp/demo.txt world"
run "for p in pipe_grep uds_grep shm_grep; do ./build/\$p /tmp/demo.txt world | md5sum; done"
printf '\n'

run "./build/pipe_grep ANNA_KARENINA.txt world | head -4"
run "for p in pipe_grep uds_grep shm_grep; do ./build/\$p ANNA_KARENINA.txt world | wc -l; done"
run "./build/uds_grep big.txt Holmes | wc -l"
run "./build/shm_grep big.txt the | wc -l"
printf '\n'

run "diff <(./build/shm_grep big.txt the | LC_ALL=C sort) <(grep -iw the big.txt | LC_ALL=C sort) && echo 与 grep -iw 的结果一致"
printf '\n'

run "./build/pipe_grep; echo \"退出码=\$?\""
run "./build/pipe_grep /nonexistent x; echo \"退出码=\$?\""
run "./build/uds_grep . x; echo \"退出码=\$?\""
run "./build/shm_grep . x; echo \"退出码=\$?\""
run "./build/shm_grep /tmp/demo.txt nomatch; echo \"无匹配时退出码=\$?\""
