#!/usr/bin/env python3
# vim: set ts=4 sw=4 sts=4 et ai:

import os, json, argparse, itertools, filecmp, re, urllib.parse, glob
from typing import Any, Dict, Generator, Union, Literal, List, Tuple, Set
from io import TextIOBase

import pynetbox, netaddr
from pynetbox.core.response import Record

ListName = str
HostStr = str
LabelName = str
LabelValue = str
PromTarget = Dict[
    Union[
        Literal['targets'],
        Literal['labels']
    ],
    Union[
        List[HostStr],
        Dict[
            LabelName,
            LabelValue
        ]
    ]
]

notcomplete = re.compile(r'^\s*(?P<scheme>https?://)?\s*(?P<port>:\d{1,5})?\s*$')

def gen_prom_targets_for_record(record: Record) -> Generator[Tuple[ListName, PromTarget], None, None]:
    if hasattr(record, 'config_context') and getattr(record, 'primary_ip', None):
        try:
            address = str(netaddr.IPNetwork(record.primary_ip.address).ip)
            labels: Dict[str, Any] = record.config_context.get('prom_labels', {})
            def setlabel(lab: str, val: str):
                if val and val.strip() != '':
                    labels[f'__meta_netbox_{lab}'] = str(val)
            setlabel("name", getattr(record, 'name', repr(record)))
            setlabel("site_name", getattr(getattr(record, 'site', {}), 'name', None))
            setlabel("site_slug", getattr(getattr(record, 'site', {}), 'slug', None))
            setlabel("device_type_model", getattr(getattr(record, 'device_type', {}), 'model', None))
            setlabel("device_type",
                getattr(
                    getattr(
                        getattr(record, 'device_type', {}),
                        'manufacturer',
                        {}
                    ),
                    'name',
                    ''
                ) + ' ' + getattr(getattr(record, 'device_type', {}), 'model', ''))
            assert isinstance(labels, Dict)
            prom_targets = {
                urllib.parse.quote(f'_devices_tag_{tag}', safe=''): {}
                for tag in getattr(record, 'tags', [])
            }
            prom_targets.update({
                urllib.parse.quote(k, safe=''): v
                for k, v in record.config_context.get('prom_targets', {}).items()
            })
            for name, target in prom_targets.items():
                if target != False:
                    assert isinstance(target, Dict)
                    result = {
                        'targets': list(
                            map(
                                lambda x: notcomplete.sub(f'\\g<scheme>{address}\\g<port>', x),
                                target.get('targets', [address])
                            )
                        ),
                        'labels': labels.copy()
                    }
                    result['labels'].update(target.get('labels', {}))
                    if result['targets'] != []:
                        for label, value in result['labels'].items():
                            if not value:
                                del result['labels'][label]
                        if result['labels'] == {}:
                            del result['labels'] 
                        yield name, result
        except AssertionError:
            print(f'Record "{record}" ({record.primary_ip}) ignored '
                'due to errors in prom_targets or prom_labels field of configcontext')

def gen_prom_targets(url: str, token: str) -> Generator[Tuple[ListName, PromTarget], None, None]:
    netbox = pynetbox.api(url, token=token)
    devices: List[Record] = netbox.dcim.devices.filter(has_primary_ip=True)
    vm: List[Record] = netbox.virtualization.virtual_machines.filter(has_primary_ip=True)
    for record in itertools.chain(devices, vm):
        yield from gen_prom_targets_for_record(record)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("url", help="URL to Netbox")
    parser.add_argument("token", help="Authentication Token")
    parser.add_argument("directory", help="Output directory")
    args = parser.parse_args()

    if not os.path.exists(args.directory):
        os.mkdir(args.directory)

    files: Dict[str, TextIOBase] = {}

    for (name, target) in gen_prom_targets(args.url, args.token):
        if name not in files:
            files[name] = open(os.path.abspath(f'{args.directory}/{name}.tmp'), 'w')
            files[name].write('[\n')
        else:
            files[name].write(',\n')
        files[name].write(json.dumps(target))
    
    for name, tmpfile in files.items():
        tmpfile.write(']\n')
        tmpfile.flush()
        tmpfile.close()
        tmpfilename = os.path.abspath(f'{args.directory}/{name}.tmp')
        filename = os.path.abspath(f'{args.directory}/{name}.json')
        if os.path.isfile(filename) and filecmp.cmp(tmpfilename, filename):
            os.remove(tmpfilename)
        else:
            os.rename(tmpfilename, filename)
            print(filename, "updated")
    
    for filename in {os.path.abspath(path) for path in glob.glob(f'{args.directory}/*.json')} - {
    os.path.abspath(f'{args.directory}/{name}.json') for name in files.keys()}:
        print(filename, "removed")
        os.remove(filename)
