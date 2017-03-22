import os
import logging
from multiprocessing import Process
from contextlib import contextmanager


import tornado.httpserver
import tornado.ioloop
import tornado.web
import tornado.websocket
from tornado.process import Subprocess


class IndexHandler(tornado.web.RequestHandler):
    def get(self):
        self.render("index.html")


class SocketHandler(tornado.websocket.WebSocketHandler):
    clients = set()
    tail_proc = None
    cat_proc = None
    log_path = None

    def __init__(self, *args, **kwargs):
        SocketHandler.log_path = kwargs.get('log_path')
        if not self.log_path:
            raise RuntimeError('Log file path was not provided. '
                               'Socket closed.')
        kwargs.pop('log_path')
        super(SocketHandler, self).__init__(*args, **kwargs)

    @staticmethod
    def send_to_all(message):
        for c in SocketHandler.clients:
            c.write_message(message)

    def open(self):
        self._send_existing_data()

        SocketHandler.clients.add(self)
        if not SocketHandler.tail_proc:
            self._start_data_stream()

    def _start_data_stream(self):
        SocketHandler.tail_proc = Subprocess(
            ['tail', '-f', SocketHandler.log_path, '-n', '0'],
            stdout=Subprocess.STREAM,
            bufsize=1)
        SocketHandler.tail_proc.set_exit_callback(self._close)
        SocketHandler.tail_proc.stdout.read_until('\n',
                                                  self.write_line_to_clients)

    def _send_existing_data(self):
        SocketHandler.cat_proc = Subprocess(['cat', self.log_path],
                                            stdout=Subprocess.STREAM,
                                            bufsize=1)
        SocketHandler.cat_proc.stdout.read_until_close(
            self.write_line_to_client)
        SocketHandler.cat_proc.wait_for_exit()
        SocketHandler.cat_proc = None

    def _close(self, *args, **kwargs):
        self.close()

    def on_close(self, *args, **kwargs):
        SocketHandler.clients.remove(self)
        if len(SocketHandler.clients) == 0:
            logging.info('All clients disconnected. Killing tail process for '
                         'log {0}'.format(self.log_path))
            SocketHandler.tail_proc.proc.terminate()
            SocketHandler.tail_proc.proc.wait()
            SocketHandler.tail_proc = None

    def write_line_to_clients(self, data):
        logging.info("Returning to clients: %s" % data.strip())
        SocketHandler.send_to_all(data.strip() + '<br/>')
        SocketHandler.tail_proc.stdout.read_until('\n',
                                                  self.write_line_to_clients)

    def write_line_to_client(self, data):
        logging.info("Returning to client: %s" % data.strip())
        self.write_message(data.strip().replace('\n', '<br/>') + '<br/>')


class LogStreamHttpServer(object):

    def __init__(self, create_file=True):
        self.create_file = create_file
        self.tornado_proc = None

    @staticmethod
    def _create_log_file(log_path):
        if not os.path.exists(log_path):
            if not os.path.isdir(os.path.dirname(log_path)):
                os.makedirs(os.path.dirname(log_path))
            open(log_path, 'a').close()

    @staticmethod
    def _start_tornado_instance(log_path, port):
        app = tornado.web.Application(
            handlers=[(r'/', IndexHandler),
                      (r'/tail', SocketHandler, {'log_path': log_path})],
            template_path=os.path.join(os.path.dirname(__file__), 'resources'),
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
    def start(self, log_path, port):
        if self.create_file:
            self._create_log_file(log_path)
            self.tornado_proc = Process(target=self._start_tornado_instance,
                                        args=(log_path, port))
        try:
            self.tornado_proc.start()
            yield self
        finally:
            self.tornado_proc.terminate()

    def _join(self):
        self.tornado_proc.join()

    def _close(self):
        self.tornado_proc.terminate()


streamer = LogStreamHttpServer()


# if __name__ == '__main__':
#     with streamer.start('/tmp/log.test', 7777) as stream:
#         stream._join()


# if __name__ == '__main__':
#     streamer = LogStreamHttpServer('/tmp/log.test', 7777)
#     streamer.start()
#     streamer._join()
