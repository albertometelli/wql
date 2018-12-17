"""Deep Q learning graph

The functions in this file can are used to create the following functions:

======= act ========

    Function to chose an action given an observation

    Parameters
    ----------
    observation: object
        Observation that can be feed into the output of make_obs_ph
    stochastic: bool
        if set to False all the actions are always deterministic (default False)
    update_eps_ph: float
        update epsilon a new value, if negative not update happens
        (default: no update)

    Returns
    -------
    Tensor of dtype tf.int64 and shape (BATCH_SIZE,) with an action to be performed for
    every element of the batch.


======= act (in case of parameter noise) ========

    Function to chose an action given an observation

    Parameters
    ----------
    observation: object
        Observation that can be feed into the output of make_obs_ph
    stochastic: bool
        if set to False all the actions are always deterministic (default False)
    update_eps_ph: float
        update epsilon to a new value, if negative no update happens
        (default: no update)
    reset_ph: bool
        reset the perturbed policy by sampling a new perturbation
    update_param_noise_threshold_ph: float
        the desired threshold for the difference between non-perturbed and perturbed policy
    update_param_noise_scale_ph: bool
        whether or not to update the scale of the noise for the next time it is re-perturbed

    Returns
    -------
    Tensor of dtype tf.int64 and shape (BATCH_SIZE,) with an action to be performed for
    every element of the batch.


======= train =======

    Function that takes a transition (s,a,r,s') and optimizes Bellman equation's error:

        td_error = Q(s,a) - (r + gamma * max_a' Q(s', a'))
        loss = huber_loss[td_error]

    Parameters
    ----------
    obs_t: object
        a batch of observations
    action: np.array
        actions that were selected upon seeing obs_t.
        dtype must be int32 and shape must be (batch_size,)
    reward: np.array
        immediate reward attained after executing those actions
        dtype must be float32 and shape must be (batch_size,)
    obs_tp1: object
        observations that followed obs_t
    done: np.array
        1 if obs_t was the last observation in the episode and 0 otherwise
        obs_tp1 gets ignored, but must be of the valid shape.
        dtype must be float32 and shape must be (batch_size,)
    weight: np.array
        imporance weights for every element of the batch (gradient is multiplied
        by the importance weight) dtype must be float32 and shape must be (batch_size,)

    Returns
    -------
    td_error: np.array
        a list of differences between Q(s,a) and the target in Bellman's equation.
        dtype is float32 and shape is (batch_size,)

======= update_target ========

    copy the parameters from optimized Q function to the target Q function.
    In Q learning we actually optimize the following error:

        Q(s,a) - (r + gamma * max_a' Q'(s', a'))

    Where Q' is lagging behind Q to stablize the learning. For example for Atari

    Q' is set to Q once every 10000 updates training steps.

"""
import tensorflow as tf
import baselines.common.tf_util as U
import numpy as np

def scope_vars(scope, trainable_only=False):
    """
    Get variables inside a scope
    The scope can be specified as a string
    Parameters
    ----------
    scope: str or VariableScope
        scope in which the variables reside.
    trainable_only: bool
        whether or not to return only the variables that were marked as trainable.
    Returns
    -------
    vars: [tf.Variable]
        list of variables in `scope`.
    """
    return tf.get_collection(
        tf.GraphKeys.TRAINABLE_VARIABLES if trainable_only else tf.GraphKeys.GLOBAL_VARIABLES,
        scope=scope if isinstance(scope, str) else scope.name
    )


def scope_name():
    """Returns the name of current scope as a string, e.g. deepq/q_func"""
    return tf.get_variable_scope().name


def absolute_scope_name(relative_scope_name):
    """Appends parent scope name to `relative_scope_name`"""
    return scope_name() + "/" + relative_scope_name


def default_param_noise_filter(var):
    if var not in tf.trainable_variables():
        # We never perturb non-trainable vars.
        return False
    if "fully_connected" in var.name:
        # We perturb fully-connected layers.
        return True

    # The remaining layers are likely conv or layer norm layers, which we do not wish to
    # perturb (in the former case because they only extract features, in the latter case because
    # we use them for normalization purposes). If you change your network, you will likely want
    # to re-consider which layers to perturb and which to keep untouched.
    return False


def build_act(make_obs_ph, q_func, num_actions, scope="deepq", reuse=None):
    """Creates the act function:

    Parameters
    ----------
    make_obs_ph: str -> tf.placeholder or TfInput
        a function that take a name and creates a placeholder of input with that name
    q_func: (tf.Variable, int, str, bool) -> tf.Variable
        the model that takes the following inputs:
            observation_in: object
                the output of observation placeholder
            num_actions: int
                number of actions
            scope: str
            reuse: bool
                should be passed to outer variable scope
        and returns a tensor of shape (batch_size, num_actions) with values of every action.
    num_actions: int
        number of actions.
    scope: str or VariableScope
        optional scope for variable_scope.
    reuse: bool or None
        whether or not the variables should be reused. To be able to reuse the scope must be given.

    Returns
    -------
    act: (tf.Variable, bool, float) -> tf.Variable
        function to select and action given observation.
`       See the top of the file for details.
    """
    with tf.variable_scope(scope, reuse=reuse):
        observations_ph = make_obs_ph("observation")
        stochastic_ph = tf.placeholder(tf.bool, (), name="stochastic")
        update_eps_ph = tf.placeholder(tf.float32, (), name="update_eps")

        eps = tf.get_variable("eps", (), initializer=tf.constant_initializer(0))
        eval_ph = tf.placeholder(tf.bool, (), name="eval_ph")

        q_values, sigma_values = q_func(observations_ph.get(), num_actions, scope="q_func")
        deterministic_actions = tf.argmax(q_values, axis=1)
        batch_size = tf.shape(observations_ph.get())[0]

        q_samples = tf.distributions.Normal(loc=q_values, scale=sigma_values).sample()
        sampled_actions = tf.argmax(q_samples, axis=1)

        output_actions = tf.cond(eval_ph, lambda: deterministic_actions, lambda: sampled_actions)
        random_actions = tf.random_uniform(tf.stack([batch_size]), minval=0, maxval=num_actions, dtype=tf.int64)
        chose_random = tf.random_uniform(tf.stack([batch_size]), minval=0, maxval=1, dtype=tf.float32) < eps
        actions = tf.where(chose_random, random_actions, output_actions)

        update_eps_expr = eps.assign(tf.cond(update_eps_ph >= 0, lambda: update_eps_ph, lambda: eps))
        ''' tf.name_scope('act_summaries'):
            with tf.name_scope('q'):
                tf.summary.scalar("q_values", q_values)
            with tf.name_scope('sigma'):
                tf.summary.scalar("sigma_values", sigma_values)
            merged = tf.summary.merge_all()'''
        _act = U.function(inputs=[observations_ph, stochastic_ph, update_eps_ph, eval_ph],
                          outputs=[q_values, sigma_values, actions, q_samples, eps],
                          givens={update_eps_ph: -1.0, stochastic_ph: True},
                          updates=[update_eps_expr])


        def act(ob, stochastic=True, update_eps=-1, eval_flag=False):

            return _act(ob, stochastic, update_eps, eval_flag)
        return act


def build_act_with_param_noise(make_obs_ph, q_func, num_actions, scope="deepq", reuse=None, param_noise_filter_func=None):
    """Creates the act function with support for parameter space noise exploration (https://arxiv.org/abs/1706.01905):

    Parameters
    ----------
    make_obs_ph: str -> tf.placeholder or TfInput
        a function that take a name and creates a placeholder of input with that name
    q_func: (tf.Variable, int, str, bool) -> tf.Variable
        the model that takes the following inputs:
            observation_in: object
                the output of observation placeholder
            num_actions: int
                number of actions
            scope: str
            reuse: bool
                should be passed to outer variable scope
        and returns a tensor of shape (batch_size, num_actions) with values of every action.
    num_actions: int
        number of actions.
    scope: str or VariableScope
        optional scope for variable_scope.
    reuse: bool or None
        whether or not the variables should be reused. To be able to reuse the scope must be given.
    param_noise_filter_func: tf.Variable -> bool
        function that decides whether or not a variable should be perturbed. Only applicable
        if param_noise is True. If set to None, default_param_noise_filter is used by default.

    Returns
    -------
    act: (tf.Variable, bool, float, bool, float, bool) -> tf.Variable
        function to select and action given observation.
`       See the top of the file for details.
    """
    if param_noise_filter_func is None:
        param_noise_filter_func = default_param_noise_filter

    with tf.variable_scope(scope, reuse=reuse):
        observations_ph = make_obs_ph("observation")
        stochastic_ph = tf.placeholder(tf.bool, (), name="stochastic")
        update_eps_ph = tf.placeholder(tf.float32, (), name="update_eps")
        update_param_noise_threshold_ph = tf.placeholder(tf.float32, (), name="update_param_noise_threshold")
        update_param_noise_scale_ph = tf.placeholder(tf.bool, (), name="update_param_noise_scale")
        reset_ph = tf.placeholder(tf.bool, (), name="reset")

        eps = tf.get_variable("eps", (), initializer=tf.constant_initializer(0))
        param_noise_scale = tf.get_variable("param_noise_scale", (), initializer=tf.constant_initializer(0.01), trainable=False)
        param_noise_threshold = tf.get_variable("param_noise_threshold", (), initializer=tf.constant_initializer(0.05), trainable=False)

        # Unmodified Q.
        q_values = q_func(observations_ph.get(), num_actions, scope="q_func")

        # Perturbable Q used for the actual rollout.
        q_values_perturbed = q_func(observations_ph.get(), num_actions, scope="perturbed_q_func")
        # We have to wrap this code into a function due to the way tf.cond() works. See
        # https://stackoverflow.com/questions/37063952/confused-by-the-behavior-of-tf-cond for
        # a more detailed discussion.
        def perturb_vars(original_scope, perturbed_scope):
            all_vars = scope_vars(absolute_scope_name(original_scope))
            all_perturbed_vars = scope_vars(absolute_scope_name(perturbed_scope))
            assert len(all_vars) == len(all_perturbed_vars)
            perturb_ops = []
            for var, perturbed_var in zip(all_vars, all_perturbed_vars):
                if param_noise_filter_func(perturbed_var):
                    # Perturb this variable.
                    op = tf.assign(perturbed_var, var + tf.random_normal(shape=tf.shape(var), mean=0., stddev=param_noise_scale))
                else:
                    # Do not perturb, just assign.
                    op = tf.assign(perturbed_var, var)
                perturb_ops.append(op)
            assert len(perturb_ops) == len(all_vars)
            return tf.group(*perturb_ops)

        # Set up functionality to re-compute `param_noise_scale`. This perturbs yet another copy
        # of the network and measures the effect of that perturbation in action space. If the perturbation
        # is too big, reduce scale of perturbation, otherwise increase.
        q_values_adaptive = q_func(observations_ph.get(), num_actions, scope="adaptive_q_func")
        perturb_for_adaption = perturb_vars(original_scope="q_func", perturbed_scope="adaptive_q_func")
        kl = tf.reduce_sum(tf.nn.softmax(q_values) * (tf.log(tf.nn.softmax(q_values)) - tf.log(tf.nn.softmax(q_values_adaptive))), axis=-1)
        mean_kl = tf.reduce_mean(kl)
        def update_scale():
            with tf.control_dependencies([perturb_for_adaption]):
                update_scale_expr = tf.cond(mean_kl < param_noise_threshold,
                    lambda: param_noise_scale.assign(param_noise_scale * 1.01),
                    lambda: param_noise_scale.assign(param_noise_scale / 1.01),
                )
            return update_scale_expr

        # Functionality to update the threshold for parameter space noise.
        update_param_noise_threshold_expr = param_noise_threshold.assign(tf.cond(update_param_noise_threshold_ph >= 0,
            lambda: update_param_noise_threshold_ph, lambda: param_noise_threshold))

        # Put everything together.
        deterministic_actions = tf.argmax(q_values_perturbed, axis=1)
        batch_size = tf.shape(observations_ph.get())[0]
        random_actions = tf.random_uniform(tf.stack([batch_size]), minval=0, maxval=num_actions, dtype=tf.int64)
        chose_random = tf.random_uniform(tf.stack([batch_size]), minval=0, maxval=1, dtype=tf.float32) < eps
        stochastic_actions = tf.where(chose_random, random_actions, deterministic_actions)

        output_actions = tf.cond(stochastic_ph, lambda: stochastic_actions, lambda: deterministic_actions)
        update_eps_expr = eps.assign(tf.cond(update_eps_ph >= 0, lambda: update_eps_ph, lambda: eps))
        updates = [
            update_eps_expr,
            tf.cond(reset_ph, lambda: perturb_vars(original_scope="q_func", perturbed_scope="perturbed_q_func"), lambda: tf.group(*[])),
            tf.cond(update_param_noise_scale_ph, lambda: update_scale(), lambda: tf.Variable(0., trainable=False)),
            update_param_noise_threshold_expr,
        ]
        _act = U.function(inputs=[observations_ph, stochastic_ph, update_eps_ph, reset_ph, update_param_noise_threshold_ph, update_param_noise_scale_ph],
                         outputs=output_actions,
                         givens={update_eps_ph: -1.0, stochastic_ph: True, reset_ph: False, update_param_noise_threshold_ph: False, update_param_noise_scale_ph: False},
                         updates=updates)
        def act(ob, reset=False, update_param_noise_threshold=False, update_param_noise_scale=False, stochastic=True, update_eps=-1):
            return _act(ob, stochastic, update_eps, reset, update_param_noise_threshold, update_param_noise_scale)
        return act


def build_train(make_obs_ph, q_func, num_actions, optimizer, sigma_optimizer,
                grad_norm_clipping=None, gamma=1.0, scope="deepq", reuse=None,
                sigma_weight = 0.5):
    """Creates the train function:

    Parameters
    ----------
    make_obs_ph: str -> tf.placeholder or TfInput
        a function that takes a name and creates a placeholder of input with that name
    q_func: (tf.Variable, int, str, bool) -> tf.Variable
        the model that takes the following inputs:
            observation_in: object
                the output of observation placeholder
            num_actions: int
                number of actions
            scope: str
            reuse: bool
                should be passed to outer variable scope
        and returns a tensor of shape (batch_size, num_actions) with values of every action.
    num_actions: int
        number of actions
    reuse: bool
        whether or not to reuse the graph variables
    optimizer: tf.train.Optimizer
        optimizer to use for the Q-learning objective.
    grad_norm_clipping: float or None
        clip gradient norms to this value. If None no clipping is performed.
    gamma: float
        discount rate.
    double_q: bool
        if true will use Double Q Learning (https://arxiv.org/abs/1509.06461).
        In general it is a good idea to keep it enabled.
    scope: str or VariableScope
        optional scope for variable_scope.
    reuse: bool or None
        whether or not the variables should be reused. To be able to reuse the scope must be given.
    param_noise: bool
        whether or not to use parameter space noise (https://arxiv.org/abs/1706.01905)
    param_noise_filter_func: tf.Variable -> bool
        function that decides whether or not a variable should be perturbed. Only applicable
        if param_noise is True. If set to None, default_param_noise_filter is used by default.

    Returns
    -------
    act: (tf.Variable, bool, float) -> tf.Variable
        function to select and action given observation.
`       See the top of the file for details.
    train: (object, np.array, np.array, object, np.array, np.array) -> np.array
        optimize the error in Bellman's equation.
`       See the top of the file for details.
    update_target: () -> ()
        copy the parameters from optimized Q function to the target Q function.
`       See the top of the file for details.
    debug: {str: function}
        a bunch of functions to print debug data like q_values.
    """

    act_f = build_act(make_obs_ph, q_func, num_actions, scope=scope, reuse=reuse)


    with tf.variable_scope(scope, reuse=reuse):
        # set up placeholders
        obs_t_input = make_obs_ph("obs_t")
        act_t_ph = tf.placeholder(tf.int32, [None], name="action")
        rew_t_ph = tf.placeholder(tf.float32, [None], name="reward")
        obs_tp1_input = make_obs_ph("obs_tp1")
        done_mask_ph = tf.placeholder(tf.float32, [None], name="done")
        importance_weights_ph = tf.placeholder(tf.float32, [None], name="weight")
        weighted_update_ph = tf.placeholder(tf.bool,  name="weighted_update")
        # q network evaluation
        q_t, sigma_t = q_func(obs_t_input.get(), num_actions, scope="q_func", reuse=True)  # reuse parameters from act
        q_func_vars = tf.get_collection(tf.GraphKeys.GLOBAL_VARIABLES, scope=tf.get_variable_scope().name + "/q_func")
        sigma_vars = tf.get_collection(tf.GraphKeys.GLOBAL_VARIABLES, scope=tf.get_variable_scope().name + "/q_func/sigma")

        # target q network evalution
        q_tp1, sigmas_tp1 = q_func(obs_tp1_input.get(), num_actions, scope="target_q_func")

        target_q_func_vars = tf.get_collection(tf.GraphKeys.GLOBAL_VARIABLES, scope=tf.get_variable_scope().name + "/target_q_func")

        # q scores for actions which we know were selected in the given state.
        q_t_selected = tf.reduce_sum(q_t * tf.one_hot(act_t_ph, num_actions), 1)

        sigma_t_selected = tf.reduce_sum(sigma_t * tf.one_hot(act_t_ph, num_actions), 1)
        # compute estimate of best possible value starting from state at t + 1

        prob = calculate_prob(q_tp1, sigmas_tp1, num_actions)
        q_target_weighted = tf.reduce_sum(tf.multiply(prob, q_tp1), 1)
        sigma_target_weighted = tf.reduce_sum(tf.multiply(prob, sigmas_tp1), 1)

        best_target = tf.argmax(q_tp1, axis=1)
        q_target_mean = tf.reduce_max(q_tp1, axis=1)
        sigma_target_mean = tf.reduce_sum(sigmas_tp1 * tf.one_hot(best_target, num_actions), 1)

        q_target_unmasked = tf.cond(weighted_update_ph, lambda: q_target_weighted, lambda: q_target_mean)
        sigma_target_unmasked = tf.cond(weighted_update_ph, lambda: sigma_target_weighted, lambda: sigma_target_mean)

        q_target_masked = (1.0 - done_mask_ph) * q_target_unmasked
        sigma_target_masked = (1.0 - done_mask_ph) * sigma_target_unmasked

        q_target = rew_t_ph + gamma * q_target_masked
        sigma_target = gamma * sigma_target_masked

        # compute the error (potentially clipped)
        td_error = (q_t_selected - q_target) ** 2 + \
                   sigma_weight * (sigma_t_selected - sigma_target) ** 2
        errors = U.huber_loss(td_error)
        #weighted_error = tf.reduce_mean(importance_weights_ph * errors)

        # compute optimization op (potentially with gradient clipping)
        q_vars = []
        for i, var in enumerate(q_func_vars):
            if var not in sigma_vars:
                q_vars.append(var)

        #sigma_optimizer = tf.train.MomentumOptimizer(learning_rate=lr_sigma, momentum=0.9)
        q_gradients = optimizer.compute_gradients(errors, var_list=q_vars)
        sigma_gradients = sigma_optimizer.compute_gradients(errors, var_list=sigma_vars)
        '''for i, (grad, var) in enumerate(sigma_gradients):
            if grad is not None:
                sigma_gradients[i] = (grad * 0.000000001, var)'''
        gradients = q_gradients + sigma_gradients

        if grad_norm_clipping is not None:
            for i, (grad, var) in enumerate(q_gradients):
                if grad is not None:
                    q_gradients[i] = (tf.clip_by_norm(grad, grad_norm_clipping), var)
            for i, (grad, var) in enumerate(sigma_gradients):
                if grad is not None:
                    sigma_gradients[i] = (tf.clip_by_norm(grad, grad_norm_clipping), var)
            '''for i, (grad, var) in enumerate(gradients):
                if grad is not None:
                    gradients[i] = (tf.clip_by_norm(grad, grad_norm_clipping), var)'''
        optimize_q_expr = optimizer.apply_gradients(q_gradients)
        optimize_sigma_expr = sigma_optimizer.apply_gradients(sigma_gradients)


        g = tf.reshape(gradients[0], [-1, 1])
        for i, grad in enumerate(gradients):
            if i != 0:
                col_vec = tf.reshape(gradients[i], [-1, 1])
                g = tf.concat([g, col_vec], axis=0)

        with tf.name_scope('train_summaries'):
            tf.summary.scalar("loss", tf.reduce_mean(td_error))
            tf.summary.tensor_summary("q", tf.reduce_mean(q_t, axis=1))
            tf.summary.tensor_summary("sigma", tf.reduce_mean(sigma_t, axis=1))
            tf.summary.scalar("average_q", tf.reduce_mean(q_t))
            tf.summary.scalar("average_sigma", tf.reduce_mean(sigma_t))
            tf.summary.scalar("average_q_target", tf.reduce_mean(q_tp1))
            tf.summary.scalar("average_sigma_target", tf.reduce_mean(sigmas_tp1))
            #tf.summary.scalar("average_sigma_gradient", tf.reduce_mean(gradients))
            #tf.summary.scalar("average_sigma_gradient2", tf.reduce_mean(sigma_gradients))
            tf.summary.histogram('qs', q_t)
            tf.summary.histogram('sigmas', sigma_t)
            grad_hist = tf.summary.histogram("gradient_hist", g)
        merged = tf.summary.merge_all()


        # update_target_fn will be called periodically to copy Q network to target Q network
        update_target_expr = []
        for var, var_target in zip(sorted(q_func_vars, key=lambda v: v.name),
                                   sorted(target_q_func_vars, key=lambda v: v.name)):
            update_target_expr.append(var_target.assign(var))
        update_target_expr = tf.group(*update_target_expr)

        # Create callable functions
        train = U.function(
            inputs=[
                obs_t_input,
                act_t_ph,
                rew_t_ph,
                obs_tp1_input,
                done_mask_ph,
                importance_weights_ph,
                weighted_update_ph,
            ],
            outputs=[td_error, q_t, sigma_t, q_tp1, sigmas_tp1, prob, q_target, merged],
            givens={weighted_update_ph: True},
            updates=[optimize_q_expr, optimize_sigma_expr]
        )

        update_target = U.function([], [], updates=[update_target_expr])

        q_values = U.function(inputs=[obs_t_input], outputs=[q_t, sigma_t])
        target_q_values = U.function(inputs=[obs_tp1_input], outputs=[q_tp1, sigmas_tp1])
        return act_f, train, update_target, {'q_values': q_values ,
                                             'target_q_values': target_q_values}

def build_act_double(make_obs_ph, q_func, num_actions, scope="deepq", reuse=None):
    """Creates the act function:

    Parameters
    ----------
    make_obs_ph: str -> tf.placeholder or TfInput
        a function that take a name and creates a placeholder of input with that name
    q_func: (tf.Variable, int, str, bool) -> tf.Variable
        the model that takes the following inputs:
            observation_in: object
                the output of observation placeholder
            num_actions: int
                number of actions
            scope: str
            reuse: bool
                should be passed to outer variable scope
        and returns a tensor of shape (batch_size, num_actions) with values of every action.
    num_actions: int
        number of actions.
    scope: str or VariableScope
        optional scope for variable_scope.
    reuse: bool or None
        whether or not the variables should be reused. To be able to reuse the scope must be given.

    Returns
    -------
    act: (tf.Variable, bool, float) -> tf.Variable
        function to select and action given observation.
`       See the top of the file for details.
    """
    with tf.variable_scope(scope, reuse=reuse):
        observations_ph = make_obs_ph("observation")
        stochastic_ph = tf.placeholder(tf.bool, (), name="stochastic")
        update_eps_ph = tf.placeholder(tf.float32, (), name="update_eps")

        eps = tf.get_variable("eps", (), initializer=tf.constant_initializer(0))
        eval_ph = tf.placeholder(tf.bool, (), name="eval_ph")

        q_values = q_func(observations_ph.get(), num_actions, scope="q_func")
        sigma_values = q_func(observations_ph.get(), num_actions, sigmas=True, scope="sigma_func")
        deterministic_actions = tf.argmax(q_values, axis=1)
        batch_size = tf.shape(observations_ph.get())[0]

        q_samples = tf.distributions.Normal(loc=q_values, scale=sigma_values).sample()
        sampled_actions = tf.argmax(q_samples, axis=1)

        output_actions = tf.cond(eval_ph, lambda: deterministic_actions, lambda: sampled_actions)
        random_actions = tf.random_uniform(tf.stack([batch_size]), minval=0, maxval=num_actions, dtype=tf.int64)
        chose_random = tf.random_uniform(tf.stack([batch_size]), minval=0, maxval=1, dtype=tf.float32) < eps
        actions = tf.where(chose_random, random_actions, output_actions)

        update_eps_expr = eps.assign(tf.cond(update_eps_ph >= 0, lambda: update_eps_ph, lambda: eps))
        ''' tf.name_scope('act_summaries'):
            with tf.name_scope('q'):
                tf.summary.scalar("q_values", q_values)
            with tf.name_scope('sigma'):
                tf.summary.scalar("sigma_values", sigma_values)
            merged = tf.summary.merge_all()'''
        _act = U.function(inputs=[observations_ph, stochastic_ph, update_eps_ph, eval_ph],
                          outputs=[q_values, sigma_values, actions, q_samples, eps],
                          givens={update_eps_ph: -1.0, stochastic_ph: True},
                          updates=[update_eps_expr])


        def act(ob, stochastic=True, update_eps=-1, eval_flag=False):

            return _act(ob, stochastic, update_eps, eval_flag)
        return act

def build_train_double(make_obs_ph, q_func, num_actions, sigma_optimizer, optimizer,
                       grad_norm_clipping=None, gamma=0.99, scope="deepq", reuse=None, ):
    """Creates the train function:

    Parameters
    ----------
    make_obs_ph: str -> tf.placeholder or TfInput
        a function that takes a name and creates a placeholder of input with that name
    q_func: (tf.Variable, int, str, bool) -> tf.Variable
        the model that takes the following inputs:
            observation_in: object
                the output of observation placeholder
            num_actions: int
                number of actions
            scope: str
            reuse: bool
                should be passed to outer variable scope
        and returns a tensor of shape (batch_size, num_actions) with values of every action.
    num_actions: int
        number of actions
    reuse: bool
        whether or not to reuse the graph variables
    optimizer: tf.train.Optimizer
        optimizer to use for the Q-learning objective.
    grad_norm_clipping: float or None
        clip gradient norms to this value. If None no clipping is performed.
    gamma: float
        discount rate.
    double_q: bool
        if true will use Double Q Learning (https://arxiv.org/abs/1509.06461).
        In general it is a good idea to keep it enabled.
    scope: str or VariableScope
        optional scope for variable_scope.
    reuse: bool or None
        whether or not the variables should be reused. To be able to reuse the scope must be given.
    param_noise: bool
        whether or not to use parameter space noise (https://arxiv.org/abs/1706.01905)
    param_noise_filter_func: tf.Variable -> bool
        function that decides whether or not a variable should be perturbed. Only applicable
        if param_noise is True. If set to None, default_param_noise_filter is used by default.

    Returns
    -------
    act: (tf.Variable, bool, float) -> tf.Variable
        function to select and action given observation.
`       See the top of the file for details.
    train: (object, np.array, np.array, object, np.array, np.array) -> np.array
        optimize the error in Bellman's equation.
`       See the top of the file for details.
    update_target: () -> ()
        copy the parameters from optimized Q function to the target Q function.
`       See the top of the file for details.
    debug: {str: function}
        a bunch of functions to print debug data like q_values.
    """

    act_f = build_act_double(make_obs_ph, q_func, num_actions, scope=scope, reuse=reuse)

    with tf.variable_scope(scope, reuse=reuse):
        # set up placeholders
        obs_t_input = make_obs_ph("obs_t")
        act_t_ph = tf.placeholder(tf.int32, [None], name="action")
        rew_t_ph = tf.placeholder(tf.float32, [None], name="reward")
        obs_tp1_input = make_obs_ph("obs_tp1")
        done_mask_ph = tf.placeholder(tf.float32, [None], name="done")
        importance_weights_ph = tf.placeholder(tf.float32, [None], name="weight")
        weighted_update_ph = tf.placeholder(tf.bool, name="weighted_update")
        # q network evaluation
        q_t = q_func(obs_t_input.get(), num_actions, scope="q_func", reuse=True)  # reuse parameters from act
        sigma_t = q_func(obs_t_input.get(), num_actions, scope="sigma_func", reuse=True, sigmas=True)
        q_func_vars = tf.get_collection(tf.GraphKeys.GLOBAL_VARIABLES, scope=tf.get_variable_scope().name + "/q_func")

        sigma_func_vars = tf.get_collection(tf.GraphKeys.GLOBAL_VARIABLES,
                                       scope=tf.get_variable_scope().name + "/sigma_func")

        # target q network evalution
        q_tp1 = q_func(obs_tp1_input.get(), num_actions, scope="target_q_func")
        sigmas_tp1 = q_func(obs_tp1_input.get(), num_actions, sigmas=True, scope="target_sigma_func")

        target_q_func_vars = tf.get_collection(tf.GraphKeys.GLOBAL_VARIABLES,
                                               scope=tf.get_variable_scope().name + "/target_q_func")
        target_sigma_func_vars = tf.get_collection(tf.GraphKeys.GLOBAL_VARIABLES,
                                               scope=tf.get_variable_scope().name + "/target_sigma_func")

        # q scores for actions which we know were selected in the given state.
        q_t_selected = tf.reduce_sum(q_t * tf.one_hot(act_t_ph, num_actions), 1)

        sigma_t_selected = tf.reduce_sum(sigma_t * tf.one_hot(act_t_ph, num_actions), 1)
        # compute estimate of best possible value starting from state at t + 1

        prob = calculate_prob(q_tp1, sigmas_tp1, num_actions)
        q_target_weighted = tf.reduce_sum(tf.multiply(prob, q_tp1), 1)
        sigma_target_weighted = tf.reduce_sum(tf.multiply(prob, sigmas_tp1), 1)

        best_target = tf.argmax(q_tp1, axis=1)
        q_target_mean = tf.reduce_max(q_tp1, axis=1)
        sigma_target_mean = tf.reduce_sum(sigmas_tp1 * tf.one_hot(best_target, num_actions), 1)

        q_target_unmasked = tf.cond(weighted_update_ph, lambda: q_target_weighted, lambda: q_target_mean)
        sigma_target_unmasked = tf.cond(weighted_update_ph, lambda: sigma_target_weighted, lambda: sigma_target_mean)

        q_target_masked = (1.0 - done_mask_ph) * q_target_unmasked
        sigma_target_masked = (1.0 - done_mask_ph) * sigma_target_unmasked

        q_target = rew_t_ph + gamma * q_target_masked
        sigma_target = gamma * sigma_target_masked

        # compute the error (potentially clipped)
        td_error = (q_t_selected - q_target) ** 2 + \
                   (sigma_t_selected - sigma_target) ** 2
        errors = U.huber_loss(td_error)
        # weighted_error = tf.reduce_mean(importance_weights_ph * errors)

        # compute optimization op (potentially with gradient clipping)

        #sigma_optimizer = tf.train.MomentumOptimizer(learning_rate=lr_sigma, momentum=momentum)

        q_gradients = optimizer.compute_gradients(errors, var_list=q_func_vars)
        sigma_gradients = sigma_optimizer.compute_gradients(errors, var_list=sigma_func_vars)

        if grad_norm_clipping is not None:

            for i, (grad, var) in enumerate(q_gradients):
                if grad is not None:
                    q_gradients[i] = (tf.clip_by_norm(grad, grad_norm_clipping), var)
            for i, (grad, var) in enumerate(sigma_gradients):
                if grad is not None:
                    sigma_gradients[i] = (tf.clip_by_norm(grad, grad_norm_clipping), var)

        optimize_q_expr = optimizer.apply_gradients(q_gradients)
        optimize_sigma_expr = sigma_optimizer.apply_gradients(sigma_gradients)


        with tf.name_scope('train_summaries'):
            tf.summary.scalar("loss", tf.reduce_mean(td_error))
            tf.summary.tensor_summary("q", tf.reduce_mean(q_t, axis=1))
            tf.summary.tensor_summary("sigma", tf.reduce_mean(sigma_t, axis=1))
            tf.summary.scalar("average_q", tf.reduce_mean(q_t))
            tf.summary.scalar("average_sigma", tf.reduce_mean(sigma_t))
            tf.summary.histogram('qs', q_t)
            tf.summary.histogram('sigmas', sigma_t)
        merged = tf.summary.merge_all()

        # update_target_fn will be called periodically to copy Q network to target Q network
        update_target_expr = []
        for var, var_target in zip(sorted(q_func_vars, key=lambda v: v.name),
                                   sorted(target_q_func_vars, key=lambda v: v.name)):
            update_target_expr.append(var_target.assign(var))
        for var, var_target in zip(sorted(sigma_func_vars, key=lambda v: v.name),
                                   sorted(target_sigma_func_vars, key=lambda v: v.name)):
            update_target_expr.append(var_target.assign(var))
        update_target_expr = tf.group(*update_target_expr)

        # Create callable functions
        train = U.function(
            inputs=[
                obs_t_input,
                act_t_ph,
                rew_t_ph,
                obs_tp1_input,
                done_mask_ph,
                importance_weights_ph,
                weighted_update_ph,
            ],
            outputs=[td_error, q_t, sigma_t, q_tp1, sigmas_tp1, prob, q_target, merged],
            givens={weighted_update_ph: True},
            updates=[optimize_q_expr, optimize_sigma_expr]
        )
        update_target = U.function([], [], updates=[update_target_expr])

        q_values = U.function(inputs=[obs_t_input], outputs=[q_t, sigma_t])
        target_q_values = U.function(inputs=[obs_tp1_input], outputs=[q_tp1, sigmas_tp1])
        return act_f, train, update_target, {'q_values': q_values,
                                             'target_q_values': target_q_values}


def build_act_particle(make_obs_ph, q_func, num_actions, scope, reuse, k, q_max):
    """Creates the act function:

        Parameters
        ----------
        make_obs_ph: str -> tf.placeholder or TfInput
            a function that take a name and creates a placeholder of input with that name
        q_func: (tf.Variable, int, str, bool) -> tf.Variable
            the model that takes the following inputs:
                observation_in: object
                    the output of observation placeholder
                num_actions: int
                    number of actions
                scope: str
                reuse: bool
                    should be passed to outer variable scope
            and returns a tensor of shape (batch_size, num_actions) with values of every action.
        num_actions: int
            number of actions.
        scope: str or VariableScope
            optional scope for variable_scope.
        reuse: bool or None
            whether or not the variables should be reused. To be able to reuse the scope must be given.

        Returns
        -------
        act: (tf.Variable, bool, float) -> tf.Variable
            function to select and action given observation.
    `       See the top of the file for details.
        """
    with tf.variable_scope(scope, reuse=reuse):
        observations_ph = make_obs_ph("observation")
        stochastic_ph = tf.placeholder(tf.bool, (), name="stochastic")
        update_eps_ph = tf.placeholder(tf.float32, (), name="update_eps")

        eps = tf.get_variable("eps", (), initializer=tf.constant_initializer(0))
        eval_ph = tf.placeholder(tf.bool, (), name="eval_ph")

        q_values = q_func(observations_ph.get(), num_actions, k=k, q_max=q_max, scope="q_func")
        means = tf.reduce_mean(q_values, axis=0)

        deterministic_actions = tf.argmax(means, axis=1)
        batch_size = tf.shape(observations_ph.get())[0]

        indexes = tf.random_uniform(tf.stack([batch_size, num_actions]), minval=0, maxval=k, dtype=tf.int32)
        index_one_hot = tf.one_hot(indexes, k)
        q_l = tf.gather(q_values, [i for i in range(len(q_values))])
        q_l = tf.transpose(q_l, [1, 2, 0])
        q_samples = tf.reduce_sum(index_one_hot * q_l, axis=2)
        sampled_actions = tf.argmax(q_samples, axis=1)

        output_actions = tf.cond(eval_ph, lambda: deterministic_actions, lambda: sampled_actions)
        random_actions = tf.random_uniform(tf.stack([batch_size]), minval=0, maxval=num_actions, dtype=tf.int64)
        chose_random = tf.random_uniform(tf.stack([batch_size]), minval=0, maxval=1, dtype=tf.float32) < eps
        actions = tf.where(chose_random, random_actions, output_actions)

        update_eps_expr = eps.assign(tf.cond(update_eps_ph >= 0, lambda: update_eps_ph, lambda: eps))

        _act = U.function(inputs=[observations_ph, stochastic_ph, update_eps_ph, eval_ph],
                          outputs=[q_values, actions, q_samples, eps],
                          givens={update_eps_ph: -1.0, stochastic_ph: True},
                          updates=[update_eps_expr])

        def act(ob, stochastic=True, update_eps=-1, eval_flag=False):
            return _act(ob, stochastic, update_eps, eval_flag)

        return act


def build_train_particle(make_obs_ph, q_func, num_actions, optimizer,
                grad_norm_clipping=None, gamma=1.0, scope="deepq", reuse=None, k=10, q_max=100):
    """Creates the train function:

    Parameters
    ----------
    make_obs_ph: str -> tf.placeholder or TfInput
        a function that takes a name and creates a placeholder of input with that name
    q_func: (tf.Variable, int, str, bool) -> tf.Variable
        the model that takes the following inputs:
            observation_in: object
                the output of observation placeholder
            num_actions: int
                number of actions
            scope: str
            reuse: bool
                should be passed to outer variable scope
        and returns a tensor of shape (batch_size, num_actions) with values of every action.
    num_actions: int
        number of actions
    reuse: bool
        whether or not to reuse the graph variables
    optimizer: tf.train.Optimizer
        optimizer to use for the Q-learning objective.
    grad_norm_clipping: float or None
        clip gradient norms to this value. If None no clipping is performed.
    gamma: float
        discount rate.
    double_q: bool
        if true will use Double Q Learning (https://arxiv.org/abs/1509.06461).
        In general it is a good idea to keep it enabled.
    scope: str or VariableScope
        optional scope for variable_scope.
    reuse: bool or None
        whether or not the variables should be reused. To be able to reuse the scope must be given.
    param_noise: bool
        whether or not to use parameter space noise (https://arxiv.org/abs/1706.01905)
    param_noise_filter_func: tf.Variable -> bool
        function that decides whether or not a variable should be perturbed. Only applicable
        if param_noise is True. If set to None, default_param_noise_filter is used by default.

    Returns
    -------
    act: (tf.Variable, bool, float) -> tf.Variable
        function to select and action given observation.
`       See the top of the file for details.
    train: (object, np.array, np.array, object, np.array, np.array) -> np.array
        optimize the error in Bellman's equation.
`       See the top of the file for details.
    update_target: () -> ()
        copy the parameters from optimized Q function to the target Q function.
`       See the top of the file for details.
    debug: {str: function}
        a bunch of functions to print debug data like q_values.
    """

    act_f = build_act_particle(make_obs_ph, q_func, num_actions, k=k, q_max=q_max, scope=scope, reuse=reuse)


    with tf.variable_scope(scope, reuse=reuse):
        # set up placeholders
        obs_t_input = make_obs_ph("obs_t")
        act_t_ph = tf.placeholder(tf.int32, [None], name="action")
        rew_t_ph = tf.placeholder(tf.float32, [None], name="reward")
        obs_tp1_input = make_obs_ph("obs_tp1")
        done_mask_ph = tf.placeholder(tf.float32, [None], name="done")
        weighted_update_ph = tf.placeholder(tf.bool,  name="weighted_update")
        # q network evaluation
        q_t = q_func(obs_t_input.get(), num_actions, k=k,  q_max=q_max, scope="q_func", reuse=True)  # reuse parameters from act
        q_func_vars = tf.get_collection(tf.GraphKeys.GLOBAL_VARIABLES, scope=tf.get_variable_scope().name + "/q_func")

        # target q network evalution
        q_tp1 = q_func(obs_tp1_input.get(), num_actions, k=k,  q_max=q_max, scope="target_q_func")

        target_q_func_vars = tf.get_collection(tf.GraphKeys.GLOBAL_VARIABLES, scope=tf.get_variable_scope().name + "/target_q_func")

        action_one_hot = tf.one_hot(act_t_ph, num_actions)

        # q scores for actions which we know were selected in the given state.
        q_t_selected = []
        for i in range(k):
            q_t_selected.append(
                tf.reduce_sum(q_t[i] * action_one_hot, axis=1)
            )
        # compute estimate of best possible value starting from state at t + 1

        q_list = tf.gather(q_tp1, [i for i in range(len(q_tp1))])
        q_list = tf.transpose(q_list, [1, 2, 0])
        prob = calculate_prob_particles(q_list, num_actions, k)
        q_target_weighted = []

        means_target = tf.reduce_mean(q_list, axis=1)
        best_target = tf.argmax(means_target, axis=1)
        best_target_one_hot = tf.one_hot(best_target, num_actions)
        q_target_mean = []

        for i in range(k):
            q_target_weighted.append(tf.reduce_sum(tf.multiply(prob, q_tp1[i]), 1))
            q_target_mean.append(tf.reduce_sum(q_tp1[i] * best_target_one_hot, 1))

        '''q_target_weighted = tf.gather(q_target_weighted, [i for i in range(len(q_target_weighted))])
        q_target_weighted = tf.reshape(q_target_weighted, [tf.shape(q_target_weighted)[1], k])

        q_target_mean = tf.gather(q_target_mean, [i for i in range(len(q_target_mean))])
        q_target_mean = tf.reshape(q_target_mean, [tf.shape(q_target_mean)[1], k])'''


        q_target_unmasked = tf.cond(weighted_update_ph, lambda: q_target_weighted, lambda: q_target_mean)

        q_target_masked = (1.0 - done_mask_ph) * q_target_unmasked

        q_target = rew_t_ph + gamma * q_target_masked
        q_selected_sorted = tf.contrib.framework.sort(q_t_selected, axis=0)
        q_target_sorted = tf.contrib.framework.sort(q_target, axis=0)
        # compute the error (potentially clipped)
        td_error = 0
        for i in range(k):
            td_error += (q_selected_sorted[i] - q_target_sorted[i])
        errors = U.huber_loss(td_error)

        # compute optimization op (potentially with gradient clipping)
        gradients = optimizer.compute_gradients(errors, var_list=q_func_vars)

        if grad_norm_clipping is not None:
            for i, (grad, var) in enumerate(gradients):
                if grad is not None:
                    gradients[i] = (tf.clip_by_norm(grad, grad_norm_clipping), var)
        optimize_expr = optimizer.apply_gradients(gradients)

        g = tf.reshape(gradients[0], [-1, 1])
        for i, grad in enumerate(gradients):
            if i != 0:
                col_vec = tf.reshape(gradients[i], [-1, 1])
                g = tf.concat([g, col_vec], axis=0)

        with tf.name_scope('train_summaries'):
            tf.summary.scalar("loss", tf.reduce_mean(td_error))
            tf.summary.tensor_summary("q", tf.reduce_mean(q_t, axis=1))
            tf.summary.scalar("average_q", tf.reduce_mean(q_t))
            tf.summary.scalar("average_q_target", tf.reduce_mean(q_tp1))
            tf.summary.histogram('qs', q_t)
            grad_hist = tf.summary.histogram("gradient_hist", g)
        merged = tf.summary.merge_all()


        # update_target_fn will be called periodically to copy Q network to target Q network
        update_target_expr = []
        for var, var_target in zip(sorted(q_func_vars, key=lambda v: v.name),
                                   sorted(target_q_func_vars, key=lambda v: v.name)):
            update_target_expr.append(var_target.assign(var))
        update_target_expr = tf.group(*update_target_expr)

        # Create callable functions
        train = U.function(
            inputs=[
                obs_t_input,
                act_t_ph,
                rew_t_ph,
                obs_tp1_input,
                done_mask_ph,
                weighted_update_ph,
            ],
            outputs=[td_error, merged, q_selected_sorted, q_target_sorted, q_target_masked],
            updates=[optimize_expr]
        )

        update_target = U.function([], [], updates=[update_target_expr])

        q_values = U.function(inputs=[obs_t_input], outputs=[q_t])
        target_q_values = U.function(inputs=[obs_tp1_input], outputs=[q_tp1])
        return act_f, train, update_target, {'q_values': q_values,
                                             'target_q_values': target_q_values}

def calculate_prob_particles(qs, num_actions, k):

    def compute_prob_max(q_list):

        score = tf.cast((q_list[:, :, None, None] >= q_list), tf.int32)
        prob = tf.reduce_sum(tf.reduce_sum(tf.reduce_sum(score, axis=3), axis=2), axis=1)
        prob = tf.cast(prob, tf.float32)
        return prob / tf.reduce_sum(prob)

    tensor_type = tf.placeholder(tf.float32, [num_actions])
    return tf.map_fn(compute_prob_max, qs, dtype=tensor_type.dtype)

def calculate_prob(qs,sigmas, num_actions):

    qs_and_sigmas_target = tf.stack([qs, sigmas], axis=2)

    def integrate(qs_and_sigmas):
        qs = qs_and_sigmas[:, 0]

        sigmas = qs_and_sigmas[:, 1]
        lower_limit = qs - 8 * sigmas
        upper_limit = qs + 8 * sigmas
        n_trapz = 100
        probs = []

        for a in range(num_actions):

            def f(x):
                p = 1
                p *= tf.distributions.Normal(loc=qs[a], scale=sigmas[a]).prob(x)
                for k in range(num_actions):
                    if k != a:
                        p *= tf.distributions.Normal(loc=qs[k], scale=sigmas[k]).cdf(x)
                return p

            x = tf.lin_space(lower_limit[a], upper_limit[a], n_trapz)
            y = f(x)
            probs.append(((upper_limit[a] - lower_limit[a]) / (2 * (n_trapz - 1))) * \
                         (y[0] + y[-1] + 2 * tf.reduce_sum(y[1:-1], axis=0)))
        p = tf.reshape(tf.stack(probs), [num_actions])
        return p / tf.reduce_sum(p)

    tensor_type = tf.placeholder(tf.float32, [num_actions])
    return tf.map_fn(integrate, qs_and_sigmas_target, dtype=tensor_type.dtype)

