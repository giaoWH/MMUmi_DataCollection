# UMI_DataCollection 项目功能状态详细文档

分支feature/end-effector-tcp-latest是从分支media-exp扇出的，基础功能保留一致，新增了末端夹爪模式（上位机模式）

## 已实现但未经测试的功能


- **问题**:
  - 没有测试校准失败场景（样本不足、四元数异常）
  - 没有测试 `com_pos`（质心偏移）非零时的补偿效果

### 2. 上位机模式 (End Effector / TCP Latest) — 未验证

将传感器数据打包，通过树莓派的千兆网口向上位机发送数据，但是千兆网口极有可能存在带宽不足

上位机模式下，电机由机械臂独立控制，所以不参与数据打包

后续解决方案：
将树莓派换成一个高带宽独立供电扩展坞（和当前设备上的一致即可）。

两个扩展坞互联后其中一个连接到上位机，sdk完全放在上位机端，所有数据采集/解算/标注全部在上位机端完成。
  

### 3. 轨迹解算 (ORB-SLAM3 Trajectory Processing) — 代码完整，测试不足
注：轨迹结算需要把ThirdParty/ORBSLAM3仓库补充完整，然后把当前仓库中的这个CMakeList替换进去，再把sdk_stereo_inertial_offline.cc文件替换到完整的ORBSLAM3/Example/Stereo-Inertial中去。然后编译整个ORBSLAM3仓库。之后即可结算轨迹

### 4. 导出功能 (Exporters) — 代码完整，测试仅覆盖验证层

Lerobot数据集导出功能已完善，导出的数据包含了明确的action和observation，经过dataloader调整可以用于训练
但是rosbag，HDF5，CSV，RLDS等均未验证，可导出但大概率无法直接用于训练

### 5. 坐标变换处理器

**缺失**: 不同坐标系之间的变换（传感器坐标系到世界坐标系）。
IMU与FT传感器在安装时的物理坐标一致，但是传感器坐标系与轨迹坐标系不同，需要匹配

### 6. 轨迹解算模式

`sdk/perception/orbslam3/` 目前只支持 `stereo_inertial` 模式。


## 关键风险点总结

2. **上位机模式未经硬件测试** — TCP 传输的稳定性、断线重连、帧丢失等场景未验证
3. **轨迹解算完全没有测试** — ORB-SLAM3 的 7 个源文件没有任何测试
4. **导出功能代码完整但无端到端测试** — CSV/HDF5/ROS Bag/RLDS 四种导出都没有实际导出测试
