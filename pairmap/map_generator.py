from .utils import find_bridges
from .utils.mcs import get_score_matrix

import numpy as np
import os
import itertools

import networkx as nx


class MapGenerator:
    def __init__(self, intermediate_list, optimal_path_mode = False, maxPathLength = 4, cycleLength = 3, maxOptimalPathLength = 3, roughMaxPathLength = 2, roughScoreThreshold = 0.5, minScoreThreshold = 0.2, chunkScale = 10, source_node_index = 0, target_node_index = 1, jobs = 0, custum_score_matrix = None, verbose = False):
        """
        :param intermediate_list: List of RDKit molecules representing intermediates
        :param optimal_path_mode: Output map contains only the optimal path (default: False)
        :param maxPathLength: Maximum path length of the pairmap (default: 4)
        :param cycleLength: Maximum cycle length of the pairmap (default: 3)
        :param maxOptimalPathLength: Maximum path length of the optimal path (default: 3)
        :param roughMaxPathLength: Maximum path length of the rough search (default: 2)
        :param roughScoreThreshold: Score threshold of the rough search (default: 0.5)
        :param minScoreThreshold: Minimum score threshold (default: 0.2)
        :param chunkScale: Parameter for chunk processing in the map generation (default: 10)
        :param source_node_index: source node index in the intermediate list (default: 0)
        :param target_node_index: target node index in the intermediate list (default: 1)
        :param jobs: Number of jobs for parallel processing (default: 0)
        :param custum_score_matrix: original score matrix, if None, the score matrix is calculated from the intermediate list (default: None)
        :param verbose: verbose mode (default: False)
        """
        self.intermediate_list = intermediate_list
        self.intermediate_names = [intermediate.GetProp('_Name') if intermediate.HasProp('_Name')==1  else f'intermediate-{i:04d}' for i, intermediate in enumerate(intermediate_list)]

        if custum_score_matrix is not None:
            # check custum_score_matrix is square matrix
            if len(custum_score_matrix) != len(intermediate_list):
                raise Exception('The size of the custom score matrix does not match the intermediate list.')
            if len(custum_score_matrix[0]) != len(intermediate_list):
                raise Exception('The custom score matrix must be a square matrix, but the size is {}x{}'.format(len(custum_score_matrix), len(custum_score_matrix[0])))
            self.score_matrix = custum_score_matrix
        else:
            self.score_matrix = None
        self.N = len(self.intermediate_list)
        self.jobs = jobs
        self.verbose = verbose

        self.source_node_index = source_node_index
        self.target_node_index = target_node_index

        # Optimal path parameters
        self.optimal_path_mode = optimal_path_mode
        self.maxOptimalPathLength = maxOptimalPathLength
        self.roughMaxPathLength = roughMaxPathLength
        self.roughScoreThreshold = roughScoreThreshold

        # Pairmap parameters
        self.cycleLength = cycleLength
        self.maxPathLength = maxPathLength
        self.chunkScale = chunkScale
        self.minScoreThreshold = minScoreThreshold

        self.found_path = [source_node_index, target_node_index]
        self.found_links = [(source_node_index, target_node_index)]

    def make_optimal_path_graph(self):
        # graph only contains the optimal path
        graph = nx.Graph()
        for i, name in enumerate(self.intermediate_names):
            if i in self.found_path:
                graph.add_node(i)
                graph.nodes[i]['label'] = name
        for i in range(len(self.found_path)-1):
            u=self.found_path[i]
            v=self.found_path[i+1]
            graph.add_edge(u, v, score=self.score_matrix[u][v])
        return graph

    def make_graph(self, min_score = None):
        if min_score is None:
            min_score = self.minScoreThreshold
        graph = nx.Graph()
        for i, name in enumerate(self.intermediate_names):
            graph.add_node(i)
            graph.nodes[i]['label'] = name
            # set edges
            for u, v in itertools.combinations(range(self.N), 2):
                score = self.score_matrix[u][v]
                round_score = np.round(score, decimals=2)
                if round_score >= min_score:
                    graph.add_edge(u, v, score=round_score)
        return graph

    def find_optimal_path(self):
        # rough search
        # find a path with a score above the roughScoreThreshold (e.g. roughScoreThreshold=0.5 and legnth below roughMaxPathLength=2)
        graph = self.make_graph(self.roughScoreThreshold)
        source_node_index, target_node_index = self.source_node_index, self.target_node_index
        has_path = nx.has_path(graph, source_node_index, target_node_index)
        if has_path:
            path_length = nx.shortest_path_length(graph, source_node_index, target_node_index)
            if path_length <= self.roughMaxPathLength:
                print("Warning: Found a path with a score above the roughScoreThreshold and a length below the roughMaxPathLength.")
                print("Less need to introduce pairmap")

        graph = self.make_graph()
        all_simple_paths = list(nx.all_simple_paths(graph, source_node_index, target_node_index, cutoff=self.maxOptimalPathLength))
        if len(all_simple_paths) == 0:
            raise Exception('No path found, please check the input.')
        path_scores_list = []
        for path in all_simple_paths:
            path_scores = [graph.get_edge_data(path[i],path[i+1])['score'] for i in range(len(path)-1)]
            path_scores_list.append(sorted(path_scores))

        sum_scores = [np.sum(1/np.array(scores)) for scores in path_scores_list]
        best_idx = np.argmin(sum_scores)
        found_path = all_simple_paths[best_idx]
        self.found_path = found_path
        self.found_links = [(found_path[i], found_path[i+1]) if found_path[i]<found_path[i+1] else (found_path[i+1], found_path[i]) for i in range(len(found_path)-1)]

        return self.found_path

    def get_cycled_nodes(self, graph):
        all_simple_cycles = list(nx.simple_cycles(graph, length_bound=self.cycleLength))
        unique_nodes = set()
        for path in all_simple_cycles:
            if any([node in self.found_path[1:-1] for node in path]):
                unique_nodes.update(path)
        cycled_nodes = set(self.found_path).intersection(unique_nodes)
        return cycled_nodes

    def get_cycled_edges(self, graph):
        bridges = find_bridges(graph)
        cycled_found_links = set(self.found_links).difference(bridges)
        return cycled_found_links

    def check_node_cycle_covering(self, graph):
        return len(self.initialCycledNodesSet.difference(self.get_cycled_nodes(graph)))==0

    def check_edge_cycle_covering(self, graph):
        return len(self.initialCycledEdgesSet.difference(self.get_cycled_edges(graph)))==0

    def check_constraints(self, graph):
        constraintsMet = True
        if constraintsMet:
            constraintsMet = self.check_node_cycle_covering(graph)
        if constraintsMet:
            constraintsMet = self.check_edge_cycle_covering(graph)
        return constraintsMet

    def get_main_subgraph(self, graph):
        subgraphs = list(nx.connected_components(graph))
        subgraph = graph.subgraph([])
        for nodes in subgraphs:
            if all([node in nodes for node in self.found_path]):
                subgraph = graph.subgraph(nodes)
                break
        is_invalid = not all([node in subgraph.nodes for node in self.found_path])

        if is_invalid:
            raise Exception('invalid graph: get_main_subgraph')
        return subgraph

    def get_reachable_subgraph(self, graph):
        all_simple_paths = list(nx.all_simple_paths(graph, self.source_node_index, self.target_node_index, cutoff = self.maxPathLength))
        unique_nodes = set()
        unique_nodes.update(self.found_path)
        for path in all_simple_paths:
            unique_nodes.update(path)
        subgraph = graph.subgraph(unique_nodes)

        is_invalid = not all([node in subgraph.nodes for node in self.found_path])
        if is_invalid:
            raise Exception('invalid graph: get_reachable_subgraph')
        return subgraph

    def get_cycle_subgraph(self, graph):
        all_simple_cycles = list(nx.simple_cycles(graph, length_bound=self.cycleLength))
        unique_nodes = set()
        unique_nodes.update(self.found_path)
        for path in all_simple_cycles:
            if any([node in self.found_path for node in path]):
                unique_nodes.update(path)
        subgraph = graph.subgraph(unique_nodes)
        is_invalid = not all([node in subgraph.nodes for node in self.found_path])
        if is_invalid:
            raise Exception('invalid graph: get_cycle_subgraph')
        return subgraph

    def generate_initial_graph(self):
        graph = self.make_graph()
        for u,v in graph.edges:
            graph[u][v]['found_path']=False
        for i in range(len(self.found_path)-1):
            u=self.found_path[i]
            v=self.found_path[i+1]
            graph[u][v]['found_path']=True
        return graph

    def chunk_process(self, edge_chunk, data_chunk, chunk_size, idx):
        subgraph = self.tmp_subgraph
        if self.check_chunk(edge_chunk, data_chunk):
            # Edges can be removed
            return True
        elif chunk_size == 1:
            # The edge cannot be removed
            return False
        else:
            # Re-split when there are multiple chunks
            if self.verbose:
                print('Split: #E={}, {} {}'.format(len(subgraph.edges()), idx, idx + chunk_size))
            # Run chunk_process recursively with smaller chunks
            chunk_size = max(chunk_size // self.chunkScale, 1)
            crt=0
            while crt < len(edge_chunk):
                edge_chunk_in = []
                data_chunk_in = []
                while len(edge_chunk_in) < chunk_size and crt < len(edge_chunk):
                    u,v = edge_chunk[crt]
                    if subgraph.get_edge_data(u,v):
                        edge_chunk_in+=[edge_chunk[crt]]
                        data_chunk_in+=[data_chunk[crt]]
                    crt+=1
                ret = self.chunk_process(edge_chunk_in, data_chunk_in, chunk_size, idx+crt)
                # If unremovable edges are found, try to check the rest of the chunk
                if not ret:
                    edge_chunk_x = [(u,v) for u,v in edge_chunk[crt:] if subgraph.get_edge_data(u,v) is not None]
                    data_chunk_x = [d for (u,v), d in zip(edge_chunk[crt:], data_chunk[crt:]) if subgraph.get_edge_data(u,v) is not None]
                    if self.check_chunk(edge_chunk_x, data_chunk_x):
                        # Remain edges can be removed
                        break
            return True

    def check_chunk(self, edge_chunk, data_chunk):
        '''Check constraints by chunk'''
        subgraph = self.tmp_subgraph
        removables = [d['score'] < 1.0 and not d['found_path'] for d in data_chunk]
        if not all(removables):
            if not any(removables):
                # Skip if all edges are unremovable
                if self.verbose:
                    print('Skip (score=1.0): {}'.format(len(edge_chunk)))
                return True
            # Restore edges because they contain edges that cannot be removed
            return False
        else:
            # Remove edges in the chunk and check constraints
            subgraph.remove_edges_from(edge_chunk)
            exgraph = self.get_reachable_subgraph(subgraph)
            exgraph = self.get_cycle_subgraph(exgraph)
            exgraph = self.get_main_subgraph(exgraph)
            is_invalid = not all([node in exgraph.nodes for node in self.found_path])
            if is_invalid:
                for (i, j), d in zip(edge_chunk, data_chunk):
                    subgraph.add_edge(i, j, **d)
                return False
            satisfied = self.check_constraints(exgraph)
            if not satisfied:
                for (i, j), d in zip(edge_chunk, data_chunk):
                    subgraph.add_edge(i, j, **d)
                return False
            if self.verbose:
                print('Removed: {}'.format(len(edge_chunk)))
            subgraph = exgraph.copy()
            if self.verbose:
                print('#E={}, #N={}'.format(len(subgraph.edges()), len(subgraph)))
            self.tmp_subgraph = subgraph
            return True

    def get_score_matrix(self):
        '''Get score matrix from intermediate list'''
        if self.score_matrix is None:
            self.score_matrix = get_score_matrix(self.intermediate_list, jobs=self.jobs)
        return self.score_matrix

    def build_map(self):
        '''Map generation'''
        # calculate score matrix
        _ = self.get_score_matrix()

        # find optimal path
        found_path = self.find_optimal_path()

        if self.verbose:
            print('Found path found:', found_path)
            print('Found links:', self.found_links)

        self.optimal_path_graph = self.make_optimal_path_graph()
        if self.optimal_path_mode:
            self.final_graph = self.optimal_path_graph
            return self.final_graph

        # execute map generation

        subgraph = self.generate_initial_graph()

        self.scoresList = list(subgraph.edges(data='score'))
        self.scoresList.sort(key=lambda entry: entry[2])

        edges = [(i, j) for i, j, d in self.scoresList]
        data = [subgraph[i][j] for i, j, d in self.scoresList]
        chunk_size = self.chunkScale **int(np.log(len(self.scoresList))/np.log(self.chunkScale))

        self.initialCycledNodesSet = self.get_cycled_nodes(subgraph)
        self.initialCycledEdgesSet = self.get_cycled_edges(subgraph)

        exgraph = self.get_reachable_subgraph(subgraph)
        exgraph = self.get_cycle_subgraph(exgraph)
        exgraph = self.get_main_subgraph(exgraph)
        is_invalid = not all([node in exgraph.nodes for node in found_path])
        if is_invalid:
            raise Exception('invalid initial graph: get_reachable_subgraph')
        else:
            subgraph = exgraph.copy()

        if self.verbose:
            print('Build map with subgraphing')
        self.tmp_subgraph = subgraph
        crt=0
        while crt < len(data):
            edge_chunk = []
            data_chunk = []
            while len(edge_chunk) < chunk_size and crt < len(data):
                subgraph = self.tmp_subgraph
                u,v = edges[crt]
                if subgraph.get_edge_data(u,v):
                    edge_chunk+=[edges[crt]]
                    data_chunk+=[data[crt]]
                crt+=1
            self.chunk_process(edge_chunk, data_chunk, chunk_size, crt)

        if self.verbose:
            print('Rebuild graph with no subgraphing')
        tmp_graph = self.tmp_subgraph.copy()
        self.scoresList = list(tmp_graph.edges(data='score'))
        self.scoresList.sort(key=lambda entry: entry[2])
        for u, v, _ in self.scoresList:
            edge_data = tmp_graph.get_edge_data(u,v)
            if edge_data == None or edge_data['found_path']:
                continue
            tmp_graph.remove_edge(u,v)
            satisfied = self.check_constraints(tmp_graph)
            if not satisfied:
                tmp_graph.add_edge(u,v, **edge_data)

        exgraph = tmp_graph.copy()
        exgraph = self.get_main_subgraph(exgraph)
        tmp_graph = exgraph

        self.final_graph = tmp_graph.copy()

        return self.final_graph
