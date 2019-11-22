#!/usr/bin/python
"""
This is the most simple example to showcase Containernet.
"""
from mininet.net import Containernet
from mininet.node import POX, RemoteController
from mininet.cli import CLI
from mininet.link import TCLink
from mininet.log import info, setLogLevel
setLogLevel('info')

#net = Containernet(controller=POX)
net = Containernet()
info('*** Adding controller\n')
#net.addController('c0', poxArgs = 'forwarding.droplist')
net.addController('c0', controller=RemoteController, ip='127.0.0.1', port=6653)
info('*** Adding docker containers\n')
d1 = net.addDocker('d1', ip='10.0.0.251', dimage="tcpebpf")
d2 = net.addDocker('d2', ip='10.0.0.252', dimage="tcpebpf")
info('*** Adding switches\n')
s1 = net.addSwitch('s1')
info('*** Creating links\n')
net.addLink(d1, s1, cls=TCLink, delay='100ms', bw=1)
net.addLink(s1, d2)
info('*** Starting network\n')
net.start()
#info('*** Testing connectivity\n')
#net.ping([d1, d2])
CLI(net)
info('*** Stopping network')
net.stop()

