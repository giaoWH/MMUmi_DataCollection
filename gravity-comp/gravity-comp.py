import json
import os
import glob
import argparse
import numpy as np
import pandas as pd
from datetime import datetime
from scipy.interpolate import interp1d


def parse_host_time(s: str) -> float:
    """Parse '2026-05-12-19-35-42-580' into epoch seconds."""
    parts = s.split("-")
    dt = datetime(
        int(parts[0]), int(parts[1]), int(parts[2]),
        int(parts[3]), int(parts[4]), int(parts[5]),
        int(parts[6]) * 1000  # ms -> us
    )
    return dt.timestamp()


def read_jsonl(path: str) -> list:
    records = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def ft_jsonl_to_df(records: list) -> pd.DataFrame:
    """Convert FT _s.jsonl records to DataFrame with timestamp, fx,fy,fz,tx,ty,tz."""
    rows = []
    for rec in records:
        ts = parse_host_time(rec["host_time"])
        f = rec["payload"]["force"]
        t = rec["payload"]["torque"]
        rows.append([ts, f[0], f[1], f[2], t[0], t[1], t[2]])
    return pd.DataFrame(rows, columns=["timestamp", "fx", "fy", "fz", "tx", "ty", "tz"])


def imu_jsonl_to_df(records: list) -> pd.DataFrame:
    """Convert IMU _s.jsonl records to DataFrame with timestamp, ax,ay,az."""
    rows = []
    for rec in records:
        ts = parse_host_time(rec["host_time"])
        a = rec["payload"]["acceleration"]
        rows.append([ts, a[0], a[1], a[2]])
    return pd.DataFrame(rows, columns=["timestamp", "ax", "ay", "az"])


def write_pure_force_jsonl(ft_records: list, interaction_force: np.ndarray,
                           interaction_torque: np.ndarray, output_path: str):
    """Write compensated force/torque as _s.jsonl format."""
    with open(output_path, "w", encoding="utf-8") as f:
        for i, rec in enumerate(ft_records):
            out = {
                "host_time": rec["host_time"],
                "payload": {
                    "force": interaction_force[i].tolist(),
                    "torque": interaction_torque[i].tolist(),
                }
            }
            f.write(json.dumps(out, ensure_ascii=False) + "\n")


def compensate_force_sensor(
    imu_file: str,
    ft_file: str,
    output_file: str,
    mass: float = 0.496,
    num_bias_frames: int = 100,
    gravity_sign: float = 1.0,
    imu_timestamp_col: str = 'timestamp',
    imu_columns: list = ['ax', 'ay', 'az'],
    ft_timestamp_col: str = 'timestamp',
    ft_columns: list = ['fx', 'fy', 'fz'],
    imu_timestamp_scale: float = 1.0,
    ft_timestamp_scale: float = 1.0,
    interpolation_method: str = 'linear',
) -> pd.DataFrame:
    """
    利用IMU数据对六维力传感器进行重力补偿

    补偿公式:
        gravity = gravity_sign * Imu_acc * mass
        bias = FT_raw - gravity
        SessionBias = mean(bias[:num_bias_frames])
        interaction_force = FT_raw - SessionBias - gravity

    参数:
        imu_file: IMU数据CSV文件路径
        ft_file: 六维力传感器数据CSV文件路径
        output_file: 补偿后数据保存路径
        mass: 夹爪质量(kg)
        num_bias_frames: 用于计算偏置的前N帧数据
        gravity_sign: 重力补偿符号
            -1.0: IMU输出比力(方向向上), 力传感器重力分量向下, 需要取反
            +1.0: IMU输出与力传感器重力分量同向
            请用实际数据验证: 静止时 FT_raw + gravity_sign * Imu_acc * mass
            如果接近零则符号正确, 如果翻倍则符号反了
        imu_timestamp_col: IMU文件中时间戳列名
        imu_columns: IMU文件中三轴加速度列名
        ft_timestamp_col: FT文件中时间戳列名
        ft_columns: FT文件中三轴力列名
        imu_timestamp_scale: IMU时间戳缩放为秒的系数 (秒:1.0, 毫秒:0.001, 微秒:1e-6)
        ft_timestamp_scale: FT时间戳缩放为秒的系数
        interpolation_method: 插值方法 ('linear' 或 'nearest')
    """
    # 读取数据
    df_imu = pd.read_csv(imu_file)
    df_ft = pd.read_csv(ft_file)
    print(f"IMU数据: {len(df_imu)} 帧, FT数据: {len(df_ft)} 帧")

    # 标准化时间戳为秒
    imu_ts = df_imu[imu_timestamp_col].values * imu_timestamp_scale
    ft_ts = df_ft[ft_timestamp_col].values * ft_timestamp_scale

    # 检查时间重叠
    overlap_start = max(imu_ts[0], ft_ts[0])
    overlap_end = min(imu_ts[-1], ft_ts[-1])
    if overlap_start >= overlap_end:
        raise ValueError("IMU和FT数据没有时间重叠，无法同步")
    print(f"重叠时间: {overlap_start:.3f}s - {overlap_end:.3f}s ({overlap_end - overlap_start:.3f}s)")

    # 只保留重叠范围内的FT数据
    valid_mask = (ft_ts >= overlap_start) & (ft_ts <= overlap_end)
    ft_ts_valid = ft_ts[valid_mask]
    df_ft_valid = df_ft[valid_mask].reset_index(drop=True)

    if len(df_ft_valid) < num_bias_frames:
        raise ValueError(f"有效数据 {len(df_ft_valid)} 帧, 少于偏置计算所需 {num_bias_frames} 帧")

    # IMU插值到FT时间点
    imu_data = df_imu[imu_columns].values.astype(np.float64)
    imu_synced = np.zeros((len(ft_ts_valid), 3))
    for i in range(3):
        interp_fn = interp1d(imu_ts, imu_data[:, i], kind=interpolation_method, bounds_error=False)
        imu_synced[:, i] = interp_fn(ft_ts_valid)

    if np.isnan(imu_synced).any():
        nan_count = np.isnan(imu_synced).sum()
        print(f"警告: {nan_count} 个IMU插值NaN, 已填充")
        df_temp = pd.DataFrame(imu_synced)
        imu_synced = df_temp.ffill().bfill().values

    # 提取FT力数据
    ft_data = df_ft_valid[ft_columns].values.astype(np.float64)

    # 计算重力分量
    gravity = gravity_sign * imu_synced * mass

    # 计算SessionBias (前N帧: 空载、近似静止)
    bias = ft_data[:num_bias_frames] - gravity[:num_bias_frames]
    session_bias = np.mean(bias, axis=0)
    print(f"SessionBias: X={session_bias[0]:.4f}N, Y={session_bias[1]:.4f}N, Z={session_bias[2]:.4f}N")

    # 计算交互力
    interaction_force = ft_data - session_bias - gravity

    # 构建结果
    df_result = df_ft_valid.copy()
    df_result['timestamp_synced'] = ft_ts_valid
    for i, col in enumerate(imu_columns):
        df_result[f'{col}_synced'] = imu_synced[:, i]
    df_result['interaction_x'] = interaction_force[:, 0]
    df_result['interaction_y'] = interaction_force[:, 1]
    df_result['interaction_z'] = interaction_force[:, 2]

    # 保存
    df_result.to_csv(output_file, index=False)

    # 验证: 偏置期间交互力应接近0
    bias_period_mean = interaction_force[:num_bias_frames].mean(axis=0)
    print(f"偏置期间平均交互力(验证): X={bias_period_mean[0]:.6f}N, Y={bias_period_mean[1]:.6f}N, Z={bias_period_mean[2]:.6f}N")
    print(f"结果已保存到 {output_file}")

    return df_result


def compensate_session(
    session_dir: str,
    mass: float = 0.496,
    num_bias_frames: int = 100,
    gravity_sign: float = 1.0,
    interpolation_method: str = 'linear',
) -> str:
    """对单个session目录进行重力补偿, 输出 pure-force.jsonl"""
    # 查找 _s.jsonl 文件
    ft_pattern = os.path.join(session_dir, "streams", "ft", "*_s.jsonl")
    imu_pattern = os.path.join(session_dir, "streams", "imu", "*_s.jsonl")

    ft_files = glob.glob(ft_pattern)
    imu_files = glob.glob(imu_pattern)

    if not ft_files:
        raise FileNotFoundError(f"未找到FT数据: {ft_pattern}")
    if not imu_files:
        raise FileNotFoundError(f"未找到IMU数据: {imu_pattern}")

    ft_path = ft_files[0]
    imu_path = imu_files[0]
    output_path = os.path.join(session_dir, "streams", "ft", "pure-force.jsonl")

    print(f"\n--- Session: {os.path.basename(session_dir)} ---")
    print(f"FT:  {os.path.basename(ft_path)}")
    print(f"IMU: {os.path.basename(imu_path)}")

    # 读取 JSONL
    ft_records = read_jsonl(ft_path)
    imu_records = read_jsonl(imu_path)
    print(f"IMU数据: {len(imu_records)} 帧, FT数据: {len(ft_records)} 帧")

    # 转为 DataFrame
    df_imu = imu_jsonl_to_df(imu_records)
    df_ft = ft_jsonl_to_df(ft_records)

    # 时间戳已是秒, scale=1.0
    imu_ts = df_imu["timestamp"].values
    ft_ts = df_ft["timestamp"].values

    # 检查时间重叠
    overlap_start = max(imu_ts[0], ft_ts[0])
    overlap_end = min(imu_ts[-1], ft_ts[-1])
    if overlap_start >= overlap_end:
        raise ValueError("IMU和FT数据没有时间重叠，无法同步")
    print(f"重叠时间: {overlap_start:.3f}s - {overlap_end:.3f}s ({overlap_end - overlap_start:.3f}s)")

    # 只保留重叠范围内的FT数据
    valid_mask = (ft_ts >= overlap_start) & (ft_ts <= overlap_end)
    ft_ts_valid = ft_ts[valid_mask]
    valid_indices = np.where(valid_mask)[0]

    if len(valid_indices) < num_bias_frames:
        raise ValueError(f"有效数据 {len(valid_indices)} 帧, 少于偏置计算所需 {num_bias_frames} 帧")

    # IMU插值到FT时间点
    imu_acc = df_imu[["ax", "ay", "az"]].values.astype(np.float64)
    imu_synced = np.zeros((len(ft_ts_valid), 3))
    for i in range(3):
        interp_fn = interp1d(imu_ts, imu_acc[:, i], kind=interpolation_method, bounds_error=False)
        imu_synced[:, i] = interp_fn(ft_ts_valid)

    if np.isnan(imu_synced).any():
        nan_count = np.isnan(imu_synced).sum()
        print(f"警告: {nan_count} 个IMU插值NaN, 已填充")
        df_temp = pd.DataFrame(imu_synced)
        imu_synced = df_temp.ffill().bfill().values

    # 提取FT力和力矩
    ft_force = df_ft[["fx", "fy", "fz"]].values.astype(np.float64)[valid_mask]
    ft_torque = df_ft[["tx", "ty", "tz"]].values.astype(np.float64)[valid_mask]

    # 计算重力分量
    gravity = gravity_sign * imu_synced * mass

    # SessionBias (前N帧: 空载、近似静止)
    # 力: bias = ft_force - gravity
    session_bias_force = np.mean(ft_force[:num_bias_frames] - gravity[:num_bias_frames], axis=0)
    # 力矩: 重力不直接产生力矩, 只减去静态偏置
    session_bias_torque = np.mean(ft_torque[:num_bias_frames], axis=0)

    print(f"SessionBias Force:  X={session_bias_force[0]:.4f}N, Y={session_bias_force[1]:.4f}N, Z={session_bias_force[2]:.4f}N")
    print(f"SessionBias Torque: X={session_bias_torque[0]:.6f}Nm, Y={session_bias_torque[1]:.6f}Nm, Z={session_bias_torque[2]:.6f}Nm")

    # 计算交互力和力矩
    interaction_force = ft_force - session_bias_force - gravity
    interaction_torque = ft_torque - session_bias_torque

    # 只保留重叠范围内的原始FT记录
    ft_records_valid = [ft_records[i] for i in valid_indices]

    # 写入输出
    write_pure_force_jsonl(ft_records_valid, interaction_force, interaction_torque, output_path)

    # 验证
    bias_force_mean = interaction_force[:num_bias_frames].mean(axis=0)
    bias_torque_mean = interaction_torque[:num_bias_frames].mean(axis=0)
    print(f"偏置期间平均交互力(验证):  X={bias_force_mean[0]:.6f}N, Y={bias_force_mean[1]:.6f}N, Z={bias_force_mean[2]:.6f}N")
    print(f"偏置期间平均交互力矩(验证): X={bias_torque_mean[0]:.6f}Nm, Y={bias_torque_mean[1]:.6f}Nm, Z={bias_torque_mean[2]:.6f}Nm")
    print(f"已保存到 {output_path} ({len(ft_records_valid)} 帧)")

    return output_path


def batch_process(
    testdata_dir: str,
    mass: float = 0.496,
    num_bias_frames: int = 100,
    gravity_sign: float = 1.0,
    interpolation_method: str = 'linear',
) -> list:
    """批量处理testdata下所有session"""
    output_paths = []
    session_dirs = sorted(glob.glob(os.path.join(testdata_dir, "*")))

    for session_dir in session_dirs:
        if not os.path.isdir(session_dir):
            continue
        ft_dir = os.path.join(session_dir, "streams", "ft")
        if not os.path.isdir(ft_dir):
            continue
        try:
            path = compensate_session(
                session_dir, mass, num_bias_frames,
                gravity_sign, interpolation_method,
            )
            output_paths.append(path)
        except Exception as e:
            print(f"Session {os.path.basename(session_dir)}: FAILED - {e}")

    return output_paths


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="六维力传感器重力补偿")
    parser.add_argument("--session", type=str, help="单个session目录路径")
    parser.add_argument("--all", type=str, dest="testdata_dir", help="testdata目录路径, 批量处理所有session")
    parser.add_argument("--mass", type=float, default=0.496, help="夹爪质量(kg)")
    parser.add_argument("--bias-frames", type=int, default=100, help="偏置计算帧数")
    parser.add_argument("--gravity-sign", type=float, default=1.0, help="重力补偿符号 (-1.0 或 +1.0)")
    parser.add_argument("--interp", type=str, default="linear", choices=["linear", "nearest"], help="插值方法")
    args = parser.parse_args()

    if args.session:
        compensate_session(
            args.session, args.mass, args.bias_frames,
            args.gravity_sign, args.interp,
        )
    elif args.testdata_dir:
        paths = batch_process(
            args.testdata_dir, args.mass, args.bias_frames,
            args.gravity_sign, args.interp,
        )
        print(f"\n共处理 {len(paths)} 个session")
    else:
        parser.print_help()
