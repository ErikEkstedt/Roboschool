'''
Here are the models for the "Coordination" module.

The Policy class contains relevant functions for all policies in this
PPO implementation (act, sample, evaluate_actions).

The MLPPolicy is an MLP/fully connected network (S,St) -> V, A, A_std
The CNNPolicy is an ConvNet. (O,Ot) -> V, A, A_std
'''
import copy
import math
import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.optim as optim
from torch.autograd import Variable

from functools import reduce
import operator

from gesture.utils.utils import Conv2d_out_shape, ConvTranspose2d_out_shape

def total_params(p):
    n = 1
    for i in p:
        n *= int(i)
    return n

def weights_init_mlp(m):
    classname = m.__class__.__name__
    if classname.find('Linear') != -1:
        m.weight.data.normal_(0, 1)
        m.weight.data *= 1 / torch.sqrt(
            m.weight.data.pow(2).sum(1, keepdim=True))

        if m.bias is not None:
            m.bias.data.fill_(0)


class Policy(object):
    """ Super Class for Policies
    Functions:

    : evaluate_actions : In(s_t, actions), Out(value, a_logprobs, dist_entropy)
    : sample           : In(s_t), Out(value, a, a_logprobs, a_logstd)
    : act              : In(s_t), Out(value, action)
    : std              : In(x), Out(logstd)
    : get_std          : In( ), Out(std)

    Superclass for the different policies (CNN/MLP) containing common funcs.
    """
    def evaluate_actions(self, s, s_target, o , o_target, actions):
        ''' requires_Grad=True for all in training'''
        actions = Variable(actions)
        o, o_target = Variable(o), Variable(o_target)
        s, s_target = Variable(s), Variable(s_target)
        v, action_mean, action_logstd = self(s, s_target, o, o_target)
        action_std = action_logstd.exp()

        # calculate `old_log_probs` directly in exploration.
        action_log_probs = -0.5 * ((actions - action_mean) / action_std).pow(2)\
            - 0.5 * math.log(2 * math.pi) - action_logstd
        action_log_probs = action_log_probs.sum(1, keepdim=True)

        dist_entropy = 0.5 + math.log(2 * math.pi) + action_log_probs
        dist_entropy = dist_entropy.sum(-1).mean()
        return v, action_log_probs, dist_entropy

    def sample(self, s, s_target, o, o_target):
        ''' volatile input here during exploration. We want gradients at training'''
        s, s_target = Variable(s, volatile=True), Variable(s_target, volatile=True)
        o, o_target = Variable(o, volatile=True), Variable(o_target, volatile=True)

        v, action_mean, action_logstd = self(s, s_target, o, o_target)
        action_std = action_logstd.exp()

        noise = Variable(torch.randn(action_std.size()))
        if action_mean.is_cuda:
            noise = noise.cuda()
        action = action_mean +  action_std * noise

        # calculate `old_log_probs` directly in exploration.
        action_log_probs = -0.5 * ((action - action_mean) / action_std).pow(2)\
            - 0.5 * math.log(2 * math.pi) - action_logstd
        action_log_probs = action_log_probs.sum(1, keepdim=True)

        dist_entropy = 0.5 + math.log(2 * math.pi) + action_log_probs
        dist_entropy = dist_entropy.sum(-1).mean()
        return v, action, action_log_probs, action_std

    def act(self, s, s_target, o , o_target):
        o, o_target = Variable(o, volatile=True), Variable(o_target, volatile=True)
        s, s_target = Variable(s, volatile=True), Variable(s_target, volatile=True)
        v, action, _ = self(s, s_target, o, o_target)
        return v, action


class MLPPolicy(nn.Module, Policy):
    def __init__(self, input_size, a_shape, args):
        super(MLPPolicy, self).__init__()
        self.fc1 = nn.Linear(input_size, args.hidden)
        self.fc2 = nn.Linear(args.hidden, args.hidden)

        self.value = nn.Linear(args.hidden, 1)
        self.action = nn.Linear(args.hidden, a_shape)
        self.train()

        self.n         = 0
        self.total_n   = args.num_frames
        self.std_start = args.std_start
        self.std_stop  = args.std_stop

    def forward(self, s, st, o=None, ot=None):
        s_cat = torch.cat((s, st), dim=1)
        x = F.tanh(self.fc1(s_cat))
        x = F.tanh(self.fc2(x))
        v = self.value(x)
        ac_mean = self.action(x)
        ac_std = self.std(ac_mean)  #std annealing
        return v, ac_mean, ac_std

    def std(self, x):
        ''' linearly decreasing standard deviation '''
        ratio = self.n/self.total_n
        self.log_std_value = self.std_start - (self.std_start - self.std_stop)*ratio
        std = torch.FloatTensor([self.log_std_value])
        ones = torch.ones(x.data.size())
        if x.is_cuda:
            std = std.cuda()
            ones=ones.cuda()
        std = std*ones
        std = Variable(std)
        return std

    def get_std(self):
        return math.exp(self.log_std_value)
    def total_parameters(self):
        p = 0
        for parameter in self.parameters():
            tmp_params = reduce(operator.mul, parameter.shape)
            p += tmp_params
        return p


class CNNPolicy(nn.Module, Policy):
    def __init__(self, input_shape=(6, 40, 40),
                 action_shape=2,
                 in_channels=3,
                 feature_maps=[128, 64, 8],
                 kernel_sizes=[5, 5, 5],
                 strides=[2, 2, 2],
                 args=None):
        super(CNNPolicy, self).__init__()
        self.input_shape  = input_shape
        self.action_shape = action_shape
        self.feature_maps = feature_maps
        self.kernel_sizes = kernel_sizes
        self.strides      = strides

        self.conv1          = nn.Conv2d(input_shape[0], feature_maps[0], kernel_size  = kernel_sizes[0], stride = strides[0])
        self.out_shape1     = Conv2d_out_shape(self.conv1, input_shape)
        self.conv2          = nn.Conv2d(feature_maps[0], feature_maps[1], kernel_size = kernel_sizes[1], stride = strides[1])
        self.out_shape2     = Conv2d_out_shape(self.conv2, self.out_shape1)
        self.conv3          = nn.Conv2d(feature_maps[1], feature_maps[2], kernel_size = kernel_sizes[2], stride = strides[2])
        self.out_shape3     = Conv2d_out_shape(self.conv3, self.out_shape2)
        self.total_conv_out = total_params(self.out_shape3)

        self.fc1       = nn.Linear(self.total_conv_out, args.hidden)
        self.value     = nn.Linear(args.hidden, 1)
        self.action    = nn.Linear(args.hidden, action_shape)
        self.train()

        self.n         = 0
        self.total_n   = args.num_frames
        self.std_start = args.std_start
        self.std_stop  = args.std_stop

    def forward(self, x):
        x = F.relu(self.conv1(x))
        x = F.relu(self.conv2(x))
        x = F.relu(self.conv3(x))
        x = x.view(x.size(0), -1)
        x = F.tanh(self.fc1(x))
        v = self.value(x)
        ac_mean = self.action(x)
        ac_std = self.std(ac_mean)  #std annealing
        return v, ac_mean, ac_std


# Understanding
class VanillaCNN(nn.Module):
    ''' Simple CNN model RGB -> state
    - 3 Conv w/ stride 2
    - MLP Hidden layer
    - MLP output
    '''
    def __init__(self,
                 input_shape=(3,100,100),
                 s_shape=4,
                 feature_maps=[16, 32, 64],
                 kernel_sizes=[5, 5, 5],
                 strides=[2, 2, 2],
                 args=None):
        super(VanillaCNN, self).__init__()
        self.input_shape    = input_shape
        self.s_shape        = s_shape
        self.feature_maps   = feature_maps
        self.kernel_sizes   = kernel_sizes
        self.strides        = strides

        self.conv1        = nn.Conv2d(input_shape[0], feature_maps[0], kernel_size  = kernel_sizes[0], stride = strides[0])
        self.out_shape1   = Conv2d_out_shape(self.conv1, input_shape)
        self.conv2        = nn.Conv2d(feature_maps[0], feature_maps[1], kernel_size = kernel_sizes[1], stride = strides[1])
        self.out_shape2   = Conv2d_out_shape(self.conv2, self.out_shape1)
        self.conv3        = nn.Conv2d(feature_maps[1], feature_maps[2], kernel_size = kernel_sizes[2], stride = strides[2])
        self.out_shape3   = Conv2d_out_shape(self.conv3, self.out_shape2)
        self.n_out        = total_params(self.out_shape3)
        self.head           = nn.Linear(self.n_out, args.hidden)
        self.out            = nn.Linear(args.hidden, s_shape)

    def forward(self, x):
        x = F.relu(self.conv1(x))
        x = F.relu(self.conv2(x))
        x = F.relu(self.conv3(x))
        x = x.view(x.size(0), -1)
        x = F.relu(self.head(x))
        return self.out(x)


# ========= Tests ================
def test_CNNPolicy(args):
    ''' WORKS '''
    from gesture.environments.utils import rgb_tensor_render
    from gesture.agent.memory import StackedObs
    import numpy as np

    ob_shape = (64,64,3)
    ac_shape = 2
    CurrentObs = StackedObs(args.num_proc, args.num_stack, ob_shape)

    pi = CNNPolicy(input_shape=CurrentObs.obs_shape,
                   action_shape=ac_shape,
                   in_channels=3,
                   feature_maps=[64, 64, 64],
                   kernel_sizes=[5, 5, 5],
                   strides=[2, 2, 2],
                   args=args)


    obs = np.random.rand(*(args.num_proc,*ob_shape))*255  # env returns numpy
    CurrentObs.update(obs)

    print('-'*55)
    print('\nIN:\n', CurrentObs().size())

    if True:
        CurrentObs.cuda()
        pi.cuda()

    v, action_mean, action_logstd, action_std = pi.sample(CurrentObs())
    print('OUT:\n')
    print('Value:\n'         , v.size())
    print('Action_mean:\n'   , action_mean.size())
    print('Action_logstd:\n' , action_logstd.size())
    print('Action_std:\n'    , action_std.size())
    if False:
        print('\n\nDATA:')
        print('Value:\n'         , v)
        print('Action_mean:\n'   , action_mean)
        print('Action_logstd:\n' , action_logstd)
        print('Action_std:\n'    , action_std)

def test_MLPPolicy(args):
    ''' WORKS '''
    from gesture.environments.utils import rgb_tensor_render
    from gesture.agent.memory import StackedState
    from gesture.environments.social import Social
    import numpy as np
    args.num_proc = 1

    env = Social(args)
    env.seed(args.seed)

    o_shape = env.observation_space.shape
    s_shape = env.state_space.shape[0]
    print(s_shape)
    a_shape = env.action_space.shape[0]
    env.set_target([np.array(s_shape), np.array((2,2,2))])

    CurrentState = StackedState(1, args.num_stack, s_shape)
    pi = MLPPolicy(CurrentState.state_shape, a_shape, args)
    optimizer_pi = optim.Adam(pi.parameters(), lr=args.pi_lr)

    if args.cuda:
        CurrentState.cuda()
        pi.cuda()

    s, st, o ,ot = env.reset()
    print(s.shape)
    for i in range(100):
        CurrentState.update(s)
        # v, action_mean, action_logstd, action_std = pi.sample(CurrentState())
        value, action, action_log_prob, a_std = pi.sample(CurrentState())
        if args.num_proc == 1:
            cpu_actions = action.data.cpu().numpy()[0]
        else:
            cpu_actions = action.data.squeeze(1).cpu().numpy()


        s, st, o, ot, r, d, _ = env.step(cpu_actions)
        if args.verbose:
            print('state:', s)


    print('OUT:\n')
    print('Value:\n'         , value.size())
    print('Action_mean:\n'   , action.size())
    print('Action_logstd:\n' , a_std.size())
    print('Action_std:\n'    , action_log_prob.size())
    if False:
        print('\n\nDATA:')
        print('Value:\n'         , v)
        print('Action_mean:\n'   , action_mean)
        print('Action_logstd:\n' , action_logstd)
        print('Action_std:\n'    , action_std)

def test_roboschool(args):
    """test using roboschool """
    import roboschool
    import gym
    import numpy as np

    env_id = 'RoboschoolReacher-v1'
    env = gym.make(env_id)

    ob_shape = env.observation_space.shape[0]
    ac_shape = env.action_space.shape[0]
    pi = MLPPolicy(ob_shape, ac_shape, args)
    print(pi)

    s = env.reset()
    s = torch.from_numpy(s).float()
    for i in range(100):
        print('s: ', s)
        # obs_stats.observes(s)
        # s = obs_stats.normalize(s)
        # print('s_norm: ',s)
        input('Press Enter to continue')
        v, ac, ac_log_probs, ac_std = pi.sample(s)
        print(ac)
        s, r, done, _ = env.step(ac[0].data.numpy())
        s = torch.from_numpy(s).float()
        print(r)

if __name__ == '__main__':
    from gesture.utils.arguments import get_args
    args = get_args()
    # test_CNNPolicy(args)
    test_MLPPolicy(args)
