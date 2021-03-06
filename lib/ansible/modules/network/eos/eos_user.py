#!/usr/bin/python
#
# This file is part of Ansible
#
# Ansible is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Ansible is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Ansible.  If not, see <http://www.gnu.org/licenses/>.
#

DOCUMENTATION = """
---
module: eos_user
version_added: "2.3"
author: "Peter Sprygada (@privateip)"
short_description: Manage the collection of local users on EOS devices
description:
  - This module provides declarative management of the local usernames
    configured on Arista EOS devices.  It allows playbooks to manage
    either individual usernames or the collection of usernames in the
    current running config.  It also supports purging usernames from the
    configuration that are not explicitly defined.
notes:
  - This module requires connection to be network_cli
options:
  users:
    description:
      - The set of username objects to be configured on the remote
        Arista EOS device.  The list entries can either be the username
        or a hash of username and properties.  This argument is mutually
        exclusive with the C(username) argument.
    required: false
    default: null
  username:
    description:
      - The username to be configured on the remote Arista EOS
        device.  This argument accepts a stringv value and is mutually
        exclusive with the C(users) argument.
    required: false
    default: null
  update_password:
    description:
      - Since passwords are encrypted in the device running config, this
        argument will instruct the module when to change the password.  When
        set to C(always), the password will always be updated in the device
        and when set to C(on_create) the password will be updated only if
        the username is created.
    required: false
    default: always
    choices: ['on_create', 'always']
  privilege:
    description:
      - The C(privilege) argument configures the privilege level of the
        user when logged into the system.  This argument accepts integer
        values in the range of 1 to 15.
    required: false
    default: null
  role:
    description:
      - The C(role) argument configures the role for the username in the
        device running configuration.  The argument accepts a string value
        defining the role name.  This argument does not check if the role
        has been configured on the device.
    required: false
    default: null
  sshkey:
    description:
      - The C(sshkey) argument defines the SSH public key to configure
        for the username.  This argument accepts a valid SSH key value.
    required: false
    default: null
  nopassword:
    description:
      - The C(nopassword) argument defines the username without assigning
        a password.  This will allow the user to login to the system
        without being authenticated by a password.  This argument accepts
        boolean values.
    required: false
    default: null
    choices: ['true', 'false']
  purge:
    description:
      - The C(purge) argument instructs the module to consider the
        resource definition absolute.  It will remove any previously
        configured usernames on the device with the exception of the
        `admin` user which cannot be deleted per EOS constraints.
    required: false
    default: false
  state:
    description:
      - The C(state) argument configures the state of the uername definition
        as it relates to the device operational configuration.  When set
        to I(present), the username(s) should be configured in the device active
        configuration and when set to I(absent) the username(s) should not be
        in the device active configuration
    required: false
    default: present
    choices: ['present', 'absent']
"""

EXAMPLES = """
- name: create a new user
  eos_user:
    username: ansible
    sshkey: "{{ lookup('file', '~/.ssh/id_rsa.pub') }}"
    state: present

- name: remove all users except admin
  eos_user:
    purge: yes

- name: set multiple users to privilege level
  users:
    - username: netop
    - username: netend
  privilege: 15
  state: present
"""

RETURN = """
commands:
  description: The list of configuration mode commands to send to the device
  returned: always
  type: list
  sample:
    - username ansible secret password
    - username admin secret admin
session_name:
  description: The EOS config session name used to load the configuration
  returned: when changed is True
  type: str
  sample: ansible_1479315771
start:
  description: The time the job started
  returned: always
  type: str
  sample: "2016-11-16 10:38:15.126146"
end:
  description: The time the job ended
  returned: always
  type: str
  sample: "2016-11-16 10:38:25.595612"
delta:
  description: The time elapsed to perform all operations
  returned: always
  type: str
  sample: "0:00:10.469466"
"""
import re

from functools import partial

from ansible.module_utils.local import LocalAnsibleModule
from ansible.module_utils.eos import get_config, load_config
from ansible.module_utils.six import iteritems

def validate_privilege(value, module):
    if not 1 <= value <= 15:
        module.fail_json(msg='privilege must be between 1 and 15, got %s' % value)

def map_obj_to_commands(updates, module):
    commands = list()
    state = module.params['state']
    update_password = module.params['update_password']

    for update in updates:
        want, have = update

        needs_update = lambda x: want.get(x) and (want.get(x) != have.get(x))
        add = lambda x: commands.append('username %s %s' % (want['username'], x))

        if want['state'] == 'absent':
            commands.append('no username %s' % want['username'])
            continue

        if needs_update('role'):
            add('role %s' % want['role'])

        if needs_update('privilege'):
            add('privilege %s' % want['privilege'])

        if needs_update('password'):
            if update_password == 'always' or not have:
                add('secret %s' % want['password'])

        if needs_update('sshkey'):
            add('sshkey %s' % want['sshkey'])

        if needs_update('nopassword'):
            if want['nopassword']:
                add('nopassword')
            else:
                add('no username %s nopassword' % want['username'])

    return commands

def parse_role(data):
    match = re.search(r'role (\S+)', data, re.M)
    if match:
        return match.group(1)

def parse_sshkey(data):
    match = re.search(r'sshkey (.+)$', data, re.M)
    if match:
        return match.group(1)

def parse_privilege(data):
    match = re.search(r'privilege (\S+)', data, re.M)
    if match:
        return int(match.group(1))

def map_config_to_obj(module):
    data = get_config(module, flags=['section username'])

    match = re.findall(r'^username (\S+)', data, re.M)
    if not match:
        return list()

    instances = list()

    for user in set(match):
        regex = r'username %s .+$' % user
        cfg = re.findall(r'username %s .+$' % user, data, re.M)
        cfg = '\n'.join(cfg)
        obj = {
            'username': user,
            'state': 'present',
            'nopassword': 'nopassword' in cfg,
            'password': None,
            'sshkey': parse_sshkey(cfg),
            'privilege': parse_privilege(cfg),
            'role': parse_role(cfg)
        }
        instances.append(obj)

    return instances

def get_param_value(key, item, module):
    # if key doesn't exist in the item, get it from module.params
    if not item.get(key):
        value = module.params[key]

    # if key does exist, do a type check on it to validate it
    else:
        value_type = module.argument_spec[key].get('type', 'str')
        type_checker = module._CHECK_ARGUMENT_TYPES_DISPATCHER[value_type]
        type_checker(item[key])
        value = item[key]

    # validate the param value (if validator func exists)
    validator = globals().get('validate_%s' % key)
    if all((value, validator)):
        validator(value, module)

    return value

def map_params_to_obj(module):
    users = module.params['users']
    if not users:
        if not module.params['username'] and module.params['purge']:
            return list()
        elif not module.params['username']:
            module.fail_json(msg='username is required')
        else:
            collection = [{'username': module.params['username']}]
    else:
        collection = list()
        for item in users:
            if not isinstance(item, dict):
                collection.append({'username': item})
            elif 'username' not in item:
                module.fail_json(msg='username is required')
            else:
                collection.append(item)

    objects = list()

    for item in collection:
        get_value = partial(get_param_value, item=item, module=module)
        item['password'] = get_value('password')
        item['nopassword'] = get_value('nopassword')
        item['privilege'] = get_value('privilege')
        item['role'] = get_value('role')
        item['sshkey'] = get_value('sshkey')
        item['state'] = get_value('state')
        objects.append(item)

    return objects

def update_objects(want, have):
    updates = list()
    for entry in want:
        item = next((i for i in have if i['username'] == entry['username']), None)
        if all((item is None, entry['state'] == 'present')):
            updates.append((entry, {}))
        elif item:
            for key, value in iteritems(entry):
                if value and value != item[key]:
                    updates.append((entry, item))
    return updates

def main():
    """ main entry point for module execution
    """
    argument_spec = dict(
        users=dict(type='list', no_log=True),
        username=dict(),

        password=dict(no_log=True),
        nopassword=dict(type='bool'),
        update_password=dict(default='always', choices=['on_create', 'always']),

        privilege=dict(type='int'),
        role=dict(),

        sshkey=dict(),

        purge=dict(type='bool', default=False),
        state=dict(default='present', choices=['present', 'absent'])
    )

    mutually_exclusive = [('username', 'users')]

    module = LocalAnsibleModule(argument_spec=argument_spec,
                                mutually_exclusive=mutually_exclusive,
                                supports_check_mode=True)

    result = {'changed': False}

    want = map_params_to_obj(module)
    have = map_config_to_obj(module)

    commands = map_obj_to_commands(update_objects(want, have), module)

    if module.params['purge']:
        want_users = [x['username'] for x in want]
        have_users = [x['username'] for x in have]
        for item in set(have_users).difference(want_users):
            if item != 'admin':
                commands.append('no username %s' % item)

    result['commands'] = commands

    # the eos cli prevents this by rule so capture it and display
    # a nice failure message
    if 'no username admin' in commands:
        module.fail_json(msg='cannot delete the `admin` account')

    if commands:
        commit = not module.check_mode
        response = load_config(module, commands, commit=commit)
        if response.get('diff') and module._diff:
            result['diff'] = {'prepared': response.get('diff')}
        result['session_name'] = response.get('session')
        result['changed'] = True

    module.exit_json(**result)

if __name__ == '__main__':
    main()
