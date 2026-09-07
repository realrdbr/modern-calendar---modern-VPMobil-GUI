"""Protect the routing and trust boundaries that prevent internal publish 429s."""
from pathlib import Path
import unittest

import yaml


class DeliveryNetworkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = yaml.safe_load((Path(__file__).resolve().parents[1] / 'docker-compose.yml').read_text())

    def test_only_ntfy_and_vp_join_internal_delivery_network(self):
        members = {name for name, service in self.config['services'].items()
                   if 'ntfy_delivery' in service.get('networks', {})}
        self.assertEqual(members, {'vp', 'ntfy'})
        self.assertTrue(self.config['networks']['ntfy_delivery']['internal'])

    def test_exemption_tracks_static_vp_address_and_route(self):
        services = self.config['services']
        self.assertEqual(services['ntfy']['environment']['NTFY_VISITOR_REQUEST_LIMIT_EXEMPT_HOSTS'],
                         services['vp']['networks']['ntfy_delivery']['ipv4_address'])
        self.assertEqual(services['vp']['environment']['NTFY_INTERNAL_URL'], 'http://ntfy-delivery')
        self.assertIn('ntfy-delivery', services['ntfy']['networks']['ntfy_delivery']['aliases'])
        self.assertNotEqual(services['vp']['networks']['ntfy_delivery']['ipv4_address'],
                            services['ntfy']['networks']['ntfy_delivery']['ipv4_address'])
