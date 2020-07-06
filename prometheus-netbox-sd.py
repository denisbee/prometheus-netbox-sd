#!/usr/bin/env python3
# vim: set ts=4 sw=4 sts=4 et ai:
from asyncio import tasks
from multiprocessing import Event
import os, json, argparse, itertools, filecmp, re, urllib.parse, glob, signal, time, multiprocessing, datetime, asyncio, functools
from typing import Any, Awaitable, Dict, Generator, AsyncGenerator, Union, Literal, List, Tuple, Optional, Callable
from io import TextIOBase

import pynetbox, netaddr
from aiohttp import web
from aionetbox import AIONetbox
from pynetbox.core.response import Record

# file name component of prometheus sd target file ({args.directory}/netbox_{ListName}.json)
# https://prometheus.io/docs/prometheus/latest/configuration/configuration/#file_sd_config
ListName = str

# https://prometheus.io/docs/prometheus/latest/configuration/configuration/#host
Host = str

# https://prometheus.io/docs/prometheus/latest/configuration/configuration/#labelname
LabelName = str

# https://prometheus.io/docs/prometheus/latest/configuration/configuration/#labelvalue
LabelValue = str

# https://prometheus.io/docs/prometheus/latest/configuration/configuration/#static_config
StaticConfig = Dict[
    Union[
        Literal['targets'],
        Literal['labels']
    ],
    Union[
        List[Host],
        Dict[
            LabelName,
            LabelValue
        ]
    ]
]

# incomplete_address_port = re.compile(r'^\s*(?P<scheme>https?://)?\s*(?P<port>:\d{1,5})?\s*$')
incomplete_address = re.compile(r'^\s*(?P<port>:\d{1,5})?\s*$')

class backoff_function:
    def __init__(self, minimum: float = 10, maximum: float = 320):
        self.min, self.max = minimum, maximum
        self.current = minimum
        self.last_hit_epoch = 0.0
    def __call__(self):
        t = time.time()
        if t - self.last_hit_epoch > self.max:
            self.current = self.min
        elif t - self.last_hit_epoch > self.current * 1.5:
            self.current = max(self.min, self.current / 2)
        else:
            self.current = min(self.max, self.current * 2)
        self.last_hit_epoch = t
        return self.current

backoff = backoff_function()

async def gen_prom_targets(url: str, token: str) -> AsyncGenerator[Tuple[ListName, StaticConfig], None]:
    """Generates tuples (ListName, StaticConfig) for Devices and Virtual Machines with primary IP addresses and
    `prom_targets` field in Config Contexts.

    ListName and StaticConfig iterated from keys and values of `prom_targets` json object in
    rendered Config Context of NetBox record.
    """
    netbox = pynetbox.api(url, token=token)
    loop=asyncio.get_event_loop()
    devices: Awaitable[List[Record]] = loop.run_in_executor(None, functools.partial(netbox.dcim.devices.filter, has_primary_ip=True))
    vms: Awaitable[List[Record]] = loop.run_in_executor(None, functools.partial(netbox.virtualization.virtual_machines.filter, has_primary_ip=True))
    for record in [*await devices, *await vms]:
        if hasattr(record, 'config_context') and getattr(record, 'primary_ip', None):
            try:
                address = str(netaddr.IPNetwork(record.primary_ip.address).ip)

                # use labels from `prom_labels` config context field for all prometheus targets
                labels: Dict[str, Any] = record.config_context.get('prom_labels', {})
                assert isinstance(labels, Dict)

                # set record specific labels
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
                
                # automatically add target list for every record's tag. List name urlencoded for the sake of
                # rough directory traversal protection (used as file path component later)
                prom_targets: Dict[ListName, Optional[StaticConfig]] = {
                    urllib.parse.quote(f'_tag__{tag}', safe=''): {}
                    for tag in getattr(record, 'tags', [])
                }
                # add target lists from `prom_targets` Config Context field 
                prom_targets.update({
                    urllib.parse.quote(k, safe=''): v
                    for k, v in record.config_context.get('prom_targets', {}).items()
                })
                # yield (ListName, <static_config>) tuples:
                # - skip if <static_config> is nulled (None) 
                # - update <host> with `address` if it in form of ":<port>" 
                # - use [`address`]  if `<static_config>.targets` absent
                for name, target in prom_targets.items():
                    if target != None:
                        assert isinstance(target, Dict)
                        result = {
                            'targets': list(
                                map(
                                    lambda x: incomplete_address.sub(f'{address}\\g<port>', x),
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
                    'due to errors in prom_targets or prom_labels fields of Config Context')

async def update_loop(url: str, token: str, directory: str, periodic: float, trigger: asyncio.Event) -> None:
    while True:
        await trigger.wait()
        print('Webhook event')
        
        trigger.clear()

        if not os.path.exists(directory):
            os.mkdir(directory)

        files: Dict[str, TextIOBase] = {}

        async for (name, target) in gen_prom_targets(url, token):
            if name not in files:
                files[name] = open(os.path.abspath(f'{directory}/netbox_{name}.tmp'), 'w')
                files[name].write('[\n')
            else:
                files[name].write(',\n')
            files[name].write(json.dumps(target))
        
        for name, tmpfile in files.items():
            tmpfile.write(']\n')
            tmpfile.flush()
            tmpfile.close()
            tmpfilename = os.path.abspath(f'{directory}/netbox_{name}.tmp')
            filename = os.path.abspath(f'{directory}/netbox_{name}.json')
            if os.path.isfile(filename) and filecmp.cmp(tmpfilename, filename):
                os.remove(tmpfilename)
            else:
                os.rename(tmpfilename, filename)
                print(filename, "updated")
        
        for filename in {os.path.abspath(path) for path in glob.glob(f'{directory}/netbox_*.json')} - {
        os.path.abspath(f'{directory}/netbox_{name}.json') for name in files.keys()}:
            print(filename, "removed")
            os.remove(filename)

        delay = backoff()
        print('Backoff', int(delay), 'seconds')
        await asyncio.sleep(delay)

async def server(address: str, port: int, trigger: asyncio.Event):
    async def hook(request):
        print('GET')
        trigger.set()
        return web.Response(status=204, content_type='text/plain')
    app = web.Application()
    app.add_routes([web.get('/hook', hook)])
    runner = web.AppRunner(app)
    print(f'Waiting NetBox at http://{args.address}:{args.port}/hook.')
    await runner.setup()
    site = web.TCPSite(runner, address, port)
    await site.start()
    await site._server.wait_closed()

async def main(args: argparse.Namespace):
    trigger = asyncio.Event()
    return await asyncio.wait(
        [
            asyncio.create_task(server(args.address, args.port, trigger), name='webserver'),
            asyncio.create_task(update_loop(args.url, args.token, args.directory, args.periodic, trigger), name='fetcher')
        ],
        return_when=asyncio.FIRST_COMPLETED
    )

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("url", help="URL to NetBox")
    parser.add_argument("token", help="NetBox authentication token")
    parser.add_argument("directory", help="Output directory")
    parser.add_argument("--periodic", help="Periodicity in seconds. Default is 1800", type=int, default=1800)
    parser.add_argument("--port", help="Webhook bind port. Default is 9956.", type=int, default=9956)
    parser.add_argument("--address", help="Webhook bind address. Default is 0.0.0.0. URL path is /hook", type=str, default="0.0.0.0")
    args = parser.parse_args()
    xx = asyncio.run(main(args))
    for x in xx:
        print(x)

    # update_loop(args.url, args.token, args.directory, args.periodic)