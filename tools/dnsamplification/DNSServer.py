"""
    A barebone DNS server that will reply to any request with a large amount of
    data to showcase the large difference in size between a DNS request and a
    DNS reply and how this difference can be used for DDoS attacks.

    Simon Jouet <simon.jouet@glasgow.ac.uk>
    Netlab Networked Systems Research Laboratory (https://netlab.dcs.gla.ac.uk)
"""

import socket
import struct

class DNSLabel(object):
    def __init__(self, label=''):
        self.label = label

    @classmethod
    def parse(cls, data, offset=0):
        record = cls()
        label_len = ord(data[offset])
        record.label = data[offset+1:offset+1+label_len]

        if label_len:
            return record
        else:
            return None

    @classmethod
    def parseall(cls, data, offset=0):
        labels = []

        label = cls.parse(data, offset)
        while label:
            labels.append(label)
            offset += len(label.label) + 1
            label = cls.parse(data, offset)

        return (labels, offset)

    def pack(self):
        return struct.pack('B', len(self.label)) + self.label

    @classmethod
    def packall(self, labels, eol=True):
        buffer = ''
        for label in labels:
            buffer += label.pack()

        if eol:
            buffer += struct.pack('B', 0) # end of labels

        return buffer


class DNSAdditionalRecord(object):
    ARStruct = struct.Struct('>BHHBBHH')

    @classmethod
    def parse(cls, data, offset=0):
        record = cls()
        record.name, record.type, record.payload_size, record.rcode, record.version, record.z, record.data_length = cls.ARStruct.unpack_from(data, offset)
        return record

    def pack(self):
        return DNSAdditionalRecord.ARStruct.pack(self.name, self.type, self.payload_size, self.rcode, self.version, self.z, self.data_length)

class DNSQueryRecord(object):
    QRStruct = struct.Struct('>HH')

    @classmethod
    def parse(cls, data, offset=0):
        record = cls()
        record.labels, offset = DNSLabel.parseall(data, offset)
        offset += 1
        record.qtype, record.qclass = cls.QRStruct.unpack_from(data, offset)
        return (record, offset+cls.QRStruct.size)

    def pack(self):
        # The query
        buffer = DNSLabel.packall(self.labels)
        buffer += DNSQueryRecord.QRStruct.pack(self.qtype, self.qclass)
        return buffer

class DNSAnswerRecord(object):
    ARStruct = struct.Struct('>HHIH')

    def __init__(self, labels=[], qtype=0, qclass=0, ttl=0, data_length=0, data=''):
        self.labels = labels
        self.qtype = qtype
        self.qclass = qclass
        self.ttl = ttl
        self.data_length = data_length
        self.data = data

    @classmethod
    def parse(cls, data, offset=0):
        print 'NYI'

    def pack(self):
        buffer = DNSLabel.packall(self.labels)

        # compute the data length if not provided explicitly
        if self.data_length == 0:
            self.data_length = len(self.data)

        buffer += DNSAnswerRecord.ARStruct.pack(self.qtype, self.qclass, self.ttl, self.data_length)
        buffer += self.data
        return buffer

class DNSPacket(object):
    DNSStruct = struct.Struct('>HHHHHH')

    A_RECORD = 1
    NS_RECORD = 2
    SOA_RECORD = 6
    CNAME_RECORD = 5
    PTR_RECORD = 12
    MX_RECORD = 15
    TXT_RECORD = 16

    def __init__(self):
        self.query_records = []
        self.answer_records = []
        self.authority_records = []
        self.additional_records = []

    @classmethod
    def parse(cls, data):
        pkt = cls()
        pkt.id, flags, pkt.qdcount, pkt.ancount, pkt.nscount, pkt.arcount = cls.DNSStruct.unpack_from(data, 0)
        pkt.qr = flags >> 15
        pkt.opcode = (flags >> 11) & 0xF
        pkt.aa = (flags >> 10) & 1
        pkt.tc = (flags >> 9) & 1
        pkt.rd = (flags >> 8) & 1
        pkt.ra = (flags >> 7) & 1
        pkt.rcode = flags & 0xF
        pkt.payload = data[cls.DNSStruct.size:]

        offset = 0
        for _ in range(pkt.qdcount):
            record, length = DNSQueryRecord.parse(pkt.payload, offset)
            offset += length
            pkt.query_records.append(record)

        if pkt.nscount > 0:
            print 'NYI'

        for _ in range(pkt.arcount):
            pkt.additional_records.append(DNSAdditionalRecord.parse(pkt.payload, offset))
            offset += DNSAdditionalRecord.ARStruct.size

        return pkt

    def pack(self):
        buffer = ''
        buffer += DNSPacket.DNSStruct.pack(
            self.id,
            (self.qr << 15) | (self.opcode << 11) | (self.aa << 10) | (self.tc << 9) | (self.rd << 8) | (self.ra << 7) | self.rcode,
            self.qdcount,
            self.ancount,
            self.nscount,
            self.arcount
        )

        # The query
        for record in self.query_records:
            buffer += record.pack()

        # The answer
        for record in self.answer_records:
            buffer += record.pack()

        # the ns records
        for record in self.authority_records:
            buffer += record.pack()

        # The additional records
        for record in self.additional_records:
            buffer += record.pack()

        return buffer

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind(('0.0.0.0', 53))

while True:
    data, addr = sock.recvfrom(1024)
    print data.encode('hex'), addr

    dnspkt = DNSPacket.parse(data)
    for query_record in dnspkt.query_records:
        print query_record.labels

    print 'additional records', len(dnspkt.additional_records)

    # response
    dnspkt.qr = 1       # packet is a DNS response
    dnspkt.additional_records = [] # Clean additional_records

    # A Record
    dnspkt.answer_records.append(DNSAnswerRecord(
        labels=dnspkt.query_records[0].labels,
        qtype=DNSPacket.A_RECORD,
        qclass=1,
        ttl=0,
        data=socket.inet_aton('192.168.0.10')
    ))

    # TXT Record
    txtrecord = 'v=spf1 include:_spf.{} ~all'.format('.'.join([ l.label for l in dnspkt.query_records[0].labels ]))
    dnspkt.answer_records.append(DNSAnswerRecord(
        labels=dnspkt.query_records[0].labels,
        qtype=DNSPacket.TXT_RECORD,
        qclass=1,
        ttl=0,
        data=chr(len(txtrecord)) + txtrecord # txt data is prefixed by the txt length
    ))

    # MX Record
    for i in range(4):
        dnspkt.answer_records.append(DNSAnswerRecord(
            labels=dnspkt.query_records[0].labels,
            qtype=DNSPacket.MX_RECORD,
            qclass=1,
            ttl=0,
            # Preference followed by the MX record label
            data=struct.pack('>H', (i+1)*10) + DNSLabel.packall([DNSLabel('alt{}'.format(i+1))] + dnspkt.query_records[0].labels)
        ))

    # Authority section
    for i in range(4):
        dnspkt.authority_records.append(DNSAnswerRecord(
            labels=dnspkt.query_records[0].labels,
            qtype=DNSPacket.NS_RECORD,
            qclass=1,
            ttl=0,
            data=DNSLabel.packall([DNSLabel('ns{}'.format(i+1))] + dnspkt.query_records[0].labels)
        ))


    # Additional A records for alt
    for i in range(4):
        dnspkt.additional_records.append(DNSAnswerRecord(
            labels=[DNSLabel('alt{}'.format(i+1))] + dnspkt.query_records[0].labels,
            qtype=DNSPacket.A_RECORD,
            qclass=1,
            ttl=0,
            data=socket.inet_aton('192.168.0.10')
        ))

    # Additional A records for ns
    for i in range(4):
        dnspkt.additional_records.append(DNSAnswerRecord(
            labels=[DNSLabel('ns{}'.format(i+1))] + dnspkt.query_records[0].labels,
            qtype=DNSPacket.A_RECORD,
            qclass=1,
            ttl=0,
            data=socket.inet_aton('192.168.0.10')
        ))

    dnspkt.ancount = len(dnspkt.answer_records)
    dnspkt.arcount = len(dnspkt.additional_records)
    dnspkt.nscount = len(dnspkt.authority_records)

    dnspkt_data = dnspkt.pack()
    sock.sendto(dnspkt_data, addr)
