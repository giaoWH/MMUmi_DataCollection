# 末端执行器 TCP Latest 模式改动总结

## 分支

- 当前分支: `feature/end-effector-tcp-latest`
- 注意: 开始前工作区已有 `configs/record.yaml` 和 `configs/record.yaml.bak` 未提交改动，本次实现未编辑这两个文件。

## 实现内容

- 新增简化版末端执行器模式:
  - `device_role: collector | end_effector`
  - 默认仍为 `collector`，原录制路径保持不变。
  - `end_effector` 模式优先于 `interactive` 分流。
- 新增 TCP latest 上行配置:
  - `network_uplink.enabled`
  - `network_uplink.host`
  - `network_uplink.port`
  - `network_uplink.mode`
  - `network_uplink.reconnect_interval_sec`
  - `network_uplink.send_timeout_sec`
  - `network_uplink.poll_interval_sec`
- 末端执行器运行行为:
  - 树莓派作为 TCP client 主动连接上位机。
  - 跳过 motors 注册。
  - 启动其余 enabled sensors 并等待 ready。
  - 不创建 `SessionWriter`，不创建 session 目录，不写盘，不做 aligned frame。
  - 主循环 drain 每路 sensor 队列，只把最后一帧写入该 sensor 的 latest slot。
- TCP 协议:
  - 每条消息为 `8-byte big-endian payload_length + pickle.dumps(SensorFrame, protocol=pickle.HIGHEST_PROTOCOL)`。
  - 不做 ACK、不做应用层重传、不做显式 chunk。
  - 写慢或断线时不堆积历史；重连后只发送每路当前 latest frame。
- 新增上位机 receiver:
  - `scripts/sdk_network_receiver.py`
  - 监听 TCP 端口，读取长度前缀，还原 `SensorFrame`。
  - 按 `sensor_name` 更新内存 latest frame。
  - v1 只打印统计，不写 SDK session。

## 主要改动文件

- `scripts/sdk_record.py`
  - 增加 `device_role` / `network_uplink` 配置解析。
  - `build_registry(..., skip_motors=True)` 支持末端模式跳过 motors。
  - 增加 `_run_end_effector()`，实现 TCP latest 采集发送主循环。
- `sdk/transport/tcp_latest.py`
  - 增加 `NetworkUplinkConfig`、`LatestFrameStore`、`TcpLatestFrameSender`。
  - 增加 length-prefixed pickle 编码和读取函数。
- `scripts/sdk_network_receiver.py`
  - 增加 PC 端 TCP latest receiver。
- 测试:
  - `tests/test_tcp_latest_transport.py`
  - `tests/test_end_effector_tcp_latest.py`
  - 更新配置解析和 registry 测试。

## 配置示例

```yaml
device_role: end_effector
sensor_source: real
interactive: true  # end_effector 模式会优先分流，因此不会进入交互录制
duration_sec: 0.0
startup_discard_sec: 1.0

enable_ft: true
enable_imu: true
enable_realsense: true
enable_motors: true  # end_effector 模式会跳过 motors
enable_microphone: false
enable_camera: false
enable_gelsight: false

network_uplink:
  enabled: true
  host: 192.168.10.2
  port: 8765
  mode: per_sensor_latest_raw_frame
  reconnect_interval_sec: 1.0
  send_timeout_sec: 2.0
  poll_interval_sec: 0.005
```

## 运行命令

上位机:

```bash
conda activate umi_sdk
python scripts/sdk_network_receiver.py --host 0.0.0.0 --port 8765
```

树莓派:

```bash
conda activate umi_sdk
python scripts/sdk_record.py --config configs/record.yaml
```

也可以临时覆盖上位机地址:

```bash
python scripts/sdk_record.py \
  --config configs/record.yaml \
  --device-role end_effector \
  --network-uplink-host 192.168.10.2 \
  --network-uplink-port 8765
```

## 测试结果

已执行:

```bash
conda run -n umi_sdk python -m pytest -q \
  tests/test_tcp_latest_transport.py \
  tests/test_record_config_loading.py \
  tests/test_sensor_registry_extensions.py \
  tests/test_end_effector_tcp_latest.py
```

结果:

```text
23 passed in 1.01s
```

基础环境中 `pytest` 不可用:

```text
pytest: command not found
python -m pytest: No module named pytest
```

因此使用仓库环境 `umi_sdk` 完成验证。

## 已知限制

- `pickle` 只能用于可信网线直连或可信局域网，不能暴露给不可信网络。
- v1 receiver 不落盘，只维护内存 latest frame 和打印诊断。
- TCP 发送 `sendall()` 在内核缓冲写慢时可能阻塞到 `send_timeout_sec`；采集主循环仍独立运行并继续覆盖 latest slot。
- TCP 是可靠字节流。如果连接中途断开，正在发送的那条消息可能被 receiver 丢弃；重连后不会补发历史。
- 底层 sensor adapter 仍可能有内部短队列；末端模式在 SDK 主循环层只保留每路最新帧。
