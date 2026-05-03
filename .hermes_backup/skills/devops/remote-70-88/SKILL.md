---
name: remote-70-88
description: 远程操作 GPU 服务器 (10.10.70.88) — SSH 连接、CUDA/nvcc 安装、NCCL 版本管理、编译 nccl-tests、运行带宽测试
version: 1.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [ssh, gpu, cuda, nccl, nvcc, bandwidth-test, remote, devops]
    related_skills: [remote-deploy, systematic-debugging]
---

# Remote GPU Server (10.10.70.88)

远程操作 GPU 服务器 10.10.70.88 的常用工作流。包含 SSH 连接、CUDA 工具链安装、NCCL 版本管理、nccl-tests 编译与运行、带宽测试等操作。

## 环境信息

| 项目 | 值 |
|------|-----|
| 主机 | 10.10.70.88 |
| 用户 | jianliu |
| 系统 | Ubuntu 22.04 LTS |
| GPU | 8× NVIDIA RTX 5090 (Blackwell, CC 12.0) |
| 驱动 | 580.126.09 |
| CUDA Driver API | 13.0 |
| CUDA Toolkit | 13.0.88 (仅 nvcc，非完整 toolkit) |
| NCCL | 2.28.9-1+cuda13.0 (系统级) |
| Python | 系统 Python + PyTorch (torchrun) |
| 工作目录 | ~/work/bandwidth_test/ |

## SSH 连接

```bash
# 首次连接接受 host key
ssh -o StrictHostKeyChecking=accept-new jianliu@10.10.70.88 "echo connected"

# 交互式登录
ssh -t jianliu@10.10.70.88

# 执行命令并返回
ssh -t jianliu@10.10.70.88 "command"

# 需要 sudo 时通过管道传密码
ssh -t jianliu@10.10.70.88 "echo 'PASSWORD' | sudo -S apt-get install -y PACKAGE"
```

**注意:** `-t` 参数强制分配伪终端，某些 sudo 操作需要。如果不需要交互，可以省略 `-t`。

## CUDA / nvcc 安装与管理

### 安装单独的 nvcc（不是完整 toolkit）

NVIDIA 官方仓库提供细粒度包，可以只装 nvcc 编译器（~87 MB vs 完整 toolkit 3.2 GB）：

```bash
# 添加 NVIDIA 仓库（仅首次）
wget https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2204/x86_64/cuda-keyring_1.1-1_all.deb
echo 'PASSWORD' | sudo -S dpkg -i cuda-keyring_1.1-1_all.deb
echo 'PASSWORD' | sudo -S apt-get update

# 安装指定版本的 nvcc（选择与驱动匹配的版本）
echo 'PASSWORD' | sudo -S apt-get install -y cuda-nvcc-13-0

# 会自动安装依赖：cuda-cudart-13-0, cuda-cccl-13-0, cuda-crt-13-0 等
# 自动设置 /usr/local/cuda -> /usr/local/cuda-13.0 备选链接
```

### 选择正确的 nvcc 版本

```bash
# 查看驱动支持的 CUDA 版本
ssh jianliu@10.10.70.88 "nvidia-smi -q | grep 'CUDA Version'"

# 查看可用版本
curl -sL "https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2204/x86_64/Packages.gz" \
  | gunzip -c | grep -E "^Package:|^Version:" | grep -B1 "cuda-nvcc" | tail -20
```

**版本匹配规则:**
- Blackwell (RTX 5090, CC 12.0) 需要 **CUDA 12.8+**
- 优先选择 nvidia-smi 报告的 CUDA Version 对应的大版本
- 例如驱动报告 CUDA 13.0 → 安装 `cuda-nvcc-13-0`
- 可用版本: `cuda-nvcc-12-8`, `cuda-nvcc-12-9`, `cuda-nvcc-13-0`, `cuda-nvcc-13-1`, `cuda-nvcc-13-2`

### 将 CUDA 加入 PATH

```bash
# 添加到 .profile（login shell）和 .bashrc（interactive shell）
echo 'export PATH=/usr/local/cuda/bin:$PATH' >> ~/.profile
echo 'export LD_LIBRARY_PATH=/usr/local/cuda/lib64:$LD_LIBRARY_PATH' >> ~/.profile
echo 'export PATH=/usr/local/cuda/bin:$PATH' >> ~/.bashrc
echo 'export LD_LIBRARY_PATH=/usr/local/cuda/lib64:$LD_LIBRARY_PATH' >> ~/.bashrc

# 验证
bash -l -c "nvcc --version"
```

## NCCL 版本管理

### 查看已安装的 NCCL

```bash
ssh jianliu@10.10.70.88 "
  dpkg -l | grep -i nccl;
  awk '/NCCL_MAJOR|NCCL_MINOR|NCCL_PATCH/{print}' /usr/include/nccl.h | head -3;
  ldconfig -p | grep nccl
"
```

### 查看可用的 NCCL 版本

```bash
# 列出所有可用的 libnccl2 版本
ssh jianliu@10.10.70.88 "apt-cache madison libnccl2"
```

### 降级/升级 NCCL 以匹配 CUDA 版本

**重要：** NCCL 的包名包含编译时用的 CUDA 版本号（如 `2.28.9-1+cuda13.0`）。必须使用与驱动支持的 CUDA 版本匹配的 NCCL 包。

```bash
# 降级到指定 CUDA 版本的 NCCL
echo 'PASSWORD' | sudo -S apt-get install -y --allow-downgrades \
  libnccl2=2.28.9-1+cuda13.0 libnccl-dev=2.28.9-1+cuda13.0
```

**版本对应关系（Ubuntu 22.04 NVIDIA 仓库）:**

| CUDA 版本 | 最新 NCCL | 说明 |
|-----------|-----------|------|
| cuda13.2 | 2.30.4 | 最新，但需要 CUDA 13.2 驱动 |
| **cuda13.0** | **2.28.9** | **当前机器驱动支持的最新版本** |
| cuda12.9 | 2.30.4 | 可降级到此版本 |
| cuda12.8 | 2.26.2 | 最低 Blackwell 支持 |

### 验证 NCCL

```bash
# 编译测试
cat > /tmp/test_nccl.cu << 'EOF'
#include <nccl.h>
#include <stdio.h>
int main() {
  printf("NCCL compiled OK\n");
#if NCCL_VERSION_CODE
  printf("NCCL version code: %d\n", NCCL_VERSION_CODE);
#endif
  return 0;
}
EOF
/usr/local/cuda/bin/nvcc -I/usr/include -o /tmp/test_nccl /tmp/test_nccl.cu -lnccl 2>&1 && /tmp/test_nccl
```

## 编译 nccl-tests

nccl-tests 在 `~/work/bandwidth_test/nccl-tests/` 目录下。

```bash
# 编译（Makefile 自动检测 CUDA 版本，Blackwell sm_120 已内置支持）
cd ~/work/bandwidth_test/nccl-tests
make -j8

# 编译产物在 build/ 目录
ls -lh build/*_perf
```

**Makefile 自动处理的细节:**
- CUDA 13.0 检测到后自动使用 `-std=c++17`
- Blackwell (sm_120) 自动加入 GENCODE
- NCCL 从系统默认路径链接 (`-lnccl`)

### 故障排除

如果遇到 `fatal error: nccl.h: No such file or directory`:
- 确认 `libnccl-dev` 已安装
- 检查 `/usr/include/nccl.h` 是否存在
- 可手动指定 `NCCL_HOME=/usr` 或 `CUDAT_HOME=/usr/local/cuda`

如果遇到链接错误 `cannot find -lnccl`:
- 确认 `ldconfig -p | grep nccl` 有输出
- 确认 `/usr/lib/x86_64-linux-gnu/libnccl.so` 存在
- 运行 `echo 'PASSWORD' | sudo -S ldconfig` 刷新库缓存

## 自动化 Benchmark 脚本（一键运行）

软件源里有一个可复用的自动化脚本，封装了完整的 benchmark 工作流（环境检查 → 构建 → 逐项测试 → 自动生成 Markdown 报告）：

**脚本路径:** `~/work/bandwidth_test/nccl_test/run_nccl_benchmark.sh`

```bash
# 完整测试（2卡 + 6×8卡集合操作，约15-20分钟）
bash ~/work/bandwidth_test/nccl_test/run_nccl_benchmark.sh full

# 快速测试（仅8卡 all_reduce，约5分钟）
bash ~/work/bandwidth_test/nccl_test/run_nccl_benchmark.sh quick

# 重新生成报告（重用上次原始数据，不跑测试）
bash ~/work/bandwidth_test/nccl_test/run_nccl_benchmark.sh summary
```

脚本输出：
- `raw_results_YYYYMMDD_HHMMSS.txt` — 原始 nccl-tests 输出
- `nccl_benchmark_YYYYMMDD_HHMMSS.md` — 结构化 Markdown 报告（7个章节）
- `nccl_benchmark_latest.md` — 最新报告的符号链接

**脚本特性:**
- 自动检测 nccl-tests 是否已编译，按需构建
- 单卡验证 → 2卡 all_reduce → 8卡全集合操作（all_reduce/all_gather/broadcast/reduce_scatter/alltoall/sendrecv）
- 正确解析 nccl-tests 输出列（`time_us=$6, algbw=$7, busbw=$8`），自动生成数据表
- 报告包含：环境信息、完整阶梯数据表、汇总对比表、延迟分析、2卡vs8卡对比、关键发现
- 所有数字从实际测试结果动态提取，无硬编码

## 运行带宽测试（手动 Benchmark 工作流）

### 基准测试流程（推荐顺序）

建议先运行 2 卡再跑 8 卡，逐项收集完整结果后汇总为报告。

#### Step 1: 单 GPU 验证（快速检查编译是否正确）

```bash
cd ~/work/bandwidth_test/nccl-tests/build
./all_reduce_perf -b 8 -e 256M -f 2 -g 1 2>&1 | tail -5
```
注意：单 GPU 的 all_reduce 带宽为 0，这是正常的（all_reduce 需要 ≥2 卡才有意义）。用于验证程序能正常运行不崩溃。

#### Step 2: 2 卡测试（检查 ring 基础性能）

```bash
cd ~/work/bandwidth_test/nccl-tests/build
./all_reduce_perf -b 8 -e 8G -f 2 -g 2 2>&1
```

预计结果：大数据峰值 ~35 GB/s (AlgoBW)，延迟 ~7.8 us

#### Step 3: 8 卡 ALL 集合操作测试

```bash
cd ~/work/bandwidth_test/nccl-tests/build

# all_reduce（最常用的集合操作）
./all_reduce_perf -b 8 -e 8G -f 2 -g 8 2>&1
# all_gather
./all_gather_perf -b 8 -e 8G -f 2 -g 8 2>&1
# broadcast
./broadcast_perf -b 8 -e 8G -f 2 -g 8 2>&1
# reduce_scatter
./reduce_scatter_perf -b 8 -e 8G -f 2 -g 8 2>&1
# alltoall（PCIe 拓扑下最慢）
./alltoall_perf -b 8 -e 8G -f 2 -g 8 2>&1
# sendrecv（P2P 带宽）
./sendrecv_perf -b 8 -e 8G -f 2 -g 8 2>&1
```

#### Step 4: 汇总生成报告

收集各测试的 tail 输出（关注最后几行的大数据峰值和 Avg bus bandwidth），汇总为 Markdown 报告：

- 报告应包含：环境信息、各操作带宽对比表、延迟对比表、2卡vs8卡对比、关键发现
- 报告文件可复制到 Windows 路径: `cp REPORT.md "/mnt/c/Users/liuch/Documents/warpdriveai/5090/"`

### 输出解读

```
     Size      Count  Type   RedOp   Root   AlgoBW (GB/s)  BusBW (GB/s)
  134217728  33554432  float  sum      -1     5986.48        22.42       39.24
```

- **AlgoBW**: 算法带宽（实际传输的数据量/时间）
- **BusBW**: 总线带宽（考虑了 NCCL 内部数据搬运倍数，通常 AlgoBW × 1.7~2.0）
- **Avg bus bandwidth**: 所有 size 的平均总线带宽
- **关注点**: 尾行（最大 size）反映峰值带宽，Avg bus bandwidth 反映整体效率

### 8× RTX 5090 参考值（实测 2025-04-29）

| 操作 | 8B 延迟 (us) | 峰值 BusBW (GB/s) | Avg BusBW (GB/s) |
|------|:-----------:|:----------------:|:---------------:|
| all_reduce (2卡) | 7.84 | 35.36 | 15.34 |
| all_reduce (8卡) | 31.76 | 40.70 | 16.95 |
| all_gather | 40.7 | 39.74 | 15.75 |
| broadcast | 30.5 | 41.25 | 17.61 |
| reduce_scatter | 40.5 | 38.12 | 15.43 |
| alltoall | 54.8 | 27.58 | 8.81 |
| sendrecv | 42.6 | 25.82 | 10.54 |

**Note:** 峰值 BusBW 40+ GB/s 为 PCIe 5.0 x16 ring 算法理论极限。alltoall/sendrecv 较低为 PCIe 拓扑无 NVSwitch 所限。

## 验证综合检查

一键检查所有关键组件：

```bash
ssh -t jianliu@10.10.70.88 "
  echo '=== GPU ==='; nvidia-smi --query-gpu=name,driver_version --format=csv,noheader | head -1;
  echo '=== CUDA Driver ==='; nvidia-smi -q | grep 'CUDA Version';
  echo '=== nvcc ==='; /usr/local/cuda/bin/nvcc --version 2>&1 | tail -2;
  echo '=== NCCL ==='; awk '/NCCL_(MAJOR|MINOR|PATCH)/{printf \"%s.%s.%s\n\", \$3, \$3, \$3}' /usr/include/nccl.h;
  echo '=== nccl-tests ==='; ls ~/work/bandwidth_test/nccl-tests/build/*_perf 2>/dev/null | wc -l;
  echo '=== Python torch ==='; python3 -c 'import torch; print(\"torch\", torch.__version__)' 2>/dev/null || echo 'torch not found'
"
```

## 常用快捷命令

| 目的 | 命令 |
|------|------|
| 快速检查状态 | `ssh jianliu@10.10.70.88 "nvidia-smi --query-gpu=name,driver_version,memory.used --format=csv"` |
| 监控 GPU | `ssh -t jianliu@10.10.70.88 "watch -n 1 nvidia-smi"` |
| 查看日志 | `ssh -t jianliu@10.10.70.88 "journalctl -n 50 -f"` |
| 查看磁盘 | `ssh jianliu@10.10.70.88 "df -h /"` |
| 网络测试 | `ssh jianliu@10.10.70.88 "iperf3 -c <server>"` |

## Docker GPU 容器配置（nvidia-container-toolkit）

在 GPU 服务器上运行 `docker run --gpus all` 需要安装 **nvidia-container-toolkit**。Docker 29+ 默认使用 CDI (Container Device Interface) 发现 GPU，必须通过此工具包生成 CDI 规范。

### 常见错误

```
docker: Error response from daemon: failed to discover GPU vendor from CDI: no known GPU vendor found
```

**原因：** `nvidia-container-toolkit` 未安装，CDI 配置不存在，Docker 无法通过 CDI 找到 GPU。

### 诊断步骤

```bash
# 1. 检查是否已安装
dpkg -l | grep nvidia-container
# 如果无输出 → 未安装

# 2. 检查 CDI 目录是否存在
ls /etc/cdi/ 2>/dev/null || echo 'No CDI config'

# 3. 检查 nvidia-ctk 命令
which nvidia-ctk 2>/dev/null || echo 'nvidia-ctk not installed'

# 4. 检查 Docker 运行时列表
echo 'PASSWORD' | sudo -S docker info 2>&1 | grep -A5 'Runtimes'
# 如果只显示 runc 不显示 nvidia → 未配置
```

### 安装与配置

```bash
# 1. 安装 nvidia-container-toolkit
echo 'PASSWORD' | sudo -S apt-get install -y nvidia-container-toolkit

# 2. 配置 Docker 运行时（生成 CDI specs + 注册 nvidia runtime）
echo 'PASSWORD' | sudo -S nvidia-ctk runtime configure --runtime=docker

# 3. 重启 Docker daemon
echo 'PASSWORD' | sudo -S systemctl restart docker

# 4. 验证——应列出 nvidia CDI specs
echo 'PASSWORD' | sudo -S nvidia-ctk cdi list
```

### 验证 GPU 容器可用

```bash
# 运行测试容器（使用 CDI device 语法——Docker 29.4 推荐方式）
echo 'PASSWORD' | sudo -S docker run --rm --device='nvidia.com/gpu=all' nvidia/cuda:13.0-base-ubuntu22.04 nvidia-smi

# 检查 /etc/cdi/ 目录
ls /etc/cdi/nvidia.yaml 2>/dev/null && echo 'CDI config OK'
```

### 常见 CDI 错误排查

**错误：** `docker: Error response from daemon: AMD CDI spec not found`

**原因：** Docker 29.4 的 CDI 实现在使用 `--gpus all` 时有兼容性缺陷。即使 CDI 配置完全正确（`nvidia-ctk cdi list` 能列出所有 GPU），`--gpus all` 仍可能失败。

**修复：** 用 `--device='nvidia.com/gpu=all'` 替代 `--gpus all`：
```bash
# ❌ 可能在 Docker 29.4 上失败
docker run --rm --gpus all nvidia/cuda:13.0-base-ubuntu22.04 nvidia-smi

# ✅ 总是有效
docker run --rm --device='nvidia.com/gpu=all' nvidia/cuda:13.0-base-ubuntu22.04 nvidia-smi
```

**错误：** `docker: Error response from daemon: failed to discover GPU vendor from CDI: no known GPU vendor found`

**原因：** `nvidia-container-toolkit` 未安装，CDI 配置不存在。

**修复：** 安装并配置（见上一节）。

**错误：** `docker: Error response from daemon: unknown or invalid runtime name: nvidia`

**原因：** Docker daemon 未重启导致新配置未加载。`nvidia-ctk runtime configure` 在 `/etc/docker/daemon.json` 中添加了 nvidia runtime，但 Docker 需要重启才能生效。如果无法重启 Docker（如影响其他运行中的容器），仍可使用 `--device='nvidia.com/gpu=all'` CDI 语法来绕过。

### 注意事项

- `nvidia-ctk` 是 `nvidia-container-toolkit` 包的一部分，安装后自动可用
- 旧版 Docker（< 29）可能使用 `--runtime=nvidia`，但 Docker 29+ 统一使用 CDI
- **Docker 29.4 的已知问题：** `--gpus all` 可能失败。推荐使用 `--device='nvidia.com/gpu=all'`，这是标准的 CDI 语法，在所有支持 CDI 的 Docker 版本上都能工作
- 如果已安装但还不工作，检查 `/etc/docker/daemon.json` 是否包含 `"runtimes": {"nvidia": ...}` 配置
- 此操作不需要安装完整的 NVIDIA 驱动（驱动已通过 `nvidia-driver-580-server-open` 安装）

## 注意事项

**注意:** `-t` 参数对于需要 PTY 的命令（watch、top、sudo 交互）是必须的，否则会报 "Pseudo-terminal will not be allocated"
- sudo 密码通过管道传入时要注意特殊字符转义（如 `!` 在 bash 中有特殊含义，需要用单引号包裹）
- NVIDIA 仓库在中国区域会自动重定向到 `developer.download.nvidia.cn`（镜像加速）
- 不要在 cron job 或非交互式脚本中硬编码密码 — 优先设置 SSH key 免密登录
- nccl-tests 编译时 `make -j8` 利用 8 核并行编译，约 30-60 秒完成
- Docker 29+ 使用 CDI (Container Device Interface)。**不要使用 `--gpus all`**（会导致 `AMD CDI spec not found` 错误），应使用 **`--device='nvidia.com/gpu=all'`**。如果 `nvidia-ctk cdi list` 能列出 GPU 但 `--gpus all` 失败，这是 Docker 29.4 的一个已知 CDI 兼容性问题。

## nccl-tests 输出列布局（解析脚本时必读）

nccl-tests 的 `*_perf` 输出是**双列表**（out-of-place + in-place），解析时必须用正确的列号：

```
行格式:
  size($1)  count($2)  type($3)  redop($4)  root($5)  time_us($6)  algbw($7)  busbw($8)  #wrong($9)  [in-place: time($10)  algbw($11)  busbw($12)  #wrong($13)]
```

**关键规则:**
- Out-of-place 数据（最常用）: `$6`=延时(us), `$7`=AlgoBW, `$8`=BusBW
- In-place 数据: `$10`=延时(us), `$11`=AlgoBW, `$12`=BusBW
- 首列 8 字节行 = 最小延迟底噪
- 尾行（最大数据量）= 峰值带宽

### 常见解析陷阱（多次踩坑记录）

1. **`set -euo pipefail` 和 grep 不兼容** — 任何 grep 无匹配时脚本会静默终止/退出。修复：所有可能无匹配的 grep 加 `|| true`：
   ```bash
   # ❌ 危险（无匹配时脚本退出）
   start_line=$(grep -n "^--- Running: test$" "$logfile" | cut -d: -f1)
   # ✅ 安全
   start_line=$(grep -n "^--- Running: test$" "$logfile" | cut -d: -f1 || true)
   ```

2. **日志 section 标记必须一致** — 写入和解析格式必须完全匹配：
   ```bash
   # 写入时
   echo "--- Running: all_reduce (8 cards) ---" >> "$log"
   # 解析时
   grep -n "^--- Running: all_reduce (8 cards) ---$" "$log" || true
   ```

3. **跳过全零行时要精确** — nccl-tests 的首行有效数据是 `0 0 float none ...`（skip 行），而 alltoall 等操作的合法小数据行也可能含 0 但非全零。用 `grep -v '^[[:space:]]*0[[:space:]]'` 更安全。

4. **`awk '/^--- End of /{exit}1'` 在节头不匹配时会输出整个文件** — 务必先用 `|| true` 保护 grep 找 start_line。

5. **备份旧的 raw_results 文件** — `summary` 模式会找最新的 `raw_results_*.txt`。多次运行后旧文件累积，容易搞混。
