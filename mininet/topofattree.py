"""
    FatTree topology for data centre multi-path emulation on Mininet.
    This topology contains the switches, hosts and links of a FatTree with
    a span of 4, as well as hosts collocated with every switch to host virtual
    network functions and additional hosts at the egress of the fabric to
    generate traffic.

    Modified by Simon Jouet <simon.jouet@glasgow.ac.uk> based on the original
    script by howar31 (https://github.com/howar31/MiniNet/).
"""

from mininet.topo import Topo
from mininet.node import OVSSwitch
from mininet.link import TCLink

class FatTree(Topo):

    def __init__(self):

        # Topology settings
        K = 4                           # K-ary FatTree
        podNum = K                      # Pod number in FatTree
        coreSwitchNum = pow((K/2),2)    # Core switches
        aggrSwitchNum = ((K/2)*K)       # Aggregation switches
        edgeSwitchNum = ((K/2)*K)       # Edge switches
        hostNum = (K*pow((K/2),2))      # Hosts in K-ary FatTree

        # Initialize topology
        Topo.__init__(self)

        coreSwitches = []
        aggrSwitches = []
        edgeSwitches = []

        # Core
        for core in range(0, coreSwitchNum):
            coreThis = self.addSwitch('cs_{}'.format(core), dpid='{:010X}'.format(core+1))
            coreSwitches.append(coreThis)

        # Pod
        for pod in range(0, podNum):
        # Aggregate
            for aggr in range(0, aggrSwitchNum/podNum):
                aggrThis = self.addSwitch('as_{}_{}'.format(pod, aggr), dpid='{:06X}{:02X}{:02X}'.format(1, pod, aggr))
                aggrSwitches.append(aggrThis)
                for x in range((K/2)*aggr, (K/2)*(aggr+1)):
                    self.addLink(aggrThis, coreSwitches[x], cls=TCLink, bw=100.0, delay='1ms')

        # Edge
            for edge in range(0, edgeSwitchNum/podNum):
                edgeThis = self.addSwitch('es_{}_{}'.format(pod, edge), dpid='{:06X}{:02X}{:02X}'.format(2, pod, edge))
                edgeSwitches.append(edgeThis)
                for x in range((edgeSwitchNum/podNum)*pod, ((edgeSwitchNum/podNum)*(pod+1))):
                    self.addLink(edgeThis, aggrSwitches[x], cls=TCLink, bw=100.0, delay="0.5ms")

        # Host
                for x in range(0, (hostNum/podNum/(edgeSwitchNum/podNum))):
                    mac = '02:00:00:{:02}:{:02}:{:02}'.format(pod, edge, x)
                    host = self.addHost('h_{}_{}_{}'.format(pod, edge, x), mac=mac, ip='10.{}.{}.{}'.format(pod, edge, x+1))
                    self.addLink(edgeThis, host, cls=TCLink, bw=100.0)

        # Each switch as a collocated host to run network functions
        print coreSwitches + aggrSwitches + edgeSwitches
        for sw in coreSwitches + aggrSwitches + edgeSwitches:
            host = self.addHost('mb_{}'.format(sw), ip='0.0.0.0')
            self.addLink(sw, host, cls=TCLink, bw=100.0)
            self.addLink(sw, host, cls=TCLink, bw=100.0)

        # Create the Host/Switch for the internet traffic
        wanSwitch = self.addSwitch('wan0', dpid='{:06X}{:02X}{:02X}'.format(3, 0, 0))
        wanHost0 = self.addHost('wanh0', mac='02:FF:00:00:00:01', ip='10.255.0.1')
        wanHost1 = self.addHost('wanh1', mac='02:FF:00:00:00:02', ip='10.255.0.2')

        for core in range(0, coreSwitchNum):
            self.addLink(wanSwitch, coreSwitches[core])

        self.addLink(wanSwitch, wanHost0, cls=TCLink, bw=400)
        self.addLink(wanSwitch, wanHost1, cls=TCLink, bw=100)

topos = { 'fattree': ( lambda: FatTree() ) }
