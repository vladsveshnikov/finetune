import numpy as np
import tensorflow as tf

from finetune.utils import shape_list
from finetune.activations import act_fns


def norm(x, scope, axis=[-1], e=None, fp16=False, debug=False):
    with tf.variable_scope(scope):
        e = e or (1e-5 if not fp16 else 1e-1)
        n_state = shape_list(x)[-1]
        g = tf.get_variable("g", [n_state], initializer=tf.constant_initializer(1))
        b = tf.get_variable("b", [n_state], initializer=tf.constant_initializer(0))
        if fp16:
            g = tf.cast(g, tf.float16)
            b = tf.cast(b, tf.float16)
        u = tf.reduce_mean(x, axis=axis, keepdims=True)
        s = tf.reduce_mean(tf.square(x - u), axis=axis, keepdims=True)
        if debug:
            u = tf.Print(u, [u[0, 0], s[0, 0]])
        x = (x - u) * tf.rsqrt(s + e)
        x = x * g + b
        return x


def dropout(x, pdrop, train):
    if train and pdrop > 0:
        x = tf.nn.dropout(x, 1 - pdrop)
    return x


def mask_attn_weights(w, fp16=False):
    n = shape_list(w)[-1]
    b = tf.matrix_band_part(tf.ones([n, n]), -1, 0)
    b = tf.reshape(b, [1, 1, n, n])
    if fp16:
        b = tf.cast(b, tf.float16)
    w = w * b + (-1e4 if fp16 else -1e9) * (1 - b)
    return w


def _attn(q, k, v, attn_pdrop, train=False, scale=False, mask=True, fp16=False):
    w = tf.matmul(q, k)

    if scale:
        n_state = shape_list(v)[-1]
        w = w * tf.rsqrt(tf.cast(n_state, tf.float16 if fp16 else tf.float32))

    if mask:
        w = mask_attn_weights(w, fp16=fp16)
    w = tf.nn.softmax(w)

    w = dropout(w, attn_pdrop, train)

    a = tf.matmul(w, v)
    return a


def split_states(x, n):
    x_shape = shape_list(x)
    m = x_shape[-1]
    new_x_shape = x_shape[:-1] + [n, m // n]
    return tf.reshape(x, new_x_shape)


def merge_states(x):
    x_shape = shape_list(x)
    new_x_shape = x_shape[:-2] + [np.prod(x_shape[-2:])]
    return tf.reshape(x, new_x_shape)


def split_heads(x, n, k=False):
    if k:
        return tf.transpose(split_states(x, n), [0, 2, 3, 1])
    else:
        return tf.transpose(split_states(x, n), [0, 2, 1, 3])


def merge_heads(x):
    return merge_states(tf.transpose(x, [0, 2, 1, 3]))


def conv1d(x, scope, nf, rf, w_init=tf.random_normal_initializer(stddev=0.02), b_init=tf.constant_initializer(0),
           pad='VALID', train=False, fp16=False):
    with tf.variable_scope(scope):
        nx = shape_list(x)[-1]
        w = tf.get_variable("w", [rf, nx, nf], initializer=w_init)
        b = tf.get_variable("b", [nf], initializer=b_init)
        if fp16:
            w = tf.cast(w, tf.float16)
            b = tf.cast(b, tf.float16)
        if rf == 1:  # faster 1x1 conv
            c = tf.reshape(tf.matmul(tf.reshape(x, [-1, nx]), tf.reshape(w, [-1, nf])) + b, shape_list(x)[:-1] + [nf])
        else:  # was used to train LM
            c = tf.nn.conv1d(x, w, stride=1, padding=pad) + b
        return c


def attn(x, scope, n_state, n_head, resid_pdrop, attn_pdrop, train=False, scale=False, mask=True, fp16=False):
    assert n_state % n_head == 0
    with tf.variable_scope(scope):
        c = conv1d(x, 'c_attn', n_state * 3, 1, train=train, fp16=fp16)
        q, k, v = tf.split(c, 3, 2)
        q = split_heads(q, n_head)
        k = split_heads(k, n_head, k=True)
        v = split_heads(v, n_head)
        a = _attn(q, k, v, attn_pdrop=attn_pdrop, train=train, scale=scale,
                  mask=mask, fp16=fp16)
        a = merge_heads(a)
        a = conv1d(a, 'c_proj', n_state, 1, train=train, fp16=fp16)
        a = dropout(a, resid_pdrop, train)
        return a


def mlp(x, scope, n_state, act_fn, resid_pdrop, train=False, fp16=False):
    with tf.variable_scope(scope):
        nx = shape_list(x)[-1]
        act = act_fns[act_fn]
        h = act(conv1d(x, 'c_fc', n_state, 1, train=train, fp16=fp16))
        h2 = conv1d(h, 'c_proj', nx, 1, train=train, fp16=fp16)
        h2 = dropout(h2, resid_pdrop, train)
        return h2


def block(x, n_head, act_fn, resid_pdrop, attn_pdrop, scope, train=False, scale=False, fp16=False):
    with tf.variable_scope(scope):
        nx = shape_list(x)[-1]
        a = attn(x, 'attn', nx, n_head, resid_pdrop, attn_pdrop, train=train, scale=scale, fp16=fp16)
        n = norm(x + a, 'ln_1', e=1e-3 if fp16 else 1e-5, fp16=fp16)
        m = mlp(n, 'mlp', nx * 4, act_fn, resid_pdrop, train=train, fp16=fp16)
        h = norm(n + m, 'ln_2', e=1e-3 if fp16 else 1e-5, fp16=fp16)
        return h


def embed(X, we):
    e = tf.gather(we, X)
    #    h = add_timing_signal_1d(e[:, :, 0])
    h = tf.reduce_sum(e, 2)
    return h


def embed_no_timing(X, we):
    return tf.gather(we, X[:, :, 0])
