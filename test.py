import os, signal, time, bjoern
def h(_p0, _p1) -> None:
    print('signaled')
    os._exit(0)

def application(a, b):
    pass

signal.signal(signal.SIGCHLD, h)

if not os.fork():
    print('child')
    time.sleep(10)
    os._exit(0)
bjoern.run(application, '0.0.0.0', 9956, reuse_port=True)
print('end')