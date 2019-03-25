from enum import Enum, unique
from sonic_ax_impl import mibs
from ax_interface import MIBMeta, ValueType, SubtreeMIBEntry
from swsssdk import SonicV2Connector

CHASSIS_INFO_KEY_TEMPLATE = 'chassis {}'
PSU_INFO_KEY_TEMPLATE = 'PSU {}'

PSU_PRESENCE_OK = 'true'
PSU_STATUS_OK = 'true'

@unique
class CHASSISInfoDB(bytes, Enum):
    """
    CHASSIS info keys
    """

    PSU_NUM = b"psu_num"

@unique
class PSUInfoDB(bytes, Enum):
    """
    PSU info keys
    """

    PRESENCE = b"presence"
    STATUS = b"status"

def get_chassis_data(chassis_info):
    """
    :param chassis_info: chassis info dict
    :return: tuple (psu_num) of chassis;
    Empty string if field not in chassis_info
    """

    return tuple(chassis_info.get(chassis_field.value, b"").decode() for chassis_field in CHASSISInfoDB)

def get_psu_data(psu_info):
    """
    :param psu_info: psu info dict
    :return: tuple (presence, status) of psu;
    Empty string if field not in psu_info
    """

    return tuple(psu_info.get(psu_field.value, b"").decode() for psu_field in PSUInfoDB)

class PowerStatusHandler:
    """
    Class to handle the SNMP request
    """
    def __init__(self):
        """
        init the handler
        """
        self.statedb = SonicV2Connector()
        self.statedb.connect(self.statedb.STATE_DB)

    def _getPsuNum(self):
        """
        Get PSU number
        :return: the number of supported PSU
        """
        chassis_name = CHASSIS_INFO_KEY_TEMPLATE.format(1)
        chassis_info = self.statedb.get_all(self.statedb.STATE_DB, mibs.chassis_info_table(chassis_name))
        psu_num = get_chassis_data(chassis_info)

        return int(psu_num[0])

    def _getPsuPresence(self, psu_index):
        """
        Get PSU presence
        :return: the presence of particular PSU
        """
        psu_name = PSU_INFO_KEY_TEMPLATE.format(psu_index)
        psu_info = self.statedb.get_all(self.statedb.STATE_DB, mibs.psu_info_table(psu_name))
        presence, status = get_psu_data(psu_info)

        return presence == PSU_PRESENCE_OK

    def _getPsuStatus(self, psu_index):
        """
        Get PSU status
        :return: the status of particular PSU
        """
        psu_name = PSU_INFO_KEY_TEMPLATE.format(psu_index)
        psu_info = self.statedb.get_all(self.statedb.STATE_DB, mibs.psu_info_table(psu_name))
        presence, status = get_psu_data(psu_info)

        return status == PSU_STATUS_OK

    def _getPsuIndex(self, sub_id):
        """
        Get the PSU index from sub_id
        :return: the index of supported PSU
        """
        if not sub_id or len(sub_id) > 1:
            return None

        psu_index = int(sub_id[0])

        try:
            psu_num = self._getPsuNum()
        except Exception:
            # Any unexpected exception or error, log it and keep running
            mibs.logger.exception("PowerStatusHandler._getPsuIndex() caught an unexpected exception during _getPsuNum()")
            return None

        if psu_index < 1 or psu_index > psu_num:
            return None

        return psu_index

    def get_next(self, sub_id):
        """
        :param sub_id: The 1-based snmp sub-identifier query.
        :return: the next sub id.
        """
        if not sub_id:
            return (1,)

        psu_index = self._getPsuIndex(sub_id)
        try:
            psu_num = self._getPsuNum()
        except Exception:
            # Any unexpected exception or error, log it and keep running
            mibs.logger.exception("PowerStatusHandler.get_next() caught an unexpected exception during _getPsuNum()")
            return None

        if psu_index and psu_index + 1 <= psu_num:
            return (psu_index + 1,)

        return None

    def getPsuStatus(self, sub_id):
        """
        :param sub_id: The 1-based sub-identifier query.
        :return: the status of requested PSU according to cefcModuleOperStatus ModuleOperType
                 2 - PSU has correct functionalling - ok
                 7 - PSU has a problem with functionalling - failed
                 8 - the module is provisioned, but it is missing. This is a failure state.
        :ref: https://www.cisco.com/c/en/us/td/docs/switches/wan/mgx/mgx_8850/software/mgx_r2-0-10/pxm/reference/guide/pxm/cscoent.html
        """
        psu_index = self._getPsuIndex(sub_id)

        if not psu_index:
            return None

        try:
            psu_presence = self._getPsuPresence(psu_index)
        except Exception:
            # Any unexpected exception or error, log it and keep running
            mibs.logger.exception("PowerStatusHandler.getPsuStatus() caught an unexpected exception during _getPsuPresence()")
            return None

        if psu_presence:
            try:
                psu_status = self._getPsuStatus(psu_index)
            except Exception:
                # Any unexpected exception or error, log it and keep running
                mibs.logger.exception("PowerStatusHandler.getPsuStatus() caught an unexpected exception during _getPsuStatus()")
                return None

            if psu_status:
                return 2

            return 7
        else:
            return 8

class cefcFruPowerStatusTable(metaclass=MIBMeta, prefix='.1.3.6.1.4.1.9.9.117.1.1.2'):
    """
    'cefcFruPowerStatusTable' http://oidref.com/1.3.6.1.4.1.9.9.117.1.1.2
    """

    power_status_handler = PowerStatusHandler()

    # cefcFruPowerStatusTable = '1.3.6.1.4.1.9.9.117.1.1.2'
    # csqIfQosGroupStatsEntry = '1.3.6.1.4.1.9.9.117.1.1.2.1'

    psu_status = SubtreeMIBEntry('1.2', power_status_handler, ValueType.INTEGER, power_status_handler.getPsuStatus)
