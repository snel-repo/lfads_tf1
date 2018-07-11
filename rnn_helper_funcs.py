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

from customcells import ComplexCell
from customcells import CustomGRUCell


class BidirectionalDynamicRNN(object):
    def __init__(self, state_dim, batch_size, name, sequence_lengths,
                 inputs=None, initial_state=None, rnn_type='gru',
                 clip_value = None, recurrent_collections = None):
        #                 output_keep_prob=1.0,
        #                 input_keep_prob=1.0):


        # pick your cell
        if rnn_type.lower() == 'lstm':
            self.cell = tf.nn.rnn_cell.LSTMCell(num_units=state_dim,
                                                state_is_tuple=True)
        elif rnn_type.lower() == 'gru':
            self.cell = tf.nn.rnn_cell.GRUCell(num_units=state_dim)
            #self.cell = tf.contrib.cudnn_rnn.CudnnCompatibleGRUCell(num_units=state_dim)
        elif rnn_type.lower() == 'customgru':
            self.cell = CustomGRUCell(num_units = state_dim,
                                      batch_size = batch_size,
                                      clip_value = clip_value,
                                      recurrent_collections = recurrent_collections)
        else:
            raise ValueError("Didn't understand rnn_type '%s'."%(rnn_type))

        
        if initial_state is None:
            # need initial states for fw and bw
            self.init_stddev = 1 / np.sqrt(float(state_dim))
            self.init_initter = tf.random_normal_initializer(0.0, self.init_stddev, dtype=tf.float32)

            self.init_h_fw = tf.get_variable(name + '_init_h_fw', [1, state_dim],
                                             initializer=self.init_initter,
                                             dtype=tf.float32)
            self.init_h_bw = tf.get_variable(name + '_init_h_bw', [1, state_dim],
                                             initializer=self.init_initter,
                                             dtype=tf.float32)
            # # lstm has a second parameter c
            # if rnn_type.lower() == 'lstm':
            #     self.init_c_fw = tf.get_variable(name + '_init_c_fw', [1, state_dim],
            #                                      initializer=self.init_initter,
            #                                      dtype=tf.float32)
            #     self.init_c_bw = tf.get_variable(name + '_init_c_bw', [1, state_dim],
            #                                      initializer=self.init_initter,
            #                                      dtype=tf.float32)

            tile_dimensions = [batch_size, 1]

            # tile the h param
            self.init_h_fw_tiled = tf.tile(self.init_h_fw,
                                           tile_dimensions, name=name + '_h_fw_tile')
            self.init_h_bw_tiled = tf.tile(self.init_h_bw,
                                           tile_dimensions, name=name + '_h_bw_tile')
            # tile the c param if needed
            # if rnn_type.lower() == 'lstm':
            #     self.init_c_fw_tiled = tf.tile(self.init_c_fw,
            #                                    tile_dimensions, name=name + '_c_fw_tile')
            #     self.init_c_bw_tiled = tf.tile(self.init_c_bw,
            #                                    tile_dimensions, name=name + '_c_bw_tile')

            # do tupling if needed
            if rnn_type.lower() == 'lstm':
                # lstm state is a tuple
                #init_fw = tf.contrib.rnn.LSTMStateTuple(self.init_c_fw_tiled, self.init_h_fw_tiled)
                #init_bw = tf.contrib.rnn.LSTMStateTuple(self.init_c_bw_tiled, self.init_h_bw_tiled)
                #self.init_fw = tf.zeros_like( init_fw )
                #self.init_bw = tf.zeros_like( init_bw )
                self.init_fw = self.cell.zero_state(batch_size, tf.float32)
                self.init_bw = self.cell.zero_state(batch_size, tf.float32)
            else:
                #self.init_fw = self.init_h_fw_tiled
                #self.init_bw = self.init_h_bw_tiled
                self.init_fw = tf.zeros_like( self.init_h_fw_tiled )
                self.init_bw = tf.zeros_like( self.init_h_bw_tiled )
                
        else:  # if initial state is None
            self.init_fw, self.init_bw = initial_state

        # add dropout if requested
        #self.cell = tf.contrib.rnn.DropoutWrapper(
        #        self.cell, output_keep_prob=output_keep_prob)


        # for some reason I can't get dynamic_rnn to work without inputs
        #  so generate fake inputs if needed...
        if inputs is None:
            inputs = tf.zeros([batch_size, sequence_lengths, 1],
                              dtype=tf.float32)
        #inputs.set_shape((None, sequence_lengths, inputs.get_shape()[2]))
        self.states, self.last = tf.nn.bidirectional_dynamic_rnn(
            cell_fw=self.cell,
            cell_bw=self.cell,
            dtype=tf.float32,
            # sequence_length = sequence_lengths,
            inputs=inputs,
            initial_state_fw=self.init_fw,
            initial_state_bw=self.init_bw,
        )

        # concatenate the outputs of the encoders (h only) into one vector
        self.last_fw, self.last_bw = self.last

        if rnn_type.lower() == 'lstm':
            #self.last_fw.h, _ = self.last_fw
            #self.last_bw.h, _ = self.last_bw
            self.last_tot = tf.concat(axis=1, values=[self.last_fw.h, self.last_bw.h])
        else:
            self.last_tot = tf.concat(axis=1, values=[self.last_fw, self.last_bw])


class DynamicRNN(object):
    def __init__(self, state_dim, batch_size, name, sequence_lengths,
                 inputs=None, initial_state=None, rnn_type='gru',
                 clip_value = None, recurrent_collections = None):
        #                 output_keep_prob=1.0,
        #                 input_keep_prob=1.0):
        # pick your cell
        if rnn_type.lower() == 'lstm':
            self.cell = tf.nn.rnn_cell.LSTMCell(num_units=state_dim,
                                                state_is_tuple=True)
        elif rnn_type.lower() == 'gru':
            self.cell = tf.nn.rnn_cell.GRUCell(num_units=state_dim)
            #self.cell = tf.contrib.cudnn_rnn.CudnnCompatibleGRUCell(num_units=state_dim)
        elif rnn_type.lower() == 'customgru':
            self.cell = CustomGRUCell(num_units = state_dim,
                                      batch_size = batch_size,
                                      clip_value = clip_value,
                                      recurrent_collections = recurrent_collections)
        else:
            raise ValueError("Didn't understand rnn_type '%s'."%(rnn_type))
            
        if initial_state is None:
            # need initial states for fw and bw
            self.init_stddev = 1 / np.sqrt(float(state_dim))
            self.init_initter = tf.random_normal_initializer(0.0, self.init_stddev, dtype=tf.float32)

            self.init_h = tf.get_variable(name + '_init_h', [1, state_dim],
                                          initializer=self.init_initter,
                                          dtype=tf.float32)
            tile_dimensions = [batch_size, 1]
            self.init_h_tiled = tf.tile(self.init_h,
                                        tile_dimensions, name=name + '_tile')

            if rnn_type.lower() == 'lstm':
                 self.init_c = tf.get_variable(name + '_init_c', [1, state_dim],
                                               initializer=self.init_initter,
                                               dtype=tf.float32)

                 self.init_c_tiled = tf.tile(self.init_c,
                                             tile_dimensions, name=name + '_tile')
                 # tuple for lstm
                 #init1 = tf.contrib.rnn.LSTMStateTuple(self.init_c_tiled, self.init_h_tiled)
                 self.init = self.cell.zero_state(batch_size, dtype = tf.float32)
            else:
                #self.init = self.init_h_tiled
                self.init = tf.zeros_like( self.init_h_tiled )
        else:  # if initial state is None
            # for lstms, we have to split whatever initial state was passed in and turn it into a tuple
            if rnn_type.lower() == 'lstm':
                split0, split1 = tf.split( initial_state, 2, axis=1 )
                self.init = tf.contrib.rnn.LSTMStateTuple( split0, split1 )
            else:
                self.init = initial_state
            

        # add dropout if requested
        #self.cell = tf.contrib.rnn.DropoutWrapper(
        #        self.cell, output_keep_prob=output_keep_prob)

        # for some reason I can't get dynamic_rnn to work without inputs
        #  so generate fake inputs if needed...
        if inputs is None:
            inputs = tf.zeros([batch_size, sequence_lengths, 1],
                              dtype=tf.float32)
        # call dynamic_rnn
        #inputs.set_shape((None, sequence_lengths, inputs.get_shape()[2]))
        states, last = tf.nn.dynamic_rnn(
            cell=self.cell,
            dtype=tf.float32,
            inputs=inputs,
            initial_state=self.init
        )
        # sequence_length = sequence_lengths,

        if rnn_type.lower() == 'lstm':
            #self.last_fw.h, _ = self.last_fw
            #self.last_bw.h, _ = self.last_bw
            self.last = last.h
            # lstm states only only output h state (and not c)
            self.states = states
        else:
            self.last = last
            self.states = states

