/**
* This file is part of ORB-SLAM3
*
* Copyright (C) 2017-2021 Carlos Campos, Richard Elvira, Juan J. Gomez Rodriguez, Jose M.M. Montiel and Juan D. Tardos, University of Zaragoza.
* Copyright (C) 2014-2016 Raul Mur-Artal, Jose M.M. Montiel and Juan D. Tardos, University of Zaragoza.
*
* ORB-SLAM3 is free software: you can redistribute it and/or modify it under the terms of the GNU General Public
* License as published by the Free Software Foundation, either version 3 of the License, or
* (at your option) any later version.
*
* ORB-SLAM3 is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even
* the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
* GNU General Public License for more details.
*
* You should have received a copy of the GNU General Public License along with ORB-SLAM3.
* If not, see <http://www.gnu.org/licenses/>.
*/

#include <algorithm>
#include <fstream>
#include <iostream>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

#include <opencv2/core/core.hpp>
#include <opencv2/imgcodecs.hpp>

#include "ImuTypes.h"
#include "System.h"

using namespace std;

namespace {

struct Args {
    string vocab_path;
    string settings_path;
    string association_path;
    string imu_path;
    string output_path;
};

struct StereoFrame {
    double timestamp;
    string left_path;
    string right_path;
};

struct Sample3D {
    double timestamp;
    cv::Point3f value;
};

struct ImuRows {
    vector<Sample3D> accel;
    vector<Sample3D> gyro;
};

vector<string> Split(const string& value, const char delimiter) {
    vector<string> parts;
    string item;
    stringstream stream(value);
    while (getline(stream, item, delimiter)) {
        parts.push_back(item);
    }
    return parts;
}

string ResolvePath(const string& raw_path, const string& base_dir) {
    if (raw_path.empty()) {
        return raw_path;
    }
    if (raw_path[0] == '/') {
        return raw_path;
    }
    return base_dir + "/" + raw_path;
}

Args ParseArgs(const int argc, char** argv) {
    Args args;
    for (int index = 1; index < argc; index += 2) {
        const string flag(argv[index]);
        if (index + 1 >= argc) {
            throw runtime_error("missing value for flag " + flag);
        }
        const string value(argv[index + 1]);
        if (flag == "--vocab") {
            args.vocab_path = value;
        } else if (flag == "--settings") {
            args.settings_path = value;
        } else if (flag == "--association") {
            args.association_path = value;
        } else if (flag == "--imu") {
            args.imu_path = value;
        } else if (flag == "--output") {
            args.output_path = value;
        } else {
            throw runtime_error("unknown flag " + flag);
        }
    }

    if (args.vocab_path.empty() || args.settings_path.empty() || args.association_path.empty() ||
        args.imu_path.empty() || args.output_path.empty()) {
        throw runtime_error(
            "usage: ./sdk_stereo_inertial_offline --vocab path --settings path --association path --imu path --output path"
        );
    }
    return args;
}

vector<StereoFrame> LoadAssociations(const string& association_path) {
    ifstream input(association_path.c_str());
    if (!input.is_open()) {
        throw runtime_error("failed to open association file: " + association_path);
    }

    const size_t slash_pos = association_path.find_last_of('/');
    const string base_dir = slash_pos == string::npos ? "." : association_path.substr(0, slash_pos);
    vector<StereoFrame> frames;
    string line;
    while (getline(input, line)) {
        if (line.empty() || line[0] == '#') {
            continue;
        }
        stringstream stream(line);
        StereoFrame frame{};
        string left_path;
        string right_path;
        double right_timestamp = 0.0;
        stream >> frame.timestamp >> left_path >> right_timestamp >> right_path;
        if (!stream.fail()) {
            frame.left_path = ResolvePath(left_path, base_dir);
            frame.right_path = ResolvePath(right_path, base_dir);
            frames.push_back(frame);
        }
    }

    if (frames.empty()) {
        throw runtime_error("association file has no stereo frames: " + association_path);
    }

    sort(frames.begin(), frames.end(), [](const StereoFrame& lhs, const StereoFrame& rhs) {
        return lhs.timestamp < rhs.timestamp;
    });
    return frames;
}

ImuRows LoadImuRows(const string& imu_path) {
    ifstream input(imu_path.c_str());
    if (!input.is_open()) {
        throw runtime_error("failed to open imu file: " + imu_path);
    }

    ImuRows rows;
    string line;
    while (getline(input, line)) {
        if (line.empty()) {
            continue;
        }
        const vector<string> parts = Split(line, ',');
        if (parts.size() != 5 || parts[0] == "timestamp") {
            continue;
        }

        Sample3D sample{};
        sample.timestamp = stod(parts[0]);
        sample.value = cv::Point3f(stof(parts[2]), stof(parts[3]), stof(parts[4]));

        if (parts[1] == "accel") {
            rows.accel.push_back(sample);
        } else if (parts[1] == "gyro") {
            rows.gyro.push_back(sample);
        }
    }

    if (rows.accel.empty() || rows.gyro.empty()) {
        throw runtime_error("imu.csv must contain both accel and gyro rows");
    }

    sort(rows.accel.begin(), rows.accel.end(), [](const Sample3D& lhs, const Sample3D& rhs) {
        return lhs.timestamp < rhs.timestamp;
    });
    sort(rows.gyro.begin(), rows.gyro.end(), [](const Sample3D& lhs, const Sample3D& rhs) {
        return lhs.timestamp < rhs.timestamp;
    });
    return rows;
}

cv::Point3f InterpolateAccel(const vector<Sample3D>& accel_rows, const double target_time) {
    if (target_time <= accel_rows.front().timestamp) {
        return accel_rows.front().value;
    }
    if (target_time >= accel_rows.back().timestamp) {
        return accel_rows.back().value;
    }

    const auto upper = lower_bound(
        accel_rows.begin(),
        accel_rows.end(),
        target_time,
        [](const Sample3D& row, const double time) {
            return row.timestamp < time;
        }
    );
    if (upper == accel_rows.begin()) {
        return upper->value;
    }
    if (upper == accel_rows.end()) {
        return accel_rows.back().value;
    }

    const Sample3D& current = *upper;
    const Sample3D& previous = *(upper - 1);
    const double duration = current.timestamp - previous.timestamp;
    if (duration <= 0.0) {
        return current.value;
    }

    const float factor = static_cast<float>((target_time - previous.timestamp) / duration);
    return cv::Point3f(
        previous.value.x + (current.value.x - previous.value.x) * factor,
        previous.value.y + (current.value.y - previous.value.y) * factor,
        previous.value.z + (current.value.z - previous.value.z) * factor
    );
}

vector<ORB_SLAM3::IMU::Point> BuildSyncedImu(const ImuRows& rows) {
    vector<ORB_SLAM3::IMU::Point> synced_rows;
    synced_rows.reserve(rows.gyro.size());

    for (const Sample3D& gyro : rows.gyro) {
        const cv::Point3f accel = InterpolateAccel(rows.accel, gyro.timestamp);
        synced_rows.emplace_back(accel, gyro.value, gyro.timestamp);
    }

    return synced_rows;
}

size_t FindInitialImuIndex(const vector<ORB_SLAM3::IMU::Point>& imu_rows, const double first_frame_time) {
    size_t imu_index = 0;
    while (imu_index < imu_rows.size() && imu_rows[imu_index].t <= first_frame_time) {
        ++imu_index;
    }
    if (imu_index > 0) {
        --imu_index;
    }
    return imu_index;
}

}  // namespace

int main(int argc, char** argv) {
    try {
        const Args args = ParseArgs(argc, argv);
        const vector<StereoFrame> frames = LoadAssociations(args.association_path);
        const ImuRows imu_rows = LoadImuRows(args.imu_path);
        const vector<ORB_SLAM3::IMU::Point> synced_imu = BuildSyncedImu(imu_rows);
        if (synced_imu.empty()) {
            throw runtime_error("no synced IMU rows available for stereo inertial tracking");
        }

        ORB_SLAM3::System slam(
            args.vocab_path,
            args.settings_path,
            ORB_SLAM3::System::IMU_STEREO,
            false
        );

        size_t imu_index = FindInitialImuIndex(synced_imu, frames.front().timestamp);
        for (size_t frame_index = 0; frame_index < frames.size(); ++frame_index) {
            const StereoFrame& frame = frames[frame_index];
            cv::Mat left_image = cv::imread(frame.left_path, cv::IMREAD_UNCHANGED);
            cv::Mat right_image = cv::imread(frame.right_path, cv::IMREAD_UNCHANGED);
            if (left_image.empty()) {
                throw runtime_error("failed to load left image: " + frame.left_path);
            }
            if (right_image.empty()) {
                throw runtime_error("failed to load right image: " + frame.right_path);
            }

            vector<ORB_SLAM3::IMU::Point> frame_imu;
            if (frame_index > 0) {
                while (imu_index < synced_imu.size() && synced_imu[imu_index].t <= frame.timestamp) {
                    frame_imu.push_back(synced_imu[imu_index]);
                    ++imu_index;
                }
            }

            slam.TrackStereo(left_image, right_image, frame.timestamp, frame_imu);
        }

        slam.Shutdown();
        slam.SaveTrajectoryEuRoC(args.output_path);
        return 0;
    } catch (const exception& exc) {
        cerr << exc.what() << endl;
        return 1;
    }
}
