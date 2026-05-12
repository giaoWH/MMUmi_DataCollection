# 六维力传感器重力补偿工具

## 概述

本工具利用 IMU 加速度计数据对六维力/力矩（FT）传感器进行重力补偿，从原始读数中消除夹爪自身重力的影响，提取出真实的交互力与交互力矩。

## 使用方法

### 命令行

```bash
python gravity-comp.py --session <session目录路径> [选项]
python gravity-comp.py --all <testdata目录路径> [选项]
```

#### 处理单个 session

```bash
python gravity-comp.py --session gravity-comp-testdata/01
```

程序会在 `gravity-comp-testdata/01/streams/ft/` 下查找 `*_s.jsonl` 作为 FT 数据源，在 `streams/imu/` 下查找 `*_s.jsonl` 作为 IMU 数据源，输出 `pure-force.jsonl` 到 `streams/ft/` 目录。

#### 批量处理所有 session

```bash
python gravity-comp.py --all gravity-comp-testdata
```

程序遍历 `gravity-comp-testdata/` 下的所有子目录，对每个包含 `streams/ft/` 的 session 目录依次执行补偿。

#### 命令行参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `--session` | str | — | 单个 session 目录路径 |
| `--all` | str | — | testdata 目录路径，批量处理其下所有 session |
| `--mass` | float | 0.496 | 夹爪质量（kg） |
| `--bias-frames` | int | 100 | 用于计算 SessionBias 的前 N 帧数 |
| `--gravity-sign` | float | 1.0 | 重力补偿符号（+1.0 或 -1.0） |
| `--interp` | str | linear | IMU 插值方法（linear 或 nearest） |

`--session` 和 `--all` 二选一，不提供时打印帮助信息。

### Python API

```python
from gravity_comp import compensate_session, batch_process

# 处理单个 session
output_path = compensate_session(
    session_dir="gravity-comp-testdata/01",
    mass=0.496,
    num_bias_frames=100,
    gravity_sign=1.0,
    interpolation_method="linear",
)

# 批量处理
output_paths = batch_process(
    testdata_dir="gravity-comp-testdata",
    mass=0.496,
    num_bias_frames=100,
    gravity_sign=1.0,
    interpolation_method="linear",
)
```

另有 `compensate_force_sensor()` 函数，接受 CSV 文件路径作为输入，适用于非 session 目录结构的场景。详细参数见源码。

### 数据格式要求

#### 输入

程序读取 session 目录下的简化版 JSONL 文件（`*_s.jsonl`）：

**FT 数据** — `streams/ft/*_s.jsonl`，每行一条记录：

```json
{"host_time": "2026-05-12-19-35-42-580", "payload": {"force": [fx, fy, fz], "torque": [tx, ty, tz]}}
```

**IMU 数据** — `streams/imu/*_s.jsonl`，每行一条记录：

```json
{"host_time": "2026-05-12-19-35-42-570", "payload": {"acceleration": [ax, ay, az], "angular_velocity": [...], "quaternion": [...]}}
```

`host_time` 格式为 `年-月-日-时-分-秒-毫秒`，程序内部将其解析为 Unix 时间戳（秒）。

#### 输出

`streams/ft/pure-force.jsonl`，格式与 FT 输入一致，但 `force` 和 `torque` 值为补偿后的交互力/力矩：

```json
{"host_time": "2026-05-12-19-35-42-580", "payload": {"force": [fx, fy, fz], "torque": [tx, ty, tz]}}
```

### 前置条件

- 采集开始时夹爪处于空载、近似静止状态（至少持续 `num_bias_frames` 帧）
- IMU 与 FT 传感器的时间戳存在重叠区间

---

## 算法原理

### 问题背景

六维力传感器安装在机械臂末端与夹爪之间，测量夹爪与环境之间的力/力矩。但传感器读数中始终包含夹爪自身重力引起的分量。当机械臂运动时，重力在传感器坐标系下的投影随姿态变化，因此不能简单地减去一个固定偏置。

IMU 加速度计固定在夹爪上，能够感知夹爪在自身坐标系下的加速度。静止时，IMU 输出比力（specific force），其值等于重力加速度在 IMU 坐标系下的投影。运动时，IMU 输出中还包含运动加速度。利用这一信息，可以逐帧估计重力在传感器坐标系下的分量并予以消除。

### 核心公式

对每一帧：

```
gravity       = gravity_sign × a_imu × m
SessionBias   = mean(FT_raw[:N] - gravity[:N])
interaction   = FT_raw - SessionBias - gravity
```

其中：
- `a_imu` — IMU 三轴加速度（m/s²）
- `m` — 夹爪质量（kg）
- `gravity_sign` — 符号系数，取 `+1.0` 或 `-1.0`，取决于 IMU 与 FT 传感器的坐标系关系
- `N` — 偏置帧数，取采集起始段空载静止的帧数

### 公式推导

#### 1. FT 传感器的读数构成

FT 传感器的原始读数由三部分叠加：

```
FT_raw = F_gravity + F_bias + F_interaction
```

- `F_gravity` — 夹爪重力在传感器坐标系下的投影，随姿态变化
- `F_bias` — 传感器本身的零偏和温度漂移等系统性偏差，在单次采集（session）内近似恒定
- `F_interaction` — 夹爪与环境之间的真实交互力，这是我们想要提取的量

#### 2. 用 IMU 估计重力分量

夹爪质量为 `m`，重力加速度为 `g`。当夹爪静止时，IMU 加速度计输出比力：

```
a_imu = g_imu_frame
```

即 IMU 输出等于重力加速度在 IMU 坐标系下的投影。此时夹爪重力在 FT 传感器坐标系下产生的力为：

```
F_gravity = gravity_sign × a_imu × m
```

`gravity_sign` 的取值取决于 IMU 与 FT 传感器的安装关系。如果两者坐标系一致（重力在两者上的投影方向相同），则 `gravity_sign = +1.0`；如果方向相反，则 `gravity_sign = -1.0`。

当夹爪运动时，IMU 输出变为 `a_imu = g_imu_frame + a_motion`，此时：

```
gravity_sign × a_imu × m = F_gravity + gravity_sign × a_motion × m
```

多出了一项运动加速度对应的惯性力。但在实际应用中，夹爪运动加速度通常较小（远小于重力加速度 9.8 m/s²），该项作为近似误差可以接受。如果需要更高精度，应结合 IMU 的陀螺仪和运动学模型进行更严格的补偿。

#### 3. 计算 SessionBias

在采集起始段（前 N 帧），夹爪空载且近似静止，此时 `F_interaction ≈ 0`，因此：

```
FT_raw[:N] = F_gravity[:N] + F_bias + 0
```

于是：

```
FT_raw[:N] - gravity[:N] = F_bias
```

取均值得到 SessionBias：

```
SessionBias = mean(FT_raw[:N] - gravity[:N]) ≈ F_bias
```

SessionBias 吸收了传感器零偏和温度漂移等系统性偏差。

#### 4. 提取交互力

将 SessionBias 和重力分量从原始读数中减去：

```
interaction = FT_raw - SessionBias - gravity
            = (F_gravity + F_bias + F_interaction) - F_bias - F_gravity
            = F_interaction
```

这正是我们需要的交互力。

#### 5. 力矩的处理

力矩的补偿与力略有不同。夹爪重力产生的力矩为：

```
T_gravity = r × F_gravity
```

其中 `r` 是夹爪质心到传感器中心的力臂。由于 `r` 是常量，`T_gravity` 随 `F_gravity` 变化，理论上也应该用 IMU 数据补偿。但实际中力臂 `r` 很小且难以精确标定，因此当前算法采用简化策略：

```
SessionBias_torque = mean(FT_torque[:N])
interaction_torque = FT_torque - SessionBias_torque
```

即只减去静止段的平均力矩偏置。这在空载静止时准确（交互力矩为零），在运动时会有残余误差，量级取决于力臂大小和姿态变化幅度。

### 时间同步

IMU 和 FT 传感器以不同频率独立采样，时间戳不对齐。程序的处理步骤：

1. 将两者的时间戳统一到秒（Unix epoch）
2. 计算两者的时间重叠区间，只保留重叠范围内的数据
3. 对 IMU 三轴加速度分别做线性插值（`scipy.interpolate.interp1d`），插值到 FT 的时间点上

这样每一帧 FT 数据都有一个时间对齐的 IMU 加速度值可用。

### gravity_sign 的确定

`gravity_sign` 的正确取值取决于 IMU 和 FT 传感器的坐标系安装关系。判断方法：

1. 保持夹爪空载静止
2. 分别记录 FT 原始读数和 IMU 加速度
3. 计算 `FT_raw + gravity_sign × a_imu × m`
   - 如果结果接近零 → `gravity_sign` 正确
   - 如果结果约为 `2 × FT_raw` → `gravity_sign` 符号反了

当前系统中，IMU 输出比力方向与 FT 传感器重力分量方向一致，因此 `gravity_sign = +1.0`。

### 程序执行流程

```
1. 定位文件
   ├── 查找 streams/ft/*_s.jsonl（FT 数据）
   └── 查找 streams/imu/*_s.jsonl（IMU 数据）

2. 读取与解析
   ├── 解析 host_time 字符串为 Unix 时间戳
   ├── FT → DataFrame(timestamp, fx, fy, fz, tx, ty, tz)
   └── IMU → DataFrame(timestamp, ax, ay, az)

3. 时间对齐
   ├── 计算时间重叠区间
   ├── 裁剪到重叠范围
   └── IMU 加速度插值到 FT 时间点

4. 重力补偿
   ├── gravity = gravity_sign × imu_acc × mass
   ├── SessionBias_force = mean(FT_force[:N] - gravity[:N])
   ├── SessionBias_torque = mean(FT_torque[:N])
   ├── interaction_force = FT_force - SessionBias_force - gravity
   └── interaction_torque = FT_torque - SessionBias_torque

5. 输出
   ├── 保留原始 host_time
   ├── 替换 payload 为补偿后的 force/torque
   └── 写入 pure-force.jsonl

6. 验证
   └── 打印偏置期间平均交互力/力矩（应接近零）
```

### 已知局限

1. **运动加速度近似**：IMU 加速度包含运动加速度，当夹爪加速度较大时，重力估计会引入误差。更精确的做法是结合陀螺仪做姿态估计后分离重力分量。
2. **力矩补偿简化**：当前只减去静态偏置，未用 IMU 数据补偿重力引起的力矩变化。如果力臂较大或姿态变化剧烈，力矩残余误差会较明显。
3. **SessionBias 假设恒定**：假设传感器零偏在整个 session 内不变。长时间采集或温度变化较大时，零偏可能漂移。
4. **IMU-FT 刚体假设**：假设 IMU 和 FT 传感器刚性连接，无相对振动或弹性变形。
