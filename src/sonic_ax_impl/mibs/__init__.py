import pprint
import re
import os

from swsssdk import SonicV2Connector
from swsssdk import SonicDBConfig
from swsssdk import port_util
from swsssdk.port_util import get_index, get_index_from_str
from ax_interface.mib import MIBUpdater
from ax_interface.util import oid2tuple
from sonic_ax_impl import logger

COUNTERS_PORT_NAME_MAP = b'COUNTERS_PORT_NAME_MAP'
COUNTERS_QUEUE_NAME_MAP = b'COUNTERS_QUEUE_NAME_MAP'
LAG_TABLE = b'LAG_TABLE'
LAG_MEMBER_TABLE = b'LAG_MEMBER_TABLE'
LOC_CHASSIS_TABLE = b'LLDP_LOC_CHASSIS'
APPL_DB = 'APPL_DB'
ASIC_DB = 'ASIC_DB'
COUNTERS_DB = 'COUNTERS_DB'
CONFIG_DB = 'CONFIG_DB'
STATE_DB = 'STATE_DB'
SNMP_OVERLAY_DB = 'SNMP_OVERLAY_DB'

TABLE_NAME_SEPARATOR_COLON = ':'
TABLE_NAME_SEPARATOR_VBAR = '|'

# This is used in both rfc2737 and rfc3433
SENSOR_PART_ID_MAP = {
    "temperature":  1,
    "voltage":      2,
    "rx1power":     11,
    "rx2power":     21,
    "rx3power":     31,
    "rx4power":     41,
    "tx1bias":      12,
    "tx2bias":      22,
    "tx3bias":      32,
    "tx4bias":      42,
    "tx1power":     13,
    "tx2power":     23,
    "tx3power":     33,
    "tx4power":     43,
}

RIF_COUNTERS_AGGR_MAP = {
    b"SAI_PORT_STAT_IF_IN_OCTETS": b"SAI_ROUTER_INTERFACE_STAT_IN_OCTETS",
    b"SAI_PORT_STAT_IF_IN_UCAST_PKTS": b"SAI_ROUTER_INTERFACE_STAT_IN_PACKETS",
    b"SAI_PORT_STAT_IF_IN_ERRORS": b"SAI_ROUTER_INTERFACE_STAT_IN_ERROR_PACKETS",
    b"SAI_PORT_STAT_IF_OUT_OCTETS": b"SAI_ROUTER_INTERFACE_STAT_OUT_OCTETS",
    b"SAI_PORT_STAT_IF_OUT_UCAST_PKTS": b"SAI_ROUTER_INTERFACE_STAT_OUT_PACKETS",
    b"SAI_PORT_STAT_IF_OUT_ERRORS": b"SAI_ROUTER_INTERFACE_STAT_OUT_ERROR_PACKETS"
}

RIF_DROPS_AGGR_MAP = {
    b"SAI_PORT_STAT_IF_IN_ERRORS": b"SAI_ROUTER_INTERFACE_STAT_IN_ERROR_PACKETS",
    b"SAI_PORT_STAT_IF_OUT_ERRORS": b"SAI_ROUTER_INTERFACE_STAT_OUT_ERROR_PACKETS"
}

# IfIndex to OID multiplier for transceiver
IFINDEX_SUB_ID_MULTIPLIER = 1000

redis_kwargs = {'unix_socket_path': '/var/run/redis/redis.sock'}

def chassis_info_table(chassis_name):
    """
    :param: chassis_name: chassis name
    :return: chassis info entry for this chassis
    """

    return "CHASSIS_INFO" + TABLE_NAME_SEPARATOR_VBAR + chassis_name

def psu_info_table(psu_name):
    """
    :param: psu_name: psu name
    :return: psu info entry for this psu
    """

    return "PSU_INFO" + TABLE_NAME_SEPARATOR_VBAR + psu_name

def counter_table(sai_id):
    """
    :param if_name: given sai_id to cast.
    :return: COUNTERS table key.
    """
    return b'COUNTERS:oid:0x' + sai_id

def queue_table(sai_id):
    """
    :param sai_id: given sai_id to cast.
    :return: COUNTERS table key.
    """
    return b'COUNTERS:' + sai_id

def queue_key(port_index, queue_index):
    return str(port_index) + ':' + str(queue_index)

def transceiver_info_table(port_name):
    """
    :param: port_name: port name
    :return: transceiver info entry for this port
    """

    return "TRANSCEIVER_INFO" + TABLE_NAME_SEPARATOR_VBAR + port_name

def transceiver_dom_table(port_name):
    """
    :param: port_name: port name
    :return: transceiver dom entry for this port
    """

    return "TRANSCEIVER_DOM_SENSOR" + TABLE_NAME_SEPARATOR_VBAR + port_name

def lldp_entry_table(if_name):
    """
    :param if_name: given interface to cast.
    :return: LLDP_ENTRY_TABLE key.
    """
    return b'LLDP_ENTRY_TABLE:' + if_name


def if_entry_table(if_name):
    """
    :param if_name: given interface to cast.
    :return: PORT_TABLE key.
    """
    return b'PORT_TABLE:' + if_name


def vlan_entry_table(if_name):
    """
    :param if_name: given interface to cast.
    :return: VLAN_TABLE key.
    """
    return b'VLAN_TABLE:' + if_name


def lag_entry_table(lag_name):
    """
    :param lag_name: given lag to cast.
    :return: LAG_TABLE key.
    """
    return b'LAG_TABLE:' + lag_name


def mgmt_if_entry_table(if_name):
    """
    :param if_name: given interface to cast
    :return: MGMT_PORT_TABLE key
    """

    return b'MGMT_PORT|' + if_name


def mgmt_if_entry_table_state_db(if_name):
    """
    :param if_name: given interface to cast
    :return: MGMT_PORT_TABLE key
    """

    return b'MGMT_PORT_TABLE|' + if_name


def config(**kwargs):
    global redis_kwargs
    redis_kwargs = {k:v for (k,v) in kwargs.items() if k in ['unix_socket_path', 'host', 'port']}

def init_db():
    """
    Connects to DB
    :return: db_conn
    """
    # SyncD database connector. THIS MUST BE INITIALIZED ON A PER-THREAD BASIS.
    # Redis PubSub objects (such as those within swsssdk) are NOT thread-safe.
    db_conn = SonicV2Connector(**redis_kwargs) 

    return db_conn

def init_mgmt_interface_tables(db_conn):
    """
    Initializes interface maps for mgmt ports
    :param db_conn: db connector
    :return: tuple of mgmt name to oid map and mgmt name to alias map
    """

    db_conn.connect(CONFIG_DB)
    db_conn.connect(STATE_DB)

    mgmt_ports_keys = db_conn.keys(CONFIG_DB, mgmt_if_entry_table(b'*'))

    if not mgmt_ports_keys:
        logger.debug('No managment ports found in {}'.format(mgmt_if_entry_table(b'')))
        return {}, {}

    mgmt_ports = [key.split(mgmt_if_entry_table(b''))[-1] for key in mgmt_ports_keys]
    oid_name_map = {get_index(mgmt_name): mgmt_name for mgmt_name in mgmt_ports}
    logger.debug('Managment port map:\n' + pprint.pformat(oid_name_map, indent=2))

    if_alias_map = dict()

    for if_name in oid_name_map.values():
        if_entry = db_conn.get_all(CONFIG_DB, mgmt_if_entry_table(if_name), blocking=True)
        if_alias_map[if_name] = if_entry.get(b'alias', if_name)

    logger.debug("Management alias map:\n" + pprint.pformat(if_alias_map, indent=2))

    return oid_name_map, if_alias_map

def init_sync_d_interface_tables(db_conn):
    """
    Initializes interface maps for SyncD-connected MIB(s).
    :return: tuple(if_name_map, if_id_map, oid_map, if_alias_map)
    """

    # Make sure we're connected to COUNTERS_DB
    db_conn.connect(COUNTERS_DB)

    # { if_name (SONiC) -> sai_id }
    # ex: { "Ethernet76" : "1000000000023" }
    if_name_map, if_id_map = port_util.get_interface_oid_map(db_conn)
    if_name_map = {if_name: sai_id for if_name, sai_id in if_name_map.items() if \
                   (re.match(port_util.SONIC_ETHERNET_RE_PATTERN, if_name.decode()) or \
                    re.match(port_util.SONIC_ETHERNET_BP_RE_PATTERN, if_name.decode()))}
    if_id_map = {sai_id: if_name for sai_id, if_name in if_id_map.items() if \
                 (re.match(port_util.SONIC_ETHERNET_RE_PATTERN, if_name.decode()) or \
                  re.match(port_util.SONIC_ETHERNET_BP_RE_PATTERN, if_name.decode()))}
    logger.debug("Port name map:\n" + pprint.pformat(if_name_map, indent=2))
    logger.debug("Interface name map:\n" + pprint.pformat(if_id_map, indent=2))

    # { OID -> sai_id }
    oid_sai_map = {get_index(if_name): sai_id for if_name, sai_id in if_name_map.items()
                   # only map the interface if it's a style understood to be a SONiC interface.
                   if get_index(if_name) is not None}
    logger.debug("OID sai map:\n" + pprint.pformat(oid_sai_map, indent=2))

    # { OID -> if_name (SONiC) }
    oid_name_map = {get_index(if_name): if_name for if_name in if_name_map
                    # only map the interface if it's a style understood to be a SONiC interface.
                    if get_index(if_name) is not None}

    logger.debug("OID name map:\n" + pprint.pformat(oid_name_map, indent=2))

    # SyncD consistency checks.
    if not oid_sai_map:
        # In the event no interface exists that follows the SONiC pattern, no OIDs are able to be registered.
        # A RuntimeError here will prevent the 'main' module from loading. (This is desirable.)
        message = "No interfaces found matching pattern '{}'. SyncD database is incoherent." \
            .format(port_util.SONIC_ETHERNET_RE_PATTERN)
        logger.error(message)
        raise RuntimeError(message)
    elif len(if_id_map) < len(if_name_map) or len(oid_sai_map) < len(if_name_map):
        # a length mismatch indicates a bad interface name
        logger.warning("SyncD database contains incoherent interface names. Interfaces must match pattern '{}'"
                       .format(port_util.SONIC_ETHERNET_RE_PATTERN))
        logger.warning("Port name map:\n" + pprint.pformat(if_name_map, indent=2))

    db_conn.connect(APPL_DB)

    if_alias_map = dict()

    for if_name in if_name_map:
        if_entry = db_conn.get_all(APPL_DB, if_entry_table(if_name), blocking=True)
        if_alias_map[if_name] = if_entry.get(b'alias', if_name)

    logger.debug("Chassis name map:\n" + pprint.pformat(if_alias_map, indent=2))

    return if_name_map, if_alias_map, if_id_map, oid_sai_map, oid_name_map

  
def init_sync_d_rif_tables(db_conn):
    """
    Initializes map of RIF SAI oids to port SAI oid.
    :return: dict
    """
    rif_port_map = port_util.get_rif_port_map(db_conn)

    if not rif_port_map:
        return {}, {}
    port_rif_map = {port: rif for rif, port in rif_port_map.items()}
    logger.debug("Rif port map:\n" + pprint.pformat(rif_port_map, indent=2))

    return rif_port_map, port_rif_map


def init_sync_d_vlan_tables(db_conn):
    """
    Initializes vlan interface maps for SyncD-connected MIB(s).
    :return: tuple(vlan_name_map, oid_sai_map, oid_name_map)
    """

    vlan_name_map = port_util.get_vlan_interface_oid_map(db_conn)

    logger.debug("Vlan oid map:\n" + pprint.pformat(vlan_name_map, indent=2))

    # { OID -> sai_id }
    oid_sai_map = {get_index(if_name): sai_id for sai_id, if_name in vlan_name_map.items()
                   # only map the interface if it's a style understood to be a SONiC interface.
                   if get_index(if_name) is not None}
    logger.debug("OID sai map:\n" + pprint.pformat(oid_sai_map, indent=2))

    # { OID -> if_name (SONiC) }
    oid_name_map = {get_index(if_name): if_name for sai_id, if_name in vlan_name_map.items()
                   # only map the interface if it's a style understood to be a SONiC interface.
                   if get_index(if_name) is not None}

    logger.debug("OID name map:\n" + pprint.pformat(oid_name_map, indent=2))

    return vlan_name_map, oid_sai_map, oid_name_map


def init_sync_d_lag_tables(db_conn):
    """
    Helper method. Connects to and initializes LAG interface maps for SyncD-connected MIB(s).
    :param db_conn: database connector
    :return: tuple(lag_name_if_name_map, if_name_lag_name_map, oid_lag_name_map)
    """
    # { lag_name (SONiC) -> [ lag_members (if_name) ] }
    # ex: { "PortChannel0" : [ "Ethernet0", "Ethernet4" ] }
    lag_name_if_name_map = {}
    # { if_name (SONiC) -> lag_name }
    # ex: { "Ethernet0" : "PortChannel0" }
    if_name_lag_name_map = {}
    # { OID -> lag_name (SONiC) }
    oid_lag_name_map = {}
    # { lag_name (SONiC) -> lag_oid (SAI) }
    lag_sai_map = {}

    db_conn.connect(APPL_DB)
    lag_entries = db_conn.keys(APPL_DB, b"LAG_TABLE:*")

    if not lag_entries:
        return lag_name_if_name_map, if_name_lag_name_map, oid_lag_name_map, lag_sai_map

    db_conn.connect(COUNTERS_DB)
    lag_sai_map = db_conn.get_all(COUNTERS_DB, b"COUNTERS_LAG_NAME_MAP")
    lag_sai_map = {name: sai_id.lstrip(b"oid:0x") for name, sai_id in lag_sai_map.items()}

    for lag_entry in lag_entries:
        lag_name = lag_entry[len(b"LAG_TABLE:"):]
        lag_members = db_conn.keys(APPL_DB, b"LAG_MEMBER_TABLE:%s:*" % lag_name)
        # TODO: db_conn.keys() should really return [] instead of None
        if lag_members is None:
            lag_members = []

        def member_name_str(val, lag_name):
            return val[len(b"LAG_MEMBER_TABLE:%s:" % lag_name):]

        lag_member_names = [member_name_str(m, lag_name) for m in lag_members]
        lag_name_if_name_map[lag_name] = lag_member_names
        for lag_member_name in lag_member_names:
            if_name_lag_name_map[lag_member_name] = lag_name

    for if_name in lag_name_if_name_map.keys():
        idx = get_index(if_name)
        if idx:
            oid_lag_name_map[idx] = if_name

    return lag_name_if_name_map, if_name_lag_name_map, oid_lag_name_map, lag_sai_map

def init_sync_d_queue_tables(db_conn):
    """
    Initializes queue maps for SyncD-connected MIB(s).
    :return: tuple(port_queues_map, queue_stat_map)
    """

    # Make sure we're connected to COUNTERS_DB
    db_conn.connect(COUNTERS_DB)

    # { Port index : Queue index (SONiC) -> sai_id }
    # ex: { "1:2" : "1000000000023" }
    queue_name_map = db_conn.get_all(COUNTERS_DB, COUNTERS_QUEUE_NAME_MAP, blocking=True)
    logger.debug("Queue name map:\n" + pprint.pformat(queue_name_map, indent=2))

    # Parse the queue_name_map and create the following maps:
    # port_queues_map -> {"if_index : queue_index" : sai_oid}
    # queue_stat_map -> {queue stat table name : {counter name : value}}
    # port_queue_list_map -> {if_index: [sorted queue list]}
    port_queues_map = {}
    queue_stat_map = {}
    port_queue_list_map = {}

    for queue_name, sai_id in queue_name_map.items():
        port_name, queue_index = queue_name.decode().split(':')
        queue_index = ''.join(i for i in queue_index if i.isdigit())
        port_index = get_index_from_str(port_name)
        key = queue_key(port_index, queue_index)
        port_queues_map[key] = sai_id

        queue_stat_name = queue_table(sai_id)
        queue_stat = db_conn.get_all(COUNTERS_DB, queue_stat_name, blocking=False)
        if queue_stat is not None:
            queue_stat_map[queue_stat_name] = queue_stat

        if not port_queue_list_map.get(int(port_index)):
            port_queue_list_map[int(port_index)] = [int(queue_index)]
        else:
            port_queue_list_map[int(port_index)].append(int(queue_index))

    # SyncD consistency checks.
    if not port_queues_map:
        # In the event no queue exists that follows the SONiC pattern, no OIDs are able to be registered.
        # A RuntimeError here will prevent the 'main' module from loading. (This is desirable.)
        logger.error("No queues found in the Counter DB. SyncD database is incoherent.")
        raise RuntimeError('The port_queues_map is not defined')
    elif not queue_stat_map:
        logger.error("No queue stat counters found in the Counter DB. SyncD database is incoherent.")
        raise RuntimeError('The queue_stat_map is not defined')

    for queues in port_queue_list_map.values():
        queues.sort()

    return port_queues_map, queue_stat_map, port_queue_list_map

def get_device_metadata(db_conn):
    """
    :param db_conn: Sonic DB connector
    :return: device metadata
    """

    DEVICE_METADATA = "DEVICE_METADATA|localhost"
    db_conn.connect(db_conn.STATE_DB)

    device_metadata = db_conn.get_all(db_conn.STATE_DB, DEVICE_METADATA)
    return device_metadata

def get_transceiver_sub_id(ifindex):
    """
    Returns sub OID for transceiver. Sub OID is calculated as folows:
    +------------+------------+
    |Interface   |Index       |
    +------------+------------+
    |Ethernet[X] |X * 1000    |
    +------------+------------+
    ()
    :param ifindex: interface index
    :return: sub OID of a port calculated as sub OID = {{index}} * 1000
    """

    return (ifindex * IFINDEX_SUB_ID_MULTIPLIER, )

def get_transceiver_sensor_sub_id(ifindex, sensor):
    """
    Returns sub OID for transceiver sensor. Sub OID is calculated as folows:
    +-------------------------------------+------------------------------+
    |Sensor                               |Index                         |
    +-------------------------------------+------------------------------+
    |RX Power for Ethernet[X]/[LANEID]    |X * 1000 + LANEID * 10 + 1    |
    |TX Bias for Ethernet[X]/[LANEID]     |X * 1000 + LANEID * 10 + 2    |
    |Temperature for Ethernet[X]          |X * 1000 + 1                  |
    |Voltage for Ethernet[X]/[LANEID]     |X * 1000 + 2                  |
    +-------------------------------------+------------------------------+
    ()
    :param ifindex: interface index
    :param sensor: sensor key
    :return: sub OID = {{index}} * 1000 + {{lane}} * 10 + sensor id
    """

    transceiver_oid, = get_transceiver_sub_id(ifindex)
    return (transceiver_oid + SENSOR_PART_ID_MAP[sensor], )

class RedisOidTreeUpdater(MIBUpdater):
    def __init__(self, prefix_str):
        super().__init__()

        self.db_conn = Namespace.init_namespace_dbs() 
        if prefix_str.startswith('.'):
            prefix_str = prefix_str[1:]
        self.prefix_str = prefix_str

    def get_next(self, sub_id):
        """
        :param sub_id: The 1-based sub-identifier query.
        :return: the next sub id.
        """
        raise NotImplementedError

    def reinit_data(self):
        """
        Subclass update loopback information
        """
        pass

    def update_data(self):
        """
        Update redis (caches config)
        Pulls the table references for each interface.
        """
        self.oid_list = []
        self.oid_map = {}

        keys = Namespace.dbs_keys(self.db_conn, SNMP_OVERLAY_DB, self.prefix_str + '*') 
        # TODO: fix db_conn.keys to return empty list instead of None if there is no match
        if keys is None:
            keys = []

        for key in keys:
            key = key.decode()
            oid = oid2tuple(key, dot_prefix=False)
            self.oid_list.append(oid)
            value = Namespace.dbs_get_all(self.db_conn, SNMP_OVERLAY_DB, key) 
            if value[b'type'] in [b'COUNTER_32', b'COUNTER_64']:
                self.oid_map[oid] = int(value[b'data'])
            else:
                raise ValueError("Invalid value type")

        self.oid_list.sort()

    def get_oidvalue(self, oid):
        if oid not in self.oid_map:
            return None
        return self.oid_map[oid]

class Namespace:
    @staticmethod
    def init_namespace_dbs():
        db_conn= []
        SonicDBConfig.load_sonic_global_db_config()
        for namespace in SonicDBConfig.get_ns_list():
            db = SonicV2Connector(use_unix_socket_path=True, namespace=namespace)
            db_conn.append(db)

        return db_conn

    @staticmethod
    def connect_all_dbs(dbs, db_name):
        for db_conn in dbs:
            db_conn.connect(db_name)

    @staticmethod
    def dbs_keys(dbs, db_name, pattern='*'):
        """
        db keys function execute on global and all namespace DBs.
        """
        result_keys=[]
        for db_conn in dbs:
            db_conn.connect(db_name)
            keys = db_conn.keys(db_name, pattern)
            if keys is not None:
                result_keys.extend(keys)
        return result_keys

    @staticmethod
    def dbs_get_all(dbs, db_name, _hash, *args, **kwargs):
        """
        db get_all function executed on global and all namespace DBs.
        """
        result = {}
        for db_conn in dbs:
            db_conn.connect(db_name)
            if(db_conn.exists(db_name, _hash)):
                ns_result = db_conn.get_all(db_name, _hash, *args, **kwargs)
                if ns_result is not None:
                    result.update(ns_result)
        return result

    @staticmethod
    def get_non_host_dbs(dbs):
        """
        From the list of all dbs, return the list of dbs
        which will have interface related tables.
        For single namespace db, return the single db.
        For multiple namespace dbs, return all dbs except the
        host namespace db which is the first db in the list.
        """
        if len(dbs) == 1:
            return dbs
        else:
            return dbs[1:]
        

    @staticmethod
    def init_namespace_sync_d_interface_tables(dbs):
        if_name_map = {}
        if_alias_map = {}
        if_id_map = {}
        oid_sai_map = {}
        oid_name_map = {}

        """
        all_ns_db - will have db_conn to all namespace DBs and
        global db. First db in the list is global db.
        Ignore first global db to get interface tables if there
        are multiple namespaces.
        """
        for db_conn in Namespace.get_non_host_dbs(dbs):
            if_name_map_ns, \
            if_alias_map_ns, \
            if_id_map_ns, \
            oid_sai_map_ns, \
            oid_name_map_ns = init_sync_d_interface_tables(db_conn)
            if_name_map.update(if_name_map_ns)
            if_alias_map.update(if_alias_map_ns)
            if_id_map.update(if_id_map_ns)
            oid_sai_map.update(oid_sai_map_ns)
            oid_name_map.update(oid_name_map_ns)

        return if_name_map, if_alias_map, if_id_map, oid_sai_map, oid_name_map

    @staticmethod
    def init_namespace_sync_d_lag_tables(dbs):

        lag_name_if_name_map = {}
        if_name_lag_name_map = {}
        oid_lag_name_map = {}
        lag_sai_map = {}

        """
        all_ns_db - will have db_conn to all namespace DBs and
        global db. First db in the list is global db.
        Ignore first global db to get lag tables if
        there are multiple namespaces.
        """
        for db_conn in Namespace.get_non_host_dbs(dbs):
            lag_name_if_name_map_ns, \
            if_name_lag_name_map_ns, \
            oid_lag_name_map_ns, \
            lag_sai_map_ns = init_sync_d_lag_tables(db_conn)
            lag_name_if_name_map.update(lag_name_if_name_map_ns)
            if_name_lag_name_map.update(if_name_lag_name_map_ns)
            oid_lag_name_map.update(oid_lag_name_map_ns)
            lag_sai_map.update(lag_sai_map_ns)

        return lag_name_if_name_map, if_name_lag_name_map, oid_lag_name_map, lag_sai_map

    @staticmethod
    def init_namespace_sync_d_rif_tables(dbs):
        rif_port_map = {}
        port_rif_map = {}

        for db_conn in Namespace.get_non_host_dbs(dbs):
            rif_port_map_ns, \
            port_rif_map_ns = init_sync_d_rif_tables(db_conn)
            rif_port_map.update(rif_port_map_ns)
            port_rif_map.update(port_rif_map_ns)

        return rif_port_map, port_rif_map

    @staticmethod
    def init_namespace_sync_d_vlan_tables(dbs):
        vlan_name_map = {}
        oid_sai_map = {}
        oid_name_map = {}

        for db_conn in Namespace.get_non_host_dbs(dbs):
            vlan_name_map_ns, \
            oid_sai_map_ns, \
            oid_name_map_ns = init_sync_d_vlan_tables(db_conn)
            vlan_name_map.update(vlan_name_map_ns)
            oid_sai_map.update(oid_sai_map_ns)
            oid_name_map.update(oid_name_map_ns)

        return vlan_name_map, oid_sai_map, oid_name_map

    @staticmethod
    def init_namespace_sync_d_queue_tables(dbs):
        port_queues_map = {}
        queue_stat_map = {}
        port_queue_list_map = {}

        """
        all_ns_db - will have db_conn to all namespace DBs and
        global db. First db in the list is global db.
        Ignore first global db to get queue tables if there
        are multiple namespaces.
        """
        for db_conn in Namespace.get_non_host_dbs(dbs):
            port_queues_map_ns, \
            queue_stat_map_ns, \
            port_queue_list_map_ns = init_sync_d_queue_tables(db_conn)
            port_queues_map.update(port_queues_map_ns)
            queue_stat_map.update(queue_stat_map_ns)
            port_queue_list_map.update(port_queue_list_map_ns)

        return port_queues_map, queue_stat_map, port_queue_list_map

    @staticmethod
    def dbs_get_bridge_port_map(dbs, db_name):
        """
        get_bridge_port_map from all namespace DBs
        """
        if_br_oid_map = {}
        for db_conn in Namespace.get_non_host_dbs(dbs):
            if_br_oid_map_ns = port_util.get_bridge_port_map(db_conn)
            if_br_oid_map.update(if_br_oid_map_ns)
        return if_br_oid_map

    @staticmethod
    def dbs_get_vlan_id_from_bvid(dbs, bvid):
        for db_conn in Namespace.get_non_host_dbs(dbs):
            db_conn.connect('ASIC_DB')
            vlan_obj = db.keys('ASIC_DB', "ASIC_STATE:SAI_OBJECT_TYPE_VLAN:" + bvid)
            if vlan_obj is not None:
                return port_util.get_vlan_id_from_bvid(db_conn, bvid)
