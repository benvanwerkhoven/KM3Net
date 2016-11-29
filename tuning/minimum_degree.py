#!/usr/bin/env python
from __future__ import print_function

from scipy.sparse import csr_matrix
from collections import OrderedDict
import numpy as np
from kernel_tuner import tune_kernel

from context import get_kernel_path
from km3net.util import generate_large_correlations_table, create_sparse_matrix

def tune_minimum_degree():

    with open(get_kernel_path()+'minimum_degree.cu', 'r') as f:
        kernel_string = f.read()

    N = np.int32(4.5e6)
    sliding_window_width = np.int32(1500)
    problem_size = (N, 1)

    #tune params here
    tune_params = OrderedDict()
    tune_params["block_size_x"] = [2**i for i in range(5,11)]
    tune_params["threshold"] = [3]

    max_blocks = int(np.ceil(N / float(max(tune_params["block_size_x"]))))

    #generate input data with an expected density of correlated hits
    correlations, sums = generate_large_correlations_table(N, sliding_window_width)
    row_idx, col_idx, prefix_sums = create_sparse_matrix(correlations, sums)

    #setup all kernel inputs
    minimum = np.zeros(max_blocks).astype(np.int32)
    num_nodes = np.zeros(max_blocks).astype(np.int32)

    #call the CUDA kernel
    args = [minimum, num_nodes, sums, row_idx, col_idx, prefix_sums, N]
    return tune_kernel("minimum_degree", kernel_string, problem_size, args, tune_params, verbose=True)



if __name__ == "__main__":
    tune_minimum_degree()
