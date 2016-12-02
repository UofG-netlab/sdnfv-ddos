"""
    Experiment setup to show the impact of ddos on the network infrastructure
    and the benefits of blocking the attack at the different layers of the
    topology.

    Simon Jouet <simon.jouet@glasgow.ac.uk>
    Netlab Networked Systems Research Laboratory (https://netlab.dcs.gla.ac.uk)
"""

from mininet.net import Mininet
from topofattree import FatTree
from mininet.node import RemoteController
from mininet.cli import CLI
import time


def measureLatencies(host, prefix):
    host.cmd('timeout -s2 15m ~/latency/latency -i 100 10.0.0.1 2> ~/results/{}_attacked.log'.format(prefix))
    host.cmd('timeout -s2 15m ~/latency/latency -i 100 10.0.0.2 2> ~/results/{}_attacked_edge.log'.format(prefix))
    host.cmd('timeout -s2 15m ~/latency/latency -i 100 10.0.1.1 2> ~/results/{}_attacked_pod.log'.format(prefix))
    host.cmd('timeout -s2 15m ~/latency/latency -i 100 10.3.1.1 2> ~/results/{}_attacked_other.log'.format(prefix))
    time.sleep(20)

def blockUDPTraffic(switches):
    for switch in switches:
        net.getNodeByName(switch).cmd('ovs-ofctl add-flow {} udp,priority=10,actions=drop'.format(switch))


net = Mininet(topo=FatTree(), controller=RemoteController)

net.start()

wanh0 = net.getNodeByName('wanh0')
wanh1 = net.getNodeByName('wanh1')

# Wait for the system to be stable
time.sleep(5)

# Measure the idle latency
wanh1.cmd('timeout -s2 15m ~/latency/latency -i 100 10.0.0.1 2> ~/results/idle.log')
time.sleep(5)

# Run the UDP DDoS attack
wanh0.cmd('hping3 --udp --flood -d 1472 --rand-source 10.0.0.1 &')

# Wait for the attack to be stable
time.sleep(20)

# Measure the latency to the attacked host, under the same edge, agg and core
measureLatencies(wanh1, '')

# Block traffic at the edge
blockUDPTraffic([ 'es_0_0' ])
measureLatencies(wanh1, 'blocked_edge')

# Block traffic at the agg
blockUDPTraffic([ 'as_0_0', 'as_0_1' ])
measureLatencies(wanh1, 'blocked_agg')

# Block traffic at the core
blockUDPTraffic([ 'cs_0', 'cs_1', 'cs_2', 'cs_3' ])
measureLatencies(wanh1, 'blocked_core')

#CLI(net)

net.stop()
