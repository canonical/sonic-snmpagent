import os
import sys
from unittest import TestCase

import tests.mock_tables.dbconnector
from sonic_ax_impl.mibs import Namespace

modules_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(modules_path, 'src'))

from sonic_ax_impl import mibs

class TestGetNextPDU(TestCase):
    @classmethod
    def setUpClass(cls):
        tests.mock_tables.dbconnector.load_namespace_config()

    def test_init_namespace_sync_d_lag_tables(self):
        dbs = Namespace.init_namespace_dbs()

        lag_name_if_name_map, \
        if_name_lag_name_map, \
        oid_lag_name_map, \
        lag_sai_map = Namespace.init_namespace_sync_d_lag_tables(dbs)
        #PortChannel in asic0 Namespace
        self.assertTrue(b"PortChannel01" in lag_name_if_name_map)
        self.assertTrue(b"Ethernet-BP0" in lag_name_if_name_map[b"PortChannel01"])
        self.assertTrue(b"Ethernet-BP4" in lag_name_if_name_map[b"PortChannel01"])
        #PortChannel in asic2 Namespace
        self.assertTrue(b"PortChannel03" in lag_name_if_name_map)
        self.assertTrue(b"Ethernet-BP16" in lag_name_if_name_map[b"PortChannel03"])
        self.assertTrue(b"Ethernet-BP20" in lag_name_if_name_map[b"PortChannel03"])

        self.assertTrue(b"PortChannel_Temp" in lag_name_if_name_map)
        self.assertTrue(lag_name_if_name_map[b"PortChannel_Temp"] == [])

    @classmethod
    def tearDownClass(cls):
        tests.mock_tables.dbconnector.clean_up_config()
