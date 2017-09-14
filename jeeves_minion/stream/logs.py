import os
import logging
from multiprocessing import Process
from contextlib import contextmanager
from pkg_resources import resource_filename

import tornado.httpserver
import tornado.ioloop
import tornado.web
import tornado.websocket
import tornado.options
from tornado.process import Subprocess


class IndexHandler(tornado.web.RequestHandler):
    def get(self):
        self.render("index.html")


class SocketHandler(tornado.websocket.WebSocketHandler):
    clients = {}
    tail_procs = {}
    cat_proc = None

    def __init__(self, *args, **kwargs):
        self.task_id = None
        super(SocketHandler, self).__init__(*args, **kwargs)

    def send_to_all(self, message):
        for c in SocketHandler.clients.get(self.task_id):
            c.write_message(message)

    def open(self, workflow_id, task_id):
        self.task_id = task_id
        log_path = self._get_log_path(workflow_id, task_id)
        if not os.path.isfile(log_path):
            # TODO: Task didn't start yet. Send notification instead of
            # creating a file.
            self._create_log_file(log_path)
        self._send_existing_data(log_path)

        self._add_client()
        if not SocketHandler.tail_procs.get(task_id):
            self._start_data_stream(log_path)

    @staticmethod
    def _get_log_path(workflow_id, task_id):
        return os.path.join('/tmp',
                            workflow_id,
                            task_id,
                            '{}.log'.format(task_id))

    def _add_client(self):
        clients = SocketHandler.clients.get(self.task_id, set())
        clients.add(self)
        SocketHandler.clients[self.task_id] = clients

    def _remove_client(self):
        clients = SocketHandler.clients.get(self.task_id)
        clients.remove(self)
        SocketHandler.clients[self.task_id] = clients

    @staticmethod
    def _add_proc(task_id, proc):
        SocketHandler.tail_procs[task_id] = proc

    def _remove_proc(self):
        proc = SocketHandler.tail_procs.get(self.task_id)
        if proc:
            proc.proc.terminate()
            proc.proc.wait()
        SocketHandler.tail_procs[self.task_id] = None

    def _start_data_stream(self, log_path):
        tail_proc = Subprocess(
            ['tail', '-f', log_path, '-n', '0'],
            stdout=Subprocess.STREAM,
            bufsize=1)
        tail_proc.set_exit_callback(self._close)
        self._add_proc(self.task_id, tail_proc)
        tail_proc.stdout.read_until('\n', self.write_line_to_clients)

    def _send_existing_data(self, log_file):

        SocketHandler.cat_proc = Subprocess(['cat', log_file],
                                            stdout=Subprocess.STREAM,
                                            bufsize=1)
        SocketHandler.cat_proc.stdout.read_until_close(
            self.write_line_to_client)
        SocketHandler.cat_proc.wait_for_exit()
        SocketHandler.cat_proc = None

    def _close(self, *args, **kwargs):
        self.close(*args, **kwargs)

    def on_close(self, *args, **kwargs):

        self._remove_client()
        if len(SocketHandler.clients.get(self.task_id)) == 0:
            logging.info('All clients disconnected. Killing tail process for '
                         'task {0}'.format(self.task_id))
            self._remove_proc()

    def write_line_to_clients(self, data):
        logging.info('Returning to clients: %s' % data.strip())
        self.send_to_all(data.strip() + '<br/>')
        tail_proc = SocketHandler.tail_procs.get(self.task_id)
        tail_proc.stdout.read_until('\n', self.write_line_to_clients)

    def write_line_to_client(self, data):
        logging.info('Returning to client: %s' % data.strip())
        self.write_message(data.strip().replace('\n', '<br/>') +
                           '<br/>' + '\n')

    @staticmethod
    def _create_log_file(log_path):
        if not os.path.exists(log_path):
            if not os.path.isdir(os.path.dirname(log_path)):
                os.makedirs(os.path.dirname(log_path))
            open(log_path, 'a').close()


class LogStreamHttpServer(object):

    def __init__(self):
        self.tornado_proc = None

    @staticmethod
    def _start_tornado_instance(port):
        app = tornado.web.Application(
            handlers=[(r'/', IndexHandler),
                      (r'/tail/(.*)/(.*)', SocketHandler)],
            template_path=resource_filename('stream', 'resources'),
            # static_path = os.path.join(os.path.dirname(__file__), 'static'
        )
        # define('port',
        #        default=self.port,
        #        help='Run server on port {}'.format(self.port),
        #        type=int)
        server = tornado.httpserver.HTTPServer(app)
        tornado.options.parse_command_line()
        server.listen(port)
        logging.info('Execution logs available at http://localhost:{}/tail'
                     .format(port))
        tornado.ioloop.IOLoop.instance().start()

    @contextmanager
    def with_start(self, port):

        self.tornado_proc = Process(target=self._start_tornado_instance,
                                    args=[port])
        try:
            self.tornado_proc.start()
            yield self
        finally:
            self._close()

    def start(self, port):
        self.tornado_proc = Process(target=self._start_tornado_instance,
                                    args=[port])
        self.tornado_proc.start()

    def _join(self):
        self.tornado_proc.join()

    def _close(self):
        self.tornado_proc.terminate()


streamer = LogStreamHttpServer()


if __name__ == '__main__':
    with streamer.with_start(7777) as stream:
        stream._join()


# if __name__ == '__main__':
#     streamer = LogStreamHttpServer('/tmp/log.test', 7777)
#     streamer.start()
#     streamer._join()
