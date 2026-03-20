# 复现 Rajomon NSDI'25 Figure 7 指南

## 概述

本指南说明如何复现 Rajomon 论文 (NSDI'25) 中 Figure 7 的实验结果。  
Figure 7 展示了在并发 Search Hotel + Reserve Hotel 请求下（总负载 4k–16k RPS），
五种过载控制方案的 **P95 尾延迟** 和 **有效吞吐量 (Goodput)** 对比：

- **None** — 无过载控制（基线）
- **Dagor** — 基于准入控制的方案（B/U 优先级）
- **Breakwater / Breakwaterd** — 基于信用的 AIMD 方案
- **TopFull** — Token Bucket + SLO 方案
- **Rajomon** — 基于价格的双向过载控制方案

---

## 1. 已完成的代码集成

以下文件已修改，将四种网关集成到 hotelReservation 系统：

### 核心文件
| 文件 | 说明 |
|------|------|
| `overloadcontrol/overloadcontrol.go` | 统一的过载控制抽象层 |
| `go.mod` | 添加四个网关依赖 + replace 指令 |
| `dialer/dialer.go` | 添加 `WithClientInterceptor()` |

### 服务端（服务器拦截器）
所有服务 (`frontend`, `search`, `geo`, `rate`, `profile`, `reservation`, `user`) 均已修改：
- `services/*/server.go` — 添加 `OCType` 字段，链式拦截器
- `cmd/*/main.go` — 添加 `overloadcontrol.GetOCType()` 初始化

### 客户端（客户端拦截器）
- `frontend/server.go` — 对下游服务的 gRPC 连接添加客户端拦截器
- `search/server.go` — 对 geo/rate 服务的 gRPC 连接添加客户端拦截器

### 配置
- `docker-compose.yml` — 所有服务添加 `OC_TYPE` 环境变量

---

## 2. 环境要求

### 论文原始环境（推荐）
论文使用以下硬件：
- **7 台 CloudLab c220g2 服务器**
- 每台：20 核 Intel Xeon E5-2660 v3, 160GB RAM, 10Gbps NIC
- 操作系统：Ubuntu 20.04
- 容器编排：Kubernetes

### 最低要求
- 多核 CPU（建议 ≥ 16 核）
- ≥ 32GB RAM
- Docker + Docker Compose
- Go 1.21+
- Python 3 + matplotlib/numpy/pandas（用于画图）

> **注意**：在单机上运行无法完全复现论文结果。单机环境下，瓶颈可能是 CPU/内存而非网络，
> 过载控制的效果会与论文中的多机集群环境有差异。建议使用 CloudLab 或类似集群进行复现。

---

## 3. 本地单机复现步骤

### 3.1 构建

```bash
cd DeathStarBench-master/hotelReservation

# 确认编译通过
go build ./...

# 构建 Docker 镜像
docker-compose build
```

### 3.2 运行实验

使用环境变量 `OC_TYPE` 选择过载控制方案：

```bash
# 可选值: none, rajomon, dagor, breakwater, topfull
export OC_TYPE=none

# 启动所有服务
docker-compose up -d

# 等待服务就绪（约 30 秒）
sleep 30

# 运行负载测试
cd benchmark
go run loadgen.go \
  -addr http://localhost:5000 \
  -rps 4000 \
  -duration 15 \
  -warmup 5 \
  -workers 1000 \
  -search-ratio 0.5 \
  -output ../results/none_4000.csv

# 停止服务
docker-compose down
```

### 3.3 使用自动化脚本

```bash
# 使用 run_figure7.sh 自动化实验
chmod +x run_figure7.sh
./run_figure7.sh
```

脚本会自动遍历所有 OC 类型和负载级别（4k, 6k, 8k, 10k, 12k, 14k, 16k RPS），
每种配置运行 5 次取平均值。

### 3.4 生成图表

```bash
pip install matplotlib numpy pandas

python plot_figure7.py
```

生成的 `figure7.png` 即为复现的 Figure 7。

---

## 4. CloudLab 集群复现步骤（推荐）

### 4.1 申请 CloudLab 资源

1. 注册 [CloudLab](https://www.cloudlab.us/) 账号
2. 创建 profile，申请 7 台 c220g2 节点
3. 节点分配：
   - 1 台：Kubernetes Master + 负载生成器
   - 5 台：Kubernetes Worker（运行微服务）
   - 1 台：监控 + 数据收集

### 4.2 部署 Kubernetes

```bash
# 在 Master 节点
sudo apt update && sudo apt install -y docker.io kubeadm kubelet kubectl
sudo kubeadm init --pod-network-cidr=10.244.0.0/16
kubectl apply -f https://raw.githubusercontent.com/coreos/flannel/master/Documentation/kube-flannel.yml

# 在每个 Worker 节点
sudo kubeadm join <master-ip>:6443 --token <token> --discovery-token-ca-cert-hash <hash>
```

### 4.3 部署 Hotel Reservation

```bash
# 使用 Helm chart 部署
cd DeathStarBench-master/hotelReservation/helm-chart/hotelreservation

# 修改 values.yaml 设置 OC_TYPE
# 或者在部署时通过 --set 传入
helm install hotel . --set global.ocType=rajomon
```

### 4.4 运行负载测试

论文使用 `ghz` gRPC 负载生成器：

```bash
# 安装 ghz
go install github.com/bojand/ghz/cmd/ghz@latest

# 对每种 OC 类型和负载级别运行测试
for oc_type in none dagor breakwater topfull rajomon; do
  # 重新部署服务，设置 OC_TYPE=$oc_type
  kubectl set env deployment --all OC_TYPE=$oc_type
  kubectl rollout status deployment --timeout=120s
  
  for rps in 4000 6000 8000 10000 12000 14000 16000; do
    ghz --insecure \
      --proto ./proto/frontend.proto \
      --call frontend.FrontendService.SearchHotel \
      --rps $rps \
      --duration 15s \
      --concurrency 1000 \
      --connections 100 \
      localhost:5000 \
      -O csv -o results/${oc_type}_${rps}.csv
  done
done
```

### 4.5 实验参数（论文设置）

| 参数 | 值 |
|------|-----|
| 并发 gRPC workers | 1000 |
| 负载分布 | Poisson 过程 |
| 预热时间 | 5 秒 |
| 过载持续时间 | 10 秒 |
| 负载范围 | 4k – 16k RPS |
| Search:Reserve 比例 | ~1:1（并发请求） |
| SLO 阈值 | 200ms |
| 重复次数 | 5 次取中位数 |

---

## 5. OC_TYPE 环境变量说明

| 值 | 方案 | 说明 |
|----|------|------|
| `none` | 无 | 不启用任何过载控制（基线） |
| `rajomon` | Rajomon | 价格机制 + 调用图感知 |
| `dagor` | Dagor | 准入控制 + B/U 业务优先级 |
| `breakwater` | Breakwater | AIMD 信用机制 |
| `topfull` | TopFull | Token Bucket + SLO |

---

## 6. 预期结果

### P95 尾延迟
- **低负载 (4k RPS)**：所有方案延迟接近，约 10-50ms
- **中等负载 (8-10k RPS)**：None 方案延迟急剧上升，超过 SLO (200ms)
- **高负载 (12-16k RPS)**：Rajomon 保持最低延迟；Dagor/Breakwater 次之；TopFull 和 None 延迟最高

### Goodput
- **低负载**：所有方案 goodput ≈ RPS
- **高负载**：Rajomon 维持最高 goodput；None 方案 goodput 大幅下降

---

## 7. 故障排除

### 编译错误
```bash
# 如果出现 vendor 不一致
go mod vendor
go build ./...
```

### Docker 内存不足
```bash
# 增加 Docker 内存限制（Docker Desktop 设置中）
# 建议至少 8GB
```

### 服务启动失败
```bash
# 检查日志
docker-compose logs <service-name>

# 检查 Consul 注册
curl http://localhost:8500/v1/agent/services
```

### OC_TYPE 未生效
```bash
# 确认环境变量已传递
docker-compose exec frontend env | grep OC_TYPE
```
