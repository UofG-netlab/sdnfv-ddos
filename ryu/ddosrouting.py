"""
    Ryu controller application to insert the flow table entries necessary for
    multi-path routing in a FatTree Topology. The placement of each switch and
    host in the topology is based on the datapath id (dpid) and MAC address
    for consistent forwarding rules between experiments.

    The forwarding tables within the switches have the following format:

    ||               Table 0                  ||             Table 1          ||
    ----------------------------------------------------------------------------
    priority | action                         ||
           3 | traffic from vNF, goto table 1 ||
           2 | redirect traffic to vNF        || known destination, send to port
           1 | default goto table 1           || default send to ECMP buckets

    Simon Jouet <simon.jouet@glasgow.ac.uk>
    Netlab Networked Systems Research Laboratory (https://netlab.dcs.gla.ac.uk)
"""

from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER
from ryu.controller.handler import set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib.packet import packet
from ryu.lib.packet import ethernet
from ryu.lib.packet import arp
from ryu.lib.packet import ether_types

import struct

K = 4                           # K-ary FatTree
podNum = K                      # Pod number in FatTree
coreSwitchNum = pow((K/2),2)    # Core switches
aggrSwitchNum = ((K/2)*K)       # Aggregation switches
edgeSwitchNum = ((K/2)*K)       # Edge switches
hostNum = (K*pow((K/2),2))      # Hosts in K-ary FatTree


def getSwitchType(dpid):
    layer = (dpid >> 16) & 0xff
    pod = (dpid >> 8) & 0xff
    switch = dpid & 0xff

    return (layer, pod, switch)

def ipToMac(ip):
    _, pod, edge, host = [ int(x) for x in ip.split('.') ]

    # If it's WAN traffic (pod id is 255)
    if pod == 255:
        return '02:ff:00:00:00:{:02X}'.format(host)

    return '02:00:00:{:02X}:{:02X}:{:02X}'.format(pod, edge, host-1)

class DDOSRouting(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(DDOSRouting, self).__init__(*args, **kwargs)

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        datapath = ev.msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        layer, pod, switch = getSwitchType(datapath.id)
        self.logger.info("Switch feature handler %s %s", datapath.id, getSwitchType(datapath.id))

        # Add different flow entries depending on the layer of the switch
        if layer == 0:
            self.add_core_flows(datapath)
        elif layer == 1:
            self.add_agg_flows(datapath, pod, switch)
        elif layer == 2:
            self.add_edge_flows(datapath, pod, switch)
        elif layer == 3:
            self.add_internet_flows(datapath)


    def add_flow(self, datapath, priority, match, actions, buffer_id=None, table_id=0):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS,
                                             actions)]
        if buffer_id:
            mod = parser.OFPFlowMod(datapath=datapath, buffer_id=buffer_id, table_id=table_id,
                                    priority=priority, match=match,
                                    instructions=inst)
        else:
            mod = parser.OFPFlowMod(datapath=datapath, priority=priority, table_id=table_id,
                                    match=match, instructions=inst)
        datapath.send_msg(mod)

    def remove_table_flows(self, datapath, table_id, match, instructions):
        ofproto = datapath.ofproto
        flow_mod = datapath.ofproto_parser.OFPFlowMod(datapath, 0, 0, table_id, ofproto.OFPFC_DELETE, 0, 0, 1, ofproto.OFPCML_NO_BUFFER, ofproto.OFPP_ANY, ofproto.OFPG_ANY, 0, match, instructions)
        datapath.send_msg(flow_mod)


    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def _packet_in_handler(self, ev):
	self.logger.info("Packet in handler")
        msg = ev.msg
        datapath = msg.datapath
        ofproto = datapath.ofproto
        inPort = msg.match['in_port']

        pkt = packet.Packet(data=msg.data)
        etherFrame = pkt.get_protocol(ethernet.ethernet)

        if etherFrame.ethertype == ether_types.ETH_TYPE_ARP:
            self.logger.info("ARP packet")
            self.receive_arp(datapath, pkt, etherFrame, inPort)
            return 0
        else:
            self.logger.debug("Drop packet")
            return 1

    def receive_arp(self, datapath, pkt, etherFrame, inPort):
        arpPacket = pkt.get_protocol(arp.arp)

        if arpPacket.opcode == arp.ARP_REQUEST:
            arp_dstIp = arpPacket.dst_ip

            # From the dest IP figure out the mac address
            targetMac = etherFrame.src
            targetIp = arpPacket.src_ip
            srcMac = ipToMac(arp_dstIp) # Get the MAC address of the ip looked up

            e = ethernet.ethernet(targetMac, srcMac, ether_types.ETH_TYPE_ARP)
            a = arp.arp(opcode=arp.ARP_REPLY, src_mac=srcMac, src_ip=arp_dstIp, dst_mac=targetMac, dst_ip=targetIp)
            p = packet.Packet()
            p.add_protocol(e)
            p.add_protocol(a)
            p.serialize()

            ofproto = datapath.ofproto
            parser = datapath.ofproto_parser
            datapath.send_msg(parser.OFPPacketOut(datapath=datapath, buffer_id=ofproto.OFP_NO_BUFFER, in_port=ofproto.OFPP_CONTROLLER, actions=[ parser.OFPActionOutput(inPort) ], data=p.data))

        elif arpPacket.opcode == ARP_REPLY:
            pass

    def add_internet_flows(self, datapath):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        # clear the groups and flows
        mod = parser.OFPGroupMod(datapath, ofproto.OFPGC_DELETE, ofproto.OFPGT_SELECT, 1, None)
        datapath.send_msg(mod)
        self.remove_table_flows(datapath, 0, parser.OFPMatch(), []) # Clear the routing table

        # Add the group for ECMP
        buckets = []

        for coreswitch in range(coreSwitchNum):
            buckets.append(parser.OFPBucket(weight=1, actions=[ parser.OFPActionOutput(coreswitch + 1) ]))

        mod = parser.OFPGroupMod(datapath, ofproto.OFPGC_ADD, ofproto.OFPGT_SELECT, 1, buckets)
        datapath.send_msg(mod)

        self.add_flow(datapath, 2, parser.OFPMatch(eth_dst='02:ff:00:00:00:01'), actions=[ parser.OFPActionOutput(coreSwitchNum+1) ], table_id=0)
        self.add_flow(datapath, 2, parser.OFPMatch(eth_dst='02:ff:00:00:00:02'), actions=[ parser.OFPActionOutput(coreSwitchNum+2) ], table_id=0)
        self.add_flow(datapath, 1, parser.OFPMatch(), [ parser.OFPActionGroup(1) ], table_id=0)

        # ARP
        self.add_flow(datapath, 3, parser.OFPMatch(eth_type=ether_types.ETH_TYPE_ARP), [ parser.OFPActionOutput(ofproto.OFPP_CONTROLLER) ], table_id=0)


    def add_core_flows(self, datapath):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        # clear the flows
        self.remove_table_flows(datapath, 0, parser.OFPMatch(), [])
        self.remove_table_flows(datapath, 1, parser.OFPMatch(), [])

        ### Steering Table
        # Traffic from middlebox goes directly to routing table, K+1 ingress, K+2 egress
        mb_egress_port = K+2
        mod = parser.OFPFlowMod(datapath=datapath, priority=3, match=parser.OFPMatch(in_port=mb_egress_port), instructions=[ parser.OFPInstructionGotoTable(1) ], table_id=0)
        datapath.send_msg(mod)

        # Default action for traffic is to go to the routing table
        mod = parser.OFPFlowMod(datapath=datapath, priority=1, match=parser.OFPMatch(), instructions=[ parser.OFPInstructionGotoTable(1) ], table_id=0)
        datapath.send_msg(mod)

        ### Routing Table
        # add pod route
        for pod in range(podNum):
            match = parser.OFPMatch(eth_dst=('02:00:00:{:02X}:00:00'.format(pod), 'ff:ff:ff:ff:00:00'))
            actions = [parser.OFPActionOutput(pod + 1)]  # The ports are 0 indexed
            self.add_flow(datapath, 1, match, actions, table_id=1)

        self.add_flow(datapath, 1, parser.OFPMatch(), [ parser.OFPActionOutput(podNum + 2 + 1) ], table_id=1) # + 2 for mb +1 fir the internet port


    def add_agg_flows(self, datapath, pod, switch):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        # clear the groups and flows
        mod = parser.OFPGroupMod(datapath, ofproto.OFPGC_DELETE, ofproto.OFPGT_SELECT, 1, None)
        datapath.send_msg(mod)
        self.remove_table_flows(datapath, 0, parser.OFPMatch(), []) # Clear the steering table
        self.remove_table_flows(datapath, 1, parser.OFPMatch(), []) # Clear the routing table

        # Add the group for ECMP
        buckets = []

        for coreswitch in range(coreSwitchNum/2): # Each agg switch is connected to half of the core switches
            buckets.append(parser.OFPBucket(weight=1, actions=[ parser.OFPActionOutput(coreswitch + 1) ]))

        mod = parser.OFPGroupMod(datapath, ofproto.OFPGC_ADD, ofproto.OFPGT_SELECT, 1, buckets)
        datapath.send_msg(mod)

        ### Steering Table
        # Traffic from middlebox goes directly to routing table
        mb_egress_port = coreSwitchNum/2 + edgeSwitchNum/podNum + 2 # +2 for the egress
        mod = parser.OFPFlowMod(datapath=datapath, priority=3, match=parser.OFPMatch(in_port=mb_egress_port), instructions=[ parser.OFPInstructionGotoTable(1) ], table_id=0)
        datapath.send_msg(mod)

        # Default action for traffic is to go to the routing table
        mod = parser.OFPFlowMod(datapath=datapath, priority=1, match=parser.OFPMatch(), instructions=[ parser.OFPInstructionGotoTable(1) ], table_id=0)
        datapath.send_msg(mod)

        ### Routing Table
        # Add the flows
        for edge in range(edgeSwitchNum/podNum):
            match = parser.OFPMatch(eth_dst=('02:00:00:{:02X}:{:02X}:00'.format(pod, edge), 'ff:ff:ff:ff:ff:00'))
            actions = [parser.OFPActionOutput(coreSwitchNum/2 + edge + 1)] # links to edge starts after the connections to core
            self.add_flow(datapath, 2, match, actions, table_id=1)

        # Add the default
        self.add_flow(datapath, 1, parser.OFPMatch(), [ parser.OFPActionGroup(1) ], table_id=1)

    def add_edge_flows(self, datapath, pod, edge):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        # clear of the flows
        mod = parser.OFPGroupMod(datapath, ofproto.OFPGC_DELETE, ofproto.OFPGT_SELECT, 1, None)
        datapath.send_msg(mod)
        self.remove_table_flows(datapath, 0, parser.OFPMatch(), [])

        # Add the group for ECMP
        buckets = []

        # Hack alternate the buckets to avoid hashing always going through one path at the aggregation layer
        aggswitches = range(aggrSwitchNum/podNum)
        #if edge % 2 == 1:
        #    aggswitches.reverse()

        for aggswitch in aggswitches: # Each edge switch is connected to all the agg switches
            buckets.append(parser.OFPBucket(weight=1, actions=[ parser.OFPActionSetField(eth_src="02:01:00:{:02X}:{:02X}:{:02X}".format(1, pod, edge)), parser.OFPActionOutput(aggswitch + 1) ]))


        mod = parser.OFPGroupMod(datapath, ofproto.OFPGC_ADD, ofproto.OFPGT_SELECT, 1, buckets)
        datapath.send_msg(mod)


        ### Steering Table
        # Traffic from middlebox goes directly to routing table
        mb_egress_port = aggrSwitchNum/podNum + hostNum/edgeSwitchNum + 2 # +2 for the egress
        mod = parser.OFPFlowMod(datapath=datapath, priority=3, match=parser.OFPMatch(in_port=mb_egress_port), instructions=[ parser.OFPInstructionGotoTable(1) ], table_id=0)
        datapath.send_msg(mod)

        # Default action for traffic is to go to the routing table
        mod = parser.OFPFlowMod(datapath=datapath, priority=1, match=parser.OFPMatch(), instructions=[ parser.OFPInstructionGotoTable(1) ], table_id=0)
        datapath.send_msg(mod)

        ### Routing Table
        # Add the flows
        for host in range(hostNum/edgeSwitchNum):
            match = parser.OFPMatch(eth_dst=('02:00:00:{:02X}:{:02X}:{:02X}'.format(pod, edge, host), 'ff:ff:ff:ff:ff:ff'))
            actions = [parser.OFPActionOutput(aggrSwitchNum/podNum + host + 1)] # links to edge starts after the connections to core
            self.add_flow(datapath, 2, match, actions, table_id=1)

        # Add the default
        self.add_flow(datapath, 1, parser.OFPMatch(), [ parser.OFPActionGroup(1) ], table_id=1)

        # ARP
        self.add_flow(datapath, 3, parser.OFPMatch(eth_type=ether_types.ETH_TYPE_ARP), [ parser.OFPActionOutput(ofproto.OFPP_CONTROLLER) ], table_id=0)
