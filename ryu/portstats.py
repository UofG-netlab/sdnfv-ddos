"""
    Ryu controller application to monitor in realtime the status of the network.
    This application continuously polls the port statistics using openflow.
    A WebUI is provided to display graphically the port statistics as they are
    received from the switches using WebSockets.

    Simon Jouet <simon.jouet@glasgow.ac.uk>
    Netlab Networked Systems Research Laboratory (https://netlab.dcs.gla.ac.uk)
"""

from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER
from ryu.controller.handler import set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib import hub

import json
import os
from webob import Response
from webob.static import DirectoryApp

from ryu.app.wsgi import ControllerBase, WSGIApplication, route, WebSocketRPCServer, websocket


class PortStatistics(object):
    def __init__(self, rx_packets, tx_packets, rx_bytes, tx_bytes, rx_errors, tx_errors):
        self.rx_packets = rx_packets
        self.tx_packets = tx_packets
        self.rx_bytes = rx_bytes
        self.tx_bytes = tx_bytes
        self.rx_errors = rx_errors
        self.tx_errors = tx_errors

class SwitchStatistics(object):
    def __init__(self):
        self.time = []
        self.data = {}

    # This won't deal well if ports get added or removed. The time serie should be in sync with the data points
    def add_port_stats(self, portstats):
        added = {}

        #
        self.time.append(portstats[0].duration_sec)
        if len(self.time) > 100:
            self.time.pop(0)

        #
        for portstat in portstats:
            # If the port is above that it's a logical port, ignore it
            if portstat.port_no < ofproto_v1_3.OFPP_MAX:

                points = self.data.setdefault(portstat.port_no, [])

                point = PortStatistics(
                    portstat.rx_packets,
                    portstat.tx_packets,
                    portstat.rx_bytes,
                    portstat.tx_bytes,
                    portstat.rx_errors,
                    portstat.tx_errors
                )

                points.append(point)
                added[portstat.port_no] = point

                # cap the collection
                if len(points) > 100:
                    points.pop(0)

        return (added, portstats[0].duration_sec)

class PortStats(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    _CONTEXTS = {
        'wsgi': WSGIApplication
    }

    def __init__(self, *args, **kwargs):
        super(PortStats, self).__init__(*args, **kwargs)

        self.connections = {}
        self.statistics = {}
        self.datapaths = []
        self.monitor_thread = hub.spawn(self._monitor)

        wsgi = kwargs['wsgi']
        wsgi.register(PortStatsController, { 'port_stats_app': self })
        self._ws_manager = wsgi.websocketmanager

    def _monitor(self):
        while True:
            if len(self.datapaths):
                datapath = self.datapaths.pop(0)
                self.logger.info("Querying port stats %d", datapath.id)
                self.send_port_stats_request(datapath)

            hub.sleep(0.2)

    @set_ev_cls(ofp_event.EventOFPPortStatsReply, MAIN_DISPATCHER)
    def _port_stats_reply_handler(self, ev):
        body = ev.msg.body
        datapath = ev.msg.datapath
        ofp = datapath.ofproto

        added, time = self.statistics[datapath.id].add_port_stats(body)
        self._ws_manager.broadcast(unicode(json.dumps({ "dpid": datapath.id, "time": time, "data": added }, cls=StatsEncoder)))

        self.datapaths.append(datapath)
        self.logger.info("Received port stats %d", datapath.id)

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
        """Create OFP flow mod message to remove flows from table."""
        ofproto = datapath.ofproto
        flow_mod = datapath.ofproto_parser.OFPFlowMod(datapath, 0, 0, table_id, ofproto.OFPFC_DELETE, 0, 0, 1, ofproto.OFPCML_NO_BUFFER, ofproto.OFPP_ANY, ofproto.OFPG_ANY, 0, match, instructions)
        datapath.send_msg(flow_mod)


    def send_port_stats_request(self, datapath):
        ofp = datapath.ofproto
        ofp_parser = datapath.ofproto_parser

        req = ofp_parser.OFPPortStatsRequest(datapath, port_no=ofp.OFPP_ANY)
        datapath.send_msg(req)

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        datapath = ev.msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        self.logger.info("Switch feature handler")

        self.datapaths.append(datapath)
        self.statistics[datapath.id] = SwitchStatistics()
        self.connections[datapath.id] = datapath

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def _packet_in_handler(self, ev):
	self.logger.info("Packet in handler")

    def add_middlebox(self, mac, dpid):
        datapath = self.connections[dpid]
        parser = datapath.ofproto_parser

        ingress = len(self.statistics[datapath.id].data) - 1
        match = parser.OFPMatch(eth_src=(mac))
        actions = [parser.OFPActionOutput(ingress)]

        self.add_flow(datapath, 2, match, actions, table_id=0)

    def remove_middlebox(self, mac, dpid):
        datapath = self.connections[dpid]
        parser = datapath.ofproto_parser

        match = parser.OFPMatch(eth_src=(mac))
        self.remove_table_flows(datapath, 0, match, [])


class StatsEncoder(json.JSONEncoder):
    def default(self, o):
        return o.__dict__


class PortStatsController(ControllerBase):
    def __init__(self, req, link, data, **config):
        super(PortStatsController, self).__init__(req, link, data, **config)
        self.port_stats_app = data['port_stats_app']
        self.static_app = DirectoryApp(os.path.dirname(__file__) + '/public/')

    @route('portstats', '/{filename:.*}')
    def static_handler(self, req, **kwargs):
        if kwargs['filename']:
            req.path_info = kwargs['filename']
        return self.static_app(req)

    @route('portstats', '/api/stats', methods=['GET'])
    def get_stats(self, req, **kwargs):
        res = Response(content_type='application/json', body=json.dumps(self.port_stats_app.statistics, cls=StatsEncoder))
        res.headers['Access-Control-Allow-Origin'] = '*'
        return res

    @route('portstats', '/api/middlebox', methods=['POST'])
    def host_middlebox(self, req, **kwargs):
        mac = req.POST['mac']
        dpid = int(req.POST['switch'])
        action = req.POST['action']

        if action == 'add':
            self.port_stats_app.add_middlebox(mac, dpid)
        elif action == 'remove':
            self.port_stats_app.remove_middlebox(mac, dpid)

    @websocket('portstats', '/api/ws')
    def _websocket_handler(self, ws):
        rpc_server = WebSocketRPCServer(ws, self)
        rpc_server.serve_forever()
