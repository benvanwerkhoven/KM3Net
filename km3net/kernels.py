# from __future__ import print_function

import numpy as np
import pycuda.driver as drv
from pycuda.compiler import SourceModule

from km3net.util import *

class CorrelateSparse(object):
    """ Base class for kernels that correlate hits and output a sparse matrix """

    def __init__(self, N, sliding_window_width, cc, kernel_name, block_size_x):
        """ Generic constructor, to be overridden by subclasses

        Subclasses should call this constructor with the right kernel_name
        """
        self.N = np.int32(N)
        self.sliding_window_width = np.int32(sliding_window_width)
        self.threads = (block_size_x, 1, 1)
        self.grid = (int(np.ceil(N/float(block_size_x))), 1)

        with open(get_kernel_path()+'correlate_full.cu', 'r') as f:
            kernel_string = f.read()
        prefix = "#define block_size_x " + str(block_size_x) + "\n" + "#define window_width " + str(sliding_window_width) + "\n"
        kernel_string = prefix + kernel_string

        compiler_options = ['-Xcompiler=-Wall', '--std=c++11', '-O3']

        self.compute_sums = SourceModule("#define write_sums 1\n" + kernel_string, options=compiler_options,
                    arch='compute_' + cc, code='sm_' + cc,
                    cache_dir=False, no_extern_c=True).get_function(kernel_name)
        self.compute_sparse_matrix = SourceModule("#define write_spm 1\n" + kernel_string, options=compiler_options,
                    arch='compute_' + cc, code='sm_' + cc,
                    cache_dir=False, no_extern_c=True).get_function(kernel_name)


    def compute(self, x, y, z, ct):
        """ perform a computation of the correlating algorithm and produce sparse matrix

        :param x: an array storing the x-coordinates of the hits,
            either a numpy ndarray or an array stored on the GPU
        :type x: numpy ndarray or pycuda.driver.DeviceAllocation

        :param y: an array storing the y-coordinates of the hits,
            either a numpy ndarray or an array stored on the GPU
        :type y: numpy ndarray or pycuda.driver.DeviceAllocation

        :param z: an array storing the z-coordinates of the hits,
            either a numpy ndarray or an array stored on the GPU
        :type z: numpy ndarray or pycuda.driver.DeviceAllocation

        :param ct: an array storing the 'ct' value of the hits,
            either a numpy ndarray or an array stored on the GPU.
            For the quadratic difference kernel this is the time in nano seconds multiplied with the speed of light.
            For the match 3b kernel this is the time of the hits in nano seconds.
        :type ct: numpy ndarray or pycuda.driver.DeviceAllocation

        :returns: The sparse matrix in CSR notation, and the number of correlated hits per hit (degree).

            * d_col_idx: stores the column indices, the size equals the number of correlations (or edges in the graph).
            * d_prefix_sums: stores per row, the start index of the row within the column index array. The size of d_prefix_sums is equal to the number of hits.
            * d_degrees: The number of correlated hits per hit, stored as an array of size equal to the number of hits.

        :rtype: tuple( pycuda.driver.DeviceAllocation )

        """
        d_x = ready_input(x)
        d_y = ready_input(y)
        d_z = ready_input(z)
        d_ct = ready_input(ct)

        #run the first kernel
        row_idx = np.zeros(10).astype(np.int32)
        col_idx = np.zeros(10).astype(np.int32)
        prefix_sums = np.zeros(10).astype(np.int32)
        degrees = np.zeros(self.N).astype(np.int32)

        d_degrees = allocate_and_copy(degrees)
        d_row_idx = allocate_and_copy(row_idx)
        d_col_idx = allocate_and_copy(col_idx)
        d_prefix_sums = allocate_and_copy(prefix_sums)

        args_list = [d_row_idx, d_col_idx, d_prefix_sums, d_degrees, self.N, self.sliding_window_width, d_x, d_y, d_z, d_ct]
        self.compute_sums(*args_list, block=self.threads, grid=self.grid, stream=None, shared=0)

        #allocate space to store sparse matrix
        drv.memcpy_dtoh(degrees, d_degrees)
        total_correlated_hits = degrees.sum()
        col_idx = np.zeros(total_correlated_hits).astype(np.int32)
        prefix_sums = np.cumsum(degrees).astype(np.int32)

        d_col_idx = allocate_and_copy(col_idx)
        d_prefix_sums = allocate_and_copy(prefix_sums)

        args_list2 = [d_row_idx, d_col_idx, d_prefix_sums, d_degrees, self.N, self.sliding_window_width, d_x, d_y, d_z, d_ct]
        self.compute_sparse_matrix(*args_list2, block=self.threads, grid=self.grid, stream=None, shared=0)

        return d_col_idx, d_prefix_sums, d_degrees, total_correlated_hits


class QuadraticDifferenceSparse(CorrelateSparse):
    """ class that provides an interface to the Quadratic Difference GPU Kernel and maintains GPU state"""

    def __init__(self, N, sliding_window_width=1500, cc='52'):
        """instantiate QuadraticDifferenceSparse

        Create the object that provides an interface to the GPU kernel for performing the
        Quadratic Difference algorithm. This implementation of the algorithm stores the
        correlation table in a sparse manner, using CSR notation.

        When this object is instantiated the CUDA kernel
        code is compiled and some of the GPU memory is allocated.

        :param N: The largest number of hits that are to be processed by one iteration
                of the quadratic difference algorithm.
        :type N: int

        :param sliding_window_width: The width of the 'window' in which we look for correlated
                hits. This is related to the size of the detector and the expected rate of background
                induced hits. The value we currently assume is 1500.
        :type sliding_window_width: int

        :param cc: The CUDA compute capability of the target device as a string, consisting
                of the major and minor number concatenated without any separators.
        :type cc: string

        """
        block_size_x = 256
        super().__init__(N, sliding_window_width, cc, "quadratic_difference_full", block_size_x)


    def compute(self, x, y, z, ct):
        """ perform a computation of the correlating algorithm and produce sparse matrix

        :param x: an array storing the x-coordinates of the hits,
            either a numpy ndarray or an array stored on the GPU
        :type x: numpy ndarray or pycuda.driver.DeviceAllocation

        :param y: an array storing the y-coordinates of the hits,
            either a numpy ndarray or an array stored on the GPU
        :type y: numpy ndarray or pycuda.driver.DeviceAllocation

        :param z: an array storing the z-coordinates of the hits,
            either a numpy ndarray or an array stored on the GPU
        :type z: numpy ndarray or pycuda.driver.DeviceAllocation

        :param ct: an array storing the 'ct' value of the hits,
            either a numpy ndarray or an array stored on the GPU.
            This is the time in nano seconds multiplied with the speed of light.
        :type ct: numpy ndarray or pycuda.driver.DeviceAllocation

        :returns: The sparse matrix in CSR notation, and the number of correlated hits per hit (degree).

            * d_col_idx: stores the column indices, the size equals the number of correlations (or edges in the graph).
            * d_prefix_sums: stores per row, the start index of the row within the column index array. The size of d_prefix_sums is equal to the number of hits.
            * d_degrees: The number of correlated hits per hit, stored as an array of size equal to the number of hits.

        :rtype: tuple( pycuda.driver.DeviceAllocation )

        """
        return super().compute(x, y, z, ct)

class Match3BSparse(CorrelateSparse):
    """ class that provides an interface to the Match 3B GPU Kernel and maintains GPU state"""

    def __init__(self, N, sliding_window_width=1500, cc='52'):
        """instantiate Match3BSparse

        Create the object that provides an interface to the GPU kernel for performing the
        Match 3B algorithm. This implementation of the algorithm stores the
        correlation table in a sparse manner, using CSR notation.

        When this object is instantiated the CUDA kernel
        code is compiled and some of the GPU memory is allocated.

        :param N: The largest number of hits that are to be processed by one iteration
                of the match 3b algorithm.
        :type N: int

        :param sliding_window_width: The width of the 'window' in which we look for correlated
                hits. This is related to the size of the detector and the expected rate of background
                induced hits. The value we currently assume is 1500.
        :type sliding_window_width: int

        :param cc: The CUDA compute capability of the target device as a string, consisting
                of the major and minor number concatenated without any separators.
        :type cc: string

        """
        block_size_x = 512
        super().__init__(N, sliding_window_width, cc, "match3b_full", block_size_x)


    def compute(self, x, y, z, t):
        """ perform a computation of the correlating algorithm and produce sparse matrix

        :param x: an array storing the x-coordinates of the hits,
            either a numpy ndarray or an array stored on the GPU
        :type x: numpy ndarray or pycuda.driver.DeviceAllocation

        :param y: an array storing the y-coordinates of the hits,
            either a numpy ndarray or an array stored on the GPU
        :type y: numpy ndarray or pycuda.driver.DeviceAllocation

        :param z: an array storing the z-coordinates of the hits,
            either a numpy ndarray or an array stored on the GPU
        :type z: numpy ndarray or pycuda.driver.DeviceAllocation

        :param t: an array storing the 't' value of the hits,
            either a numpy ndarray or an array stored on the GPU.
            This is the time in nano seconds.
        :type t: numpy ndarray or pycuda.driver.DeviceAllocation

        :returns: The sparse matrix in CSR notation, and the number of correlated hits per hit (degree).

            * d_col_idx: stores the column indices, the size equals the number of correlations (or edges in the graph).
            * d_prefix_sums: stores per row, the start index of the row within the column index array. The size of d_prefix_sums is equal to the number of hits.
            * d_degrees: The number of correlated hits per hit, stored as an array of size equal to the number of hits.

        :rtype: tuple( pycuda.driver.DeviceAllocation )

        """
        return super().compute(x, y, z, t)



class PurgingSparse(object):
    """ class that provides an interface to the GPU Kernels used for Purging and maintains GPU state"""

    def __init__(self, N, cc):
        """instantiate PurgingSparse

        Create the object that provides an interface to the GPU kernel for performing the
        Purging algorithm on a sparse correlation table. This implementation of the
        algorithm uses the correlation table in a sparse manner, produced by the
        Quadratic Difference Sparse kernel. When this object is instantiated the
        CUDA kernel codes are compiled.

        :param N: The largest number of hits that are to be processed by one iteration
                of the quadratic difference algorithm.
        :type N: int

        :param cc: The CUDA compute capability of the target device as a string, consisting
                of the major and minor number concatenated without any separators.
        :type cc: string

        """

        self.N = N

        with open(get_kernel_path()+'remove_nodes.cu', 'r') as f:
            remove_nodes_string = f.read()
        with open(get_kernel_path()+'minimum_degree.cu', 'r') as f:
            minimum_string = f.read()
        self.minimum_degree = SourceModule(minimum_string, options=['-Xcompiler=-Wall'],
                    arch='compute_' + cc, code='sm_' + cc,
                    cache_dir=False).get_function("minimum_degree")
        self.combine_blocked_min_num = SourceModule(minimum_string, options=['-Xcompiler=-Wall'],
                    arch='compute_' + cc, code='sm_' + cc,
                    cache_dir=False).get_function("combine_blocked_min_num")
        self.remove_nodes = SourceModule(remove_nodes_string, options=['-Xcompiler=-Wall'],
                    arch='compute_' + cc, code='sm_' + cc,
                    cache_dir=False).get_function("remove_nodes")

        block_size_x = 128
        self.max_blocks = (np.ceil(N / float(block_size_x))).astype(np.int32)
        self.threads = (block_size_x, 1, 1)
        self.grid = (int(self.max_blocks), 1)



    def compute(self, col_idx, prefix_sums, degrees, shift=0):
        """ perform purging on a sparse matrix

        :param col_idx: A device allocation storing the column indices of the sparse matrix.
            The size of col_idx equals the number of correlations.
        :type col_idx: numpy.ndarray or pycuda.driver.DeviceAllocation

        :param prefix_sums: The start index of each row within the column index array.
            The size of prefix_sums is equal to the number of hits.
        :type prefix_sums: numpy.ndarray or pycuda.driver.DeviceAllocation

        :param degrees: The number of correlated hits per hit, stored as an array of size equal to the number of hits.
        :type degrees: numpy.ndarray or pycuda.driver.DeviceAllocation

        :param shift: Optional parameter that can be used to shift the indices of the nodes
            that remain after purging. This could be used when sliding through a larger time
            slice to convert the indices from within the current slice to a global index.
        :type shift: int

        :returns: The list of node indices of the nodes that remain after purging.
        :rtype: list ( int )

        """
        d_col_idx = ready_input(col_idx)
        d_prefix_sums = ready_input(prefix_sums)
        d_degrees = ready_input(degrees)

        minimum = np.zeros(self.max_blocks).astype(np.int32)
        num_nodes = np.zeros(self.max_blocks).astype(np.int32)

        #setup GPU memory
        d_row_idx = allocate_and_copy(np.zeros(10).astype(np.int32))
        d_minimum = allocate_and_copy(minimum)
        d_num_nodes = allocate_and_copy(num_nodes)

        args_minimum = [d_minimum, d_num_nodes, d_degrees, d_row_idx, d_col_idx, d_prefix_sums, self.N]
        self.minimum_degree(*args_minimum, block=self.threads, grid=self.grid)

        #call the helper kernel to combine these values
        args_combine = [d_minimum, d_num_nodes, self.max_blocks]
        self.combine_blocked_min_num(*args_combine, block=self.threads, grid=(1,1))

        current_minimum = np.array([0]).astype(np.int32)
        current_num_nodes = np.array([0]).astype(np.int32)
        drv.memcpy_dtoh(current_minimum, d_minimum)
        drv.memcpy_dtoh(current_num_nodes, d_num_nodes)
        #print("current_minimum", current_minimum)
        #print("current_num_nodes", current_num_nodes)

        args_remove = [d_degrees, d_row_idx, d_col_idx, d_prefix_sums, d_minimum, self.N]

        counter = 0
        while current_minimum+1 < current_num_nodes:

            counter += 1
            self.remove_nodes(*args_remove, block=self.threads, grid=self.grid)
            self.minimum_degree(*args_minimum, block=self.threads, grid=self.grid)
            self.combine_blocked_min_num(*args_combine, block=self.threads, grid=(1,1))
            drv.memcpy_dtoh(current_minimum, d_minimum)
            drv.memcpy_dtoh(current_num_nodes, d_num_nodes)

            #print("current_minimum", current_minimum)
            #print("current_num_nodes", current_num_nodes)

        #print("finished purging, iterations = ", counter)

        degrees = np.zeros(self.N).astype(np.int32)
        drv.memcpy_dtoh(degrees, d_degrees)
        if (current_num_nodes > 0):
            #print("found clique of size=", current_num_nodes)
            indices = np.array(range(degrees.size))
            found_indices = indices[degrees >= current_minimum]
            #print(found_indices + shift)
            return found_indices + shift

        return []


class LouvainSparse(object):
    """ class that provides an interface to the GPU Kernels used for Louvain Community Detection algorithm """

    def __init__(self, N, cc):
        """instantiate LouvainSparse

        :param N: The largest number of hits that are to be processed by one iteration
                of the quadratic difference algorithm.
        :type N: int

        :param cc: The CUDA compute capability of the target device as a string, consisting
                of the major and minor number concatenated without any separators.
        :type cc: string

        """

        self.N = N

        with open(get_kernel_path()+'louvain.cu', 'r') as f:
            louvain_kernel = f.read()

        self.move_nodes = SourceModule(louvain_kernel, options=['-Xcompiler=-Wall'],
                                       arch='compute_' + cc, code='sm_' + cc,
                                       cache_dir=False).get_function("move_nodes")

        self.calc_community_degrees = SourceModule(louvain_kernel, options=['-Xcompiler=-Wall'],
                                                   arch='compute_' + cc, code='sm_' + cc,
                                                   cache_dir=False).get_function("calculate_community_degrees")

        self.calc_community_inernal = SourceModule(louvain_kernel, options=['-Xcompiler=-Wall'],
                                                arch='compute_' + cc, code='sm_' + cc,
                                                   cache_dir=False).get_function("calculate_community_internal_edges")

        self.calc_community_inernal_sum = SourceModule(louvain_kernel, options=['-Xcompiler=-Wall'],
                                                   arch='compute_' + cc, code='sm_' + cc,
                                                   cache_dir=False).get_function("calculate_community_internal_sum")
                                                
        self.calc_part_modularity = SourceModule(louvain_kernel, options=['-Xcompiler=-Wall'],
                                                arch='compute_' + cc, code='sm_' + cc,
                                                cache_dir=False).get_function("calc_part_modularity")

        block_size_x = 128
        self.max_blocks = (np.ceil(N / float(block_size_x))).astype(np.int32)
        self.threads = (block_size_x, 1, 1)
        self.grid = (int(self.max_blocks), 1)

    def compute(self, col_idx, prefix_sums, degrees, mod_threshold):

        # check input
        d_col_idx = ready_input(col_idx)
        d_prefix_sums = ready_input(prefix_sums)
        d_degrees = ready_input(degrees)

        h_degrees = np.zeros(self.N).astype(np.int32)
        drv.memcpy_dtoh(h_degrees, d_degrees)

        # allocate number of nodes
        d_total_n = allocate_and_copy(np.int32(self.N))

        # allocate number of edges
        total_m = np.int32(h_degrees.sum()/2)
        d_total_m = allocate_and_copy(total_m)

        # copy col_idx to local
        h_col_idx = np.zeros(h_degrees.sum()).astype(np.int32)
        drv.memcpy_dtoh(h_col_idx, d_col_idx)

        # copy prefix_sums to local
        h_prefix_sums = np.zeros(self.N).astype(np.int32)
        drv.memcpy_dtoh(h_prefix_sums, d_prefix_sums)

        #init state: each vertex represents its own community
        h_community_degrees = h_degrees[:]
        d_community_degrees = allocate_and_copy(h_community_degrees)

        # initially community index represents node index
        init_community_idx = np.arange(self.N).astype(np.int32)
        h_community_idx = np.arange(self.N).astype(np.int32)
        d_community_idx = allocate_and_copy(h_community_idx)

        # allocate edges weight
        h_col_weights = np.ones(len(h_col_idx)).astype(np.int32)
        d_col_weights = allocate_and_copy(h_col_weights)

        #check data
        print('##### INIT GRAPH #####')
        print('h_community_idx: ' + str(h_community_idx.size))
        print(h_community_idx)
        print('h_prefix_sums: ' + str(h_prefix_sums.size))
        print(h_prefix_sums)
        print('h_degrees: ' + str(h_degrees.size))
        print(h_degrees)
        print('h_community_degrees: ' + str(h_community_degrees.size))
        print(h_community_degrees)

        # temporary memory pointers
        d_tmp_community_idx = allocate_and_copy(np.zeros(self.N).astype(np.int32))
        d_tmp_community_degrees = allocate_and_copy(np.zeros(self.N).astype(np.int32))
        d_tmp_community_inter = allocate_and_copy(np.zeros(self.N).astype(np.int32))
        d_tmp_community_inter_sum = allocate_and_copy(np.zeros(self.N).astype(np.int32))
        d_part_mod = allocate_and_copy(np.zeros(self.N).astype(np.float32))

        # argument list for calculating node moves
        args_move_nodes = [d_total_n, d_total_m, d_col_idx, d_col_weights, d_prefix_sums, d_degrees, d_community_idx, d_community_degrees, d_tmp_community_inter_sum, d_tmp_community_idx]

        # argument list for calculating node moves
        args_calc_comm_deg = [d_total_n, d_tmp_community_idx, d_degrees, d_tmp_community_degrees]
        
        # argument list for calculating inter-connecting edges within communities
        args_calc_comm_inter = [d_total_n, d_col_idx, d_col_weights, d_prefix_sums, d_tmp_community_idx, d_tmp_community_inter]

        # argument list for calculating inter-connecting edges within communities
        args_calc_comm_inter_sum = [d_total_n, d_tmp_community_idx, d_tmp_community_inter, d_tmp_community_inter_sum]

        # argument list for calculating modulatities
        args_part_mod = [d_total_n, d_total_m, d_tmp_community_inter_sum, d_tmp_community_degrees, d_part_mod]


        total_iterations = 0
        graph_modularity = 0

        while True:
            # for debug
            modularities = []

            #get data from device
            drv.memcpy_dtoh(h_degrees, d_degrees)

            print('----------------------------------------------------')
            print('----------- ITERATION [' + str(total_iterations) + '] --------------')
            print('----------------------------------------------------')
            # START LOUVAIN

            phase_modularity = 0

            while True:
                # 1: Calculate best node moves
                self.move_nodes(*args_move_nodes, block=self.threads, grid=self.grid)

                # tmp_community_idx = np.zeros(self.N).astype(np.int32)
                # drv.memcpy_dtoh(tmp_community_idx, d_tmp_community_idx)
                # print('----------- ITERATION [' + str(total_iterations) + '] RESULT --------------')
                # print('tmp_community_idx:')
                # print(tmp_community_idx)

                # 2: Calculate degrees of communities
                self.calc_community_degrees(*args_calc_comm_deg, block=self.threads, grid=self.grid)

                # tmp_community_degrees = np.zeros(self.N).astype(np.int32)
                # drv.memcpy_dtoh(tmp_community_degrees, d_tmp_community_degrees)
                # print('NEW tmp_community_degrees:')
                # print(tmp_community_degrees)

                # 3: Calculate inter-connecting edges in communities
                # 3.1: Calculate inter-connecting edges per node within same community
                self.calc_community_inernal(*args_calc_comm_inter, block=self.threads, grid=self.grid)
                self.calc_community_inernal_sum(*args_calc_comm_inter_sum, block=self.threads, grid=self.grid)

                # tmp_internal = np.zeros(self.N).astype(np.int32)
                # drv.memcpy_dtoh(tmp_internal, d_tmp_community_inter_sum)
                # print('internal_links:')
                # print(tmp_internal)

                # 4: Calculate graph modularity
                # 4.1: Calculate partial modularity per community
                self.calc_part_modularity(*args_part_mod, block=self.threads, grid=self.grid)
                part_mod = np.zeros(self.N).astype(np.float32)
                drv.memcpy_dtoh(part_mod, d_part_mod)

                # print('part_mods:')
                # print(part_mod)

                # 4.2: Summarize modularities (additive function)
                current_mod = 0
                for i in range(self.N):
                    current_mod += part_mod[i]

                modularities = np.concatenate((modularities, [current_mod]))

                if (current_mod - phase_modularity <= mod_threshold):
                    break

                phase_modularity = current_mod

                # replace buffers
                drv.memcpy_dtoh(h_community_idx, d_tmp_community_idx)
                drv.memcpy_dtoh(h_community_degrees, d_tmp_community_degrees)
                drv.memcpy_htod(d_community_idx, h_community_idx)
                drv.memcpy_htod(d_community_degrees, h_community_degrees)

                # THIS REASSIGNMENT DOESNT WORK:
                # d_community_idx, d_tmp_community_idx = d_tmp_community_idx, d_community_idx
                # d_community_degrees, d_tmp_community_degrees = d_tmp_community_degrees, d_community_degrees
                # d_community_degrees = d_tmp_community_degrees

            
                
            print('######## MODULARITY GAIN #########')
            print(modularities)
            print('######## PHASE MODULARITY #########')
            print(phase_modularity)

            print('######## CURRENT ASSIGNMENTS ITERATION [' + str(total_iterations) + '] #########')
            print(h_community_idx)

            if (phase_modularity - graph_modularity <= mod_threshold):
                break

            graph_modularity = phase_modularity

            # merge communities


            # Replace col_idx with community_idx
            comm_col_idx = np.zeros(len(h_col_idx)).astype(np.int32)
            tmp_col_wieghts = np.zeros(len(h_col_idx)).astype(np.int32)
            comm_prefix_sum = []
            new_col_last = 0

            for i in range(self.N):
                for n in range(self.N):
                    if (h_community_idx[n] == i):
                        start = 0
                        if (n > 0):
                            start = h_prefix_sums[n - 1]
                        end = h_prefix_sums[n]

                        for j in range(start, end):
                            col = h_col_idx[j]
                            tmp_col_wieghts[new_col_last] = h_col_weights[j]
                            comm_col_idx[new_col_last] = h_community_idx[col]
                            new_col_last += 1
                comm_prefix_sum = np.append(comm_prefix_sum, new_col_last)

            # comm_prefix_sum = np.cumsum(h_community_degrees)
            comm_prefix_sum = np.array(comm_prefix_sum).astype(np.int32)

            print('========= REBUILDING GRAPH =========')
            print('comm_col_idx:')
            print(comm_col_idx)
            print('comm_prefix_sum:')
            print(comm_prefix_sum)

            new_col_idx = []
            new_weights = []
            new_degrees_weighted = np.zeros(self.N).astype(np.int32)
            new_community_degrees = np.zeros(self.N)
            for i in range(self.N):
                start = 0
                if (i > 0):
                    start = comm_prefix_sum[i - 1]
                end = comm_prefix_sum[i]

                corr_hits = comm_col_idx[start:end]
                unique_hits = np.unique(corr_hits)
                new_col_idx = np.concatenate((new_col_idx, unique_hits))
                new_local_weights = []
                for idx in unique_hits:
                    hit_weight = tmp_col_wieghts[np.where(corr_hits == idx)[0] + start].sum()
                    new_local_weights = np.append(new_local_weights, hit_weight)
                new_weights = np.concatenate((new_weights, new_local_weights))
                new_community_degrees[i] = len(unique_hits)
                new_degrees_weighted[i] = np.array(new_local_weights).sum()

            new_col_idx = np.array(new_col_idx).astype(np.int32)
            new_weights = np.array(new_weights).astype(np.int32)
            new_prefix_sums = np.array(np.cumsum(new_community_degrees)).astype(np.int32)

            print('########## NEW GRAPH ##############')
            print('new_col_idx ' + str(new_col_idx.size))
            print(new_col_idx)
            print('new_prefix_sums ' + str(new_prefix_sums.size))
            print(new_prefix_sums)
            print('new_degrees_weighted ' + str(new_degrees_weighted.size) + ' /sum: ' + str(new_degrees_weighted.sum()))
            print(new_degrees_weighted)
            print('new_weights ' + str(new_weights.size) + ' /sum: ' + str(new_weights.sum()))
            print(new_weights)
            # print('new_community_degrees ' + str(new_community_degrees.size))
            # print(new_community_degrees)
            # print('new_total_hits: ' + str(new_community_degrees.sum()))

            # update community_idx
            for i in range(self.N):
                init_community_idx[i] = h_community_idx[init_community_idx[i]]

            # CREATE NEW GRAPH
            h_col_idx = new_col_idx
            h_prefix_sums = new_prefix_sums
            h_degrees = new_degrees_weighted
            h_community_degrees = new_degrees_weighted
            h_col_weights = new_weights

            drv.memcpy_htod(d_col_idx, h_col_idx)
            drv.memcpy_htod(d_community_idx, init_community_idx)
            drv.memcpy_htod(d_tmp_community_idx, init_community_idx)
            drv.memcpy_htod(d_prefix_sums, h_prefix_sums)
            drv.memcpy_htod(d_degrees, h_degrees)
            drv.memcpy_htod(d_col_weights, h_col_weights)

            drv.memcpy_htod(d_community_degrees, h_community_degrees)

            self.calc_community_inernal(*args_calc_comm_inter, block=self.threads, grid=self.grid)
            self.calc_community_inernal_sum(*args_calc_comm_inter_sum, block=self.threads, grid=self.grid)
            

            total_iterations +=1
                    
        return init_community_idx
