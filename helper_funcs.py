from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import numpy as np
import tensorflow as tf
import os
import h5py
import json
import sys
import warnings
import errno

def kind_dict_definition():
    # used in the graph's keep probability
    return {
        'train': 1,
        'posterior_sample_and_average': 2,
        'posterior_mean': 3,
        'prior_sample': 4,
        'write_model_params': 5,
    }


def kind_dict(kind_str):
    # used in the graph's keep probability
    kd = kind_dict_definition()
    return kd[kind_str]


def kind_dict_key(kind_number):
    # get the key for a certain ind
    kd = kind_dict_definition()
    for key, val in kd.iteritems():
        if val == kind_number:
            return key


def mkdir_p(path):
    try:
        os.makedirs(path)
    except OSError as exc:  # Python >2.5
        if exc.errno == errno.EEXIST and os.path.isdir(path):
            pass
        else:
            raise


def printer(data):
    # prints on the same line
    sys.stdout.write("\r\x1b[K" + data.__str__())
    sys.stdout.flush()


def write_data(data_fname, data_dict, use_json=False, compression=None):
    """Write data in HDF5 format.

    Args:
      data_fname: The filename of teh file in which to write the data.
      data_dict:  The dictionary of data to write. The keys are strings
        and the values are numpy arrays.
      use_json (optional): human readable format for simple items
      compression (optional): The compression to use for h5py (disabled by
        default because the library borks on scalars, otherwise try 'gzip').
    """

    dir_name = os.path.dirname(data_fname)
    if not os.path.exists(dir_name):
        os.makedirs(dir_name)

    if use_json:
        the_file = open(data_fname, 'w')
        json.dump(data_dict, the_file)
        the_file.close()
    else:
        try:
            with h5py.File(data_fname, 'w') as hf:
                for k, v in data_dict.items():
                    clean_k = k.replace('/', '_')
                    if clean_k is not k:
                        print('Warning: saving variable with name: ', k, ' as ', clean_k)
                    else:
                        print('Saving variable with name: ', clean_k)
                    hf.create_dataset(clean_k, data=v, compression=compression)
        except IOError:
            print("Cannot open %s for writing.", data_fname)
            raise


def init_linear_transform(in_size, out_size, name=None, collections=None, mat_init_value=None, bias_init_value=None, normalized=False, do_bias=True):
    # generic function (we use linear transforms in a lot of places)
    # initialize the weights of the linear transformation based on the size of the inputs

    # initialze with a random distribuion
    stddev = 1 / np.sqrt(float(in_size))
    mat_init = tf.random_normal_initializer(0.0, stddev, dtype=tf.float32)

    # weight matrix
    w_collections = [tf.GraphKeys.GLOBAL_VARIABLES, "norm-variables"]
    if collections:
        w_collections += collections
    wname = (name + "/W") if name else "/W"
    w = tf.get_variable(wname, [in_size, out_size], initializer=mat_init,
                        dtype=tf.float32, collections=w_collections)
    if normalized:
        w = tf.nn.l2_normalize(w, axis=0)

    # biases
    bname = (name + "/b") if name else "/b"
    if do_bias:
        b = tf.get_variable(bname, [1, out_size],
                            initializer=tf.zeros_initializer(),
                            dtype=tf.float32)
    else:
        b =  tf.zeros([1, out_size],
                      name = bname,
                      dtype=tf.float32)
    return (w, b)

def linear(x, out_size, name, collections=None, mat_init_value=None, bias_init_value=None):
    # generic function (we use linear transforms in a lot of places)
    # initialize the weights of the linear transformation based on the size of the inputs
    in_size = int(x.get_shape()[1])
    W, b = init_linear_transform(in_size, out_size, name=name, collections=collections, mat_init_value=mat_init_value,
                                 bias_init_value=bias_init_value)
    return tf.matmul(x, W) + b


def ListOfRandomBatches(num_trials, batch_size):
    if num_trials <= batch_size:
        warnings.warn("Your batch size is bigger than num_trials! Using single batch ...")
        return [np.random.permutation(range(num_trials))]

    random_order = np.random.permutation(range(num_trials))
    even_num_of_batches = int(np.floor(num_trials / batch_size))
    trials_to_keep = even_num_of_batches * batch_size
    # if num_trials % batch_size != 0:
    #    print("Warning: throwing out %i trials per epoch" % (num_trials-trials_to_keep) )
    random_order = random_order[0:(trials_to_keep)]
    batches = [random_order[i:i + batch_size] for i in range(0, len(random_order), batch_size)]
    return batches


class Gaussian(object):
    """Base class for Gaussian distribution classes."""

    @property
    def mean(self):
        return self.mean_bxn

    @property
    def logvar(self):
        return self.logvar_bxn

    def noise(self):
        return self.noise_bxn

    def sample(self):
        return self.mean + tf.exp(0.5 * self.logvar) * self.noise()


def diag_gaussian_log_likelihood(z, mu=0.0, logvar=0.0):
    """Log-likelihood under a Gaussian distribution with diagonal covariance.
      Returns the log-likelihood for each dimension.  One should sum the
      results for the log-likelihood under the full multidimensional model.

    Args:
      z: The value to compute the log-likelihood.
      mu: The mean of the Gaussian
      logvar: The log variance of the Gaussian.

    Returns:
      The log-likelihood under the Gaussian model.
    """

    return -0.5 * (logvar + np.log(2 * np.pi) + \
                   tf.square((z - mu) / tf.exp(0.5 * logvar)))


def gaussian_pos_log_likelihood(unused_mean, logvar, noise):
  """Gaussian log-likelihood function for a posterior in VAE

  Note: This function is specialized for a posterior distribution, that has the
  form of z = mean + sigma * noise.

  Args:
    unused_mean: ignore
    logvar: The log variance of the distribution
    noise: The noise used in the sampling of the posterior.

  Returns:
    The log-likelihood under the Gaussian model.
  """
  # ln N(z; mean, sigma) = - ln(sigma) - 0.5 ln 2pi - noise^2 / 2
  return - 0.5 * (logvar + np.log(2 * np.pi) + tf.square(noise))


class DiagonalGaussianFromExisting(Gaussian):
    """Diagonal Gaussian with different constant mean and variances in each
    dimension.
    """

    def __init__(self, mean_bxn, logvar_bxn):
        self.mean_bxn = mean_bxn
        self.logvar_bxn = logvar_bxn
        self.noise_bxn = tf.random_normal(tf.shape(self.logvar_bxn))


class LearnableDiagonalGaussian(Gaussian):
    """Diagonal Gaussian with different constant mean and variances in each
    dimension.
    """
    # MRK todo, Fix for co_dim > 0 (now it works only for co_dim=0)
    
    def __init__(self, batch_size, z_size, name, var):
        # MRK's fix, letting the mean of the prior to be trainable
        mean_init = 0.0
        z_size_ = [1]+z_size
        z_mean_1xn = tf.get_variable(name=name+"/mean", shape=z_size_,
                                 initializer=tf.constant_initializer(mean_init))
        if len(z_size) == 1:
            self.mean_bxn =  tf.tile(z_mean_1xn, tf.stack([batch_size, 1]))
            self.mean_bxn.set_shape([None, z_size[0]])
        else:
            self.mean_bxn = tf.tile(z_mean_1xn, tf.stack([batch_size, 1, 1]))
            self.mean_bxn.set_shape([None] + z_size)

        print(self.mean_bxn)
        #
        self.logvar_bxn = tf.log(tf.zeros([batch_size] + z_size) + var)
        self.noise_bxn = tf.random_normal(tf.shape(self.logvar_bxn))
 

class DiagonalGaussian(object):
  """Diagonal Gaussian with different constant mean and variances in each
  dimension.
  """

  def __init__(self, batch_size, z_size, mean, logvar):
    """Create a diagonal gaussian distribution.

    Args:
      batch_size: The size of the batch, i.e. 0th dim in 2D tensor of samples.
      z_size: The dimension of the distribution, i.e. 1st dim in 2D tensor.
      mean: The N-D mean of the distribution.
      logvar: The N-D log variance of the diagonal distribution.
    """
    size__xz = [None, z_size]
    self.mean = mean            # bxn already
    self.logvar = logvar        # bxn already
    self.noise = noise = tf.random_normal(tf.shape(logvar))
    self.sample = mean + tf.exp(0.5 * logvar) * noise
    mean.set_shape(size__xz)
    logvar.set_shape(size__xz)
    self.sample.set_shape(size__xz)

  def logp(self, z=None):
    """Compute the log-likelihood under the distribution.

    Args:
      z (optional): value to compute likelihood for, if None, use sample.

    Returns:
      The likelihood of z under the model.
    """
    if z is None:
      z = self.sample

    # This is needed to make sure that the gradients are simple.
    # The value of the function shouldn't change.
    if z == self.sample:
      return gaussian_pos_log_likelihood(self.mean, self.logvar, self.noise)

    return diag_gaussian_log_likelihood(z, self.mean, self.logvar)



class DiagonalGaussian(Gaussian):
    """Diagonal Gaussian with different constant mean and variances in each
    dimension.
    """
    def __init__(self, z_size, name, var):
        self.mean_bxn = tf.zeros( z_size )
        self.logvar_bxn = tf.log( tf.zeros( z_size ) + var)
        self.noise_bxn = tf.random_normal( tf.shape( self.logvar_bxn ) )

# NOT USED
class GaussianProcess:
  """Base class for Gaussian processes."""
  pass

# NOT USED
class LearnableAutoRegressive1Prior(GaussianProcess):
  """AR(1) model where autocorrelation and process variance are learned
  parameters.  Assumed zero mean.

  """

  def __init__(self, batch_size, z_size,
               autocorrelation_taus, noise_variances,
               do_train_prior_ar_atau, do_train_prior_ar_nvar,
               num_steps, name):
    """Create a learnable autoregressive (1) process.

    Args:
      batch_size: The size of the batch, i.e. 0th dim in 2D tensor of samples.
      z_size: The dimension of the distribution, i.e. 1st dim in 2D tensor.
      autocorrelation_taus: The auto correlation time constant of the AR(1)
      process.
        A value of 0 is uncorrelated gaussian noise.
      noise_variances: The variance of the additive noise, *not* the process
        variance.
      do_train_prior_ar_atau: Train or leave as constant, the autocorrelation?
      do_train_prior_ar_nvar: Train or leave as constant, the noise variance?
      num_steps: Number of steps to run the process.
      name: The name to prefix to learned TF variables.
    """

    # Note the use of the plural in all of these quantities.  This is intended
    # to mark that even though a sample z_t from the posterior is thought of a
    # single sample of a multidimensional gaussian, the prior is actually
    # thought of as U AR(1) processes, where U is the dimension of the inferred
    # input.
    size_bx1 = tf.stack([batch_size, 1])
    size__xu = [None, z_size]
    # process variance, the variance at time t over all instantiations of AR(1)
    # with these parameters.
    log_evar_inits_1xu = tf.expand_dims(tf.log(noise_variances), 0)
    self.logevars_1xu = logevars_1xu = \
        tf.Variable(log_evar_inits_1xu, name=name+"/logevars", dtype=tf.float32,
                    trainable=do_train_prior_ar_nvar)
    self.logevars_bxu = logevars_bxu = tf.tile(logevars_1xu, size_bx1)
    logevars_bxu.set_shape(size__xu) # tile loses shape

    # \tau, which is the autocorrelation time constant of the AR(1) process
    log_atau_inits_1xu = tf.expand_dims(tf.log(autocorrelation_taus), 0)
    self.logataus_1xu = logataus_1xu = \
        tf.Variable(log_atau_inits_1xu, name=name+"/logatau", dtype=tf.float32,
                    trainable=do_train_prior_ar_atau)

    # phi in x_t = \mu + phi x_tm1 + \eps
    # phi = exp(-1/tau)
    # phi = exp(-1/exp(logtau))
    # phi = exp(-exp(-logtau))
    phis_1xu = tf.exp(-tf.exp(-logataus_1xu))
    self.phis_bxu = phis_bxu = tf.tile(phis_1xu, size_bx1)
    phis_bxu.set_shape(size__xu)

    # process noise
    # pvar = evar / (1- phi^2)
    # logpvar = log ( exp(logevar) / (1 - phi^2) )
    # logpvar = logevar - log(1-phi^2)
    # logpvar = logevar - (log(1-phi) + log(1+phi))
    self.logpvars_1xu = \
        logevars_1xu - tf.log(1.0-phis_1xu) - tf.log(1.0+phis_1xu)
    self.logpvars_bxu = logpvars_bxu = tf.tile(self.logpvars_1xu, size_bx1)
    logpvars_bxu.set_shape(size__xu)

    # process mean (zero but included in for completeness)
    self.pmeans_bxu = pmeans_bxu = tf.zeros_like(phis_bxu)

    # For sampling from the prior during de-novo generation.
    self.means_t = means_t = [None] * num_steps
    self.logvars_t = logvars_t = [None] * num_steps
    self.samples_t = samples_t = [None] * num_steps
    self.gaussians_t = gaussians_t = [None] * num_steps
    sample_bxu = tf.zeros_like(phis_bxu)
    for t in range(num_steps):
      # process variance used here to make process completely stationary
      if t == 0:
        logvar_pt_bxu = self.logpvars_bxu
      else:
        logvar_pt_bxu = self.logevars_bxu

      z_mean_pt_bxu = pmeans_bxu + phis_bxu * sample_bxu
      gaussians_t[t] = DiagonalGaussian(batch_size, z_size,
                                        mean=z_mean_pt_bxu,
                                        logvar=logvar_pt_bxu)
      sample_bxu = gaussians_t[t].sample
      samples_t[t] = sample_bxu
      logvars_t[t] = logvar_pt_bxu
      means_t[t] = z_mean_pt_bxu

  def logp_t(self, z_t_bxu, z_tm1_bxu=None):
    """Compute the log-likelihood under the distribution for a given time t,
    not the whole sequence.

    Args:
      z_t_bxu: sample to compute likelihood for at time t.
      z_tm1_bxu (optional): sample condition probability of z_t upon.

    Returns:
      The likelihood of p_t under the model at time t. i.e.
        p(z_t|z_tm1) = N(z_tm1 * phis, eps^2)

    """
    if z_tm1_bxu is None:
      return diag_gaussian_log_likelihood(z_t_bxu, self.pmeans_bxu,
                                          self.logpvars_bxu)
    else:
      means_t_bxu = self.pmeans_bxu + self.phis_bxu * z_tm1_bxu
      logp_tgtm1_bxu = diag_gaussian_log_likelihood(z_t_bxu,
                                                    means_t_bxu,
                                                    self.logevars_bxu)
      return logp_tgtm1_bxu

class DiagonalGaussianFromInput(Gaussian):
    """Diagonal Gaussian taken as a linear transform of input.
    """

    def __init__(self, x, z_size, name, var_min=0.0):
        # size_bxn = tf.stack([tf.shape(x)[0], z_size])
        self.mean_bxn = linear(x, z_size, name=(name + "/mean"))
        logvar_bxn = linear(x, z_size, name=(name + "/logvar"))
        if var_min > 0.0:
            logvar_bxn = tf.log(tf.exp(logvar_bxn) + var_min)
        self.logvar_bxn = logvar_bxn
        self.noise_bxn = tf.random_normal(tf.shape(self.logvar_bxn))


def makeInitialState(state_dim, batch_size, name):
    init_stddev = 1 / np.sqrt(float(state_dim))
    init_initter = tf.random_normal_initializer(0.0, init_stddev, dtype=tf.float32)
    init_state = tf.get_variable(name + '_init_state', [1, state_dim],
                                 initializer=init_initter,
                                 dtype=tf.float32, trainable=True)
    tile_dimensions = [batch_size, 1]
    init_state_tiled = tf.tile(init_state,
                               tile_dimensions,
                               name=name + '_init_state_tiled')
    return init_state_tiled


class LinearTimeVarying(object):
    # self.output = linear transform
    # self.output_nl = nonlinear transform

    def __init__(self, inputs, output_size, transform_name, output_name, nonlinearity=None,
                 collections=None, W=None, b=None, normalized=False, do_bias=True):
        num_timesteps = tf.shape(inputs)[1]
        # must return "as_list" to get ints
        input_size = inputs.get_shape().as_list()[2]
        outputs = []
        outputs_nl = []
        # use any matrices provided, if they exist
        if W is not None and b is None:
            raise ValueError('LinearTimeVarying: must provide either W and b, or neither')
        if W is None and b is not None:
            raise ValueError('LinearTimeVarying: must provide either W and b, or neither')

        if W is None and b is None:
            W, b = init_linear_transform(input_size, output_size, name=transform_name,
                                         collections=collections, normalized=normalized,
                                         do_bias=do_bias)
        self.W = W
        self.b = b

        # inputs_permuted = tf.transpose(inputs, perm=[1, 0, 2])
        # initial_outputs = tf.TensorArray(dtype=tf.float32, size=num_timesteps, name='init_linear_outputs')
        # initial_outputs_nl = tf.TensorArray(dtype=tf.float32, size=num_timesteps, name='init_nl_outputs')

        # MRK: replaced tf.while_loop with a simple tf.matmul
        tiled_W = tf.tile(W, [1, tf.shape(inputs)[0]])
        tiled_W = tf.reshape(tiled_W, [-1, W.get_shape()[0], W.get_shape()[1]])

        tiled_b = tf.tile(b, [1, tf.shape(inputs)[0]])
        tiled_b = tf.reshape(tiled_b, [-1, b.get_shape()[0], b.get_shape()[1]])
        output = tf.matmul(inputs, tiled_W) + tiled_b
        if nonlinearity is 'exp':
            output_nl = tf.exp(output)
            self.output_nl = output_nl

        self.output = output  # tf.transpose( output.stack(), perm=[1, 0, 2])
        # tf.transpose( output_nl.stack(), perm=[1, 0, 2])

        # # keep going until the number of timesteps
        # def condition(t, *args):
        #     return t < num_timesteps

        # def iteration(t_, output_, output_nl_):
        #     # apply linear transform to input at this timestep
        #     ## cur = tf.gather(inputs, t, axis=1)
        #     # axis is not supported in 'gather' until 1.3
        #     #cur = tf.gather(inputs_permuted, t)
        #     cur = inputs_permuted[t_,:,:]
        #     output_this_step = tf.matmul(cur, self.W) + self.b
        #     output_ = output_.write(t_, output_this_step)
        #     if nonlinearity is 'exp':
        #         output_nl_ = output_nl_.write(t_, tf.exp(output_this_step) )
        #     return t_+1, output_, output_nl_

        # i = tf.constant(0, dtype=tf.int32)
        # t, output, output_nl = tf.while_loop(condition, iteration, \
        #                                      [i, initial_outputs, initial_outputs_nl],
        #                                      maximum_iterations=num_timesteps)

        ## this is old code for the linear time varying transform
        # was replaced by the above tf.while_loop

        # for step_index in range(tensor_shape[1]):
        #    gred = inputs[:, step_index, :]
        #    fout = tf.matmul(gred, self.W) + self.b
        #    # add a leading dimension for concatenating later
        #    outputs.append( tf.expand_dims( fout , 1) )
        #    if nonlinearity is 'exp':
        #        nlout = tf.exp(fout)
        #        # add a leading dimension for concatenating later
        #        outputs_nl.append( tf.expand_dims( nlout , 1) )
        # concatenate the created list into the factors
        # self.output = tf.concat(outputs, axis=1, name=output_name)
        # if nonlinearity is 'exp':
        #    self.output_nl = tf.concat(outputs_nl, axis=1, name=output_name)
        



class KLCost_GaussianGaussian(object):
    """log p(x|z) + KL(q||p) terms for Gaussian posterior and Gaussian prior. See
    eqn 10 and Appendix B in VAE for latter term,
    http://arxiv.org/abs/1312.6114

    The log p(x|z) term is the reconstruction error under the model.
    The KL term represents the penalty for passing information from the encoder
    to the decoder.
    To sample KL(q||p), we simply sample
          ln q - ln p
    by drawing samples from q and averaging.
    """

    def __init__(self, z, prior_z):
        """Create a lower bound in three parts, normalized reconstruction
        cost, normalized KL divergence cost, and their sum.

        E_q[ln p(z_i | z_{i+1}) / q(z_i | x)
           \int q(z) ln p(z) dz = - 0.5 ln(2pi) - 0.5 \sum (ln(sigma_p^2) + \
              sigma_q^2 / sigma_p^2 + (mean_p - mean_q)^2 / sigma_p^2)

           \int q(z) ln q(z) dz = - 0.5 ln(2pi) - 0.5 \sum (ln(sigma_q^2) + 1)

        Args:
          zs: posterior z ~ q(z|x)
          prior_zs: prior zs
        """
        # L = -KL + log p(x|z), to maximize bound on likelihood
        # -L = KL - log p(x|z), to minimize bound on NLL
        # so 'KL cost' is postive KL divergence
        kl_b = 0.0
        # for z, prior_z in zip(zs, prior_zs):
        assert isinstance(z, Gaussian)
        assert isinstance(prior_z, Gaussian)
        # ln(2pi) terms cancel
        kl_b += 0.5 * tf.reduce_sum(
            prior_z.logvar - z.logvar
            + tf.exp(z.logvar - prior_z.logvar)
            + tf.square((z.mean - prior_z.mean) / tf.exp(0.5 * prior_z.logvar))
            - 1.0, [1])

        self.kl_cost_b = kl_b
        self.kl_cost = tf.reduce_mean(kl_b)

# NOT USED
class KLCost_GaussianGaussianProcessSampled(object):
  """ log p(x|z) + KL(q||p) terms for Gaussian posterior and Gaussian process
  prior via sampling.

  The log p(x|z) term is the reconstruction error under the model.
  The KL term represents the penalty for passing information from the encoder
  to the decoder.
  To sample KL(q||p), we simply sample
        ln q - ln p
  by drawing samples from q and averaging.
  """

  def __init__(self, post_zs, prior_z_process):
    """Create a lower bound in three parts, normalized reconstruction
    cost, normalized KL divergence cost, and their sum.

    Args:
      post_zs: posterior z ~ q(z|x)
      prior_z_process: prior AR(1) process
    """
    #assert len(post_zs) > 1, "GP is for time, need more than 1 time step."
    #assert isinstance(prior_z_process, GaussianProcess), "Must use GP."

    # L = -KL + log p(x|z), to maximize bound on likelihood
    # -L = KL - log p(x|z), to minimize bound on NLL
    # so 'KL cost' is postive KL divergence
    z0_bxu = post_zs.sample()
    logq_bxu = post_zs[0].logp(z0_bxu)
    logp_bxu = prior_z_process.logp_t(z0_bxu)
    z_tm1_bxu = z0_bxu
    for z_t in post_zs[1:]:
      # posterior is independent in time, prior is not
      z_t_bxu = z_t.sample
      logq_bxu += z_t.logp(z_t_bxu)
      logp_bxu += prior_z_process.logp_t(z_t_bxu, z_tm1_bxu)
      z_tm1 = z_t_bxu

    kl_bxu = logq_bxu - logp_bxu
    kl_b = tf.reduce_sum(kl_bxu, [1])
    self.kl_cost_b = kl_b
    self.kl_cost = tf.reduce_mean(kl_b)


"""Wrappers for primitive Neural Net (NN) Operations."""

import numbers
#from tensorflow.python.eager import context
from tensorflow.python.framework import ops
from tensorflow.python.framework import tensor_shape
from tensorflow.python.framework import tensor_util
from tensorflow.python.ops import array_ops
from tensorflow.python.ops import math_ops
from tensorflow.python.ops import random_ops


# dropout that also returns the binary mask
def dropout(x, keep_prob, noise_shape=None, seed=None, name=None,
            binary_tensor=None):  # pylint: disable=invalid-name
    """Computes dropout.
    With probability `keep_prob`, outputs the input element scaled up by
    `1 / keep_prob`, otherwise outputs `0`.  The scaling is so that the expected
    sum is unchanged.
    By default, each element is kept or dropped independently.  If `noise_shape`
    is specified, it must be
    [broadcastable](http://docs.scipy.org/doc/numpy/user/basics.broadcasting.html)
    to the shape of `x`, and only dimensions with `noise_shape[i] == shape(x)[i]`
    will make independent decisions.  For example, if `shape(x) = [k, l, m, n]`
    and `noise_shape = [k, 1, 1, n]`, each batch and channel component will be
    kept independently and each row and column will be kept or not kept together.
    Args:
      x: A floating point tensor.
      keep_prob: A scalar `Tensor` with the same type as x. The probability
        that each element is kept.
      noise_shape: A 1-D `Tensor` of type `int32`, representing the
        shape for randomly generated keep/drop flags.
      seed: A Python integer. Used to create random seeds. See
        @{tf.set_random_seed}
        for behavior.
      name: A name for this operation (optional).
    Returns:
      A Tensor of the same shape of `x`.
    Raises:
      ValueError: If `keep_prob` is not in `(0, 1]` or if `x` is not a floating
        point tensor.
    """
    with ops.name_scope(name, "dropout", [x]) as name:
        x = ops.convert_to_tensor(x, name="x")
        if not x.dtype.is_floating:
            raise ValueError("x has to be a floating point tensor since it's going to"
                             " be scaled. Got a %s tensor instead." % x.dtype)

        # Only apply random dropout if mask is not provided

        if isinstance(keep_prob, numbers.Real) and not 0 < keep_prob <= 1:
            raise ValueError("keep_prob must be a scalar tensor or a float in the "
                             "range (0, 1], got %g" % keep_prob)

        keep_prob = keep_prob if binary_tensor is None else 1 - keep_prob

        keep_prob = ops.convert_to_tensor(keep_prob,
                                          dtype=x.dtype,
                                          name="keep_prob")

        keep_prob.get_shape().assert_is_compatible_with(tensor_shape.scalar())

        if binary_tensor is None:
            # Do nothing if we know keep_prob == 1
            if tensor_util.constant_value(keep_prob) == 1:
                return x, None

            noise_shape = noise_shape if noise_shape is not None else array_ops.shape(x)
            # uniform [keep_prob, 1.0 + keep_prob)
            random_tensor = keep_prob
            random_tensor += random_ops.random_uniform(noise_shape,
                                                       seed=seed,
                                                       dtype=x.dtype)
            # 0. if [keep_prob, 1.0) and 1. if [1.0, 1.0 + keep_prob)
            binary_tensor = math_ops.floor(random_tensor)
        else:
            # check if binary_tensor is a tensor with right shape
            binary_tensor = math_ops.cast(binary_tensor, dtype=x.dtype)
            # pass

        ret = math_ops.div(x, keep_prob) * binary_tensor
        # if context.in_graph_mode():
        ret.set_shape(x.get_shape())
        return ret, binary_tensor
