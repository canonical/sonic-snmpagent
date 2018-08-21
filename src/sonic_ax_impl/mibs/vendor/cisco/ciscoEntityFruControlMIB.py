import imp
import re
import sys

from sonic_ax_impl import mibs
from ax_interface import MIBMeta, ValueType, MIBUpdater, MIBEntry, SubtreeMIBEntry
from ax_interface.encodings import ObjectIdentifier

PSU_PLUGIN_MODULE_NAME = 'psuutil'
PSU_PLUGIN_MODULE_PATH = "/usr/share/sonic/platform/plugins/{}.py".format(PSU_PLUGIN_MODULE_NAME)
PSU_PLUGIN_CLASS_NAME = 'PsuUtil'

class PowerStatusHandler:
    """
    Class to handle the SNMP request
    """
    def __init__(self):
        """
        init the handler
        """
        self.psuutil = None

        try:
            module = imp.load_source(PSU_PLUGIN_MODULE_NAME, PSU_PLUGIN_MODULE_PATH)
        except ImportError as e:
            mibs.logger.error("Failed to load PSU module '%s': %s" % (PSU_PLUGIN_MODULE_NAME, str(e)), True)
            return
        except FileNotFoundError as e:
            mibs.logger.error("Failed to get platform specific PSU module '%s': %s" % (PSU_PLUGIN_MODULE_NAME, str(e)), True)
            return

        try:
            platform_psuutil_class = getattr(module, PSU_PLUGIN_CLASS_NAME)
            self.psuutil = platform_psuutil_class()
        except AttributeError as e:
            mibs.logger.error("Failed to instantiate '%s' class: %s" % (PLATFORM_SPECIFIC_CLASS_NAME, str(e)), True)

    def _getPsuIndex(self, sub_id):
        """
        Get the PSU index from sub_id
        :return: the index of supported PSU
        """
        if not self.psuutil or not sub_id or len(sub_id) > 1:
            return None

        psu_index = int(sub_id[0])

        try:
            num_psus = self.psuutil.get_num_psus()
        except Exception:
            # Any unexpected exception or error, log it and keep running
            mibs.logger.exception("PowerStatusHandler._getPsuIndex() caught an unexpected exception during get_num_psus()")
            return None

        if psu_index < 1 or psu_index > num_psus:
            return None

        return psu_index

    def get_next(self, sub_id):
        """
        :param sub_id: The 1-based snmp sub-identifier query.
        :return: the next sub id.
        """
        if not self.psuutil:
            return None

        if not sub_id:
            return (1,)

        psu_index = self._getPsuIndex(sub_id)
        try:
            num_psus = self.psuutil.get_num_psus()
        except Exception:
            # Any unexpected exception or error, log it and keep running
            mibs.logger.exception("PowerStatusHandler.get_next() caught an unexpected exception during get_num_psus()")
            return None


        if psu_index and psu_index + 1 <= num_psus:
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
            psu_presence = self.psuutil.get_psu_presence(psu_index)
        except Exception:
            # Any unexpected exception or error, log it and keep running
            mibs.logger.exception("PowerStatusHandler.getPsuStatus() caught an unexpected exception during get_psu_presence()")
            return None

        if psu_presence:
            try:
                psu_status = self.psuutil.get_psu_status(psu_index)
            except Exception:
                # Any unexpected exception or error, log it and keep running
                mibs.logger.exception("PowerStatusHandler.getPsuStatus() caught an unexpected exception during get_psu_status()")
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
