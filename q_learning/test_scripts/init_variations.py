import sys
import os
import argparse
import numpy as np
import warnings
import time
import random
from joblib import Parallel, delayed
from distutils.util import strtobool
sys.path.append('..')
sys.path.append('../..')
from mushroom.core import Core
from mushroom.environments.generators.taxi import generate_taxi
from mushroom.environments.gym_env import Gym
from mushroom.utils.callbacks import CollectDataset
from mushroom.utils.dataset import parse_dataset
from mushroom.utils.parameters import ExponentialDecayParameter, Parameter
from mushroom.policy.td_policy import EpsGreedy, Boltzmann
from mushroom.algorithms.value.td import QLearning, DoubleQLearning
from mushroom.utils.table import Table
from boot_q_learning import BootstrappedQLearning, BootstrappedDoubleQLearning
from particle_q_learning import ParticleQLearning, ParticleDoubleQLearning
from delayed_q_learning import DelayedQLearning


from policy import BootPolicy, WeightedPolicy, VPIPolicy, UCBPolicy
from envs.knight_quest import KnightQuest
from envs.chain import generate_chain
from envs.loop import generate_loop
from envs.river_swim import generate_river
from envs.six_arms import generate_arms
from utils.callbacks import CollectQs
import envs.knight_quest
from gym.envs.registration import register

policy_dict = {'eps-greedy': EpsGreedy,
               'boltzmann': Boltzmann,
               'weighted': WeightedPolicy,
               'boot': BootPolicy,
               'vpi': VPIPolicy,
               'ucb': UCBPolicy}
algorithms = ['particle-ql']
update_types = ['mean', 'weighted', 'optimistic']
#envs = ["Chain", "Taxi", "KnightQuest", "Loop", "RiverSwim", "SixArms"]
init_configs=["eq-spaced","q-max","borders"]
alg_to_policies = {
        "particle-ql": ['ucb'],
        "boot-ql": ["boot", "weighted"],
        "ql": ["boltzmann", "eps-greedy"]
    }
alg_to_update_types = {
        "particle-ql": ["weighted", "mean", "optimistic"],
        "boot-ql": ["weighted"],
        "ql": ["weighted"]
    }
env_to_qs = {
        "KnightQuest": (-20, 20),
        "Taxi": (0, 15),
        "Loop": (0, 40),
        "Chain": (0, 400),
        "RiverSwim": (0, 150000),
        "SixArms": (0, 200000)
    }
double_vec = [False, True]
env_to_R = {
        "KnightQuest": 1.,
        "Taxi": 7.,
        "Loop": 2.,
        "Chain": 10.,
        "RiverSwim": 10000.,
        "SixArms": 6000.,
    }
def set_global_seeds(i):
    try:
        import tensorflow as tf
    except ImportError:
        pass
    else:
        tf.set_random_seed(i)
    np.random.seed(i)
    random.seed(i)


def compute_scores(dataset, gamma):
    scores = list()
    disc_scores = list()
    lens = list()

    score = 0.
    disc_score = 0.
    episode_steps = 0
    n_episodes = 0
    for i in range(len(dataset)):
        score += dataset[i][2]
        disc_score += dataset[i][2] * gamma ** episode_steps
        episode_steps += 1
        if dataset[i][-1]:
            scores.append(score)
            disc_scores.append(disc_score)
            lens.append(episode_steps)
            score = 0.
            disc_score = 0.
            episode_steps = 0
            n_episodes += 1

    if len(scores) > 0:
        return len(dataset), np.min(scores), np.max(scores), np.mean(scores), np.std(scores), \
               np.min(disc_scores), np.max(disc_scores), np.mean(disc_scores), \
               np.std(disc_scores), np.mean(lens), n_episodes
    else:
        return len(dataset), 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0


def experiment(algorithm, name, update_mode, update_type, policy, n_approximators, q_max, q_min, lr_exp,
               file_name, out_dir, particles, R=1, m=1, collect_qs=False, seed=0):
    set_global_seeds(seed)
    print('Using seed %s' % seed)

    # MDP
    if name == 'Taxi':
        mdp = generate_taxi('../../grid.txt', horizon=5000)
        max_steps = 500000
        evaluation_frequency = 5000
        test_samples = 5000
    elif name == 'Chain':
        mdp = generate_chain(horizon=100)
        max_steps = 100000
        evaluation_frequency = 1000
        test_samples = 1000
    elif name == 'Loop':
        mdp = generate_loop(horizon=100)
        max_steps = 100000
        evaluation_frequency = 1000
        test_samples = 1000
    elif name == 'RiverSwim':
        mdp = generate_river(horizon=100)
        max_steps = 100000
        evaluation_frequency = 1000
        test_samples = 1000
    elif name == 'SixArms':
        mdp = generate_arms(horizon=100)
        max_steps = 100000
        evaluation_frequency = 1000
        test_samples = 1000
    elif name == 'KnightQuest':
        mdp = None
        try:
            mdp = Gym('KnightQuest-v0', gamma=0.99, horizon=10000)
        except:
            register(
                id='KnightQuest-v0',
                entry_point='envs.knight_quest:KnightQuest',
            )
            mdp = Gym('KnightQuest-v0', gamma=0.99, horizon=10000)
        max_steps = 100000
        evaluation_frequency = 1000
        test_samples = 1000
    else:
        raise NotImplementedError

    epsilon_test = Parameter(0)

    # Agent
    learning_rate = ExponentialDecayParameter(value=1., decay_exp=lr_exp,
                                              size=mdp.info.size)
    algorithm_params = dict(learning_rate=learning_rate)

    if algorithm == 'particle-ql':
        delta =0.1
        if policy not in ['weighted', 'vpi', 'ucb']:
            warnings.warn('Particle QL available with only vpi and weighted policies!')
            policy = 'weighted'
        if policy == 'ucb':
            pi = UCBPolicy(delta=delta, q_max=R / (1 - mdp.info.gamma))
        else:
            pi = policy_dict[policy](n_approximators=n_approximators)
        algorithm_params = dict(n_approximators=n_approximators,
                                    update_mode=update_mode,
                                    update_type=update_type,
                                    q_max=q_max,
                                    q_min=q_min,
                                    delta=delta,
                                    init_values=particles,
                                    **algorithm_params)

        agent = ParticleQLearning(pi, mdp.info, **algorithm_params)
        if policy == 'ucb':
            q = agent.approximator
            quantiles = [i * 1./(n_approximators-1) for i in range(n_approximators)]
            for p in range(n_approximators):
                if quantiles[p] >= 1-delta:
                    particle_bound = p
                    break
            def quantile_func(state, quantile):
                q_list = list()
                for i in range(n_approximators):
                    q_list.append(q.predict(state, idx=i))
                qs = np.array(q_list)
                out = np.zeros(qs.shape[1])
                out[:] = qs[particle_bound, :]
                return out

            def mu(state):
                q_list = list()
                for i in range(n_approximators):
                    q_list.append(q.predict(state, idx=i))
                qs = np.array(q_list)
                return np.mean(qs, axis=0)

            pi.set_quantile_func(quantile_func)
            pi.set_mu(mu)
        epsilon_train = Parameter(0)
    elif algorithm == 'delayed-ql':
        algorithm_params = dict(
                                R=R,
                                m=m,
                                **algorithm_params)

        agent = DelayedQLearning(mdp.info, **algorithm_params)
        pi = agent

    # Algorithm
    collect_dataset = CollectDataset()
    callbacks = [collect_dataset]
    if collect_qs:
        collect_qs_callback = CollectQs(agent.approximator)
        callbacks += [collect_qs_callback]
    core = Core(agent, mdp, callbacks)

    train_scores = []
    test_scores = []

    for n_epoch in range(1, max_steps // evaluation_frequency + 1):

        # Train
        if hasattr(pi, 'set_epsilon'):
            pi.set_epsilon(epsilon_train)
        if hasattr(pi, 'set_eval'):
            pi.set_eval(False)
        core.learn(n_steps=evaluation_frequency, n_steps_per_fit=1, quiet=True)
        dataset = collect_dataset.get()
        scores = compute_scores(dataset, mdp.info.gamma)

        # print('Train: ', scores)
        train_scores.append(scores)

        collect_dataset.clean()
        mdp.reset()

        if hasattr(pi, 'set_epsilon'):
            pi.set_epsilon(epsilon_test)
        if hasattr(pi, 'set_eval'):
            pi.set_eval(True)
        dataset = core.evaluate(n_steps=test_samples, quiet=True)
        mdp.reset()
        scores = compute_scores(dataset, mdp.info.gamma)
        # print('Evaluation: ', scores)
        test_scores.append(scores)
    if collect_qs:
        qs = collect_qs_callback.get_values()
        if not os.path.exists(out_dir):
            os.makedirs(out_dir)
        np.save(out_dir + '/' + file_name, qs)
    return train_scores, test_scores

def add_noise_experiment(alg,n_particles,envs,args):

    alpha=0
    max_alpha=1
    delta_alpha=args.delta_alpha
    for env in envs:
                for policy in alg_to_policies[alg]:
                    for update_type in update_types:
                        alpha = 0
                        while alpha<=max_alpha:
                            print('Env: %s - Alg: %s - Policy: %s - Update: %s' % (
                            env, alg, policy, update_type))
                            qs = env_to_qs[env]
                            mu = (qs[1] + qs[0]) / 2
                            sigma = qs[1] - qs[0]
                            R = env_to_R[env]
                            particle_list=[]
                            eq_spaced_particles = np.linspace(qs[0], qs[1], n_particles)
                            for exp in range(args.n_experiments):
                                noise_particles = np.random.randn(n_particles)*sigma+mu
                                particles = alpha*eq_spaced_particles+(1-alpha)*noise_particles
                                particle_list.append(particles)
                            file_name = 'qs_%s_%s_%s_%s_%s_coef=%s' % (
                            policy, n_particles,
                            update_type, args.lr_exp, time.time(),alpha)
                            out_dir = args.dir + '/' + env + '/' + alg
                            fun_args = [alg, env, args.update_mode, update_type, policy, n_particles, qs[1], qs[0],
                                    args.lr_exp, file_name, out_dir]
                            out = Parallel(n_jobs=affinity)(
                                delayed(experiment)(*(fun_args + [particle_list[i], R, 1, args.collect_qs if i == 0 else False, args.seed + i])) for
                                i in range(args.n_experiments))

                            if not os.path.exists(out_dir):
                                os.makedirs(out_dir)
                            file_name = 'results_noise_%s_%s_%s_%s_%s_coef=%s' % (
                            policy, n_particles, update_type, args.lr_exp, time.time(),alpha)
                            np.save(out_dir + '/' + file_name, out)
                            alpha+=delta_alpha

def init_variations_experiment(alg,n_particles,envs,args):
    for env in envs:
                for policy in alg_to_policies[alg]:
                    for update_type in update_types:
                        for init in init_configs:
                            print('Env: %s - Alg: %s - Policy: %s - Update: %s' % (
                            env, alg, policy, update_type))
                            qs = env_to_qs[env]
                            R = env_to_R[env]
                            if init == 'eq-spaced':
                                particles = np.linspace(qs[0], qs[1], n_particles)
                            elif init == 'q-max':
                                particles = np.tile(qs[1], n_particles)
                            elif init == 'borders':
                                particles = np.concatenate((np.tile(qs[0], int(n_particles / 2)),
                                                            np.tile(qs[1], n_particles- int(n_particles / 2))))
                            file_name = 'qs_%s_%s_%s_%s_%s_init=%s' % (
                            policy, n_particles,
                            update_type, args.lr_exp, time.time(),init)
                            out_dir = args.dir + '/' + env + '/' + alg
                            fun_args = [alg, env, args.update_mode, update_type, policy, n_particles, qs[1], qs[0],
                                    args.lr_exp, file_name, out_dir,particles,R,1]
                            out = Parallel(n_jobs=affinity)(
                                delayed(experiment)(*(fun_args + [args.collect_qs if i == 0 else False, args.seed + i])) for
                                i in range(args.n_experiments))

                            if not os.path.exists(out_dir):
                                os.makedirs(out_dir)
                            file_name = 'results_prior_%s_%s_%s_%s_%s_init=%s' % (
                            policy, n_particles, update_type, args.lr_exp, time.time(),init)
                            np.save(out_dir + '/' + file_name, out)

def delayed_q_experiment(alg,envs,args):

    min_m = args.min_m
    max_m = args.max_m
    delta_m = args.delta_m
    for env in envs:
                        m = min_m
                        while m <= max_m:
                            print('Env: %s - Alg: %s - m:%s' % (
                            env, alg, str(m)))
                            qs = env_to_qs[env]
                            R = env_to_R[env]
                            out_dir = args.dir + '/' + env + '/' + alg
                            file_name = 'qs_m=%s_%s' % (m, time.time())
                            fun_args = [alg, env, args.update_mode, 'delayed', 'delayed', 1, qs[1], qs[0],
                                    args.lr_exp, file_name, out_dir, np.ones(10), R, m]
                            out = Parallel(n_jobs=affinity)(
                                delayed(experiment)(*(fun_args + [args.collect_qs if i == 0 else False, args.seed + i])) for
                                i in range(args.n_experiments))
                            #out = [experiment(*(fun_args + [False, 0]))]
                            if not os.path.exists(out_dir):
                                os.makedirs(out_dir)
                            file_name = 'results_delayed_m=%s_%s' % (m, time.time())
                            np.save(out_dir + '/' + file_name, out)
                            m += delta_m
if __name__ == '__main__':

    parser = argparse.ArgumentParser()

    arg_game = parser.add_argument_group('Game')
    arg_game.add_argument("--name",
                          default='',
                          help='Name of the environment to test.')

    arg_alg = parser.add_argument_group('Algorithm')
    arg_alg.add_argument("--algorithm",
                         choices=[
                             'ql',
                             'boot-ql',
                             'particle-ql',
                             ''],
                         default='',
                         help='The algorithm.')
    arg_alg.add_argument("--update-mode",
                         choices=['deterministic', 'randomized'],
                         default='deterministic',
                         help='Whether to perform randomized or deterministic target update (only ParticleQLearning).')
    arg_alg.add_argument("--update-type",
                         choices=['mean','weighted', 'optimistic'],
                         default='',
                         help='Kind of update to perform (only ParticleQLearning).')
    arg_alg.add_argument("--policy",
                         choices=['weighted', 'vpi', 'boot', 'boltzmann', 'eps-greedy', 'ucb'],
                         default='',
                         help='Kind of policy to use (not all available for all).')
    arg_alg.add_argument("--n-approximators", type=int, default=20,
                         help="Number of approximators used in the ensemble.")
    arg_alg.add_argument("--q-max", type=float, default=40,
                         help='Upper bound for initializing the heads of the network (only ParticleQLearning).')
    arg_alg.add_argument("--q-min", type=float, default=0,
                         help='Lower bound for initializing the heads of the network (only ParticleQLearning).')
    arg_alg.add_argument("--lower-internval", action='store_true',
                         help='Initialize particles in lower bound or upper bound')
    arg_alg.add_argument("--lr-exp", type=float, default=0.2,
                         help='Exponential decay for lr')
    arg_alg.add_argument("--double", type=str, default='',
                         help='Whether to use double.')
    arg_run = parser.add_argument_group('Run')
    arg_run.add_argument("--n-experiments", type=int, default=10,
                         help='Number of experiments to execute.')
    arg_run.add_argument("--dir", type=str, default='./noise_data',
                         help='Directory where to save data.')
    arg_run.add_argument("--seed", type=int, default=0,
                         help='Seed.')
    arg_run.add_argument("--collect-qs", action='store_true')
    arg_run.add_argument("--init-variations", action ='store_true',
                         help='Run various initializations of particles')
    arg_run.add_argument("--add-noise",action='store_true',
                         help='Add noise to equally spaced init')
    arg_run.add_argument("--delayed-q", action='store_true',
                         help='Sweep parameter m of delayed Q-learning')
    arg_run.add_argument("--delta-alpha",type=float,default=0.05,
                         help='Step of noise intensity in add noise experiment')
    arg_run.add_argument("--delta-m", type=int, default=4,
                         help='Step of m parameter in delayed-q experiment')
    arg_run.add_argument("--max-m", type=int, default=81,
                         help='Maximum m parameter in delayed-q experiment')
    arg_run.add_argument("--min-m", type=int, default=1,
                         help='Maximum m parameter in delayed-q experiment')

    args = parser.parse_args()
    n_experiment = args.n_experiments

    affinity = len(os.sched_getaffinity(0))
    envs = ["Chain", "KnightQuest", "Loop", "RiverSwim", "SixArms","Taxi"]
    if args.name != '':
        envs = str.split(args.name,',')
    print(envs)
    n_particles=args.n_approximators
    alg = 'particle-ql'

    if args.add_noise:
        add_noise_experiment(alg,n_particles,envs,args)
    elif args.init_variations:
        init_variations_experiment(alg,n_particles,envs,args)
    elif args.delayed_q:
        alg = 'delayed-ql'
        envs = ["RiverSwim", "SixArms", "Taxi", "KnightQuest", "Chain"]
        if args.name !='' and args.name in envs:
            envs = [args.name]
        delayed_q_experiment(alg, envs, args)



