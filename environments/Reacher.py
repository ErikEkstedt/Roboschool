from roboschool.scene_abstract import Scene, SingleRobotEmptyScene
import os
import numpy as np
import gym
from itertools import count

# from OpenGL import GL # fix for opengl issues on desktop  / nvidia
from OpenGL import GLE # fix for opengl issues on desktop  / nvidia
try:
    # from environments.gym_env import MyGymEnv
    from environments.reacher_envs import Base
except:
    # from gym_env import MyGymEnv
    from reacher_envs import Base

PATH_TO_CUSTOM_XML = "/home/erik/com_sci/Master_code/Project/environments/xml_files"

# Target functions
def plane_target(r0, r1, x0=0, y0=0, z0=0.41):
    ''' circle in xy-plane'''
    theta = 2 * np.pi * np.random.rand()
    x = x0 + r0*np.cos(theta)
    y = y0 + r0*np.sin(theta)
    z = z0
    theta = 2 * np.pi * np.random.rand()
    x1 = x + r1*np.cos(theta)
    y1 = y + r1*np.sin(theta)
    z1 = z
    return [x, y, z, x1, y1, z1]

def sphere_target(r0, r1, x0=0, y0=0, z0=0.41):
    ''' free targets in 3d space '''
    theta = np.pi * np.random.rand()
    phi = 2 * np.pi * np.random.rand()
    x = x0 + r0*np.sin(theta)*np.cos(phi)
    y = y0 + r0*np.sin(theta)*np.sin(phi)
    z = z0 + r0*np.cos(theta)
    theta = np.pi * np.random.rand()
    phi = 2 * np.pi * np.random.rand()
    x1 = x + r1*np.sin(theta)*np.cos(phi)
    y1 = y + r1*np.sin(theta)*np.sin(phi)
    z1 = z + r1*np.cos(theta)
    return [x, y, z, x1, y1, z1]


# Reward functions
def calc_reward(self, a):
    ''' Reward function '''
    # Distance Reward
    potential_old = self.potential
    self.potential = self.calc_potential()
    r1 = self.reward_constant1 * float(self.potential[0] - potential_old[0]) # elbow
    r2 = self.reward_constant2 * float(self.potential[1] - potential_old[1]) # hand

    # Cost
    electricity_cost  = self.electricity_cost * float(np.abs(a*self.joint_speeds).mean())  # let's assume we have DC motor with controller, and reverse current braking
    electricity_cost += self.stall_torque_cost * float(np.square(a).mean())
    joints_at_limit_cost = float(self.joints_at_limit_cost * self.joints_at_limit)

    # Save rewards ?
    self.rewards = [r1, r2, electricity_cost, joints_at_limit_cost]
    return sum(self.rewards)

def calc_reward(self, a):
    ''' Absolute potential as reward '''
    self.potential = self.calc_potential()
    r1 = self.reward_constant1 * float(self.potential[0])
    r2 = self.reward_constant2 * float(self.potential[1])
    return r1 + r2

def calc_reward(self, a):
    ''' Difference potential as reward '''
    potential_old = self.potential
    self.potential = self.calc_potential()
    r1 = self.reward_constant1 * float(self.potential[0] - potential_old[0]) # elbow
    r2 = self.reward_constant2 * float(self.potential[1] - potential_old[1]) # hand
    return r1 + r2

def calc_reward(self, a):
    ''' Hierarchical Difference potential as reward '''
    potential_old = self.potential
    self.potential = self.calc_potential()
    r1 = 10 * float(self.potential[0] - potential_old[0]) # elbow
    r2 = 1 * float(self.potential[1] - potential_old[1]) # hand
    return r1 + r2

def calc_reward(self, a):
    ''' Hierarchical Difference potential as reward '''
    potential_old = self.potential
    self.potential = self.calc_potential()
    r1 = 1 * float(self.potential[0] - potential_old[0]) # elbow
    r2 = 10 * float(self.potential[1] - potential_old[1]) # hand
    return r1 + r2

def calc_reward(self, a):
    ''' IN PROGRESS Difference potential as reward '''
    potential_old = self.potential
    self.potential = self.calc_potential()
    r1 = float(self.potential[0] - potential_old[0]) # elbow
    r2 = float(self.potential[1] - potential_old[1]) # hand
    return r1 + r2


# Environments
class ReacherCommon():
    def robot_reset(self):
        ''' np.random for correct seed. '''
        for j in self.robot_joints.values():
            j.reset_current_position(np.random.uniform(low=-0.01, high=0.01 ), 0)
            j.set_motor_torque(0)

    def set_custom_target(self, coords):
        x, y, z, x1, y1, z1 = coords
        verbose = False
        for name, j in self.target_joints.items():
            if "0" in name:
                if "z" in name:
                    j.reset_current_position(z, 0)
                elif "x" in name:
                    j.reset_current_position(x,0)
                else:
                    j.reset_current_position(y, 0)
            else:
                if "z" in name:
                    j.reset_current_position(z1, 0)
                elif "x" in name:
                    j.reset_current_position(x1,0)
                else:
                    j.reset_current_position(y1, 0)

    def calc_to_target_vec(self):
        ''' gets hand position, target position and the vector in bewteen'''
        # Elbow target
        target_position1 = np.array(self.target_parts['target0'].pose().xyz())
        elbow_position = np.array(self.parts['robot_elbow'].pose().xyz())
        self.totarget1 = elbow_position - target_position1

        # Hand target
        target_position2 = np.array(self.target_parts['target1'].pose().xyz())
        hand_position = np.array(self.parts['robot_hand'].pose().xyz())
        self.totarget2 = hand_position - target_position2

        self.target_position = np.concatenate((target_position1, target_position2))
        self.important_positions = np.concatenate((elbow_position, hand_position))
        self.to_target_vec = np.concatenate((self.totarget1, self.totarget2),)

    def calc_state(self):
        j = np.array([j.current_relative_position()
                    for j in self.robot_joints.values()],
                    dtype=np.float32).flatten()
        self.joints_at_limit = np.count_nonzero(np.abs(j[0::2]) > 0.99)
        self.joint_positions = j[0::2]
        self.joint_speeds = j[1::2]
        self.calc_to_target_vec()  # calcs target_position, important_pos, to_target_vec
        return np.concatenate((self.target_position,
                            self.important_positions,
                            self.to_target_vec,
                            self.joint_positions,
                            self.joint_speeds),)

    def calc_potential(self):
        p1 = -self.potential_constant*np.linalg.norm(self.totarget1)
        p2 = -self.potential_constant*np.linalg.norm(self.totarget2)
        return p1, p2

    def get_rgb(self):
        self.camera_adjust()
        rgb, _, _, _, _ = self.camera.render(False, False, False) # render_depth, render_labeling, print_timing)
        rendered_rgb = np.fromstring(rgb, dtype=np.uint8).reshape( (self.VIDEO_H,self.VIDEO_W,3) )
        return rendered_rgb

    def camera_adjust(self):
        self.camera.move_and_look_at( 0.5, 0, 1, 0, 0, 0.4)


class ReacherPlane(ReacherCommon, Base):
    def __init__(self, args=None):
        Base.__init__(self,XML_PATH=PATH_TO_CUSTOM_XML,
                        robot_name='robot_arm',
                        target_name='target0',
                        model_xml='reacher/reacher_plane.xml',
                        ac=2, obs=22,
                        args=args)
        print('I am', self.model_xml)

    def robot_specific_reset(self):
        self.motor_names = ["robot_shoulder_joint_z",
                            "robot_elbow_joint"]
        self.motor_power = [100, 100]
        self.motors = [self.jdict[n] for n in self.motor_names]

        # target and potential
        self.robot_reset()
        self.target_reset()
        self.calc_to_target_vec()
        self.potential = self.calc_potential()

    def target_reset(self):
        r0, r1 = 0.2, 0.2
        x0, y0, z0 = 0, 0, 0.41
        coords = sphere_target(r0, r1, x0, y0, z0)
        coords = plane_target(r0, r1, x0, y0, z0)
        self.set_custom_target(coords)

    def calc_reward(self, a):
        ''' Hierarchical Difference potential as reward '''
        potential_old = self.potential
        self.potential = self.calc_potential()
        r1 = 1 * float(self.potential[0] - potential_old[0]) # elbow
        r2 = 10 * float(self.potential[1] - potential_old[1]) # hand
        return r1 + r2


class Reacher3D(ReacherCommon, Base):
    def __init__(self, args=None):
        Base.__init__(self,XML_PATH=PATH_TO_CUSTOM_XML,
                        robot_name='robot_arm',
                        target_name='target0',
                        model_xml='reacher/reacher_base.xml',
                        ac=3, obs=24,
                        args=args)
        print('I am', self.model_xml)

    def robot_specific_reset(self):
        self.motor_names = ["robot_shoulder_joint_z",
                            "robot_shoulder_joint_y",
                            "robot_elbow_joint"]
        self.motor_power = [100, 100, 100]
        self.motors = [self.jdict[n] for n in self.motor_names]

        # target and potential
        self.robot_reset()
        self.target_reset()
        self.calc_to_target_vec()
        self.potential = self.calc_potential()

    def target_reset(self):
        r0, r1 = 0.2, 0.2
        x0, y0, z0 = 0, 0, 0.41
        coords = sphere_target(r0, r1, x0, y0, z0)
        self.set_custom_target(coords)

    def calc_reward(self, a):
        ''' Reward function '''
        # Distance Reward
        potential_old = self.potential
        self.potential = self.calc_potential()
        r1 = self.reward_constant1 * float(self.potential[0] - potential_old[0]) # elbow
        r2 = self.reward_constant2 * float(self.potential[1] - potential_old[1]) # hand
        self.rewards = [r1,r2]
        return sum(self.rewards)


# test functions
def single_episodes(Env, args):
    env = Env(args)
    print('RGB: {}\tGravity: {}\tMAX: {}\t'.format(env.RGB, env.gravity, env.MAX_TIME))
    if args.RGB:
        s, obs = env.reset()
        print(s.shape)
        print(obs.shape)
        print(obs.dtype)
        input('Press Enter to start')
        while True:
            s, obs, r, d, _ = env.step(env.action_space.sample())
            R += r
            if d:
                s=env.reset()
    else:
        s = env.reset()
        print("jdict", env.jdict)
        print("robot_joints", env.robot_joints)
        print("motor_names" , env.motor_names)
        print("motor_power" , env.motor_power)
        print(s.shape)
        input()
        while True:
            a = env.action_space.sample()
            s, r, d, _ = env.step(a)
            print('Reward: ', r)
            if args.render: env.render()
            if d:
                s=env.reset()
                print('Target pos: ',env.target_position)


def test():
    from Agent.arguments import get_args
    args = get_args()
    # single_episodes(ReacherPlane, args)
    single_episodes(Reacher3D, args)

if __name__ == '__main__':
        test()