import os

import multiprocessing

port = int(os.environ.get('PORT', 5000))

bind = '0.0.0.0:{}'.format(port)

workers = min(multiprocessing.cpu_count(), 4)

backlog = 1024

worker_class = 'gevent'

worker_connections = 32

max_requests = 256

timeout = 60

keepalive = 30
