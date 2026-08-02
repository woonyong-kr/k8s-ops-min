from __future__ import annotations

import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def render_metrics() -> bytes:
    series_count = max(1, int(os.environ.get("PAYLOAD_METRIC_SERIES", "250")))
    rows = [
        "# HELP kyro_payload_metric Deterministic payload experiment series.",
        "# TYPE kyro_payload_metric gauge",
    ]
    for index in range(series_count):
        value = 99 if index == series_count // 2 else (index % 17) + 1
        rows.append(
            'kyro_payload_metric{scenario_id="payload-bench",namespace="payload-bench",'
            f'series="series-{index:04d}"}} {value}'
        )
    return ("\n".join(rows) + "\n").encode()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path not in {"/", "/metrics", "/healthz"}:
            self.send_error(404)
            return
        body = b"ok\n" if self.path == "/healthz" else render_metrics()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        return


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 9200), Handler).serve_forever()
