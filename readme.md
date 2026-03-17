UMI Data Collection SDK

这是一个面向多模态机器人数据采集（Data Collection）的统一 SDK。本项目旨在提供一套可长期扩展的、健壮的软件系统，用于采集、对齐、处理并导出多模态传感器原始数据，特别适用于机器人操作（如 UMI，Universal Manipulation Interface）和具身智能（Embodied AI）等研究领域。

当前系统已全面重构，统一收口至新的 SDK 架构。

✨ 核心特性

🔌 统一传感器接入：标准化接入 RealSense (RGB-D)、六维力传感器 (FT) 和 惯性测量单元 (IMU)。

⏱️ 多模态时间对齐：支持高频传感器与相机的硬件/主机时间戳对齐，内置 BufferedFrameAligner 处理传感器异步到达问题。

⚖️ 内置信号处理：自动处理力传感器的静态校准（Static Calibration）和动态重力补偿（Gravity Compensation），直接输出纯接触力。

🗺️ ORB-SLAM3 无缝集成：自动化导出 SLAM 图像/惯性 Bundle，调用外部 ORB-SLAM3 程序，并将轨迹结果无损写回 Session。

📦 丰富的数据集导出：支持一键导出为主流具身智能格式：RLDS, LeRobot, HDF5, CSV, ROS Bag 2。

🧪 开箱即用的 Fake 模式：内置虚拟数据生成器，无需任何真实硬件即可跑通全套数据采集、处理与导出验证闭环。

🛠️ 安装与依赖

本项目基于 Python 3 开发。推荐使用 Miniforge/Conda 进行环境管理：

# 1. 创建并激活虚拟环境 (环境名称: umi_sdk)

conda create -n umi_sdk python=3.10

conda activate umi_sdk

# 2. 安装核心依赖

pip install numpy pyyaml

可选依赖（根据使用的功能按需安装）：

RealSense 采集: pip install pyrealsense2

图像与 Bundle 处理: pip install opencv-python

HDF5 导出: pip install h5py

LeRobot 导出: pip install pyarrow

ROS Bag 2 导出: 需在真实的 ROS 2 环境中运行（需 rosbag2_py, rclpy, std_msgs）。

🚀 快速开始 (无真机验证)

为了让开发者快速熟悉系统工作流，SDK 提供了 fake 虚拟传感器模式。你可以按顺序执行以下步骤，在几分钟内体验完整闭环：

1. 录制虚拟数据

# 生成包含 RGBD、FT、IMU 的虚拟数据，录制 2 秒

python scripts/sdk_record.py --sensor-source fake --duration 2

执行完毕后，会在 sessions/ 目录下生成一个 session_YYYYMMDD_HHMMSS 文件夹。

2. 查看 Session 摘要

python scripts/sdk_inspect.py sessions/session_<YOUR_SESSION_ID>

3. 处理轨迹 (虚拟演示)

# 这里使用一段内联 Python 脚本模拟真实的 ORB-SLAM3 输出

python scripts/sdk_process_trajectory.py sessions/session_<YOUR_SESSION_ID>

  --mode rgbd_inertial

  --command "python -c \"import json,sys; sys.stdout.write(json.dumps({'timestamp':0.0, 'position':[0,0,0], 'quaternion':[1,0,0,0]}) + '\\n')\""

4. 导出为具身智能数据集格式

# 导出为 LeRobot Parquet 格式

python scripts/sdk_export.py sessions/session_<YOUR_SESSION_ID> --format lerobot

# 导出为 RLDS 格式

python scripts/sdk_export.py sessions/session_<YOUR_SESSION_ID> --format rlds

5. 校验导出结果

python scripts/sdk_validate_export.py sessions/session_<YOUR_SESSION_ID> --format lerobot

📖 核心工作流详解

系统的标准工作流为：record -> inspect -> process_trajectory -> export -> validate

1. 采集数据 (sdk_record.py)

用于实时启动传感器、对齐时间戳并落盘。

支持开启/关闭特定模态：--disable-ft, --disable-realsense等。

支持设置传感器配置：--ft-port COM3, --realsense-fps 30。

支持配置文件覆盖：--config record_config.json。

真机录制示例：

python scripts/sdk_record.py --sensor-source real

2. ORB-SLAM3 轨迹处理 (sdk_process_trajectory.py)

本系统不直接耦合 ORB-SLAM3 的 C++ 代码，而是采用基于配置的解耦调用。脚本会自动将 Session 提取为 SLAM 所需的 bundle（图像集、时间戳文件、IMU csv），并执行外部 SLAM 命令，最后将 JSONL 格式的轨迹结果写回系统。

详见：ORB-SLAM3 输入输出约定

结合配置文件的使用示例：

# 参考 configs/orbslam3/rgbd_inertial.example.json 编写配置

python scripts/sdk_process_trajectory.py sessions/session_xxx --config configs/orbslam3/my_orbslam3_cfg.json

3. 数据集导出 (sdk_export.py)

强大的离线导出工具，确保各种模态的同步帧可以对齐输出，支持：

csv: 人类可读的平面快照表格。

hdf5: 传统机器人学习常用的分层格式。

rlds: 兼容 Google RT-X 系列的 RLDS 格式。

lerobot: 兼容 HuggingFace LeRobot 的 Parquet + 视频格式体系。

rosbag2: 原生 ROS 2 sqlite3 Bag 导出，便于在 ROS 工具链中回放。

📂 项目结构

UMI_DataCollection/

├── configs/          # 外部程序的配置文件示例 (如 ORB-SLAM3)

├── docs/             # 核心文档、设计边界与契约说明

├── scripts/          # 面向用户的 CLI 入口

│   ├── sdk_record.py             # 录制主程序

│   ├── sdk_inspect.py            # 会话审查工具

│   ├── sdk_process_trajectory.py # 轨迹生成与写回

│   ├── sdk_export.py             # 数据集导出器

│   └── sdk_validate_export.py    # 导出合法性校验

├── sdk/              # SDK 核心引擎代码

│   ├── core/         # 时钟、对齐器、注册表、Session基础定义

│   ├── sensors/      # 传感器适配层 (RealSense, FT, IMU, Fake)

│   ├── processors/   # 在线/离线数据处理器 (如重力补偿)

│   ├── perception/   # 外部感知算法接入层 (ORB-SLAM3 pipeline)

│   ├── exporters/    # 多格式导出引擎

│   └── storage/      # Schema 定义，数据读写 (Reader/Writer)

├── sensors/          # (Legacy) FT / IMU 底层驱动与硬件串口通信工具

└── tests/            # 单元测试与端到端闭环测试

📚 更多文档

项目设计与总览 (Project Overview)

ORB-SLAM3 接入契约

注：本项目重点关注软件系统的健壮性。目前所有的多模态对齐、重力补偿、导出功能均已在软件侧闭环验证。如涉及 RealSense 真机、真实 ORB-SLAM3 编译环境以及真实 ROS 2 回放，请根据您的本地环境单独联调。