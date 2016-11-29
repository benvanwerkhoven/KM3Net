from __future__ import print_function

from scipy.sparse import csr_matrix
import numpy as np
import pycuda.driver as drv
from pycuda.compiler import SourceModule

from .context import skip_if_no_cuda_device
from km3net.util import get_kernel_path, get_full_matrix, generate_correlations_table, insert_clique

def test_sparse_purging_kernel():
    skip_if_no_cuda_device()

    prefix = "#define block_size_x 128 \n"
    with open(get_kernel_path()+'remove_nodes.cu', 'r') as f:
        remove_nodes_string = prefix+f.read()
    with open(get_kernel_path()+'minimum_degree.cu', 'r') as f:
        minimum_string = prefix+f.read()

    #start PyCuda
    drv.init()
    context = drv.Device(0).make_context()
    devprops = { str(k): v for (k, v) in context.get_device().get_attributes().items() }
    cc = str(devprops['COMPUTE_CAPABILITY_MAJOR']) + str(devprops['COMPUTE_CAPABILITY_MINOR'])

    #compile the kernels using PyCuda
    minimum_degree = SourceModule(minimum_string, options=['-Xcompiler=-Wall'],
                    arch='compute_' + cc, code='sm_' + cc,
                    cache_dir=False).get_function("minimum_degree")
    combine_blocked_min_num = SourceModule(minimum_string, options=['-Xcompiler=-Wall'],
                    arch='compute_' + cc, code='sm_' + cc,
                    cache_dir=False).get_function("combine_blocked_min_num")
    remove_nodes = SourceModule(remove_nodes_string, options=['-Xcompiler=-Wall'],
                    arch='compute_' + cc, code='sm_' + cc,
                    cache_dir=False).get_function("remove_nodes")

    #setup test case
    N = np.int32(300)
    sliding_window_width = np.int32(150)
    problem_size = (N, 1)
    params = { "block_size_x": 128 }
    max_blocks = (np.ceil(N / float(params["block_size_x"]))).astype(np.int32)

    threads = (params["block_size_x"], 1, 1)
    grid = (int(max_blocks), 1)

    #generate input data with a relatively high density of correlated hits
    correlations = generate_correlations_table(N, sliding_window_width, cutoff=2.0)

    #obtain full correlation matrix for reference
    dense_matrix = get_full_matrix(correlations)

    #insert a clique in the test data
    dense_matrix, clique_indices, clique_size = insert_clique(dense_matrix, sliding_window_width, 12)

    #setup all kernel inputs
    degrees = dense_matrix.sum(axis=0).astype(np.int32)
    prefix_sums = np.cumsum(degrees).astype(np.int32)
    sparse_matrix = csr_matrix(dense_matrix)

    row_idx = (sparse_matrix.nonzero()[0]).astype(np.int32)
    col_idx = (sparse_matrix.nonzero()[1]).astype(np.int32)

    minimum = np.zeros(max_blocks).astype(np.int32)
    num_nodes = np.zeros(max_blocks).astype(np.int32)

    #setup GPU memory
    def allocate_and_copy(arg):
        gpu_arg = drv.mem_alloc(arg.nbytes)
        drv.memcpy_htod(gpu_arg, arg)
        return gpu_arg

    d_degrees = allocate_and_copy(degrees)
    d_prefix_sums = allocate_and_copy(prefix_sums)
    d_row_idx = allocate_and_copy(row_idx)
    d_col_idx = allocate_and_copy(col_idx)
    d_minimum = allocate_and_copy(minimum)
    d_num_nodes = allocate_and_copy(num_nodes)

    #call the first kernel to obtain the block-wide minimum and num_nodes
    args = [d_minimum, d_num_nodes, d_degrees, d_row_idx, d_col_idx, d_prefix_sums, N]
    minimum_degree(*args, block=threads, grid=grid)

    drv.memcpy_dtoh(minimum, d_minimum)
    drv.memcpy_dtoh(num_nodes, d_num_nodes)
    print("minimum", minimum)
    print("num_nodes", num_nodes)

    #call the helper kernel to combine these values
    args = [d_minimum, d_num_nodes, max_blocks]
    combine_blocked_min_num(*args, block=threads, grid=(1,1))

    current_minimum = np.array([0]).astype(np.int32)
    current_num_nodes = np.array([0]).astype(np.int32)
    drv.memcpy_dtoh(current_minimum, d_minimum)
    drv.memcpy_dtoh(current_num_nodes, d_num_nodes)

    drv.memcpy_dtoh(degrees, d_degrees)
    print("degrees before")
    print(degrees)
    print("start purging")

    counter = 0
    while current_minimum+1 < current_num_nodes:

        #failsafe en ensure test termination
        counter += 1
        #if counter > clique_size*2:
        #    print("purging algorithm failed to detect the clique")
        #    context.pop()
        #    assert False

        print("current_minimum", current_minimum)
        print("current_num_nodes", current_num_nodes)

        #call the kernel to remove nodes
        args = [d_degrees, d_row_idx, d_col_idx, d_prefix_sums, d_minimum, N]
        remove_nodes(*args, block=threads, grid=grid)

        drv.memcpy_dtoh(degrees, d_degrees)
        print(degrees)

        args = [d_minimum, d_num_nodes, d_degrees, d_row_idx, d_col_idx, d_prefix_sums, N]
        minimum_degree(*args, block=threads, grid=grid)

        drv.memcpy_dtoh(minimum, d_minimum)
        drv.memcpy_dtoh(num_nodes, d_num_nodes)
        print("minimum", minimum)
        print("num_nodes", num_nodes)

        args = [d_minimum, d_num_nodes, max_blocks]
        combine_blocked_min_num(*args, block=threads, grid=(1,1))

        drv.memcpy_dtoh(current_minimum, d_minimum)
        drv.memcpy_dtoh(current_num_nodes, d_num_nodes)

    print("finished purging")
    print("current_minimum", current_minimum)
    print("current_num_nodes", current_num_nodes)

    print("inserted clique=")
    print(clique_indices)

    print("found clique=")
    drv.memcpy_dtoh(degrees, d_degrees)
    print(degrees)
    indices = np.array(range(degrees.size))
    found_indices = indices[degrees >= current_minimum]
    print(found_indices)

    context.pop()

    assert all(found_indices == clique_indices)
