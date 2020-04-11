import numpy as np
import scipy as sp
from quaternion import from_rotation_matrix, quaternion

from rlbench.environment import Environment
from rlbench.action_modes import ArmActionMode, ActionMode
from rlbench.observation_config import ObservationConfig
from rlbench.tasks import *

from pyrep.const import ConfigurationPathAlgorithms as Algos


def skew(x):
    return np.array([[0, -x[2], x[1]],
                    [x[2], 0, -x[0]],
                    [-x[1], x[0], 0]])


def sample_normal_pose(pos_scale, rot_scale):
    '''
    Samples a 6D pose from a zero-mean isotropic normal distribution
    '''
    pos = np.random.normal(scale=pos_scale)

    eps = skew(np.random.normal(scale=rot_scale))
    R = sp.linalg.expm(eps)
    quat_wxyz = from_rotation_matrix(R)

    return pos, quat_wxyz


def noisy_object(pose):
    _pos_scale = [0.005] * 3
    _rot_scale = [0.01] * 3
    pos, quat_wxyz = sample_normal_pose(_pos_scale, _rot_scale)
    gt_quat_wxyz = quaternion(pose[6], pose[3], pose[4], pose[5])
    perturbed_quat_wxyz = quat_wxyz * gt_quat_wxyz
    pose[:3] += pos
    pose[3:] = [perturbed_quat_wxyz.x, perturbed_quat_wxyz.y, perturbed_quat_wxyz.z, perturbed_quat_wxyz.w]
    return pose


class RandomAgent:

    # def act(self, obs):
    #     delta_pos = [(np.random.rand() * 2 - 1) * 0.005, 0, 0]
    #     delta_quat = [0, 0, 0, 1] # xyzw
    #     gripper_pos = [np.random.rand() > 0.5] # action should contain 1 extra value for gripper open close state
    #     return delta_pos + delta_quat + gripper_pos

    def act(self, obs):
        gripper_pos = obs.gripper_pose.tolist()
        gripper_pos[0] -= 0.005
        # gripper_ori = [0, 1, 0, 0]
        gripper_status = [1]
        return gripper_pos + gripper_status


class GraspController:
    def __init__(self, action_mode):
        # Initialize environment with Action mode and observations
        self.env = Environment(action_mode, '', ObservationConfig(), False)
        self.env.launch()
        # Load specified task into the environment
        self.task = self.env.get_task(EmptyContainer)

    def reset(self):
        descriptions, obs = self.task.reset()
        return descriptions, obs

    def get_objects(self, add_noise=False):
        objs = self.env._scene._active_task.get_base().get_objects_in_tree(exclude_base=True, first_generation_only=False)
        objs_dict = {}

        for obj in objs:
            name = obj.get_name()
            pose = obj.get_pose()
            if add_noise:
                pose = noisy_object(pose)
            objs_dict[name] = [obj, pose]

        return objs_dict

    def get_path(self, pose, pad=0.01):
        # TODO deal with situations when path not found
        target_pose = np.copy(pose)
        target_pose[2] -= pad
        path = self.env._robot.arm.get_path(target_pose[:3], quaternion=np.array([0, 1, 0, 0]),
                                            ignore_collisions=True, algorithm=Algos.RRTConnect, trials=1000)
        return path

    def grasp(self, obj):
        # TODO get feedback to check if grasp is successfull
        done_grab_action = False
        succ_grabbed = False
        while not succ_grabbed:
            # Repeat unitil successfully grab the object
            while not done_grab_action:
                # gradually close the gripper
                done_grab_action = self.env._robot.gripper.actuate(0, velocity=0.2)  # 0 is close
                self.env._pyrep.step()
                self.task._task.step()
                self.env._scene.step()
            succ_grabbed = self.env._robot.gripper.grasp(obj)
        return succ_grabbed

    def release(self):
        done = False
        while not done:
            done = self.env._robot.gripper.actuate(1, velocity=0.2)  # 1 is release
            self.env._pyrep.step()
            self.task._task.step()
            self.env._scene.step()
        self.env._robot.gripper.release()

    def execute_path(self, path, open_gripper=True):
        path = path._path_points.reshape(-1, path._num_joints)
        for i in range(len(path)):
            action = list(path[i]) + [int(open_gripper)]
            obs, reward, terminate = self.task.step(action)
        return obs, reward, terminate

        ### The following codes can work as well ###
        # done = False
        # path.set_to_start()
        # while not done:
        #     done = path.step()
        #     a = path.visualize()
        #     self.env._scene.step()
        # return done


if __name__ == "__main__":

    # Set Action Mode, See rlbench/action_modes.py for other action modes
    action_mode = ActionMode(ArmActionMode.ABS_JOINT_POSITION)
    # Create grasp controller with initialized environment and task
    grasp_controller = GraspController(action_mode)
    # Reset task
    descriptions, obs = grasp_controller.reset()

    objects = ['Shape', 'Shape1', 'Shape3']

    for object in objects:
        # Getting object poses, noisy or not
        # TODO detect the pose using vision and handle the noisy pose
        objs = grasp_controller.get_objects(add_noise=True)
        pose = objs[object][1]
        # Getting the path of reaching the target position
        path = grasp_controller.get_path(pose)
        # Execute the path
        obs, reward, terminate = grasp_controller.execute_path(path, open_gripper=True)

        # grasp the object
        grabbed = grasp_controller.grasp(objs[object][0])
        print('The object is grabbed?', grabbed)

        # TODO get feedback to check if grasp is successfull

        # move to home position
        pose = objs['waypoint0'][1]
        path = grasp_controller.get_path(pose, pad=0)
        obs, reward, terminate = grasp_controller.execute_path(path, open_gripper=False)

        # move above small container
        pose = objs['waypoint3'][1]
        path = grasp_controller.get_path(pose, pad=0)
        obs, reward, terminate = grasp_controller.execute_path(path, open_gripper=False)

        # release the object
        grasp_controller.release()

        # go back to home position
        pose = objs['waypoint0'][1]
        path = grasp_controller.get_path(pose, pad=0)
        obs, reward, terminate = grasp_controller.execute_path(path, open_gripper=True)

        # TODO check if any object is left in the large container and grasp again (using vision)

        #### Getting various fields from obs ####
        # current_joints = obs.joint_positions
        # gripper_pose = obs.gripper_pose
        # rgb = obs.wrist_rgb
        # depth = obs.wrist_depth
        # mask = obs.wrist_mask

    # TODO reset the task
