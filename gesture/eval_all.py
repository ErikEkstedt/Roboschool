'''
Loads a state dict, a target dataset and runs simulations.
example:
    python enjoy.py --render --MAX_TIME=3000

    python enjoy.py --record --MAX_TIME=3000

    python enjoy.py --render --MAX_TIME=3000 \
        --target-path=/PATH/to/target_data_set/ \
        --state-dict-path=/PATH/to/state_dict
'''
import numpy as np
import os
import time
import matplotlib.pyplot as plt
from itertools import count
from tqdm import tqdm
import torch
from torch.autograd import Variable

from gesture.utils.arguments import get_args
from gesture.utils.utils import record, load_dict, get_targets, get_model
from gesture.environments.utils import env_from_args
from gesture.agent.memory import Current, Targets
from gesture.models.modular import VanillaCNN

class PoseDefiner(object):
    def __init__(self, thresh=0.1, done_duration=50, max_time=300, target=None):
        self.thresh = thresh
        self.done_duration = done_duration
        self.max_time = max_time
        self.target = target

        self.counter = 0
        self.time = 0
        self.poses_achieved = 0
        self.total_poses = 1

    def reset(self, target):
        self.counter = 0
        self.time = 0
        self.target = target
        self.total_poses += 1

    def update(self, state):
        self.time += 1
        change_target = False
        dist = np.linalg.norm(state[:len(self.target)] - self.target)
        if dist < self.thresh:
            # Pose reached!
            self.counter += 1
            if self.counter >= self.done_duration:
                # Pose achieved!
                self.poses_achieved += 1
                change_target = True
        else:
            self.counter = 0
            if self.time > self.max_time:
                change_target = True
        return dist, change_target

    def distance(self, state):
        return np.linalg.norm(state[:len(self.target)] - self.target)

    def print_result(self):
        print('\nPoses reached/possible: {}/{}'.format(self.poses_achieved, self.total_poses))


def evaluate(env, targets, pi, understand, args, plot=False, USE_UNDERSTAND=False):
    if args.cuda:
        current.cuda()
        pi.cuda()
        if USE_UNDERSTAND:
            understand.cuda()

    if args.use_understand:
        name = "{}_frames{}_understand".format(args.model, args.MAX_TIME)
    else:
        name = "{}_frames{}".format(args.model, args.MAX_TIME)

    if args.record:
        import skvideo.io
        vidname = name+'.mp4'
        writer = skvideo.io.FFmpegWriter(os.path.join(args.log_dir,vidname))

    if args.continuous_targets:
        target = targets[0]
        t = 1
    else:
        target = targets()

    env.set_target(target)
    state, real_state_target, obs, o_target = env.reset()

    posedefiner = PoseDefiner(target=real_state_target, max_time=args.update_target)
    d = posedefiner.distance(state)
    X = [0]; Y = [d]

    tt = time.time()
    ep_rew, total_reward = 0, 0
    for j in tqdm(range(args.MAX_TIME)):

        if USE_UNDERSTAND:
            o_target = o_target.transpose(2, 0, 1).astype('float')
            o_target /= 255
            o_target = torch.from_numpy(o_target).float().unsqueeze(0)
            if args.cuda: o_target = o_target.cuda()
            s_target = understand(Variable(o_target)).data.cpu().numpy()
        else:
            s_target = real_state_target

        current.update(state, s_target, obs, o_target)
        s ,st, o, ot = current()
        value, action = pi.act(s, st, o, ot)

        if args.render:
            env.render('human')
            env.render('target')

        if args.record:
            record(env, writer)

        # Observe reward and next state
        cpu_actions = action.data.cpu().numpy()[0]
        state, real_state_target, obs, o_target, reward, done, info = env.step(cpu_actions)
        total_reward += reward
        ep_rew += reward

        d, pose_done = posedefiner.update(state)
        Y.append(d)
        X.append(j)
        if plot:
            plt.plot(X,Y,'-b', X, [0.1]*len(X), '-r')
            plt.pause(1e-4)

        if pose_done:
            print('episode reward:', ep_rew)
            ep_rew = 0
            if args.continuous_targets:
                target = targets[t]
                t += 1
                if t > len(targets)-1:
                    break
            else:
                target = targets()
            env.set_target(target)
            state, real_state_target, obs, o_target = env.reset()
            posedefiner.reset(real_state_target)

    print('Time for enjoyment: ', time.time()-tt)
    print('Total Reward: ', total_reward)
    if args.record:
        writer.close()

    posedefiner.print_result()
    plt.plot(X,Y,'-b', X, [0.1]*len(X), '-r')
    # plt.show()

    name += str(posedefiner.poses_achieved) +'of'+ str(posedefiner.total_poses)+'.png'
    name = os.path.join(args.log_dir,name)
    print('plotname',name)
    plt.savefig(name, bbox_inches='tight')

if __name__ == '__main__':
    args = get_args()
    print('Evaluation of ', args.model)
    args.num_proc = 1

    # Create dirs
    path='/home/erik/DATA/Humanoid/eval'
    run = 0
    while os.path.exists("{}/eval-{}".format(path, run)):
        run += 1
    path = "{}/eval-{}".format(path, run)
    os.mkdir(path)
    args.log_dir = path
    args.checkpoint_dir = path

    # === Environment and targets ===
    Env = env_from_args(args)
    env = Env(args)
    env.seed(200)  # same for all tests

    print('\nLoading targets from:')
    print('path:\t', args.test_target_path)
    datadict = load_dict(args.test_target_path)
    targets = Targets(1, datadict)
    targets.remove_speed(args.njoints)

    s_target, o_target = targets()  # random
    st_shape = s_target.shape[0]  # targets
    ot_shape = o_target.shape

    s_shape = env.state_space.shape[0]    # Joints state
    o_shape = env.observation_space.shape  # RGB (W,H,C)
    ac_shape = env.action_space.shape[0]   # Actions
    current = Current(1, args.num_stack, s_shape, st_shape, o_shape, o_shape, ac_shape)
    print('state:',s_shape)
    print('target state:',st_shape)

    if 'Semimodular' in args.model:
        from gesture.models.combine import SemiCombinePolicy
        pi = SemiCombinePolicy(s_shape=current.s_shape,
                               st_shape=current.st_shape,
                               o_shape=current.o_shape,
                               ot_shape=current.ot_shape,
                               a_shape=current.ac_shape,
                               feature_maps=args.feature_maps,
                               kernel_sizes=args.kernel_sizes,
                               strides=args.strides,
                               args=args)
    elif 'Combine' in args.model:
        from gesture.models.combine import CombinePolicy
        pi = CombinePolicy(s_shape=current.s_shape,
                            st_shape=current.st_shape,
                            o_shape=current.o_shape,
                            ot_shape=current.ot_shape,
                            a_shape=current.ac_shape,
                            feature_maps=args.feature_maps,
                            kernel_sizes=args.kernel_sizes,
                            strides=args.strides,
                            args=args)
    else:
        from gesture.models.modular import MLPPolicy
        in_size = current.st_shape + current.s_shape
        pi = MLPPolicy(input_size=in_size, a_shape=current.ac_shape, args=args)

    if args.use_understand and not 'Combine' in args.model:
        args.feature_maps = [64,64,64]
        args.hidden = 128
        understand = VanillaCNN(input_shape=current.ot_shape,
                                s_shape=current.st_shape,
                                feature_maps=args.feature_maps,
                                kernel_sizes=args.kernel_sizes,
                                strides=args.strides,
                                args=args)
        print('Loading understanding state dict from:')
        print('path:\t', args.state_dict_path2)
        understand_state_dict = torch.load(args.state_dict_path2)
        understand.load_state_dict(understand_state_dict)
        understand.eval()
    else:
        understand = None

    print('Loading coordination state dict from:')
    print('path:\t', args.state_dict_path)
    pi_state_dict = torch.load(args.state_dict_path)
    pi.load_state_dict(pi_state_dict)
    pi.eval()

    evaluate(env, targets, pi, understand, args, plot=False)
