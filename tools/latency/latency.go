/*
Copyright 2013-2014 Graham King

Modified by Simon Jouet <simon.jouet@glasgow.ac.uk> to support
continuous measurements and keep track of the packet lost.

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

For full license details see <http://www.gnu.org/licenses/>.
*/

package main

import (
    "flag"
    "fmt"
    "log"
    "math/rand"
    "net"
    "os"
    "os/signal"
    "strconv"
    "strings"
    "time"
)

var (
    ifaceParam    = flag.String("I", "", "Interface (e.g. eth0, wlan1, etc)")
    helpParam     = flag.Bool("h", false, "Print help")
    portParam     = flag.Int("p", 80, "Port to test against (default 80)")
    intervalParam = flag.Int("i", 1000, "ms interval between pings")
)

func main() {
    flag.Parse()

    if *helpParam {
        printHelp()
        os.Exit(1)
    }

    iface := *ifaceParam
    if iface == "" {
        iface = chooseInterface()
        if iface == "" {
            fmt.Println("Could not decide which net interface to use.")
            fmt.Println("Specify it with -i <iface> param")
            os.Exit(1)
        }
    }

    localAddr := interfaceAddress(iface)
    laddr := strings.Split(localAddr.String(), "/")[0] // Clean addresses like 192.168.1.30/24

    port := uint16(*portParam)

    if len(flag.Args()) == 0 {
        fmt.Println("Missing remote address")
        printHelp()
        os.Exit(1)
    }

    remoteHost := flag.Arg(0)
    fmt.Println("Measuring round-trip latency from", laddr, "to", remoteHost, "on port", port)

        pending := make(map[uint32]time.Time)

    //
    netaddr, err := net.ResolveIPAddr("ip4", laddr)
    if err != nil {
            log.Fatalf("net.ResolveIPAddr: %s. %s\n", laddr, netaddr)
    }

    conn, err := net.ListenIP("ip4:tcp", netaddr)
    if err != nil {
            log.Fatalf("ListenIP: %s\n", err)
    }

    signalChannel := make(chan os.Signal, 1)
    signal.Notify(signalChannel, os.Interrupt)
    go func() {
        <-signalChannel

        for _, sendTime := range pending {
            log.Printf("sendTime: %v\treceiveTime: %v\tlatency: %v\n", sendTime.UnixNano(), 0, "timeout")
        }

        os.Exit(1)
    }()

    go func() {
        buf := make([]byte, 1024)

        for {
            numRead, raddr, err := conn.ReadFrom(buf)
            if err != nil {
            	log.Fatalf("ReadFrom: %s\n", err)
            }

            if raddr.String() != remoteHost {
            	// this is not the packet we are looking for
                continue
            }

            receiveTime := time.Now()
            tcp := NewTCPHeader(buf[:numRead])

            // Closed port gets RST, open port gets SYN ACK
            if tcp.HasFlag(RST) || (tcp.HasFlag(SYN) && tcp.HasFlag(ACK)) {
	            if sendTime, ok := pending[tcp.AckNum-1]; ok {
	                lat := receiveTime.Sub(sendTime)
	                log.Printf("sendTime: %v\treceiveTime: %v\tlatency: %v\n", sendTime.UnixNano(), receiveTime.UnixNano(), lat)
	                delete(pending, tcp.AckNum-1)
	            } else {
	                log.Println("unexpected packet, not pending")
	            }
            }
        }
    }()

    // Send syn packet at interval
    ticker := time.Tick(time.Duration(*intervalParam) * time.Millisecond)
    for _ = range ticker {
        // Send the SYN packet and mark it as pending
        sendTime, seqNum := sendSyn(laddr, remoteHost, port)
        pending[seqNum] = sendTime
        port++
    }
}

func chooseInterface() string {
    interfaces, err := net.Interfaces()
    if err != nil {
        log.Fatalf("net.Interfaces: %s", err)
    }
    for _, iface := range interfaces {
        // Skip loopback
        if iface.Name == "lo" {
            continue
        }
        addrs, err := iface.Addrs()
        // Skip if error getting addresses
        if err != nil {
            log.Println("Error get addresses for interfaces %s. %s", iface.Name, err)
            continue
        }

        if len(addrs) > 0 {
            // This one will do
            return iface.Name
        }
    }

    return ""
}

func interfaceAddress(ifaceName string) net.Addr {
    iface, err := net.InterfaceByName(ifaceName)
    if err != nil {
        log.Fatalf("net.InterfaceByName for %s. %s", ifaceName, err)
    }
    addrs, err := iface.Addrs()
    if err != nil {
        log.Fatalf("iface.Addrs: %s", err)
    }
    return addrs[0]
}

func printHelp() {
    help := `
    USAGE: latency [-h] [-a] [-i iface] [-p port] <remote>
    Where 'remote' is an ip address or host name.
    Default port is 80
    -h: Help
    `
    fmt.Println(help)
}

func sendSyn(laddr, raddr string, port uint16) (time.Time, uint32) {
    packet := TCPHeader{
        Source:      0xaa47, // Random ephemeral port
        Destination: port,
        SeqNum:      rand.Uint32(),
        AckNum:      0,
        DataOffset:  5,      // 4 bits
        Reserved:    0,      // 3 bits
        ECN:         0,      // 3 bits
        Ctrl:        2,      // 6 bits (000010, SYN bit set)
        Window:      0xaaaa, // The amount of data that it is able to accept in bytes
        Checksum:    0,      // Kernel will set this if it's 0
        Urgent:      0,
        Options:     []TCPOption{},
    }

    data := packet.Marshal()
    packet.Checksum = Csum(data, to4byte(laddr), to4byte(raddr))

    data = packet.Marshal()
    conn, err := net.Dial("ip4:tcp", raddr)
    if err != nil {
        log.Fatalf("Dial: %s\n", err)
    }

    sendTime := time.Now()

    numWrote, err := conn.Write(data)
    if err != nil {
        log.Fatalf("Write: %s\n", err)
    }
    if numWrote != len(data) {
        log.Fatalf("Short write. Wrote %d/%d bytes\n", numWrote, len(data))
    }

    conn.Close()

    return sendTime, packet.SeqNum
}

func to4byte(addr string) [4]byte {
    parts := strings.Split(addr, ".")
    b0, err := strconv.Atoi(parts[0])
    if err != nil {
        log.Fatalf("to4byte: %s (latency works with IPv4 addresses only, but not IPv6!)\n", err)
    }
    b1, _ := strconv.Atoi(parts[1])
    b2, _ := strconv.Atoi(parts[2])
    b3, _ := strconv.Atoi(parts[3])
    return [4]byte{byte(b0), byte(b1), byte(b2), byte(b3)}
}
