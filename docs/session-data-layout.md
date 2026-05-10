# Session 数据组织与取用说明

## 1. 文档目标

这份文档面向需要直接消费 session 叶子目录数据的合作伙伴，回答下面几个问题：

- 一个 session 目录里有哪些文件和子目录
- 哪些内容是录制阶段必有，哪些是后处理后才会出现
- 每个 `frames.jsonl` 里一条记录长什么样
- 图像、音频、深度、标量分别以什么形式存储
- 推荐怎样读取，才能兼容当前 schema 和后续扩展

本文描述的是当前仓库使用的 session schema `2.2.0`。

说明：

- `2.2.0` 起，session 根目录改为 `output_root/<task_dir>/<item_dir>/`
- `2.2.0` 起，`streams/<sensor>/` 内的 `jsonl/mp4` 文件名序号与 `<item_dir>` 保持一致
- 当前 reader 不保证兼容旧的固定文件名 session；读取时应优先依赖 `manifest.json`

## 2. Session 目录总览

每次录制会在 `output_root` 下生成按 task 分组的叶子 session 目录，形如：

```text
pick_place/01/
```

典型结构如下：

```text
pick_place/
└── 01/
    ├── meta.json
    ├── manifest.json
    ├── streams/
    │   ├── camera/
    │   │   ├── pick_place_rgb_01.jsonl
    │   │   ├── pick_place_rgb_01.mp4
    │   │   └── artifacts/
    │   ├── microphone/
    │   │   ├── pick_place_audio_01.jsonl
    │   │   └── artifacts/
    │   ├── realsense/
    │   │   ├── pick_place_rgbd_01.jsonl
    │   │   ├── pick_place_rgbd_01.mp4
    │   │   └── artifacts/
    │   ├── ft/
    │   │   ├── pick_place_force_torque_01.jsonl
    │   │   └── artifacts/
    │   ├── imu/
    │   │   ├── pick_place_imu_01.jsonl
    │   │   └── artifacts/
    │   ├── motors/
    │   │   ├── pick_place_motor_state_01.jsonl
    │   │   └── artifacts/
    │   └── gelsight/
    │       ├── pick_place_visuotactile_01.jsonl
    │       ├── pick_place_visuotactile_01.mp4
    │       └── artifacts/
    ├── aligned/
    │   └── frames.jsonl
    ├── trajectory/
    │   └── frames.jsonl
    ├── annotations/
    │   ├── manifest.json
    │   ├── schema.snapshot.json
    │   ├── session.json
    │   ├── spans.jsonl
    │   └── keyframes.jsonl
    ├── quality/
    │   └── trajectory_qc.json
    ├── exports/
    │   └── ...
    └── logs/
        └── ...
```

注意：

- `meta.json`、`manifest.json`、`streams/`、`aligned/` 是录制完成后就应存在的核心内容
- `trajectory/frames.jsonl` 在未运行轨迹处理前可能不存在，或为空
- `annotations/` 只有做过标注后才会出现
- `quality/` 只有运行过轨迹质检后才会出现
- `exports/` 只有运行过导出后才会出现
- `logs/` 是脚本运行日志目录，便于排查问题，但通常不是训练或分析时的主输入

## 3. 先看哪几个文件

如果合作方第一次接触一个 session，推荐按下面顺序理解：

1. `manifest.json`
2. `aligned/frames.jsonl`
3. `streams/<sensor>/<task_slug>_<modality>_<seq>.jsonl`
4. `trajectory/frames.jsonl`
5. `annotations/`、`quality/`、`exports/`

原因是：

- `manifest.json` 告诉你这个 session 实际包含哪些传感器、每个传感器的数据路径是什么
- `aligned/frames.jsonl` 提供统一对齐后的时间轴，是大多数多模态分析的主入口
- `streams/<sensor>/*.jsonl` 是各传感器原始流
- `trajectory/frames.jsonl` 是后处理产物，不是每个 session 都有

## 4. `manifest.json` 的作用

`manifest.json` 是当前最推荐读取的总清单文件。它会告诉你：

- schema 版本
- session id
- session 根目录
- 录制时配置快照
- 当前 session 中有哪些传感器
- 每个传感器对应的 `frames.jsonl` 路径
- 每个传感器的 artifact 目录
- 是否使用媒体索引存储
- 对应媒体文件路径是什么

示意结构：

```json
{
  "schema_version": "2.2.0",
  "session_id": "20260411_153045_123456",
  "started_at": 1712811045.123,
  "session_dir": "/abs/path/to/session_xxx",
  "task_name": "pick place",
  "task_slug": "pick_place",
  "config": { "...": "..." },
  "sensors": {
    "camera": {
      "sensor_name": "camera",
      "sensor_type": "camera_sensor",
      "modality": "rgb",
      "frames_path": "streams/camera/pick_place_rgb_01.jsonl",
      "artifacts_dir": "streams/camera/artifacts",
      "storage_mode": "indexed_media",
      "media_path": "streams/camera/pick_place_rgb_01.mp4",
      "media_role": "primary_av",
      "metadata": { "...": "..." }
    }
  },
  "notes": { "...": "..." },
  "aligned_path": "aligned/frames.jsonl",
  "trajectory_path": "trajectory/frames.jsonl",
  "exports_dir": "exports"
}
```

建议：

- 不要硬编码传感器列表，先读 `manifest.json`
- 不要假设每个 session 都有 `camera`、`realsense`、`trajectory`、`annotations`
- 只要 `manifest.json` 里没有的传感器，就应该视为本 session 未启用

## 5. `meta.json` 的作用

`meta.json` 是另一份会被写出的会话元信息，内容包括：

- `schema_version`
- `session_id`
- `started_at`
- `task_name`
- `task_slug`
- `config`
- `sensors`
- `notes`

它和 `manifest.json` 有重叠，但从合作使用角度，优先读 `manifest.json` 即可。`meta.json` 更适合作为人类查看或兼容旧读取逻辑的辅助信息。

## 6. `streams/`：每个传感器的原始流

### 6.1 通用规则

每个启用的传感器在 `streams/<sensor_name>/` 下都有自己的目录，核心文件是：

- `<task_slug>_<modality>_<seq>.jsonl`
- `artifacts/`
- 可选的媒体文件，如 `<task_slug>_<modality>_<seq>.mp4`

其中：

- `*.jsonl` 负责记录逐帧索引、时间信息、payload 引用
- `artifacts/` 保存无法内嵌到 JSONL 的外部数组或图像文件
- 媒体文件保存 MP4 视频帧或主音轨

当前命名规则：

- `jsonl`：`<task_slug>_<modality>_<seq>.jsonl`
- `mp4`：`<task_slug>_<modality>_<seq>.mp4`
- `artifact`：`<task_slug>_<modality>_<seq>_<frame_id六位补零>_<payload_key>.<ext>`
- `seq` 当前固定从 `001` 开始
- `task_slug` 来自录制前必填的 `task_name`

### 6.2 单条 `frames.jsonl` 记录的公共结构

每一行都是一个独立 JSON 对象。通用结构如下：

```json
{
  "sensor_name": "camera",
  "sensor_type": "camera_sensor",
  "modality": "rgb",
  "frame_id": 123,
  "time": {
    "host_time": 1712811045.123,
    "host_time_ns": 1712811045123000000,
    "monotonic_time": 8123.456,
    "monotonic_time_ns": 8123456000000,
    "device_time": 3.233,
    "device_time_ns": 3233000000,
    "aligned_time": 1712811045.100,
    "aligned_time_ns": 1712811045100000000
  },
  "payload": {
    "...": "..."
  },
  "metadata": {
    "...": "..."
  }
}
```

字段说明：

- `sensor_name`
  - session 内部名称，通常对应 `manifest.sensors` 的 key
- `sensor_type`
  - 设备实现类型，例如 `camera_sensor`、`realsense`、`ft_sensor`
- `modality`
  - 模态类型，例如 `rgb`、`rgbd`、`audio`、`force_torque`
- `frame_id`
  - 该传感器自己的帧号，只在本传感器内递增
- `time`
  - 该帧的多种时间戳
- `payload`
  - 真正的数据内容，可能是内嵌标量，也可能是外部 artifact/媒体引用
- `metadata`
  - 该帧额外元信息，如单位、分辨率、采样率等

### 6.3 时间字段如何理解

实践中最常用的是这几个时间字段：

- `host_time_ns`
  - 主机墙上时间
- `monotonic_time_ns`
  - 主机单调时钟，适合分析采集时序
- `device_time_ns`
  - 设备侧时间，若设备支持则会填写
- `aligned_time_ns`
  - 对齐后时间轴对应时间

建议：

- 做多传感器融合，优先使用 `aligned/frames.jsonl`
- 做采集链路分析，优先看 `*_ns` 纳秒字段
- 不要自己重新对齐各传感器原始 `host_time`

## 7. Payload 的 4 种存储方式

`payload` 中的字段不一定直接保存原始数组。当前会出现 4 类常见形式。

### 7.1 直接内嵌在 JSONL

适用于标量、小向量、小字典、小列表。

例子：

```json
{
  "force": [1.0, 2.0, 3.0],
  "motor_2": {
    "position": 4.1,
    "velocity": 5.1,
    "torque": 6.1
  }
}
```

### 7.2 `mp4_frame`

表示该字段实际存储在某个 MP4 媒体文件的一帧里。

典型结构：

```json
{
  "path": "streams/camera/pick_place_rgb_01.mp4",
  "storage": "mp4_frame",
  "shape": [480, 640, 3],
  "dtype": "uint8",
  "media_frame_index": 123,
  "media_time_ns": 1712811045100000000,
  "media_duration_ns": 33333333,
  "session_offset_ns": 100000000,
  "media_kind": "mp4",
  "fps": 30,
  "channel_order": "rgb"
}
```

说明：

- `media_time_ns` / `media_duration_ns` / `session_offset_ns` 是时间真源
- `media_frame_index` 仍可用于按序号解码
- `fps` 仅表示请求/编码上下文，不能再用来推导 session 时长

### 7.3 `mp4_audio`

表示该字段实际存储在某个 MP4 的音轨里。

典型结构：

```json
{
  "path": "streams/camera/pick_place_rgb_01.mp4",
  "storage": "mp4_audio",
  "shape": [1024],
  "dtype": "int16",
  "sample_start": 245760,
  "sample_count": 1024,
  "media_time_ns": 1712811045200000000,
  "media_duration_ns": 21333333,
  "session_offset_ns": 200000000,
  "media_kind": "mp4",
  "sample_rate": 48000,
  "channels": 1
}
```

说明：

- `sample_start` / `sample_count` 仍保留给 reader 解码音频切片
- 时长判定优先依赖 `media_time_ns` / `media_duration_ns`

### 7.4 `png` / `npy`

表示该字段保存在 `streams/<sensor>/artifacts/` 下的外部文件中。

典型结构：

```json
{
  "path": "streams/realsense/artifacts/pick_place_rgbd_01_000123_depth.npy",
  "storage": "npy",
  "shape": [480, 640],
  "dtype": "uint16"
}
```

或：

```json
{
  "path": "streams/realsense/artifacts/pick_place_rgbd_01_000123_ir1.png",
  "storage": "png",
  "shape": [480, 640],
  "dtype": "uint8"
}
```

## 8. 每类传感器通常怎么保存

下面列的是当前仓库下最常见、最值得合作方依赖的字段。

### 8.1 `camera`

- 目录：`streams/camera/`
- 模态：`rgb`
- 主字段：`payload.color`
- 常见存储方式：`mp4_frame`
- 媒体文件：`streams/camera/<task_slug>_rgb_01.mp4`
- 角色：主视频容器，也是主音轨容器

说明：

- `camera` 是当前唯一的 `primary_av` 容器
- 如果 session 同时启用了 `microphone`，则麦克风音频会并入这个 MP4 的音轨

### 8.2 `microphone`

- 目录：`streams/microphone/`
- 模态：`audio`
- 主字段：`payload.audio`
- 常见存储方式：`mp4_audio`
- 实际音频位置：`streams/camera/<task_slug>_rgb_01.mp4` 的音轨

说明：

- `streams/microphone/<task_slug>_audio_01.jsonl` 中保存的是索引引用，不是独立 `wav`
- 当前默认采集参数通常是单声道 `48000 Hz`、`int16`

### 8.3 `realsense`

- 目录：`streams/realsense/`
- 模态：`rgbd`
- 常见字段：
  - `payload.color`
  - `payload.depth`
  - `payload.ir1`
  - `payload.ir2`
  - `payload.aligned_depth_to_color`
  - `payload.imu_samples`
  - `payload.pointcloud`

常见存储方式：

- `color`
  - `mp4_frame`
  - 对应媒体文件通常是 `streams/realsense/<task_slug>_rgbd_01.mp4`
- `depth`
  - `npy`
- `ir1` / `ir2`
  - 通常为 `png`
- `aligned_depth_to_color`
  - 通常为 `npy`
- `imu_samples`
  - 直接内嵌 JSON 列表
- `pointcloud.vertices`
  - 通常为 `npy`

重要提醒：

- `realsense.color` 是否存在取决于录制配置
- 当前默认配置里，`realsense.color` 常常关闭，`depth`、`ir1`、`ir2` 更常见
- 不要假设上述字段都会同时存在

### 8.4 `ft`

- 目录：`streams/ft/`
- 模态：`force_torque`
- 常见字段：
  - `payload.force`
  - `payload.torque`

说明：

- 通常直接内嵌在 JSONL 中
- `torque` 是否存在取决于 `ft.enable_torque`
- 单位一般在 `metadata.units` 中给出

### 8.5 `imu`

- 目录：`streams/imu/`
- 模态：`imu`
- 常见字段：
  - `payload.acceleration`
  - `payload.angular_velocity`
  - `payload.quaternion`

说明：

- 一般直接内嵌在 JSONL 中
- 单位通常在 `metadata.units` 中

### 8.6 `motors`

- 目录：`streams/motors/`
- 模态：`motor_state`
- 常见字段：
  - `payload.motor_1.position`
  - `payload.motor_1.velocity`
  - `payload.motor_1.torque`
  - `payload.motor_2.position`
  - `payload.motor_2.velocity`
  - `payload.motor_2.torque`

说明：

- 一般直接内嵌在 JSONL 中
- `motor_1` 或 `motor_2` 可能因为配置关闭而缺失

### 8.7 `gelsight`

- 目录：`streams/gelsight/`
- 模态：`visuotactile`
- 主字段：`payload.image`
- 常见存储方式：`mp4_frame`
- 媒体文件：`streams/gelsight/<task_slug>_visuotactile_01.mp4`

## 9. `aligned/frames.jsonl`：统一时间轴主入口

`aligned/frames.jsonl` 是多模态分析最重要的文件。每一行代表一个对齐后的时间点，而不是某个单独传感器的原始帧。

典型结构：

```json
{
  "sequence_id": 0,
  "aligned_time": 1712811045.100,
  "aligned_time_ns": 1712811045100000000,
  "missing_sensors": [],
  "age_by_sensor": {
    "camera": 0.003,
    "realsense": 0.010
  },
  "frames": {
    "camera": {
      "frame_id": 101,
      "host_time": 1712811045.099,
      "host_time_ns": 1712811045099000000,
      "monotonic_time": 8123.2,
      "monotonic_time_ns": 8123200000000,
      "device_time": 3.100,
      "device_time_ns": 3100000000
    }
  },
  "metadata": {
    }
  }
}
```

字段含义：

- `sequence_id`
  - 对齐后的统一步号，整个 session 内单调递增
- `aligned_time_ns`
  - 这一对齐步对应的统一时间
- `missing_sensors`
  - 这一对齐步上缺失的传感器名列表
- `age_by_sensor`
  - 该对齐步使用的各传感器帧相对对齐时间的时间偏差
- `frames`
  - 每个传感器在该对齐步上被选中的原始帧索引信息
- `metadata`

推荐用途：

- 做多模态同步分析
- 给标注、训练样本构建、模型输入切片提供统一时间锚点
- 查询“第 N 个对齐时刻对应各传感器用的是哪一帧”

## 10. `trajectory/frames.jsonl`：离线轨迹结果

这个文件来自录制后的离线轨迹处理，不是录制原始真源。

典型结构：

```json
{
  "source": "orbslam3",
  "frame_id": 42,
  "time": {
    "host_time": 1712811045.300,
    "host_time_ns": 1712811045300000000,
    "monotonic_time": 0.0,
    "monotonic_time_ns": 0,
    "device_time": 3.300,
    "device_time_ns": 3300000000,
    "aligned_time": 1712811045.300,
    "aligned_time_ns": 1712811045300000000
  },
  "position": [1.0, 2.0, 3.0],
  "quaternion": [1.0, 0.0, 0.0, 0.0],
  "tracking_state": "OK",
  "metadata": {
    "...": "..."
  }
}
```

字段含义：

- `source`
  - 轨迹来源，当前通常为 `orbslam3`
- `position`
  - 三维位置
- `quaternion`
  - 四元数姿态
- `tracking_state`
  - 跟踪状态

注意：

- 没有跑 `scripts/sdk_process_trajectory.py` 的 session，不应假设这个文件可用
- 即使文件存在，也不应假设每一帧都可用于训练或控制，通常还要结合 `tracking_state`
- 新版轨迹记录同时保留三类时间语义：
  - `time.device_time_ns`：ORB-SLAM3 / RealSense 源 device-time
  - `time.host_time_ns`：回绑后的真实 session host-time
  - `time.aligned_time_ns`：回绑后的统一 aligned 时间锚点
- 下游做多模态对齐时，应优先信任 `aligned_time_ns`，而不是假设 `host_time_ns == device_time_ns`

## 11. `annotations/`：人工标注真源

如果团队做过人工标注，标注会直接回写进 session 的 `annotations/` 目录。

典型文件：

- `annotations/manifest.json`
  - 标注版本、session id、标注人、创建和更新时间
- `annotations/schema.snapshot.json`
  - 本 session 使用的标注 schema 快照
- `annotations/session.json`
  - session 级别标注
- `annotations/spans.jsonl`
  - 时间段标注，主锚点是 `aligned.sequence_id`
- `annotations/keyframes.jsonl`
  - 关键帧标注，主锚点也是 `aligned.sequence_id`

如果合作方只关心原始采集数据，这一目录可以忽略；如果要做训练集筛选或阶段切分，通常需要同时读取它。

## 12. `quality/` 与 `exports/`

### 12.1 `quality/`

该目录存放规则型质检结果，常见文件是：

- `quality/trajectory_qc.json`

适合做：

- 过滤失败 session
- 查找轨迹覆盖率、动作可用率等质量指标

### 12.2 `exports/`

该目录存放导出结果，不是 session 原始真源。

当前仓库支持导出到：

- `csv`
- `hdf5`
- `rlds`
- `lerobot`
- `rosbag2`

建议：

- 想保留全部真源和最大灵活性时，直接消费 session
- 只在明确需要外部格式时，才读取 `exports/`

## 13. 最推荐的读取方式：`SessionReader`

合作方如果使用 Python，最推荐通过仓库提供的 `SessionReader` 读取，而不是自己手写 MP4 / PNG / NPY 解码逻辑。

原因：

- 它会自动读取 `manifest.json`
- 它能统一处理 `mp4_frame`、`mp4_audio`、`png`、`npy`
- `load_payload=False` 时返回引用；`load_payload=True` 时返回真正的数据数组
- 可以兼容当前 session v2 的媒体化存储

### 13.1 读取 session 摘要

```python
from sdk.storage.session_reader import SessionReader

reader = SessionReader("/abs/path/to/session_xxx")
print(reader.summary())
print(reader.sensor_names())
```

### 13.2 遍历某个传感器原始流

```python
from sdk.storage.session_reader import SessionReader

reader = SessionReader("/abs/path/to/session_xxx")

for frame in reader.iter_sensor_frames("camera", load_payload=False):
    print(frame.frame_id, frame.time.host_time_ns, frame.payload["color"])
```

如果要拿到真正的像素数组：

```python
from sdk.storage.session_reader import SessionReader

reader = SessionReader("/abs/path/to/session_xxx")

for frame in reader.iter_sensor_frames("camera", load_payload=True):
    image = frame.payload["color"]
    print(frame.frame_id, image.shape, image.dtype)
    break
```

### 13.3 遍历统一对齐时间轴

```python
from sdk.storage.session_reader import SessionReader

reader = SessionReader("/abs/path/to/session_xxx")

for record in reader.iter_aligned_records():
    seq = record["sequence_id"]
    missing = record.get("missing_sensors", [])
    frames = record.get("frames", {})
    print(seq, missing, frames.keys())
```

### 13.4 读取轨迹

```python
from sdk.storage.session_reader import SessionReader

reader = SessionReader("/abs/path/to/session_xxx")

for traj in reader.iter_trajectory_frames():
    print(traj.frame_id, traj.position, traj.quaternion, traj.tracking_state)
```

### 13.5 已知 artifact 引用时手动加载

```python
from sdk.storage.session_reader import SessionReader

reader = SessionReader("/abs/path/to/session_xxx")
frame = next(reader.iter_sensor_frames("realsense", load_payload=False))

depth_ref = frame.payload["depth"]
depth = reader.load_artifact(depth_ref)
print(depth.shape, depth.dtype)
```

## 14. 如果不使用 SDK，自行解析时要注意什么

如果合作方不想依赖本仓库代码，也可以自行解析，但需要注意以下约束。

### 14.1 先读 `manifest.json`

不要假设：

- 所有传感器都启用
- 所有 `streams/<sensor>/` 都存在
- 所有 stream 文件都叫 `frames.jsonl`
- 所有图像都是 PNG
- 麦克风有独立音频文件

尤其在 `2.2.0` 之后：

- 不要自己拼接 `streams/<sensor>/<task_slug>_<modality>_<seq>.jsonl`
- 应始终以 `manifest.json` 里的 `frames_path` / `media_path` 为准

### 14.2 识别 `payload` 是“值”还是“引用”

当一个字典同时包含下面几个键时，应把它视为外部数据引用：

- `path`
- `storage`
- `shape`
- `dtype`

此时不要把它当成普通业务字典。

### 14.3 识别不同 `storage`

- `mp4_frame`
  - 去对应 MP4 中按 `media_frame_index` 取帧
- `mp4_audio`
  - 去对应 MP4 音轨中按 `sample_start` / `sample_count` 取样本
- `png`
  - 读取 PNG 文件
- `npy`
  - 读取 NumPy 数组

### 14.4 不要假设字段恒定存在

常见可选字段：

- `ft.payload.torque`
- `motors.payload.motor_1`
- `motors.payload.motor_2`
- `realsense.payload.color`
- `realsense.payload.ir1`
- `realsense.payload.ir2`
- `realsense.payload.aligned_depth_to_color`
- `realsense.payload.imu_samples`
- `trajectory/frames.jsonl`
- `annotations/`

## 15. 合作方最常见的 3 种取数姿势

### 15.1 做多模态学习样本

推荐入口：

- 先遍历 `aligned/frames.jsonl`
- 再根据 `frames[sensor_name].frame_id` 去索引原始流
- 或直接用 `SessionReader` 把原始流 payload 解出来

适合：

- 多模态同步学习
- 样本切片
- 标注对齐

### 15.2 只看某个传感器原始数据

推荐入口：

- 直接遍历 `streams/<sensor>/*.jsonl`

适合：

- 只看 FT、IMU、motors 等单模态信号
- 排查个别设备数据质量

### 15.3 用轨迹和标注筛 session

推荐入口：

- `trajectory/frames.jsonl`
- `annotations/`
- `quality/trajectory_qc.json`

适合：

- 训练集筛选
- 有效轨迹过滤
- 按阶段、事件或质量标签切分数据

## 16. 一句话总结

对合作方来说，最稳妥的原则是：

- 用 `manifest.json` 了解 session 结构
- 用 `aligned/frames.jsonl` 做多模态主时间轴
- 用 `streams/<sensor>/*.jsonl` 看原始流
- 用 `trajectory.time.aligned_time_ns` 消费离线轨迹，并把 `device_time_ns` 只当作回绑审计字段
- 用 `SessionReader` 解码 `mp4_frame` / `mp4_audio` / `png` / `npy`
- 把 `trajectory/`、`annotations/`、`quality/`、`exports/` 都视为“附加层”，不要和原始采集真源混淆
