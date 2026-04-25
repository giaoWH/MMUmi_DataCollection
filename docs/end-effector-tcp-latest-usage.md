# 末端执行器 TCP Latest 模式使用说明

## 能不能一根网线直连就读取数据流？

可以，但不是“插上线就自动可读”。需要满足这些条件：

- 树莓派和 Windows PC 用网线连接，且两个网口处在同一 IP 网段。
- Windows PC 上先启动 `scripts/sdk_network_receiver.py` 监听端口。
- 树莓派端 `configs/record.yaml` 进入 `end_effector` 模式，并把 `network_uplink.host` 配成 Windows PC 的网口 IP。
- Windows 防火墙允许 Python 或指定 TCP 端口入站。
- 两端运行的是同一份 SDK 代码，或至少能反序列化当前 `SensorFrame` 数据结构。

这个模式是 TCP client/server：

- Windows PC 是 server，负责监听。
- 树莓派是 client，负责主动连接 PC 并发送每路最新 `SensorFrame`。

## 1. 配置网线直连 IP

推荐使用静态 IP，避免 DHCP/自动地址带来的不确定性。

示例网络：

```text
Windows PC 以太网 IP: 192.168.10.2
树莓派以太网 IP:    192.168.10.3
子网掩码:            255.255.255.0
TCP 端口:            8765
```

Windows 设置方式：

- 打开“网络连接”。
- 找到有线以太网适配器。
- IPv4 手动设置：
  - IP address: `192.168.10.2`
  - Subnet mask: `255.255.255.0`
  - Gateway: 留空即可。

树莓派设置方式根据系统不同略有差异。目标是让有线网口得到：

```text
IP: 192.168.10.3
Mask: 255.255.255.0
```

配置后，在树莓派上测试：

```bash
ping 192.168.10.2
```

能 ping 通再继续。

## 2. Windows PC 启动 Receiver

在 Windows PC 上进入 SDK 仓库目录，启动 receiver：

```bash
conda activate umi_sdk
python scripts/sdk_network_receiver.py --host 0.0.0.0 --port 8765
```

如果 Windows 防火墙弹窗，允许当前 Python 程序访问专用网络。

也可以手动放行端口 `8765/TCP`。

receiver 启动后会等待树莓派连接，并周期性打印收到的最新 frame 摘要。v1 不落盘，只在内存中按 `sensor_name` 保存 latest frame。

## 3. 树莓派配置 SDK

在树莓派端修改 `configs/record.yaml`：

```yaml
device_role: end_effector

network_uplink:
  enabled: true
  host: 192.168.10.2
  port: 8765
  mode: per_sensor_latest_raw_frame
  reconnect_interval_sec: 1.0
  send_timeout_sec: 2.0
  poll_interval_sec: 0.005
```

其他传感器开关继续使用原来的配置，例如：

```yaml
enable_ft: true
enable_imu: true
enable_realsense: true
enable_motors: true
enable_microphone: true
enable_camera: true
enable_gelsight: true
```

注意：`end_effector` 模式会强制跳过 motors，即使 `enable_motors: true` 也不会注册 motor adapter。

如果暂时不想改配置文件，也可以用命令行覆盖：

```bash
python scripts/sdk_record.py \
  --config configs/record.yaml \
  --device-role end_effector \
  --network-uplink-host 192.168.10.2 \
  --network-uplink-port 8765
```

但 `network_uplink.enabled` 仍需要在配置文件中为 `true`。

## 4. 树莓派启动发送

在树莓派端运行：

```bash
conda activate umi_sdk
python scripts/sdk_record.py --config configs/record.yaml
```

启动后行为：

- 不创建 session 目录。
- 不写本地数据。
- 不做 aligned frame。
- 每路传感器只保留当前最新帧。
- TCP 断开时继续采集并覆盖 latest slot。
- PC receiver 恢复后，只发送每路当前最新帧，不补发历史。

## 5. 如何确认数据到了 Windows PC

receiver 输出会类似：

```json
{
  "received_count": 42,
  "sensor_count": 3,
  "latest": {
    "ft": {
      "sensor_name": "ft",
      "modality": "force_torque",
      "frame_id": 120,
      "payload_keys": ["force", "torque"]
    }
  }
}
```

看到 `received_count` 增长，说明 Windows PC 正在读取树莓派发送的数据流。

## 6. 常见问题

### PC 没有收到连接

检查：

- 树莓派能否 `ping 192.168.10.2`。
- Windows receiver 是否已先启动。
- `network_uplink.host` 是否是 Windows 有线网卡 IP。
- Windows 防火墙是否放行 Python 或 `8765/TCP`。
- 两端端口是否一致。

### 树莓派提示连接失败

通常是 PC receiver 未启动、IP 写错、防火墙拦截，或两端不在同一网段。

### Windows 能不能直接用别的程序读取？

可以，但要实现当前协议：

```text
8-byte big-endian payload_length + pickle payload
```

payload 是 `pickle.dumps(SensorFrame)`。最简单方式是直接复用 SDK 的：

```python
from sdk.transport import recv_frame_message
```

### 这个模式安全吗？

只适合可信网线直连或可信局域网。因为 v1 使用 `pickle`，不要把 receiver 暴露到不可信网络。

### 是否会保存历史数据？

不会。这个模式的目标是实时性：

- SDK 应用层每路只保留 latest slot。
- 发送慢时旧帧被覆盖。
- TCP 断线后不补发历史。
- receiver v1 只维护内存 latest，不写 session。

## 7. 推荐启动顺序

1. 网线连接树莓派和 Windows PC。
2. 设置两端静态 IP。
3. 从树莓派 ping Windows PC。
4. Windows PC 启动 `sdk_network_receiver.py`。
5. 树莓派配置 `device_role: end_effector` 和 `network_uplink.enabled: true`。
6. 树莓派启动 `sdk_record.py`。
7. 在 Windows PC receiver 输出中确认 `received_count` 持续增长。
