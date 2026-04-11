# Stream 命名重构测试文档

## 1. 文档目的

本文用于补齐本次 `session stream / artifact` 命名重构后尚未在当前环境执行的测试工作。

本次重构涉及：

- `streams/<sensor>/` 下 `jsonl` 文件命名改为 `<task_slug>_<modality>_<seq>.jsonl`
- `streams/<sensor>/` 下 `mp4` 文件命名改为 `<task_slug>_<modality>_<seq>.mp4`
- `streams/<sensor>/artifacts/` 下 artifact 文件命名改为 `<task_slug>_<modality>_<seq>_<frame_id六位补零>_<payload_key>.<ext>`
- `SessionInfo` / `manifest.json` / `meta.json` 新增 `task_name` / `task_slug`
- `SessionReader` / `SessionWriter` 全面改为依赖 manifest 中的 `frames_path` / `media_path`
- `sdk_record.py` 非交互式录制要求显式提供 `task_name`
- annotation schema 必须存在 `session.task_name`
- schema 版本升级到 `2.1.0`

本文目标是保证以下链路在新命名规则下全部可运行：

- record
- inspect
- annotation
- export
- quality qc
- trajectory processing
- ORB-SLAM3 bundle/export pipeline

## 2. 当前未完成的测试原因

当前环境未能完成完整测试，原因如下：

- 未安装 `numpy`
- 未安装 `pytest`
- 部分导出链路依赖额外库，例如 `pyarrow`、`h5py`、`opencv-python`、`av`
- `rosbag2` 验证需要 ROS 2 环境

已完成的最低限度验证：

- 修改文件已通过 `python -m py_compile`
- 关键命名 helper 已做最小运行验证

因此，后续应在依赖完整的环境中执行本文档中的全部测试。

## 3. 测试总原则

- 所有路径断言优先以 `manifest.json` 为准，不依赖手工拼接路径
- 每一条链路都至少验证一次：
  - 文件真实落盘
  - `manifest.json` / `meta.json` 元数据一致
  - `SessionReader` 能成功读取
  - 下游功能能继续消费
- 重点覆盖以下两类错误：
  - 旧固定命名残留导致的读取失败
  - `task_name` / `task_slug` 缺失导致的启动失败或隐式错配

## 4. 环境准备

建议准备以下环境：

```bash
conda activate umi_sdk
python -m pip install pytest numpy
python -m pip install pyarrow h5py opencv-python av
```

若需完整覆盖：

- 真机录制环境
- fake 传感器环境
- ROS 2 环境

推荐优先执行：

```bash
python -m pytest tests/test_record_config_loading.py -q
python -m pytest tests/test_session_writer.py -q
python -m pytest tests/test_annotations.py -q
python -m pytest tests/test_export_validation.py -q
python -m pytest tests/test_trajectory_qc.py -q
python -m pytest tests/test_sdk_e2e.py -q
python -m pytest tests/test_sdk_record_interactive.py -q
```

## 5. 录制链路测试

### 5.1 非交互式录制最小成功路径

目标：

- 验证 non-interactive 在显式提供 `task_name` 时能完整生成新命名 session

步骤：

1. 使用 fake 传感器创建一份 non-interactive 配置
2. 配置中显式设置：
   - `interactive: false`
   - `task_name: pick_cube`
3. 运行：
   - `python scripts/sdk_record.py --config <config>`
4. 检查 session 目录中：
   - `manifest.json`
   - `meta.json`
   - `streams/<sensor>/...`
   - `aligned/frames.jsonl`
   - `logs/sdk_record.log`

通过标准：

- 退出码为 `0`
- `manifest.json` 中每个 sensor 的 `frames_path` / `media_path` 使用新命名
- `meta.json` 中存在 `task_name` / `task_slug`
- `SessionReader(session_dir).summary()` 返回 `task_name` / `task_slug`

### 5.2 非交互式录制缺少 task_name

目标：

- 验证 non-interactive 在缺少 `task_name` 时启动前失败

步骤：

1. 创建 non-interactive 配置，不提供 `task_name`
2. 运行 `sdk_record.py`

通过标准：

- 退出码非 `0`
- 输出中明确包含“非交互式录制要求提供有效 task_name”
- 不应产生半成品 session 目录

### 5.3 非交互式录制 schema 缺少 session.task_name

目标：

- 验证 annotation schema 缺少 `task_name` 时启动前失败

步骤：

1. patch 或提供缺失 `task_name` 的 annotation schema
2. non-interactive 配置中填写合法 `task_name`
3. 启动 `sdk_record.py`

通过标准：

- 退出码非 `0`
- 输出中明确说明 annotation schema 缺少 `session.task_name`
- 不应产生半成品 session 目录

### 5.4 交互式录制成功路径

目标：

- 验证 interactive 模式仍能通过 task name 创建 session，并写出新命名 stream 文件

步骤：

1. 用 fake 传感器运行交互式录制
2. 输入 `pick_cube`
3. 开始录制、停止录制、保存 session

通过标准：

- session 目录成功创建
- session 目录名仍沿用交互式 session name 逻辑
- `annotations/session.json` 中 `task_name == pick_cube`
- 实际 stream 文件名使用 `pick_cube` 规范化后的 `task_slug`

### 5.5 非 ASCII task_name

目标：

- 验证中文 task name 在 session dir 和 stream 文件命名中的处理方式一致

步骤：

1. 交互式输入中文 task name，例如 `抓取 方块`
2. 保存 session

通过标准：

- annotation 中保留原始 `task_name`
- `task_slug` 为规范化后的版本
- stream 文件名与 `task_slug` 一致
- `SessionReader`、`sdk_inspect.py` 可正常读取

## 6. SessionWriter / SessionReader 测试

### 6.1 manifest 与实际文件一致性

目标：

- 验证每个 sensor 的 `frames_path`、`media_path` 与真实文件一致

覆盖传感器：

- camera
- microphone
- realsense
- gelsight
- ft
- imu
- motors

通过标准：

- `manifest.sensors[*].frames_path` 指向的文件真实存在
- 若 `media_path` 非空，则目标文件真实存在
- `SessionReader.iter_sensor_frames(sensor)` 可成功读取

### 6.2 payload.path 一致性

目标：

- 验证 `payload.path` 中的所有外部引用都使用新命名

需覆盖：

- `mp4_frame`
- `mp4_audio`
- `png`
- `npy`

通过标准：

- `record["payload"][...]["path"]` 使用新命名
- `SessionReader.load_artifact(reference)` 能成功加载

### 6.3 artifact 命名

目标：

- 验证 `artifacts/` 文件名采用新前缀

建议覆盖字段：

- `realsense.depth`
- `realsense.ir1`
- `realsense.ir2`
- `realsense.aligned_depth_to_color`
- `realsense.pointcloud.vertices`

通过标准：

- 文件名符合：
  - `<task_slug>_<modality>_<seq>_<frame_id六位补零>_<payload_key>.<ext>`
- `payload.path` 与实际文件名一致

### 6.4 open_existing 追加写入

目标：

- 验证 `SessionWriter.open_existing()` 在新 schema session 上可追加 trajectory，不受命名重构影响

通过标准：

- 能读取新 manifest
- 能成功追加 `trajectory/frames.jsonl`
- 不修改已有 stream 文件名

### 6.5 旧 session 不兼容提示

目标：

- 验证旧 schema session 会以明确错误失败

步骤：

1. 构造缺少 `task_name/task_slug` 的旧 manifest 或仅旧 meta
2. 分别调用：
   - `SessionReader(session_dir)`
   - `SessionWriter.open_existing(session_dir)`

通过标准：

- 错误信息明确包含：
  - `task_name/task_slug`
  - `不支持旧命名 session`

## 7. Annotation 链路测试

### 7.1 AnnotationService 初始化

目标：

- 验证 annotation 初始化不受 stream 文件命名变化影响

通过标准：

- `AnnotationService.ensure_initialized()` 成功
- `annotations/manifest.json`
- `annotations/schema.snapshot.json`
- `annotations/session.json`
- `annotations/spans.jsonl`
- `annotations/keyframes.jsonl`
  均正常读写

### 7.2 reader.summary 与 inspect 输出

目标：

- 验证 annotation summary 与 session summary 在新 schema 下正常

通过标准：

- `SessionReader.summary()` 包含：
  - `task_name`
  - `task_slug`
  - `annotation_summary`
- `scripts/sdk_inspect.py` 输出可被 JSON 解析

### 7.3 annotation web 预览

目标：

- 验证 annotation web 可以从新命名 media/artifact 中预览数据

覆盖：

- media-backed rgb frame
- microphone audio summary
- artifact-backed image / depth

通过标准：

- 页面能正确显示预览
- 后端不出现 `FileNotFoundError`
- 前端不出现因为旧路径假设导致的请求失败

## 8. Export 链路测试

### 8.1 CSV 导出

目标：

- 验证 CSV 导出不依赖旧 stream 文件名

通过标准：

- `sdk_export.py --format csv` 成功
- `SessionExportValidator.validate("csv")` 通过

### 8.2 HDF5 导出

目标：

- 验证 HDF5 导出可消费新命名 stream / artifact

通过标准：

- `sdk_export.py --format hdf5` 成功
- 文件中包含 `streams/aligned/trajectory`
- `SessionExportValidator.validate("hdf5")` 通过

### 8.3 RLDS 导出

目标：

- 验证 RLDS 导出在新命名规则下不丢 observation

通过标准：

- `sdk_export.py --format rlds` 成功
- `episode_000000.json` 中 step 数与 aligned 记录一致
- `SessionExportValidator.validate("rlds")` 通过

### 8.4 LeRobot 导出

目标：

- 验证 LeRobot 导出能正确解码新命名的 media/artifact，并写出训练友好格式

重点覆盖：

- `realsense_depth`
- `realsense_aligned_depth_to_color`
- `camera`
- `gelsight`
- `microphone`

通过标准：

- parquet 文件存在
- 导出的图像/音频外部文件全部存在
- `SessionExportValidator.validate("lerobot")` 通过
- 多 session merge 结果无媒体文件名冲突

### 8.5 Rosbag2 导出

目标：

- 验证 rosbag2 导出至少在 ROS 2 环境中能够完整执行

通过标准：

- `sdk_export.py --format rosbag2` 成功
- 输出目录非空
- 如环境允许，抽查 topic 中 payload 不为空

## 9. Trajectory / QC / ORB-SLAM3 测试

### 9.1 trajectory 处理

目标：

- 验证 stream 命名重构不影响轨迹生成

通过标准：

- `scripts/sdk_process_trajectory.py` 成功
- 输出 `trajectory/frames.jsonl`
- `SessionReader.iter_trajectory_frames()` 正常

### 9.2 trajectory qc

目标：

- 验证 QC 逻辑不依赖旧 stream 文件名

通过标准：

- `scripts/sdk_qc_trajectory.py` 成功
- 输出 `quality/trajectory_qc.json`
- `SessionReader.trajectory_qc_summary()` 正常

### 9.3 ORB-SLAM3 bundle 导出

目标：

- 验证 bundle exporter 能通过 `SessionReader` 解码新命名的 media/artifact

覆盖：

- `rgbd_inertial`
- `stereo_inertial`

通过标准：

- `rgb/left/right/depth` 文件全部生成
- `association` 文件生成
- `imu.csv` 生成
- `bundle_manifest.json` 生成

## 10. 配置与 CLI 测试

### 10.1 parse_args

目标：

- 验证 `--task-name` CLI 参数与配置文件 `task_name` 都能被正确加载

通过标准：

- CLI 覆盖配置生效
- `RecorderConfig.task_name` 正确

### 10.2 record.yaml 样例

目标：

- 验证示例配置与当前代码约束一致

需要检查：

- `interactive: true` 时可不提供 `task_name`
- `interactive: false` 时必须提供 `task_name`

## 11. 文档抽样核对

目标：

- 验证文档与当前代码行为一致，不再误导用户使用旧固定命名

需抽查文档：

- `docs/session-data-layout.md`
- `docs/project-overview.md`
- `docs/action-observation-schema.md`
- `docs/orbslam3-io-contract.md`

重点核对项：

- stream `jsonl/mp4` 命名规则
- artifact 命名规则
- `meta.json` / `manifest.json` 示例
- non-interactive `task_name` 要求
- “以 manifest 为准，不自己拼路径”的建议

## 12. 建议执行顺序

推荐按以下顺序执行，便于快速定位问题：

1. `test_record_config_loading.py`
2. `test_session_writer.py`
3. `test_annotations.py`
4. `test_export_validation.py`
5. `test_trajectory_qc.py`
6. `test_sdk_record_interactive.py`
7. `test_sdk_e2e.py`
8. ROS 2 环境下的 `rosbag2` 真实验证
9. 真机环境下的 smoke test

## 13. 完成标准

本次命名重构可视为“测试完成”的标准如下：

- 上述 Python 自动化测试全部通过
- fake 环境下的 record / inspect / annotation / export / qc / trajectory 全链路通过
- ORB-SLAM3 bundle/export 链路通过
- annotation web 预览无路径错误
- ROS 2 环境下 rosbag2 至少完成一次真实导出验证
- 文档与配置样例完成最终人工核对

若任一链路出现“仍依赖旧固定文件名”的错误，应视为阻塞问题，不应发布。
