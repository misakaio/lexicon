from __future__ import absolute_import
import json
import logging

import requests
from lexicon.providers.base import Provider as BaseProvider


LOGGER = logging.getLogger(__name__)

NAMESERVER_DOMAINS = ['conoha.io']


def ProviderParser(subparser):
    subparser.add_argument(
        "--auth-region", help="specify region. If empty, region `tyo1` will be used.")
    subparser.add_argument(
        "--auth-token", help="specify token for authentication. If empty, the username and password will be used to create a token.")
    subparser.add_argument(
        "--auth-username", help="specify api username for authentication. Only used if --auth-token is empty.")
    subparser.add_argument(
        "--auth-password", help="specify api user password for authentication. Only used if --auth-token is empty.")
    subparser.add_argument(
        "--auth-tenant-id", help="specify tenand id for authentication. Only used if --auth-token is empty.")


class Provider(BaseProvider):

    def __init__(self, config):
        super(Provider, self).__init__(config)
        self.domain_id = None

        self.api_endpoint = ('https://dns-service.{0}.conoha.io/v1'
                             .format(self._get_provider_option('region') or 'tyo1'))
        self.auth_api_endpoint = ('https://identity.{0}.conoha.io/v2.0'
                                  .format(self._get_provider_option('region') or 'tyo1'))
        self.auth_token = None

    # Authenticate against provider,
    # Make any requests required to get the domain's id for this provider, so it can be used in subsequent calls.
    # Should throw an error if authentication fails for any reason, of if the domain does not exist.
    def authenticate(self):
        self.auth_token = self._get_provider_option('auth_token')
        if not self.auth_token:
            if not (self._get_provider_option('auth_username')
                    and self._get_provider_option('auth_password')):
                raise Exception(
                    "auth_username and auth_password or auth_token must be specified.")
            auth_response = self._send_request('POST', '{0}/tokens'
                                               .format(self.auth_api_endpoint), {
                                                   'auth': {
                                                       'passwordCredentials': {
                                                           'username': self._get_provider_option('auth_username'),
                                                           'password': self._get_provider_option('auth_password')
                                                       },
                                                       'tenantId': self._get_provider_option('auth_tenant_id')
                                                   }
                                               })
            self.auth_token = auth_response['access']['token']['id']

        payload = self._get('/domains', {
            'name': self._fqdn_name(self.domain)
        })

        if not payload['domains']:
            raise Exception('No domain found')
        if len(payload['domains']) > 1:
            raise Exception('Too many domains found. This should not happen')

        self.domain_id = payload['domains'][0]['id']

    # Create record. If record already exists with the same content, do nothing'
    def create_record(self, type, name, content):
        if not type:
            raise Exception("type must be specified.")
        if not name:
            raise Exception("name must be specified.")
        if not content:
            raise Exception("content must be specified.")
        if not self._get_lexicon_option('priority') and type in ("MX", "SRV"):
            raise Exception("priority must be specified.")

        try:
            self._post('/domains/{0}/records'.format(self.domain_id),
                       self._record_payload(type, name, content))
        except requests.exceptions.HTTPError as err:
            # 409 Duplicate Record
            if err.response.status_code != 409:
                raise err

        LOGGER.debug('create_record: %s', True)
        return True

    # List all records. Return an empty list if no records found
    # type, name and content are used to filter records.
    # If possible filter during the query, otherwise filter after response is received.
    def list_records(self, type=None, name=None, content=None):
        payload = self._get('/domains/{0}/records'.format(self.domain_id))
        records = payload['records']

        if type:
            records = [record for record in records if record['type'] == type]
        if name:
            records = [record for record in records if record['name']
                       == self._fqdn_name(name)]
        if content:
            records = [
                record for record in records if record['data'] == content]

        records = [{
            'type': record['type'],
            'name': self._full_name(record['name']),
            'ttl': record['ttl'],
            'content': record['data'],
            'id': record['id']
        } for record in records]

        LOGGER.debug('list_records: %s', records)
        return records

    # Update a record. Identifier must be specified.
    def update_record(self, identifier, type=None, name=None, content=None):
        if not identifier:
            records = self.list_records(type, name)
            if len(records) != 1:
                raise Exception("Cannot determine record")
            identifier = records[0]['id']

        self._put('/domains/{0}/records/{1}'
                  .format(self.domain_id, identifier), self._record_payload(type, name, content))

        LOGGER.debug('update_record: %s', True)
        return True

    # Delete an existing record.
    # If record does not exist, do nothing.
    # If an identifier is specified, use it, otherwise do a lookup using type, name and content.
    def delete_record(self, identifier=None, type=None, name=None, content=None):
        records = self.list_records(type, name, content)

        if identifier:
            records = [
                record for record in records if record['id'] == identifier]

        for record in records:
            self._delete(
                '/domains/{0}/records/{1}'.format(self.domain_id, record['id']))

        LOGGER.debug('delete_record: %s', True)
        return True

    # Helpers
    def _request(self, action='GET', url='/', data=None, query_params=None):
        return self._send_request(action, '{0}{1}'.format(self.api_endpoint, url),
                                  data, query_params)

    def _send_request(self, action, url, data=None, query_params=None):
        if data is None:
            data = {}
        if query_params is None:
            query_params = {}
        r = requests.request(action, url, data=json.dumps(data), params=query_params, headers={
            'X-Auth-Token': self.auth_token,
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        })
        # if the request fails for any reason, throw an error.
        r.raise_for_status()
        return r.json() if r.headers['content-type'].startswith('application/json') else r.text

    def _record_name(self, name):
        return '%s.' % name.rstrip('.') if name else None

    def _record_payload(self, type, name, content):
        priority = self._get_lexicon_option('priority')
        ttl = self._get_lexicon_option('ttl')
        return {
            'name': self._fqdn_name(name) if name else None,
            'type': type,
            'data': self._record_name(content) if type in ("CNAME", "MX", "NS", "SRV") else content,
            'priority': int(priority) if priority else None,
            'ttl': int(ttl) if ttl else None
        }
