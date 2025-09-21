import sys
from typing import Dict, List, Tuple, Optional
from collections import defaultdict
import random

class bcolors:
    if sys.stdout.isatty():
        HEADER = '\033[95m'
        OKBLUE = '\033[94m'
        OKCYAN = '\033[96m'
        OKGREEN = '\033[92m'
        WARNING = '\033[93m'
        FAIL = '\033[91m'
        ENDC = '\033[0m'
        BOLD = '\033[1m'
        UNDERLINE = '\033[4m'
    else:
        HEADER = ''
        OKBLUE = ''
        OKCYAN = ''
        OKGREEN = ''
        WARNING = ''
        FAIL = ''
        ENDC = ''
        BOLD = ''
        UNDERLINE = ''

class Node:
    def __init__(self, action: str, resource: str, operation: str,
                 subtype: str, index: int, failed_count: int = 0):
        self.action = action
        self.resource = resource
        self.operation = operation
        self.subtype = subtype
        self.index = index
        self.failed_count = failed_count

    def __repr__(self):
        return f"Node({self.action}, {self.resource}, {self.operation}, {self.subtype}, index={self.index}, failed_count={self.failed_count})"

    def key4(self) -> Tuple[str, str, str, str]:
        return self.action, self.resource, self.operation, self.subtype

class Cluster:
    def __init__(self, resource: str, operation: str):
        self.resource = resource
        self.operation = operation
        self.nodes: List[Node] = []
        self.predecessors: List['Cluster'] = []
        self.successors: List['Cluster'] = []

    def is_empty(self) -> bool:
        return len(self.nodes) == 0

    def __repr__(self):
        return f"Cluster({self.resource}, {self.operation}, nodes={len(self.nodes)})"


class DependencyGraph:
    def __init__(self, crawler):
        self.clusters: Dict[Tuple[str, str], Cluster] = {}
        self.parent_cache: Dict[str, List[str]] = defaultdict(list)
        self.crawler = crawler
        self.max_failed_count = 10

    def _ensure_placeholders(self, resource: str):
        ops = ['create', 'read', 'update', 'unknown', 'delete']
        for op in ops:
            key = (resource, op)
            if key not in self.clusters:
                self.clusters[key] = Cluster(resource, op)

        self.build_default_order_edges(resource)


    def add_node(self, node: Node):
        self._ensure_placeholders(node.resource)
        failed_count = node.failed_count
        if failed_count > self.max_failed_count:
            print(bcolors.OKGREEN + f"[Navigation] Node {node.key4()} failed too many times, skipping." + bcolors.ENDC)
            return False

        cluster = self.clusters[(node.resource, node.operation)]
        cluster.nodes.append(node)

        return True

    def _all_nodes(self) -> List[Node]:
        out = []
        for c in self.clusters.values():
            out.extend(c.nodes)
        return out

    def _has_cycle_and_get_path(self, src: Cluster, dst: Cluster):
        path = []

        def dfs(current, target, trace):
            if current in trace:
                return False
            if current == target:
                path.extend(trace + [current])
                return True
            trace.append(current)
            for succ in current.successors:
                if dfs(succ, target, trace):
                    return True
            trace.pop()
            return False

        if dfs(dst, src, []):
            cycle_start = path.index(src)
            cycle = path[cycle_start:] + [dst]
            return True, cycle
        else:
            return False, []

    def _link_clusters(self, src: Cluster, dst: Cluster):
        has_cycle, cycle_path = self._has_cycle_and_get_path(src, dst)
        if has_cycle:
            strategy = "default"
            print(bcolors.OKGREEN+"Found cycle: "+bcolors.ENDC)
            for c in cycle_path:
                print(bcolors.OKGREEN+f" - Cluster({c.resource}, {c.operation})"+bcolors.ENDC)

            if strategy == "merge":
                self._merge_clusters(cycle_path)
            elif strategy == "break":
                self._break_cycle(cycle_path)
            return
        if dst not in src.successors:
            src.successors.append(dst)
        if src not in dst.predecessors:
            dst.predecessors.append(src)

    def _merge_clusters(self, cycle_clusters: list[Cluster]):
        if not cycle_clusters:
            return

        primary = cycle_clusters[0]

        for other in cycle_clusters[1:]:
            primary.nodes.extend(other.nodes)
            for succ in other.successors:
                if succ != primary and succ not in primary.successors:
                    primary.successors.append(succ)
                    succ.predecessors.remove(other)
                    succ.predecessors.append(primary)

            for pred in other.predecessors:
                if pred != primary and pred not in primary.predecessors:
                    primary.predecessors.append(pred)
                    pred.successors.remove(other)
                    pred.successors.append(primary)

            other.successors.clear()
            other.predecessors.clear()
            other.nodes.clear()

        primary.successors = list(set(primary.successors) - {primary})
        primary.predecessors = list(set(primary.predecessors) - {primary})

    def _break_cycle(self, cycle_clusters: list[Cluster]):
        if not cycle_clusters or len(cycle_clusters) < 2:
            return

        edges = []
        for i in range(len(cycle_clusters)):
            src = cycle_clusters[i]
            dst = cycle_clusters[(i + 1) % len(cycle_clusters)]
            if dst in src.successors:
                edges.append((src, dst))

        if not edges:
            return

        src, dst = random.choice(edges)
        src.successors.remove(dst)
        dst.predecessors.remove(src)

        print(bcolors.OKGREEN+f"Break cycle by deleting ：{src.resource}.{src.operation} → {dst.resource}.{dst.operation}"+bcolors.ENDC)

    def build_default_order_edges(self, resource: str):
        order = ['create', 'read', 'update', 'unknown', 'delete']
        for i in range(len(order)-1):
            c1 = self.clusters[(resource, order[i])]
            c2 = self.clusters[(resource, order[i+1])]
            self._link_clusters(c1, c2)

    def has_predecessor_for_delete(self, cluster: Cluster):
        has_predecessor_for_delete_flag = False
        potential_parent_name = cluster.resource
        potential_parent_index_list = []
        for c in random.sample(self.clusters[(potential_parent_name, 'create')].nodes,
                               min(3, len(self.clusters[(potential_parent_name, 'create')].nodes))):
            potential_parent_index_list.append(c.index)
        for r in random.sample(self.clusters[(potential_parent_name, 'read')].nodes,
                               min(3, len(self.clusters[(potential_parent_name, 'read')].nodes))):
            potential_parent_index_list.append(r.index)
        for resource_operation in self.clusters.keys():
            if resource_operation[1] == 'delete' and resource_operation[0] != cluster.resource:
                potential_child_name = resource_operation[0]
                potential_child_index_list = []
                for c in random.sample(self.clusters[(potential_child_name, 'create')].nodes,
                                       min(3, len(self.clusters[(potential_child_name, 'create')].nodes))):
                    potential_child_index_list.append(c.index)
                for r in random.sample(self.clusters[(potential_child_name, 'read')].nodes,
                                       min(3, len(self.clusters[(potential_child_name, 'read')].nodes))):
                    potential_child_index_list.append(r.index)

                is_parent_child_relationship = self.crawler.infer_resource_dependency_relationship(
                    potential_parent_name, potential_parent_index_list, potential_child_name,
                    potential_child_index_list)

                if is_parent_child_relationship:
                    self._link_clusters(self.clusters[(potential_child_name, 'delete')], cluster)
                    if not has_predecessor_for_delete_flag:
                        has_predecessor_for_delete_flag = not self.is_all_predecessors_empty(cluster, set())
        return has_predecessor_for_delete_flag

    def is_all_predecessors_empty(self, cluster: Cluster, empty_clusters: set):
        if cluster in empty_clusters:
            return True
        for p in cluster.predecessors:
            if not p.is_empty():
                return False
            if not self.is_all_predecessors_empty(p, empty_clusters):
                return False
            else:
                empty_clusters.add(p)
        return True

class Scheduler:
    def __init__(self, graph: DependencyGraph):
        self.graph = graph

    def _eligible_clusters(self):
        candidates = []
        for c in self.graph.clusters.values():
            if self.graph.is_all_predecessors_empty(c, set()):
                candidates.append(c)
        return candidates

    def pick_and_run(self):
        candidates = self._eligible_clusters()
        cluster = None
        while candidates:
            cluster = random.choice(candidates)
            if not cluster.is_empty():
                if cluster.operation == 'delete' and not self.graph.has_predecessor_for_delete(cluster):
                    choose_delete = random.randint(0,100)
                    if choose_delete >= 90:
                        break
                else:
                    break
            candidates.remove(cluster)

        if not candidates:
            return -1

        nodes = sorted(cluster.nodes, key=lambda n: n.failed_count)
        min_fc = nodes[0].failed_count
        smallest = [n for n in nodes if n.failed_count == min_fc]
        node = random.choice(smallest)
        cluster.nodes.remove(node)
        return node.index

    def feedback(self, node: Node, succeed: bool):
        key = (node.resource, node.operation)
        if key not in self.graph.clusters:
            print(bcolors.OKGREEN + f"[Navigation] No cluster found for {key}, skipping feedback." + bcolors.ENDC)
            return
        same_cluster = self.graph.clusters[(node.resource, node.operation)]
        if succeed:
            same_cluster.nodes = [
                n for n in same_cluster.nodes if n.key4() != node.key4()
            ]
        else:
            for n in same_cluster.nodes:
                if n.key4() == node.key4():
                    n.failed_count += 1

            same_cluster.nodes = [
                n for n in same_cluster.nodes if n.failed_count <= self.graph.max_failed_count
            ]
