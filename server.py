"""Container entry point. One process owns every in-memory workspace."""
import logging
import os
import signal
import threading

from waitress import create_server
from app import create_app
from hosted import HostedSettings


def main():
    settings = HostedSettings.from_env()
    app = create_app(os.environ.get('DATA_DIR', '/data'), hosted=settings)
    # No request-body, browser-console, cookie, or traceback logging.
    logging.getLogger('waitress').setLevel(logging.CRITICAL)
    server = create_server(app, host='0.0.0.0', port=8090, threads=8,
                           connection_limit=128, channel_timeout=150,
                           max_request_body_size=98304, max_request_header_size=16384,
                           clear_untrusted_proxy_headers=True)
    stopping = threading.Event()
    signal.signal(signal.SIGTERM, lambda signum, frame: stopping.set())

    def serve():
        try:
            server.run()
        finally:
            stopping.set()

    worker = threading.Thread(target=serve, daemon=True, name='http-listener')
    worker.start()
    print('GoodnightUESTC team backend ready on container port 8090', flush=True)
    try:
        stopping.wait()
    except KeyboardInterrupt:
        pass
    finally:
        server.accepting = False
        server.task_dispatcher.shutdown(timeout=125)
        app.extensions['workspaces'].close()
        server.close()


if __name__ == '__main__':
    main()
