from pox.core import core
import pox.openflow.libopenflow_01 as of
from pox.lib.util import dpid_to_str, str_to_dpid
from pox.lib.util import str_to_bool
import pox.lib.packet.ethernet as ethpkt
import pox.lib.packet.ipv4 as ippkt
import time

log = core.getLogger()

# We don't want to flood immediately when a switch connects.
# Can be overriden on commandline.
_flood_delay = 0

class DropListPerNode (object):
    def __init__(self, src_ip, droplist):
        self.src_ip = src_ip
        self.droplist = droplist
        self.counter = 0

class DropListSwitch (object):
    def __init__ (self, connection, transparent, droplist_client, droplist_server):
        # Switch we'll be adding L2 droplist switch capabilities to
        self.connection = connection
        self.transparent = transparent
        self.client = DropListPerNode("10.0.0.252", droplist_client)
        self.server = DropListPerNode("10.0.0.251", droplist_server)

        # Our table
        self.macToPort = {}

        # We want to hear PacketIn messages, so we listen
        # to the connection
        connection.addListeners(self)

        # We just use this to know when to log a helpful message
        self.hold_down_expired = _flood_delay == 0

        #log.debug("Initializing DropList, transparent=%s",
        #          str(self.transparent))

    """
    In short, our algorithm looks like this:

    For each packet from the switch:
    1) Use source address and switch port to update address/port table
    2) Update counter for amount packets sent by sender
       Check if packet is in droplist?
       Yes:
            2a) Drop packet
                Done
    3) Is transparent = False and either Ethertype is LLDP or the packet's
        destination address is a Bridge Filtered address?
        Yes:
            3a) Drop packet -- don't forward link-local traffic (LLDP, 802.1x)
                DONE
    4) Is destination multicast?
        Yes:
            4a) Flood the packet
                DONE
    5) Port for destination address in our address/port table?
        No:
            5a) Flood the packet
                DONE
    6) Is output port the same as input port?
        Yes:
            6a) Drop packet and similar ones for a while
    7) Send the packet out appropriate port
    """
    def _handle_PacketIn (self, event):
        """
        Handle packet in messages from the switch to implement above algorithm.
        """

        packet = event.parsed

        def flood (message = None):
            """ Floods the packet """
            msg = of.ofp_packet_out()
            if time.time() - self.connection.connect_time >= _flood_delay:
            # Only flood if we've been connected for a little while...

                if self.hold_down_expired is False:
                    # Oh yes it is!
                    self.hold_down_expired = True
                    #log.info("%s: Flood hold-down expired -- flooding",
                    #    dpid_to_str(event.dpid))

                if message is not None: log.debug(message)
                #log.debug("%i: flood %s -> %s", event.dpid,packet.src,packet.dst)
                # OFPP_FLOOD is optional; on some switches you may need to change
                # this to OFPP_ALL.
                msg.actions.append(of.ofp_action_output(port = of.OFPP_FLOOD))
            else:
                pass
                #log.info("Holding down flood for %s", dpid_to_str(event.dpid))
            msg.data = event.ofp
            msg.in_port = event.port
            self.connection.send(msg)

        def drop (duration = None):
            """
            Drops this packet and optionally installs a flow to continue
            dropping similar ones for a while
            """
            if duration is not None:
                if not isinstance(duration, tuple):
                    duration = (duration,duration)
                    msg = of.ofp_flow_mod()
                    msg.match = of.ofp_match.from_packet(packet)
                    msg.idle_timeout = duration[0]
                    msg.hard_timeout = duration[1]
                    msg.buffer_id = event.ofp.buffer_id
                    self.connection.send(msg)
            elif event.ofp.buffer_id is not None:
                msg = of.ofp_packet_out()
                msg.buffer_id = event.ofp.buffer_id
                msg.in_port = event.port
                self.connection.send(msg)

        def checkDropList():
            if packet.type == ethpkt.IP_TYPE:
                ip_packet = packet.payload
                if ip_packet.protocol == ippkt.UDP_PROTOCOL or ip_packet.protocol == ippkt.TCP_PROTOCOL:
                    if self.client.src_ip == ip_packet.srcip: #2
                        self.client.counter += 1
                        if str(self.client.counter) in self.client.droplist: #2a
                            log.debug("Dropping client packet: number %d" %
                                    (self.client.counter))
                            drop()
                            return True
                        else:
                            return False
                    elif self.server.src_ip == ip_packet.srcip: #2
                        self.server.counter += 1
                        if str(self.server.counter) in self.server.droplist: #2a
                            log.debug("Dropping server packet: number %d" %
                                    (self.server.counter))
                            drop()
                            return True
                        else:
                            return False
                    else:
                        return False
                else:
                    return False
            else:
                return False
        self.macToPort[packet.src] = event.port #1

        if (checkDropList()):
            return

        if not self.transparent: # 3
            if packet.type == packet.LLDP_TYPE or packet.dst.isBridgeFiltered():
                drop() # 3a
                return

        if packet.dst.is_multicast:
            flood() # 4a
        else:
            if packet.dst not in self.macToPort: #5
                flood("Port for %s unknown -- flooding" % (packet.dst,)) # 5a
            else:
                port = self.macToPort[packet.dst]
                if port == event.port: #6
                    # 6a
                    log.warning("Same port for packet from %s -> %s on %s.%s.  Drop."
                        % (packet.src, packet.dst, dpid_to_str(event.dpid), port))
                    drop(10)
                    return
                #7
                #log.debug("installing flow for %s.%i -> %s.%i" %
                #      (packet.src, event.port, packet.dst, port))
                msg = of.ofp_packet_out()
                msg.actions.append(of.ofp_action_output(port = port))
                msg.data = event.ofp
                self.connection.send(msg)


class l2_dropper (object):
    """
    Waits for OpenFlow switches to connect and makes them droplist switches.
    """
    def __init__ (self, transparent, ignore = None, droplist_client = [], droplist_server = []):
        core.openflow.addListeners(self)
        self.transparent = transparent
        self.ignore = set(ignore) if ignore else ()
        self.droplist_client = droplist_client
        self.droplist_server = droplist_server

    def _handle_ConnectionUp (self, event):
        if event.dpid in self.ignore:
            log.debug("Ignoring connection %s" % (event.connection,))
            return
        log.debug("Connection %s" % (event.connection,))
        DropListSwitch(event.connection, self.transparent, self.droplist_client, self.droplist_server)


def launch (transparent=False, hold_down=_flood_delay, ignore = None, droplist_client = [], droplist_server = []):
    """
    Starts an L2 droplist switch.
    """
    try:
        global _flood_delay
        _flood_delay = int(str(hold_down), 10)
        assert _flood_delay >= 0
    except:
        raise RuntimeError("Expected hold-down to be a number")

    if ignore:
        ignore = ignore.replace(',', ' ').split()
        ignore = set(str_to_dpid(dpid) for dpid in ignore)

    core.registerNew(l2_dropper, str_to_bool(transparent), ignore, droplist_client, droplist_server)
