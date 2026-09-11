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

    def test_startup_waits_for_ntfy_and_provisioner_health(self):
        services = self.config['services']
        for dependency in ('ntfy', 'ntfy-provisioner'):
            self.assertEqual(services['vp']['depends_on'][dependency]['condition'], 'service_healthy')
            self.assertIn('healthcheck', services[dependency])
        self.assertEqual(services['ntfy-provisioner']['depends_on']['ntfy']['condition'], 'service_healthy')

    def test_loopback_ports_do_not_change_container_listeners_or_proxy_network(self):
        services = self.config['services']
        for service in ('app', 'vp', 'ntfy'):
            self.assertIn(':-127.0.0.1}', services[service]['ports'][0])
            self.assertIn('cal11_net', services[service]['networks'])
        self.assertIn('cal11_net', services['proxy']['networks'])
        for service in ('app', 'vp'):
            self.assertEqual(services[service]['environment']['BIND_HOST'], '${BIND_HOST:-0.0.0.0}')
        self.assertNotIn('ports', services['ntfy-provisioner'])
        self.assertEqual(services['ntfy']['environment']['NTFY_AUTH_DEFAULT_ACCESS'],
                         '${NTFY_AUTH_DEFAULT_ACCESS:-deny-all}')

    def test_uploads_and_auth_data_remain_available_as_runtime_mounts(self):
        services = self.config['services']
        self.assertIn('./uploads:/app/uploads', services['app']['volumes'])
        for service in ('ntfy', 'ntfy-provisioner'):
            self.assertIn('./data/ntfy:/var/lib/ntfy', services[service]['volumes'])

    def test_nginx_test_uses_production_notify_proxy_directives(self):
        root = Path(__file__).resolve().parents[1]
        production = (root / 'proxy/nginx.conf').read_text()
        notify_location = '    location / {' + production.rsplit('    location / {', 1)[1]
        notify_location = notify_location.replace('proxy_pass http://127.0.0.1:8090;',
                                                  'proxy_pass http://ntfy:80;')
        self.assertIn(notify_location, (root / 'tests/ntfy-proxy.nginx.conf').read_text())
