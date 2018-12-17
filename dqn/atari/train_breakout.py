import argparse
import os
import sys
import numpy as np
import tensorflow as tf
sys.path.append('..')
sys.path.append('../..')
try:
    sys.path.remove('/home/alberto/baselines')
except:
    print("")
from baselines import weighted_deepq
from baselines import bench
from baselines import logger
from baselines.common.atari_wrappers import make_atari
from eval_policy import eval_atari

def main():
    np.random.seed()

    # Argument parser
    parser = argparse.ArgumentParser()
    arg_utils = parser.add_argument_group('Utils')
    arg_utils.add_argument("--device", type=int, default=3,
                           help='Index of the GPU.')
    arg_utils.add_argument("--verbose", action='store_true')
    arg_utils.add_argument("--interactive", action='store_true')

    arg_alg = parser.add_argument_group('Algorithm')
    arg_alg.add_argument("--name",
                         default='BreakoutNoFrameskip-v4',
                         help='Atari game to play')
    arg_alg.add_argument("--mean_update", action='store_true')
    arg_alg.add_argument("--particle", action='store_true')
    arg_utils.add_argument("--k", type=int, default=10,
                           help='Number of particles in the particle algorithm')
    arg_alg.add_argument("--double_networks", action='store_true')
    arg_opt = parser.add_argument_group('Optimizer')
    arg_opt.add_argument("--lr_q", type=float, default=1e-4,
                           help='lr for q function.')
    arg_opt.add_argument("--q_max", type=float, default=100,
                         help='lr for q function.')
    arg_opt.add_argument("--lr_sigma", type=float, default=1e-7,
                           help='lr for sigma function.')
    arg_opt.add_argument("--sigma_weight", type=float, default=0.5,
                         help='Weight of sigma in loss function')
    arg_opt.add_argument("--momentum", type=float, default=0.9,
                         help='Momentum of MomentumOptimizer')
    arg_opt.add_argument("--optimizer",
                          choices=[
                              "Adam",
                              "SGD",
                              "Momentum",
                              "RmsProp"
                              ],
                          default='Adam',
                          help='Optimizer for the sigma variables')

    args = parser.parse_args()
    os.environ["CUDA_DEVICE_ORDER"] = "PCI_BUS_ID"  # see issue #152
    os.environ["CUDA_VISIBLE_DEVICES"] = str(args.device)
    logger.configure()
    env = make_atari(args.name)
    env = bench.Monitor(env, logger.get_dir())
    env = weighted_deepq.wrap_atari_dqn(env)
    e = make_atari(args.name)
    eval_env = weighted_deepq.wrap_atari_dqn(e, episode_life=False)

    def eval_policy_closure(**args):
        return eval_atari(eval_env, **args)

    algorithm_params = dict(env=env,
                            network="conv_only",
                            lr_q=args.lr_q,
                            optimizer=args.optimizer,
                            momentum=args.momentum,
                            total_timesteps=int(2e8),
                            batch_size=32,
                            buffer_size=100000,
                            exploration_fraction=0.1,
                            exploration_final_eps=0.01,
                            train_freq=4,
                            learning_starts=100000,
                            eval_freq=250000,
                            eval_timesteps=65000,
                            verbose=args.verbose,
                            interactive=args.interactive,
                            eval_policy=eval_policy_closure,
                            target_network_update_freq=10000,
                            gamma=0.99,
                            convs=[(32, 8, 4), (64, 4, 2), (64, 3, 1)],
                            hiddens=[512],
                            dueling=False,
                            weighted_update=not args.mean_update,
                            checkpoint_path="deepq_logs/" + args.name + "/" +
                                            ("mean_update" if args.mean_update else "weighted_update") +
                                            "/" + ("particle" if args.particle else "gaussian")
                            )
    if args.particle:
        learn_func = weighted_deepq.particle_learn
        algorithm_params = dict(k=args.k,
                                **algorithm_params)
    else:
        learn_func = weighted_deepq.learn
        algorithm_params = dict(lr_sigma=args.lr_sigma,
                                sigma_weight=args.sigma_weight,
                                double_network=args.double_networks,
                                q_max=args.q_max,
                                **algorithm_params)
    model = learn_func(**algorithm_params)

    model.save('breakout_model.pkl')
    env.close()

if __name__ == '__main__':
    main()