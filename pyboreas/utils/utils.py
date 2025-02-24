import os.path as osp
from bisect import bisect_left
from pathlib import Path

import numpy as np

from pyboreas.utils.lgmath import _tran2vec, _vec2tran, carrot


def load_lidar(path, dim=6):
    """Loads a pointcloud (np.ndarray) (N, 6) from path [x, y, z, intensity, laser_number, time]"""
    # Aeva: [x, y, z, intensity, Doppler, laser_number, time]
    # dtype MUST be float32 to load this properly!
    points = np.fromfile(path, dtype=np.float32).reshape((-1, dim)).astype(np.float64)
    t = get_time_from_filename(path)
    points[:, -1] += t
    return points


def roll(r):
    return np.array(
        [[1, 0, 0], [0, np.cos(r), np.sin(r)], [0, -np.sin(r), np.cos(r)]],
        dtype=np.float64,
    )


def pitch(p):
    return np.array(
        [[np.cos(p), 0, -np.sin(p)], [0, 1, 0], [np.sin(p), 0, np.cos(p)]],
        dtype=np.float64,
    )


def yaw(y):
    return np.array(
        [[np.cos(y), np.sin(y), 0], [-np.sin(y), np.cos(y), 0], [0, 0, 1]],
        dtype=np.float64,
    )


def yawPitchRollToRot(y, p, r):
    return roll(r) @ pitch(p) @ yaw(y)


def rotToYawPitchRoll(C):
    i = 2
    j = 1
    k = 0
    c_y = np.sqrt(C[i, i] ** 2 + C[j, i] ** 2)
    if c_y > 1e-14:
        r = np.arctan2(C[j, i], C[i, i])
        p = np.arctan2(-C[k, i], c_y)
        y = np.arctan2(C[k, j], C[k, k])
    else:
        r = 0
        p = np.arctan2(-C[k, i], c_y)
        y = np.arctan2(-C[j, k], C[j, j])
    return y, p, r


def rotToRollPitchYaw(C):
    i = 2
    j = 1
    k = 0
    c_y = np.sqrt(C[i, i] ** 2 + C[j, i] ** 2)
    if c_y > 1e-14:
        r = np.arctan2(C[j, i], C[i, i])
        p = np.arctan2(-C[k, i], c_y)
        y = np.arctan2(C[k, j], C[k, k])
    else:
        r = 0
        p = np.arctan2(-C[k, i], c_y)
        y = np.arctan2(-C[j, k], C[j, j])
    return r, p, y


def get_transform(gt):
    """Retrieve 4x4 homogeneous transform for a given parsed line of the ground truth pose csv
    Args:
        gt (List[float]): parsed line from ground truth csv file
    Returns:
        np.ndarray: 4x4 transformation matrix (pose of sensor)
    """
    T = np.identity(4, dtype=np.float64)
    C_enu_sensor = yawPitchRollToRot(gt[9], gt[8], gt[7])
    T[0, 3] = gt[1]
    T[1, 3] = gt[2]
    T[2, 3] = gt[3]
    T[0:3, 0:3] = C_enu_sensor
    return T


def get_transform2(R, t):
    """Returns a 4x4 homogeneous 3D transform
    Args:
        R (np.ndarray): (3,3) rotation matrix
        t (np.ndarray): (3,1) translation vector
    Returns:
        np.ndarray: 4x4 transformation matrix
    """
    T = np.identity(4, dtype=R.dtype)
    T[0:3, 0:3] = R
    T[0:3, 3] = t.squeeze()
    return T


def get_transform3(x, y, theta, dtype=np.float64):
    """Returns a 4x4 homogeneous 3D transform for a given 2D (x, y, theta).
    Args:
        x (float): x-translation
        y (float): y-translation
        theta (float): rotation
    Returns:
        np.ndarray: 4x4 transformation matrix
    """
    T = np.identity(4, dtype=dtype)
    T[0:2, 0:2] = np.array(
        [[np.cos(theta), np.sin(theta)], [-np.sin(theta), np.cos(theta)]]
    )
    T[0, 3] = x
    T[1, 3] = y
    return T


def quaternionToRot(qin):
    """Converts a quaternion to a rotation  matrix
    Args:
        qin (np.ndarray) (4,) [qx, qy, qz, qw] quaternion
    Returns:
        C (np.ndarray) (3,3) rotation matrix
    """
    q = qin.copy().reshape(4, 1)
    if np.matmul(q.transpose(), q) < 1e-14:
        return np.identity(3)
    xi = q[:3].reshape(3, 1)
    eta = q[3, 0]
    C = (
        (eta**2 - np.matmul(xi.transpose(), xi)) * np.identity(3)
        + 2 * np.matmul(xi, xi.transpose())
        - 2 * eta * carrot(xi)
    )
    return C


def rotToQuaternion(C):
    """Converts a rotation matrix to a quaternion
    Note that the space of unit-length quaternions is a double-cover of SO(3)
    which means that, C maps to +/- q, so q --> C --> +/- q
    Args:
        C (np.ndarray) (3,3) rotation matrix
    Returns:
        q (np.ndarray) (4,1) [qx, qy, qz, qw] quaternion
    """
    eta = 0.5 * np.sqrt((1 + np.trace(C)))
    if np.abs(eta) < 1e-14:
        eta = 0
        xi = np.sqrt(np.diag(0.5 * (C + np.identity(3))))
        q = np.array([xi[0], xi[1], xi[2], eta]).reshape(4, 1)
    else:
        phi = wrapto2pi(2 * np.arccos(max(min(eta, 1.0), -1.0)))
        eta = np.cos(phi / 2)
        xi_cross = (C.T - C) / (4 * eta)
        q = np.array([xi_cross[2, 1], xi_cross[0, 2], xi_cross[1, 0], eta]).reshape(
            4, 1
        )
    return q


def get_inverse_tf(T):
    """Returns the inverse of a given 4x4 homogeneous transform.
    Args:
        T (np.ndarray): 4x4 transformation matrix
    Returns:
        np.ndarray: inv(T)
    """
    T2 = T.copy()
    T2[:3, :3] = T2[:3, :3].transpose()
    T2[:3, 3:] = -1 * T2[:3, :3] @ T2[:3, 3:]
    return T2


def enforce_orthog(T, dim=3):
    """Enforces orthogonality of a 3x3 rotation matrix within a 4x4 homogeneous transformation matrix.
    Args:
        T (np.ndarray): 4x4 transformation matrix
        dim (int): dimensionality of the transform 2==2D, 3==3D
    Returns:
        np.ndarray: 4x4 transformation matrix with orthogonality conditions on the rotation matrix enforced.
    """
    if dim == 2:
        if abs(np.linalg.det(T[0:2, 0:2]) - 1) < 1e-10:
            return T
        R = T[0:2, 0:2]
        epsilon = 0.001
        if abs(R[0, 0] - R[1, 1]) > epsilon or abs(R[1, 0] + R[0, 1]) > epsilon:
            print("WARNING: this is not a proper rigid transformation:", R)
            return T
        a = (R[0, 0] + R[1, 1]) / 2
        b = (-R[1, 0] + R[0, 1]) / 2
        s = np.sqrt(a**2 + b**2)
        a /= s
        b /= s
        R[0, 0] = a
        R[0, 1] = b
        R[1, 0] = -b
        R[1, 1] = a
        T[0:2, 0:2] = R
    if dim == 3:
        if abs(np.linalg.det(T[0:3, 0:3]) - 1) < 1e-10:
            return T
        c1 = T[0:3, 1]
        c2 = T[0:3, 2]
        c1 /= np.linalg.norm(c1)
        c2 /= np.linalg.norm(c2)
        newcol0 = np.cross(c1, c2)
        newcol1 = np.cross(c2, newcol0)
        T[0:3, 0] = newcol0
        T[0:3, 1] = newcol1
        T[0:3, 2] = c2
    return T


def se3ToSE3(xi):
    """Converts 6x1 vectors representing the Lie Algebra, se(3) into a 4x4 homogeneous transform in SE(3)
        Lie Vector xi = [rho, phi]^T (6 x 1) --> SE(3) T = [C, r; 0 0 0 1] (4 x 4)
    Args:
        xi (np.ndarray): 6x1 vector
    Returns:
        np.ndarray: 4x4 transformation matrix
    """
    return _vec2tran(xi)


def SE3Tose3(TIn):
    """Converts 4x4 homogeneous transforms in SE(3) to 6x1 vectors representing the Lie Algebra, se(3)
        SE(3) T = [C, r; 0 0 0 1] (4 x 4) --> Lie Vector xi = [rho, phi]^T (6 x 1)
    Args:
        T (np.ndarray): 4x4 transformation matrix
    Returns:
        np.ndarray: 6x1 vector
    """
    return _tran2vec(TIn)


def rotation_error(T):
    """Calculates a single rotation value corresponding to the upper-left 3x3 rotation matrix.
        Uses axis-angle representation to get a single number for rotation
    Args:
        T (np.ndarray): 4x4 transformation matrix T = [C, r; 0 0 0 1]
    Returns:
        float: rotation
    """
    d = 0.5 * (np.trace(T[0:3, 0:3]) - 1)
    return np.arccos(max(min(d, 1.0), -1.0))


def translation_error(T, dim=3):
    """Calculates a euclidean distance corresponding to the translation vector within a 4x4 transform.
    Args:
        T (np.ndarray): 4x4 transformation matrix T = [C, r; 0 0 0 1]
        dim (int): If dim=2 we only use x,y, otherwise we use all dims.
    Returns:
        float: translation distance
    """
    if dim == 2:
        return np.sqrt(T[0, 3] ** 2 + T[1, 3] ** 2)
    return np.sqrt(T[0, 3] ** 2 + T[1, 3] ** 2 + T[2, 3] ** 2)


def wrapto2pi(phi):
    """Ensures that the output angle phi is within the interval [0, 2*pi)"""
    if phi < 0:
        return phi + 2 * np.pi * np.ceil(phi / (-2 * np.pi))
    elif phi >= 2 * np.pi:
        return (phi / (2 * np.pi) % 1) * 2 * np.pi
    return phi


def get_time_from_filename(file):
    """Retrieves an epoch time from a file name in seconds"""
    tstr = str(Path(file).stem)
    gpstime = float(tstr)
    timeconvert = 1e-6
    if len(tstr) != 16 and len(tstr) > 10:
        timeconvert = 10 ** (-1 * (len(tstr) - 10))
    return gpstime * timeconvert


def get_time_from_filename_microseconds(file):
    tstr = str(Path(file).stem)
    gpstime = int(tstr)
    return gpstime


def get_gt_data_for_frame(root, sensType, frame):
    """Retrieves ground truth applanix data for a given sensor frame
    Args:
        root (str): path to the sequence root
        sensType (str): [camera, lidar, or radar]
        frame (str): name/timestampd of the given sensor frame (without the extension)
    Returns:
        gt (list): A list of ground truth values from the applanix sensor_poses.scv
    """
    posepath = osp.join(root, "applanix", sensType + "_poses.csv")
    with open(posepath, "r") as f:
        f.readline()  # header
        for line in f:
            if line.split(",")[0] == frame:
                return [float(x) for x in line.split(",")]
    assert 0, "gt not found for root: {} sensType: {} frame: {}".format(
        root, sensType, frame
    )
    return None


def get_closest_index(query, targets):
    """Retrieves the index of the element in targets that is closest to query O(log n)
    Args:
        query (float): query value
        targets (list): Sorted list of float values
    Returns:
        idx (int): index of the closest element in the array to x
    """
    idx = bisect_left(targets, query)
    if idx >= len(targets):
        idx = len(targets) - 1
    d = abs(targets[idx] - query)

    # check if index above or below is closer to query
    if targets[idx] < query and idx < len(targets) - 1:
        if abs(targets[idx + 1] - query) < d:
            return idx + 1
    elif targets[idx] > query and idx > 0:
        if abs(targets[idx - 1] - query) < d:
            return idx - 1
    return idx


def get_closest_frame(query_time, frame_times, frames):
    """Retrives the closest frame to query_time
    Args:
        query_time (float)
        frame_times (list): list of timestamps which corresponds to the frames list
        frames: (list): list of frames
    Returns:
        closest_frame (SensorType)
    """
    closest = get_closest_index(query_time, frame_times)
    assert abs(query_time - frame_times[closest]) < 3.0, "query: {}".format(query_time)
    return frames[closest]


def is_sorted(x):
    """Returns True is x is a sorted list, otherwise False"""
    return (np.diff(x) >= 0).all()


def get_T_bev_metric(resolution, width):
    alpha = 1 / resolution
    if (width % 2) == 0:
        min_range = width / 2 - 0.5
    else:
        min_range = width // 2
    return np.array(
        [
            [0, alpha, 0, min_range],
            [-alpha, 0, 0, min_range],
            [0, 0, 1, 0],
            [0, 0, 0, 1],
        ]
    )
